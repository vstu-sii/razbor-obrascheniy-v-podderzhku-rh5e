import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
"""Controlled model doubles, real queue/HTTP/SQLite. No generation or keys."""
import copy
import importlib.util
import json
import os
import ssl
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from ml.cascade.engine import CascadeEngine
from ml.cascade.providers import build_payload, parse_response, ProviderFailure, MODELS, SOL_RESERVATION_MICRO, HTTPProvider, provider_tls_context

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('cascade_test_app', ROOT/'backend/main.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
from backend.contracts.cascade import SCHEMA as cascade_schema, provenance_gate as cascade_gate
# This test-local module exercises only the new contract.
api.SCHEMA = cascade_schema
api.provenance_gate = cascade_gate
from backend.worker import step


def make_assessment(request, sources, card, questions=None, *, requires_human=False,
                    reasoning_issue=False):
    """Synthetic coverage metadata; never part of the public proposal card."""
    if questions is None:
        clarify = card['mode'] == 'clarify'
        questions = [{'id': 'q1', 'question': request['ticket_text'],
                      'input_quote': request['ticket_text'],
                      'coverage': 'missing' if clarify else 'complete',
                      'source_ids': [source['source_id'] for source in sources],
                      'missing_customer_fields': list(card['missing_data']) if clarify else [],
                      'answer_excerpt': (card.get('draft_reply') or '') if card['mode'] == 'answer' else '',
                      'search_query': ''}]
    return {'questions': copy.deepcopy(questions), 'requires_human': requires_human,
            'reasoning_issue': reasoning_issue}


class FakeProvider:
    def __init__(self, plans):
        self.plans, self.calls = plans, []
        self.timeouts = []

    def __call__(self, provider, request, sources, schema, timeout, trace_id):
        self.calls.append(provider)
        self.timeouts.append(timeout)
        plan = self.plans.get(provider, 'valid')
        if isinstance(plan, Exception):
            raise plan
        card = api.Baseline([], schema, api.provenance_gate).extractive(request, sources)
        questions = None
        if plan == 'invalid':
            card['next_step'] = None
        elif plan == 'full_answer':
            card['draft_reply'] = ' '.join(source['excerpt'] for source in sources)
            quotes = ('Где скачать счёт?', 'Как настроить уведомления?')
            by_id = {source['source_id']: source for source in sources}
            answer_sources = [by_id['KB-INVOICE-v1'], by_id['KB-NOTIFY-v1']]
            questions = [{'id': 'q'+str(index), 'question': quote, 'input_quote': quote,
                          'coverage': 'complete', 'source_ids': [source['source_id']],
                          'missing_customer_fields': [], 'answer_excerpt': source['excerpt'],
                          'search_query': ''}
                         for index, (quote, source) in enumerate(zip(quotes, answer_sources), 1)]
        elif plan == 'clarify':
            card.update(mode='clarify', missing_data=['Сумма'], draft_reply='Какая сумма?', evidence=[])
        elif plan == 'escalate':
            card.update(mode='escalate', draft_reply=None)
        elif plan == 'wrong_id':
            card['request_id'] = 'other'
        elif plan == 'fake_source':
            card['evidence'][0]['excerpt'] = 'invented'
        elif plan == 'unsafe_clarify':
            card.update(mode='clarify', missing_data=['Значение токена'], draft_reply='Сообщите значение токена?', evidence=[])
        return {'card': card, 'assessment': make_assessment(request, sources, card, questions),
                'usage': {'prompt_tokens': 100, 'completion_tokens': 30},
                'returned_model': MODELS[provider], 'http_status': 200,
                'completion_id': 'controlled-fixture', 'not_sent': False}


class CascadeTests(unittest.TestCase):
    def setUp(self):
        offline = patch.dict(os.environ, AI_BASE_URL='', CASCADE_LIVE_ENABLED='false',
                             SOL_LIVE_ENABLED='false', MISTRAL_REQUESTS_PER_MINUTE='0', MISTRAL_MAX_RUNNING='2')
        offline.start()
        self.addCleanup(offline.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fake = FakeProvider({})
        self.engine = CascadeEngine(api.read_jsonl(ROOT/'data/kb.jsonl'), api.SCHEMA, api.provenance_gate, self.fake)
        target = os.environ.get('CASCADE_TEST_DATABASE_URL') or Path(self.temp.name)/'test.db'
        self.target = str(target)
        if os.environ.get('CASCADE_TEST_DATABASE_URL'):
            from urllib.parse import urlsplit
            assert urlsplit(target).path == '/cascade_test' and urlsplit(target).hostname in ('db', '127.0.0.1') and urlsplit(target).port in (5432,18562)
            import psycopg
            with psycopg.connect(target) as c:
                c.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public')
        self.client = TestClient(api.create_app(target, self.engine, 'synthetic-test', start_worker=False))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.queue = self.client.app.state.queue
        self.headers = {'Authorization': 'Bearer synthetic-test'}

    def enqueue(self, text='Где скачать счёт?', ticket=None):
        body = {'request_id': str(uuid4()), 'ticket_ref': ticket or str(uuid4()),
                'ticket_text': text, 'history': [], 'kb_version': 'v1'}
        response = self.client.post('/api/demo/tickets', json=body, headers=self.headers)
        self.assertEqual(response.status_code, 202)
        return response.json()['job_id']

    def status(self, job):
        return self.queue.status(job)

    def rows(self):
        with self.queue.connect() as c:
            return [dict(r) for r in c.execute('SELECT * FROM model_attempt ORDER BY reserved_at').fetchall()]

    def test_mistral_success_never_calls_sol(self):
        job = self.enqueue()
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'ready')
        self.assertEqual(self.fake.calls, ['mistral'])
        self.assertEqual(self.rows()[0]['reserved_micro_usd'], 0)

    def test_invalid_mistral_then_valid_sol(self):
        self.fake.plans['mistral'] = 'invalid'
        job = self.enqueue()
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'queued')
        self.assertIsNone(self.status(job)['proposal_id'])
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'ready')
        self.assertEqual(self.fake.calls, ['mistral', 'sol'])
        mistral_timeout, sol_timeout = self.fake.timeouts
        self.assertTrue(29 < mistral_timeout <= 30)
        self.assertTrue(44 < sol_timeout <= 45)
        self.assertEqual(self.rows()[1]['reserved_micro_usd'], SOL_RESERVATION_MICRO)

    def test_sol_lease_survives_a_slow_first_stage_and_inference(self):
        self.fake.plans['mistral'] = 'invalid'
        started = time.time()
        with patch('backend.cascade_queue.time.time', return_value=started):
            job = self.enqueue()
            step(self.queue, self.engine)
        with patch('backend.cascade_queue.time.time', return_value=started + 31):
            sol = self.queue.claim()
            self.assertEqual(sol['provider'], 'sol')
            self.assertEqual(sol['deadline_at'] - started, 80)
            _, remaining = self.queue.claim_inference(job, sol['claim_id'], 'sol', json.loads(sol['input_json']))
            self.assertGreaterEqual(remaining, 45)
        with patch('backend.cascade_queue.time.time', return_value=started + 75):
            self.assertEqual(self.status(job)['state'], 'running')
        with patch('backend.cascade_queue.time.time', return_value=started + 80):
            self.assertEqual(self.status(job)['state'], 'manual_review')

    def test_both_bad_manual_no_third_attempt(self):
        self.fake.plans.update(mistral='invalid', sol='wrong_id')
        job = self.enqueue()
        for _ in range(5):
            step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'manual_review')
        self.assertEqual(self.fake.calls, ['mistral', 'sol'])
        self.assertEqual(len(self.rows()), 2)

    def test_clarification_does_not_trigger_paid_model(self):
        self.fake.plans['mistral'] = 'clarify'
        job = self.enqueue()
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'ready')
        self.assertEqual(self.fake.calls, ['mistral'])

    def test_valid_mistral_escalation_gets_sol_review(self):
        self.fake.plans['mistral'] = 'escalate'
        job = self.enqueue()
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'queued')
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'ready')
        self.assertEqual(self.fake.calls, ['mistral', 'sol'])

    def test_unsafe_mistral_clarification_can_be_replaced_by_safe_sol(self):
        self.fake.plans['mistral'] = 'unsafe_clarify'
        job = self.enqueue()
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'queued')
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'ready')
        self.assertEqual(self.fake.calls, ['mistral', 'sol'])

    def test_risk_is_manual_without_any_provider(self):
        job = self.enqueue('Токен интеграции опубликован в публичном чате')
        step(self.queue, self.engine)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.status(job)['state'], 'manual_review')

    def test_missing_billing_procedure_gets_sol_before_manual(self):
        job = self.enqueue('Счёт скачал, но сумма в нём неправильная.')
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'queued')
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'manual_review')
        self.assertEqual(self.fake.calls, ['mistral', 'sol'])
        self.assertFalse(json.loads(self.rows()[0]['diagnostics_json'])['not_sent'])

    def test_complete_multiple_answers_stay_on_mistral(self):
        self.fake.plans['mistral'] = 'full_answer'
        job = self.enqueue('Где скачать счёт? Как настроить уведомления?')
        step(self.queue, self.engine)
        step(self.queue, self.engine)
        self.assertEqual(self.fake.calls, ['mistral'])
        self.assertEqual(self.status(job)['state'], 'ready')

    def test_fake_quote_routes_to_sol(self):
        self.fake.plans['mistral'] = 'fake_source'
        job = self.enqueue()
        step(self.queue, self.engine)
        step(self.queue, self.engine)
        self.assertEqual(self.fake.calls, ['mistral', 'sol'])

    def test_budget_blocks_sol_before_network(self):
        self.fake.plans['mistral'] = 'invalid'
        self.queue.money_budget_micro = SOL_RESERVATION_MICRO - 1
        job = self.enqueue()
        step(self.queue, self.engine)
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'manual_review')
        self.assertEqual(self.fake.calls, ['mistral'])

    def test_concurrent_workers_cannot_overspend_sol_reserve(self):
        self.queue.running = 2
        self.queue.money_budget_micro = SOL_RESERVATION_MICRO
        self.fake.plans['mistral'] = 'invalid'
        self.enqueue()
        self.enqueue()
        first, second = self.queue.claim(), self.queue.claim()
        for job in (first, second):
            request = json.loads(job['input_json'])
            result = self.engine.analyze_attempt(request, 'mistral', 15, job['job_id'], job['claim_id'])
            self.queue.finish(job, result)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(lambda _: self.queue.claim(), range(2)))
        self.assertEqual(sum(x is not None for x in claimed), 1)
        self.assertEqual(sum(r['reserved_micro_usd'] for r in self.rows()), SOL_RESERVATION_MICRO)

    def test_unknown_cost_stays_reserved_across_new_queue(self):
        self.queue.money_budget_micro = SOL_RESERVATION_MICRO
        self.fake.plans.update(mistral='invalid', sol=ProviderFailure('provider_transport_or_timeout'))
        first = self.enqueue()
        step(self.queue, self.engine)
        step(self.queue, self.engine)
        self.assertEqual(self.status(first)['state'], 'manual_review')
        q = api.Queue(self.queue.connect, api.module.fallback, cascade_enabled=True,
                      cascade_kb=self.engine.kb)
        q.money_budget_micro = SOL_RESERVATION_MICRO
        second = self.enqueue()
        step(q, self.engine)
        step(q, self.engine)
        self.assertEqual(q.status(second)['state'], 'manual_review')
        self.assertEqual(self.fake.calls, ['mistral', 'sol', 'mistral'])
        self.assertIsNone(self.rows()[1]['billed_micro_usd'])

    def test_failure_before_network_settles_zero(self):
        self.queue.money_budget_micro = SOL_RESERVATION_MICRO
        self.fake.plans.update(mistral='invalid', sol=ProviderFailure('configuration_missing', not_sent=True))
        for _ in range(2):
            job = self.enqueue()
            step(self.queue, self.engine)
            step(self.queue, self.engine)
            self.assertEqual(self.status(job)['state'], 'manual_review')
        self.assertEqual(self.fake.calls, ['mistral', 'sol', 'mistral', 'sol'])
        self.assertEqual(self.rows()[1]['billed_micro_usd'], 0)

    def test_crash_first_attempt_resumes_at_sol_not_mistral(self):
        job = self.enqueue()
        claimed = self.queue.claim()
        with self.queue.transaction() as c:
            c.execute('UPDATE analysis_job SET lease_until=? WHERE job_id=?', (time.time()-1, job))
        step(self.queue, self.engine)
        self.assertEqual(self.fake.calls, ['sol'])
        self.assertEqual(self.rows()[0]['state'], 'unknown')
        self.assertEqual(self.status(job)['attempts'], 2)
        self.queue.finish(claimed)  # Fenced stale worker cannot requeue.
        self.assertEqual(self.status(job)['state'], 'ready')

    def test_recovered_second_stage_without_hits_can_inspect_pinned_snapshot(self):
        job = self.enqueue('Подскажите расположение документа')
        reserved = self.queue.claim()
        with self.queue.transaction() as c:
            c.execute('UPDATE analysis_job SET lease_until=? WHERE job_id=?',(time.time()-1,job))
        step(self.queue,self.engine)
        self.assertEqual(self.fake.calls,['sol'])
        diagnostics = json.loads(self.rows()[1]['diagnostics_json'])
        self.assertFalse(diagnostics['not_sent'])
        self.assertEqual(set(diagnostics['retrieved_ids']),
                         {source['source_id'] for source in self.engine.kb
                          if source['version'] == 'v1' and source['status'] == 'approved'})
        self.assertIsNone(self.rows()[1]['billed_micro_usd'])

    def test_queue_wait_uses_shared_deadline(self):
        job = self.enqueue()
        with self.queue.transaction() as c:
            c.execute('UPDATE analysis_job SET deadline_at=? WHERE job_id=?', (time.time()-1, job))
        step(self.queue, self.engine)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.status(job)['state'], 'manual_review')

    def test_new_version_supersedes_pending_sol(self):
        self.fake.plans['mistral'] = 'invalid'
        first = self.enqueue(ticket='same-ticket')
        step(self.queue, self.engine)
        self.enqueue(ticket='same-ticket')
        self.assertEqual(self.status(first)['state'], 'superseded')
        self.assertNotIn('sol', self.fake.calls)

    def test_no_unreserved_engine_calls(self):
        with self.assertRaises(ValueError):
            self.engine.analyze({})

    def test_operator_diagnostics_and_legacy_call_are_protected(self):
        job = self.enqueue()
        self.assertEqual(self.client.get('/api/jobs/'+job+'/attempts').status_code, 401)
        self.assertEqual(self.client.get('/api/jobs/'+job+'/attempts', headers=self.headers).json(), [])
        body = {'request_id': 'r', 'ticket_ref': 't', 'ticket_text': 'Где счёт?', 'history': [], 'kb_version': 'v1'}
        with patch.dict(os.environ, ENABLE_LEGACY_ANALYZE='true'):
            self.assertEqual(self.client.post('/api/analyses', json=body, headers=self.headers).status_code, 409)
        self.assertEqual(self.fake.calls, [])

    def test_ai_service_requires_worker_auth_and_stage_metadata(self):
        from backend.ai_service import create_app
        body = {'request_id': 'r', 'ticket_ref': 't', 'ticket_text': 'Где счёт?', 'history': [], 'kb_version': 'v1'}
        with patch.dict(os.environ, SERVICE_TOKEN='synthetic-service'), patch('backend.ai_service.build_engine', return_value=self.engine):
            with TestClient(create_app()) as client:
                self.assertEqual(client.post('/v1/analyze', json=body).status_code, 401)
                headers = {'Authorization': 'Bearer synthetic-service'}
                self.assertEqual(client.post('/v1/analyze', json=body, headers=headers).status_code, 409)
                self.assertEqual(client.post('/internal/inference', json={'input':body,'timeout':15}, headers=headers).status_code, 422)
        self.assertEqual(self.fake.calls, [])

    def test_ai_service_checks_snapshot_provider_and_single_use_reservation(self):
        from backend.ai_service import create_app
        job = self.enqueue()
        reserved = self.queue.claim()
        request = {'input':json.loads(reserved['input_json']), 'timeout':30, 'provider':'mistral',
                   'job_id':job, 'attempt_id':reserved['claim_id']}
        postgres = self.target.startswith('postgresql://')
        env = {'SERVICE_TOKEN':'synthetic-service','DATABASE_URL':self.target if postgres else '',
               'DATABASE_PATH':self.target}
        with patch.dict(os.environ, env), patch('backend.ai_service.build_engine', return_value=self.engine):
            with TestClient(create_app()) as client:
                headers = {'Authorization':'Bearer synthetic-service'}
                wrong = copy.deepcopy(request); wrong['input']['ticket_text'] = 'Подменённый текст'
                self.assertEqual(client.post('/internal/inference',json=wrong,headers=headers).status_code,409)
                wrong = dict(request,provider='sol')
                self.assertEqual(client.post('/internal/inference',json=wrong,headers=headers).status_code,409)
                self.assertEqual(client.post('/internal/inference',json=dict(request,timeout=31),headers=headers).status_code,422)
                self.assertEqual(client.post('/internal/inference',json=dict(request,provider='sol',timeout=46),headers=headers).status_code,422)
                self.assertEqual(self.fake.calls,[])
                self.assertEqual(client.post('/internal/inference',json=request,headers=headers).status_code,200)
                self.assertEqual(client.post('/internal/inference',json=request,headers=headers).status_code,409)
        self.assertEqual(self.fake.calls,['mistral'])
        self.assertTrue(29 < self.fake.timeouts[0] <= 30)

    def test_parallel_inference_claim_consumes_reservation_once(self):
        from fastapi import HTTPException
        job = self.enqueue();reserved = self.queue.claim()
        def claim(_):
            try:
                self.queue.claim_inference(job,reserved['claim_id'],'mistral',json.loads(reserved['input_json']))
                return True
            except HTTPException:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(claim,range(2))).count(True),1)


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.request = {'request_id': 'r', 'ticket_text': 'test', 'history': [], 'kb_version': 'v1'}

    def test_default_and_extra_ca_keep_hostname_and_chain_verification(self):
        import certifi
        for filename in ('', ssl.get_default_verify_paths().cafile or certifi.where()):
            self.assertIsNotNone(filename)
            with patch.dict(os.environ, CASCADE_CA_FILE=filename):
                context = provider_tls_context()
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_invalid_ca_configuration_fails_before_http(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'invalid.pem';p.write_text('not a certificate')
            for filename in (str(p), str(Path(directory)/'missing.pem')):
                with patch.dict(os.environ, CASCADE_CA_FILE=filename):
                    with self.assertRaises(ProviderFailure) as raised:
                        provider_tls_context()
                self.assertTrue(raised.exception.not_sent)

    def test_paid_provider_requires_separate_live_switch(self):
        with patch.dict(os.environ, CASCADE_LIVE_ENABLED='true', SOL_LIVE_ENABLED='false'):
            with self.assertRaises(ProviderFailure) as raised:
                HTTPProvider()('sol', self.request, [], api.SCHEMA, 20, '0'*32)
        self.assertTrue(raised.exception.not_sent)
        self.assertEqual(raised.exception.reason, 'sol_inference_disabled')

    def test_provider_specific_parameters_and_no_gold(self):
        a = build_payload('mistral', self.request, [], api.SCHEMA)
        b = build_payload('sol', self.request, [], api.SCHEMA)
        self.assertIn('max_tokens', a)
        self.assertNotIn('max_tokens', b)
        self.assertEqual(b['reasoning_effort'], 'low')
        self.assertEqual(b['max_completion_tokens'], 1600)
        self.assertNotIn('candidate_proposal', json.loads(b['messages'][1]['content']))
        self.assertNotIn('expected', json.loads(b['messages'][1]['content']))
        self.assertIn('allOf', api.SCHEMA)  # No in-place schema weakening.

    def test_oversized_payload_is_rejected_without_network(self):
        self.request['ticket_text'] = 'a' * 20000
        with self.assertRaises(ProviderFailure) as raised:
            build_payload('sol', self.request, [], api.SCHEMA)
        self.assertTrue(raised.exception.not_sent)

    def test_wrong_tool_truncation_model_and_usage_are_rejected(self):
        envelope = {'card': {}, 'assessment': {'questions': [
            {'id': 'q1', 'question': 'test', 'input_quote': 'test', 'coverage': 'missing',
             'source_ids': [], 'missing_customer_fields': [], 'answer_excerpt': '', 'search_query': ''}],
            'requires_human': False, 'reasoning_issue': False}}
        obj = {'model': MODELS['sol'], 'choices': [{'finish_reason': 'tool_calls', 'message': {
            'tool_calls': [{'type': 'function', 'function': {'name': 'propose_card', 'arguments': json.dumps(envelope)}}]}}],
               'usage': {'prompt_tokens': 100, 'completion_tokens': 30}}
        self.assertEqual(parse_response(obj, 'sol')['card'], {})
        self.assertEqual(parse_response(obj, 'sol')['assessment'], envelope['assessment'])
        for kind in ('tool', 'finish', 'model', 'usage'):
            bad = copy.deepcopy(obj)
            if kind == 'tool': bad['choices'][0]['message']['tool_calls'][0]['function']['name'] = 'send_message'
            elif kind == 'finish': bad['choices'][0]['finish_reason'] = 'length'
            elif kind == 'model': bad['model'] = 'unrequested-model'
            else: bad['usage']['prompt_tokens'] = True
            with self.assertRaises(ProviderFailure): parse_response(bad, 'sol')

    def test_malformed_provider_shapes_are_rejected(self):
        for obj in (None, [], {'model':MODELS['sol'],'choices':[None]},
                    {'model':MODELS['sol'],'choices':[]}):
            with self.assertRaises(ProviderFailure):
                parse_response(obj, 'sol')
