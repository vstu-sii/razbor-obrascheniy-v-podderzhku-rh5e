"""EVT transport tests use synthetic input, real transactions and controlled clocks."""
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('event_api', ROOT/'backend/main.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
from worker import step


class EventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = os.environ.get('EVENT_TEST_DATABASE_URL') or Path(self.temp.name)/'test.db'
        if os.environ.get('EVENT_TEST_DATABASE_URL'):
            # Refuse to run destructive fixture setup against any non-test endpoint.
            assert str(self.target) == 'postgresql://audit:synthetic_audit_only@127.0.0.1:18092/event_audit'
            import psycopg
            with psycopg.connect(self.target) as c:
                c.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public;')
        self.engine = api.Baseline(api.read_jsonl(ROOT/'data/kb.jsonl'), api.SCHEMA, api.provenance_gate)
        self.client = TestClient(api.create_app(self.target, self.engine, 'synthetic-test', start_worker=False))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.queue = self.client.app.state.queue
        self.headers = {'Authorization':'Bearer synthetic-test'}
        self.body = {'request_id':str(uuid4()), 'ticket_ref':'synthetic-ticket',
                     'ticket_text':'Где скачать счёт?', 'history':[], 'kb_version':'v1'}

    def enqueue(self, **extra):
        return self.client.post('/api/demo/tickets', json=self.body|extra, headers=self.headers)

    def status(self, job):
        return self.client.get('/api/jobs/'+job, headers=self.headers).json()

    def count(self, table):
        assert table in ('analysis_job','analysis_event','model_attempt','analysis_proposal')
        with self.queue.connect() as c:
            return c.execute('SELECT COUNT(*) AS n FROM '+table).fetchone()['n']

    def test_creation_queues_without_inference_and_worker_persists_card(self):
        response = self.enqueue()
        self.assertEqual(response.status_code, 202)
        job = response.json()['job_id']
        self.assertEqual(self.status(job)['state'], 'queued')
        self.assertEqual(self.count('analysis_proposal'), 0)
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'ready')
        self.assertEqual(self.count('analysis_proposal'), 1)
        self.assertEqual(self.count('model_attempt'), 1)
        trace = self.client.get('/api/traces', headers=self.headers).json()[0]
        self.assertIn('queue_wait_ms', trace)
        self.assertIn('end_to_end_ms', trace)
        self.assertNotIn('ticket_text', trace)

    def test_duplicate_and_changed_input_same_id(self):
        first = self.enqueue().json()['job_id']
        self.assertEqual(self.enqueue().json(), {'job_id':first, 'duplicate':True})
        self.assertEqual(self.enqueue(ticket_text='Другое').status_code, 409)
        self.assertEqual(self.count('analysis_job'), 1)

    def test_newer_queued_version_supersedes_previous(self):
        first = self.enqueue().json()['job_id']
        second = self.enqueue(request_id=str(uuid4()), ticket_text='Новый счёт').json()['job_id']
        self.assertEqual(self.status(first)['state'], 'superseded')
        self.assertEqual(self.status(second)['input_version'], 2)
        step(self.queue, self.engine)
        self.assertEqual(self.status(second)['state'], 'ready')

    def test_running_result_never_becomes_current_and_stale_review_rejected(self):
        first = self.enqueue().json()['job_id']
        job = self.queue.claim()
        second = self.enqueue(request_id=str(uuid4())).json()['job_id']
        self.assertIsNone(self.queue.claim())  # same ticket cannot run concurrently
        self.queue.finish(job, self.engine.analyze(json.loads(job['input_json']), max_attempts=1))
        self.assertEqual(self.status(first)['state'], 'superseded')
        proposal = self.status(first)['proposal_id']
        review = {'decision':'accepted','confirmed':True,'reason':'Synthetic check'}
        self.assertEqual(self.client.post('/api/proposals/'+proposal+'/reviews', json=review, headers=self.headers).status_code, 409)
        saved = self.client.get('/api/proposals/'+proposal, headers=self.headers).json()
        self.assertFalse(saved['is_current'])
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            with self.queue.connect() as c:
                c.execute("INSERT INTO operator_review(review_id,proposal_id,actor_ref,decision,reason) VALUES (?,?,?,'rejected',?)", (str(uuid4()),proposal,'synthetic','stale direct SQL'))
        step(self.queue, self.engine)
        self.assertEqual(self.status(second)['state'], 'ready')

    def test_reads_and_reviews_never_enqueue(self):
        job = self.enqueue().json()['job_id']
        step(self.queue, self.engine)
        proposal = self.status(job)['proposal_id']
        path = '/api/proposals/'+proposal
        self.client.get(path, headers=self.headers)
        self.client.get('/api/tickets', headers=self.headers)
        result = self.client.post(path+'/reviews', json={'decision':'rejected','reason':'Synthetic'}, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.count('analysis_job'), 1)
        self.assertEqual(self.count('model_attempt'), 1)

    def test_full_queue_does_not_record_or_ack_event_but_known_duplicate_is_accepted(self):
        self.queue.pending = 1
        first = self.enqueue()
        self.assertEqual(first.status_code, 202)
        self.assertEqual(self.enqueue().status_code, 202)
        blocked = self.enqueue(request_id=str(uuid4()), ticket_ref='second')
        self.assertEqual(blocked.status_code, 503)
        self.assertEqual(self.count('analysis_event'), 1)
        step(self.queue, self.engine)
        self.assertEqual(self.enqueue(request_id=str(uuid4()), ticket_ref='second').status_code, 202)

    def test_parallel_claims_respect_global_limit(self):
        self.queue.running = 1
        self.enqueue()
        self.enqueue(request_id=str(uuid4()), ticket_ref='second')
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(lambda _: self.queue.claim(), range(4)))
        self.assertEqual(sum(job is not None for job in claims), 1)
        self.assertEqual(self.count('model_attempt'), 1)

    def test_claim_timestamp_is_captured_after_admission_lock(self):
        from contextlib import contextmanager
        self.enqueue()
        admitted_at = time.time()
        clock = [admitted_at-1]
        original = self.queue.transaction

        @contextmanager
        def delayed_transaction():
            with original() as connection:
                # An event can commit while a worker is waiting for the global lock.
                clock[0] = admitted_at+1
                yield connection

        with patch('events.time.time', side_effect=lambda: clock[0]):
            with patch.object(self.queue, 'transaction', delayed_transaction):
                job = self.queue.claim()
                self.assertGreaterEqual(job['claimed_at'], job['created_at'])
                self.queue.finish(job, self.engine.analyze(json.loads(job['input_json'])))
        trace = self.client.get('/api/traces', headers=self.headers).json()[0]
        self.assertGreaterEqual(trace['queue_wait_ms'], 0)

    def test_rate_and_token_limits_delay_without_unrecorded_attempt(self):
        self.queue.budget = 1
        self.enqueue()
        self.assertIsNone(self.queue.claim())
        self.assertEqual(self.count('model_attempt'), 0)
        self.queue.budget = 1000000
        self.queue.rpm = 1
        self.assertIsNotNone(self.queue.claim())
        self.enqueue(request_id=str(uuid4()), ticket_ref='second')
        self.assertIsNone(self.queue.claim())
        self.assertEqual(self.count('model_attempt'), 1)

    def test_worker_crash_does_not_reset_attempts_or_reservations(self):
        job_id = self.enqueue().json()['job_id']
        first = self.queue.claim()
        with self.queue.transaction() as c:
            c.execute('UPDATE analysis_job SET lease_until=? WHERE job_id=?', (time.time()-1, job_id))
        restarted = api.Queue(self.queue.connect, api.module.fallback)
        second = restarted.claim()
        self.assertEqual(second['attempts'], 2)
        restarted.finish(first, self.engine.analyze(json.loads(first['input_json'])))  # fenced old worker
        self.assertEqual(self.count('analysis_proposal'), 0)
        with restarted.transaction() as c:
            c.execute('UPDATE analysis_job SET lease_until=? WHERE job_id=?', (time.time()-1, job_id))
        self.assertIsNone(restarted.claim())
        self.assertEqual(self.status(job_id)['state'], 'manual_review')
        self.assertEqual(self.count('model_attempt'), 2)

    def test_deadline_expires_without_model_call(self):
        job = self.enqueue().json()['job_id']
        with self.queue.transaction() as c:
            c.execute('UPDATE analysis_job SET deadline_at=?', (time.time()-1,))
        self.assertIsNone(self.queue.claim())
        self.assertEqual(self.status(job)['state'], 'manual_review')
        self.assertEqual(self.count('model_attempt'), 0)
        proposal = self.status(job)['proposal_id']
        saved = self.client.get('/api/proposals/'+proposal, headers=self.headers).json()
        self.assertEqual(saved['input']['ticket_text'], self.body['ticket_text'])
        self.assertIsNone(saved['card']['draft_reply'])

    def test_retry_is_total_two_attempts_not_nested(self):
        class FailedEngine:
            def __init__(self): self.calls = 0
            def analyze(inner, request, **options):
                self.assertEqual(options['max_attempts'], 1)
                inner.calls += 1
                raise TimeoutError('Synthetic transport failure')
        engine = FailedEngine()
        job = self.enqueue().json()['job_id']
        step(self.queue, engine)
        with self.queue.transaction() as c: c.execute('UPDATE analysis_job SET not_before=0')
        step(self.queue, engine)
        step(self.queue, engine)
        self.assertEqual(engine.calls, 2)
        self.assertEqual(self.status(job)['state'], 'manual_review')

    def test_webhook_signature_and_snapshot_boundary(self):
        event = {'event_id':'signed-event', 'event_type':'ticket.created', 'ticket_ref':'external-ticket',
                 'input_version':1, 'occurred_at':'2026-10-26T12:00:00Z', 'kb_version':'v1'}
        def send(value):
            raw = json.dumps(value).encode()
            signature = hmac.new(b'synthetic-event-key', raw, hashlib.sha256).hexdigest()
            return self.client.post('/ingress/events', content=raw, headers={'X-Event-Signature':signature})
        with patch.dict(os.environ, {'EVENT_SECRET':'synthetic-event-key','SERVICE_TOKEN':'synthetic-service-key'}):
            self.assertEqual(self.client.post('/ingress/events', json=event).status_code, 401)
            self.assertEqual(send(event).status_code, 404)
            self.assertEqual(send(event|{'ticket_text':'Untrusted arbitrary snapshot'}).status_code, 422)
            snap = {'input_version':1,'input': self.body|{'ticket_ref':'external-ticket'}}
            self.assertEqual(self.client.post('/internal/snapshots', json=snap, headers=self.headers).status_code, 401)
            self.assertEqual(self.client.post('/internal/snapshots', json=snap, headers={'Authorization':'Bearer synthetic-service-key'}).status_code, 200)
            first = send(event)
            self.assertEqual(first.status_code, 202)
            self.assertTrue(send(event).json()['duplicate'])
            self.assertTrue(send(event|{'event_id':'another-id'}).json()['duplicate'])
            self.assertEqual(send(event|{'event_type':'internal.note'}).status_code, 422)
            self.assertEqual(self.count('analysis_job'), 1)

    def test_private_data_and_legacy_manual_inference_protected(self):
        self.assertEqual(self.client.get('/api/tickets').status_code, 401)
        self.assertEqual(self.client.get('/api/traces').status_code, 401)
        self.assertEqual(self.client.post('/api/analyses', json=self.body, headers=self.headers).status_code, 410)
        self.assertEqual(self.client.post('/v1/analyze', json=self.body, headers=self.headers).status_code, 404)
        self.assertEqual(self.enqueue(unexpected=True).status_code, 422)
        self.assertEqual(self.client.post('/api/demo/tickets', content=b'x'*65537, headers=self.headers).status_code, 413)

    def test_single_service_hosting_runs_the_same_persistent_queue(self):
        with patch.dict(os.environ, {'AI_BASE_URL':''}):
            with TestClient(api.create_app(self.target, self.engine, 'synthetic-test', start_worker=True)) as embedded:
                response = embedded.post('/api/demo/tickets', json=self.body, headers=self.headers)
                self.assertEqual(response.status_code, 202)
                job = response.json()['job_id']
                until = time.monotonic()+8
                while time.monotonic() < until:
                    state = embedded.get('/api/jobs/'+job, headers=self.headers).json()['state']
                    if state == 'ready':
                        break
                    time.sleep(.05)
                self.assertEqual(state, 'ready')


if __name__ == '__main__':
    unittest.main()
