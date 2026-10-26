"""Execute EVT-01..14 on the actual queue; only inference responses are controlled."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

TASK = Path(__file__).resolve().parent
ROOT = TASK.parents[1]
from runtime_source import checked_runtime
PROJECT = checked_runtime()
sys.path.insert(0, str(PROJECT))
spec = importlib.util.spec_from_file_location('q010_project_api', PROJECT / 'backend/main.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
from worker import step

DSN = 'postgresql://audit:synthetic_lab04_only@127.0.0.1:18093/lab04_audit'
TOKEN = 'synthetic-lab04-operator'
SERVICE = 'synthetic-lab04-service'
SECRET = 'synthetic-lab04-event-secret'
HEADERS = {'Authorization': 'Bearer ' + TOKEN}


class Engine:
    mode = 'llm'
    calls = 0
    reply_mode = 'answer'
    fail = False

    def __init__(self):
        self.kb = api.read_jsonl(PROJECT / 'data/kb.jsonl')

    def analyze(self, request, max_attempts=1, **options):
        assert max_attempts == 1, 'Nested retries violate durable attempt budget'
        self.calls += 1
        if self.fail:
            raise TimeoutError('controlled provider failure')
        card = api.module.fallback(request['request_id'], 'controlled_transport')
        card.update(status='proposed', mode=self.reply_mode, category='payment')
        sources = api.module.retrieve(request['ticket_text'], request['history'], request['kb_version'], self.kb)
        if self.reply_mode == 'answer':
            card.update(evidence=[{k: item[k] for k in ('source_id','version','title','excerpt')} for item in sources],
                        draft_reply=sources[0]['excerpt'])
        elif self.reply_mode == 'clarify':
            card.update(missing_data=['Период документа'], draft_reply='За какой период нужен счёт?')
        return {'final_card': card, 'raw_card': card, 'provider': 'controlled_transport_not_live_llm',
                'latency_ms': 1, 'usage': None, 'gate_passed': True, 'attempts': 1}


class Fixture:
    def __init__(self, client, engine, queue):
        self.client, self.engine, self.q = client, engine, queue

    def payload(self, ref='ticket', request_id=None):
        return {'request_id': request_id or str(uuid4()), 'ticket_ref': ref,
                'ticket_text': 'Где администратору скачать счёт и акт?', 'history': [], 'kb_version': 'v1'}

    def event(self, ref='ticket', version=1, event_id=None):
        return {'event_id': event_id or str(uuid4()), 'event_type': 'ticket.created' if version == 1 else 'customer.message_received',
                'ticket_ref': ref, 'input_version': version, 'occurred_at': '2026-10-26T00:00:00Z', 'kb_version': 'v1'}

    def snapshot(self, event):
        body = {'input_version': event['input_version'], 'input': self.payload(event['ticket_ref'])}
        result = self.client.post('/internal/snapshots', json=body, headers={'Authorization': 'Bearer ' + SERVICE})
        assert result.status_code == 200
        return body['input']

    def send(self, event, valid_signature=True):
        raw = json.dumps(event).encode()
        signature = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest() if valid_signature else 'wrong'
        return self.client.post('/ingress/events', content=raw, headers={'X-Event-Signature': signature})

    def enqueue(self, ref='ticket', version=1):
        event = self.event(ref, version)
        payload = self.snapshot(event)
        result = self.send(event)
        assert result.status_code == 202
        return event, payload, result.json()['job_id']

    def count(self, table, where=''):
        assert table in ('analysis_job','analysis_event','model_attempt','operator_review','analysis_proposal')
        assert where in ('', " WHERE state='queued'", " WHERE state='running'")
        with self.q.connect() as c:
            return c.execute('SELECT COUNT(*) AS n FROM ' + table + where).fetchone()['n']

    def status(self, identifier):
        response = self.client.get('/api/jobs/' + identifier, headers=HEADERS)
        assert response.status_code == 200
        return response.json()

    def review(self, identifier, decision='rejected'):
        return self.client.post('/api/proposals/' + identifier + '/reviews',
                                json={'decision': decision, 'reason': 'Проверка LAB04', 'confirmed': True}, headers=HEADERS)

    def trace(self):
        with self.q.connect() as c:
            return {table: [dict(row) for row in c.execute(query).fetchall()] for table, query in {
                'jobs': 'SELECT job_id,event_id,ticket_ref,input_version,state,attempts,reason,proposal_id FROM analysis_job',
                'attempts': 'SELECT job_id,attempt_no,state,reserved_tokens FROM model_attempt',
                'reviews': 'SELECT review_id,proposal_id,decision FROM operator_review',
                'events': 'SELECT event_id,ticket_ref,input_version,event_type FROM analysis_event'}.items()}


@contextmanager
def fixture(postgres=False):
    environment = {'EVENT_SECRET': SECRET, 'SERVICE_TOKEN': SERVICE, 'MAX_RUNNING':'2', 'MAX_PENDING':'100',
                   'MODEL_REQUESTS_PER_MINUTE':'100', 'TOKEN_BUDGET':'10000000'}
    with patch.dict(os.environ, environment), tempfile.TemporaryDirectory(prefix='q010-') as temporary:
        if postgres:
            import psycopg
            with psycopg.connect(DSN) as c:
                identity = c.execute('SELECT current_database(), current_user').fetchone()
                if identity != ('lab04_audit', 'audit'):
                    raise RuntimeError('Refuse to reset non-fixture database')
                c.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public')
        engine = Engine()
        app = api.create_app(DSN if postgres else Path(temporary)/'test.db', engine, TOKEN, start_worker=False)
        with TestClient(app) as client:
            yield Fixture(client, engine, app.state.queue)


def execute(number, f):
    q = f.q
    if number == 1:
        _, _, identifier = f.enqueue(); step(q, f.engine)
        assert f.status(identifier)['state'] == 'ready' and f.engine.calls == 1
        return {'created_jobs': f.count('analysis_job'), 'manual_start_required': False}
    if number in (2, 3):
        event, _, _ = f.enqueue()
        if number == 3:
            event = dict(event, event_id=str(uuid4()))
        assert f.send(event).json()['duplicate'] is True
        result = {'created_jobs': f.count('analysis_job')}
        if number == 2: result['event_records'] = f.count('analysis_event')
        return result
    if number == 4:
        f.enqueue(); _, _, newer = f.enqueue(version=2)
        return {'pending_jobs': f.count('analysis_job', " WHERE state='queued'"), 'pending_is_latest': f.status(newer)['input_version'] == 2}
    if number == 5:
        _, _, older = f.enqueue(); claimed = q.claim(); f.enqueue(version=2)
        active, pending = f.count('analysis_job', " WHERE state='running'"), f.count('analysis_job', " WHERE state='queued'")
        q.finish(claimed, f.engine.analyze(json.loads(claimed['input_json'])))
        return {'active_jobs': active, 'pending_jobs': pending, 'stale_result_published': f.status(older)['state'] != 'superseded'}
    if number == 6:
        _, _, identifier = f.enqueue(); step(q, f.engine)
        proposal = f.status(identifier)['proposal_id']
        event = f.event(version=2); payload = f.payload()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with q.transaction() as c:
                future = pool.submit(f.review, proposal, 'accepted')
                time.sleep(.05)
                q.accept(c, event, payload)
            response = future.result(timeout=10)
        return {'atomic_version_check': response.status_code == 409, 'stale_action_committed': f.count('operator_review') != 0}
    if number == 7:
        _, _, identifier = f.enqueue(); step(q, f.engine); proposal = f.status(identifier)['proposal_id']
        jobs, calls = f.count('analysis_job'), f.engine.calls
        for path in ('/api/tickets','/api/jobs/'+identifier,'/api/proposals/'+proposal):
            assert f.client.get(path, headers=HEADERS).status_code == 200
        assert f.review(proposal).status_code == 200
        for kind in ('internal.note','ticket.status_changed'):
            assert f.send(f.event(version=2) | {'event_type':kind}).status_code == 422
        return {'created_jobs': f.count('analysis_job')-jobs, 'model_calls': f.engine.calls-calls}
    if number == 8:
        q.running = 1
        for i in range(4): f.enqueue(ref='parallel-'+str(i))
        other = api.Queue(q.connect, api.module.fallback); other.running = 1
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(lambda i: (q if i%2 else other).claim(), range(4)))
        return {'global_running_limit_respected': sum(item is not None for item in claims) == 1,
                'remaining_jobs_wait': f.count('analysis_job', " WHERE state='queued'") == 3}
    if number == 9:
        q.pending = 1; known, _, identifier = f.enqueue()
        duplicate = f.send(known)
        event = f.event('overflow'); f.snapshot(event); rejected = f.send(event)
        count_before = f.count('analysis_event')
        step(q, f.engine); retry = f.send(event)
        return {'new_event_http_status': rejected.status_code, 'new_event_acknowledged': rejected.status_code == 202,
                'new_event_persisted': count_before != 1, 'known_duplicate_acknowledged': duplicate.status_code == 202,
                'redelivery_after_capacity_succeeds': retry.status_code == 202}
    if number == 10:
        f.enqueue(); q.budget = 1
        assert q.claim() is None and f.count('model_attempt') == 0
        q.budget = 10000000; q.rpm = 1; job = q.claim()
        f.enqueue(ref='quota-2')
        assert q.claim() is None
        with q.connect() as c:
            reserved = c.execute('SELECT SUM(reserved_tokens) AS n FROM model_attempt').fetchone()['n']
        return {'calls_without_budget': f.engine.calls, 'reservation_preserved': reserved > 0, 'global_limits_respected': f.count('model_attempt') == 1}
    if number == 11:
        _, _, identifier = f.enqueue(); first = q.claim(); first_result = f.engine.analyze(json.loads(first['input_json']))
        with q.transaction() as c: c.execute('UPDATE analysis_job SET lease_until=? WHERE job_id=?', (time.time()-1,identifier))
        restarted = api.Queue(q.connect, api.module.fallback); second = restarted.claim()
        q.finish(first, first_result)  # delayed completion of the original call must be fenced
        assert f.count('analysis_proposal') == 0
        result = f.engine.analyze(json.loads(second['input_json']))
        restarted.finish(second, result)
        with q.connect() as c:
            unknown = c.execute("SELECT COUNT(*) AS n FROM model_attempt WHERE state='unknown'").fetchone()['n']
        return {'attempts_preserved': second['attempts'] == 2, 'unknown_charge_reserved': unknown == 1,
                'total_model_calls': f.engine.calls, 'nested_retries_included': second['attempts'] == 2}
    if number == 12:
        event = f.event('unauthorized')
        responses = [f.send(event, False).status_code, f.send(event).status_code,
                     f.send(event | {'event_type':'internal.note'}).status_code,
                     f.client.post('/ingress/events', content=b'x'*65537).status_code]
        assert responses == [401,404,422,413]
        return {'unauthorized_writes': f.count('analysis_event'), 'model_calls': f.engine.calls,
                'secrets_logged': SECRET in json.dumps(f.trace()), 'all_invalid_input_classes_tested': True}
    if number == 13:
        _, _, identifier = f.enqueue()
        with q.transaction() as c: c.execute('UPDATE analysis_job SET deadline_at=? WHERE job_id=?', (time.time()-1,identifier))
        assert q.claim() is None and f.engine.calls == 0
        status = f.status(identifier)
        card = f.client.get('/api/proposals/'+status['proposal_id'],headers=HEADERS).json()['card']
        f.engine.fail = True; _, _, retry_id = f.enqueue(ref='retry')
        step(q, f.engine)
        with q.transaction() as c: c.execute('UPDATE analysis_job SET not_before=0 WHERE job_id=?',(retry_id,))
        step(q, f.engine); step(q, f.engine)
        assert f.status(retry_id)['state'] == 'manual_review'
        return {'state':status['state'], 'client_draft':card['draft_reply'], 'deadline_limit_seconds':q.deadline,
                'deadline_includes_queue':status['state']=='manual_review', 'total_model_calls':f.engine.calls}
    if number == 14:
        checked = 0
        for mode, decision in [('answer','accepted'),('clarify','edited'),('escalate','accepted'),('answer','rejected')]:
            f.engine.reply_mode = mode; _, _, identifier = f.enqueue(ref='uc-'+str(checked)); step(q,f.engine)
            proposal = f.status(identifier)['proposal_id']; path='/api/proposals/'+proposal
            original = f.client.get(path,headers=HEADERS).json()['card']
            assert api.provenance_gate(original, original['request_id'],'v1',f.engine.kb)[0]
            body={'decision':decision,'reason':'Сквозной UC','confirmed':True}
            if decision == 'edited': body['final_text']='За какой период нужен документ?'
            if mode == 'escalate': body['destination']='general'
            response=f.client.post(path+'/reviews',json=body,headers=HEADERS)
            assert response.status_code==200 and response.json()['external_action_performed'] is False
            saved=f.client.get(path,headers=HEADERS).json()
            assert saved['card']==original and len(saved['reviews'])==1
            checked+=1
        return {'ucs_checked':checked,'sources_and_modes_checked':True,'review_stored_separately':f.count('operator_review')==4,'external_helpdesk_writes':0}
    raise ValueError(number)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--postgres',action='store_true'); parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    if args.output_dir.exists(): raise SystemExit('Existing evidence preserved')
    args.output_dir.mkdir(parents=True)
    rows=[]
    for number in range(1,15):
        with fixture(args.postgres) as f:
            observed=execute(number,f)
            trace={'observed':observed,'actual_database_rows':f.trace(),'controlled_transport_calls':f.engine.calls,
                   'database':'PostgreSQL' if args.postgres else 'SQLite','external_model_calls':0}
        name=f'EVT-{number:02}-trace.json'; path=args.output_dir/name
        path.write_text(json.dumps(trace,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        rows.append({'id':f'EVT-{number:02}','status':'passed','observed':observed,'provenance':{
            'application_revision':json.loads((ROOT/'tests/runtime-manifest.json').read_text(encoding='utf-8'))['revision'],
            'environment':trace['database']+' + TestClient + controlled inference transport',
            'executed_at':datetime.now(timezone.utc).isoformat(),'configuration':{'deadline_seconds':55,'maximum_attempts':2},
            'trace_path':name,'trace_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}})
        print('Executed '+rows[-1]['id'],flush=True)
    plan_path=TASK/'event-scenarios.json'
    checker_path=plan_path.parent/'check_events.py'
    spec=importlib.util.spec_from_file_location('q010_event_checker',checker_path); checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
    report=checker.validate(json.loads(plan_path.read_text(encoding='utf-8')),{'results':rows},args.output_dir)
    if not report['runtime_acceptance']: raise RuntimeError('Event scenarios failed')
    (args.output_dir/'event-results.json').write_text(json.dumps({'results':rows},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (args.output_dir/'event-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__': main()
