"""Regression tests for diagnostic wording, trusted IDs, billing and scheduling."""
import json
import os
from pathlib import Path
import sys
import time
import unittest
import httpx
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_cascade as fixtures
from backend.worker import step
from ml.cascade.assessment import tool_schema
from ml.cascade.engine import CascadeEngine
from ml.cascade.billing import matching_receipt
from ml.cascade.providers import HTTPProvider, MODELS, ProviderFailure, build_payload, parse_response


class ProcessingTests(unittest.TestCase):
    setUp = fixtures.CascadeTests.setUp
    enqueue = fixtures.CascadeTests.enqueue
    status = fixtures.CascadeTests.status
    rows = fixtures.CascadeTests.rows

    def test_natural_diagnostic_wording_stays_on_first_stage(self):
        for text, field, draft in (
            ('Не получается войти в профиль', 'Наблюдаемое поведение', 'Что происходит при попытке войти в аккаунт?'),
            ('Импорт падает', 'Код ошибки', 'Пожалуйста, уточните код ошибки, который появляется при импорте.'),
            ('Интеграция не работает', 'Наблюдаемое поведение при попытке использования интеграции',
             'Что именно происходит при попытке использования интеграции? Например, отображается ли ошибка или отсутствуют данные?'),
        ):
            with self.subTest(draft=draft):
                request = dict(request_id='local',ticket_ref='local',ticket_text=text,history=[],kb_version='v1')
                def provider(name, req, sources, schema, timeout, trace_id):
                    card = fixtures.api.Baseline([],schema,fixtures.api.provenance_gate).extractive(req,[])
                    card.update(status='proposed',mode='clarify',missing_data=[field],draft_reply=draft,evidence=[])
                    assessment=fixtures.make_assessment(req,[],card)
                    return dict(card=card,assessment=assessment,usage={},http_status=200)
                result=CascadeEngine(self.engine.kb,fixtures.api.SCHEMA,fixtures.api.provenance_gate,provider).analyze_attempt(request,'mistral',30,'job','attempt')
                self.assertTrue(result['automatic_resolution'],result['gate_reason'])
                self.assertFalse(result['cascade_next'])

    def test_model_schema_omits_id_and_transport_restores_it(self):
        request=dict(request_id='reserved',ticket_text='Где акт?',history=[],kb_version='v1')
        schema=tool_schema(fixtures.api.SCHEMA)['properties']['card']
        self.assertNotIn('request_id',schema['properties'])
        self.assertNotIn('request_id',json.loads(build_payload('mistral',request,[],fixtures.api.SCHEMA)['messages'][1]['content']))
        envelope={'card':{'summary':'Предложение'},'assessment':{}}
        response={'model':MODELS['mistral'],'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[
            {'type':'function','function':{'name':'propose_card','arguments':json.dumps(envelope)}}]}}],
            'usage':{'prompt_tokens':10,'completion_tokens':10}}
        self.assertEqual(parse_response(response,'mistral',request_id='reserved')['card']['request_id'],'reserved')
        envelope['card']['request_id']='untrusted'
        response['choices'][0]['message']['tool_calls'][0]['function']['arguments']=json.dumps(envelope)
        with self.assertRaises(ProviderFailure):parse_response(response,'mistral',request_id='reserved')

    def test_aborted_receipt_settles_only_identified_attempt_once(self):
        failure=ProviderFailure('provider_transport_or_timeout')
        failure.metadata={'provider_request_id':'receipt-one','transport_error':'TimeoutError'}
        self.fake.plans={'mistral':'invalid','sol':failure}
        job=self.enqueue();step(self.queue,self.engine);step(self.queue,self.engine)
        attempt=[r for r in self.rows() if r['provider']=='sol'][0]
        receipt=dict(id='receipt-one',model=MODELS['sol'],status='aborted',cost_usd='0.002',
                     tokens_in=100,tokens_out=0,created_at='2026-11-19T00:00:00Z')
        with ThreadPoolExecutor(max_workers=2) as pool:
            counts = list(pool.map(lambda _: self.queue.reconcile_billing([receipt]), range(2)))
        self.assertEqual(sorted(counts), [0, 1])
        self.assertEqual(self.queue.reconcile_billing([receipt]),0)
        with self.queue.connect() as c:
            row=c.execute('SELECT * FROM model_attempt WHERE request_id=?',(attempt['request_id'],)).fetchone()
            self.assertEqual(row['billed_micro_usd'],2000)
            self.assertEqual(json.loads(row['diagnostics_json'])['billing_status'],'settled')
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM provider_billing_receipt').fetchone()['n'],1)
        second = self.enqueue(ticket='new-billing-job')
        step(self.queue,self.engine);step(self.queue,self.engine)
        self.assertEqual(self.queue.reconcile_billing([receipt]),0)
        self.assertIsNone([r for r in self.rows() if r['job_id']==second and r['provider']=='sol'][0]['billed_micro_usd'])

    def test_aborted_without_identity_keeps_reserve(self):
        self.fake.plans={'mistral':'invalid','sol':ProviderFailure('provider_transport_or_timeout')}
        job=self.enqueue();step(self.queue,self.engine);step(self.queue,self.engine)
        receipt=dict(id='uncorrelated',model=MODELS['sol'],status='aborted',cost_usd='0.002')
        self.assertEqual(self.queue.reconcile_billing([receipt]),0)
        self.assertIsNone([r for r in self.rows() if r['provider']=='sol'][0]['billed_micro_usd'])

    def test_mistral_spacing_leaves_sol_eligible(self):
        self.queue.mistral_rpm=3
        self.fake.plans={'mistral':'invalid'}
        first=self.enqueue(ticket='a');step(self.queue,self.engine)
        second=self.enqueue(ticket='b')
        step(self.queue,self.engine)
        self.assertEqual(self.fake.calls,['mistral','sol'])
        self.assertEqual(self.status(first)['state'],'ready')
        self.assertEqual(self.status(second)['state'],'queued')
        self.assertFalse(step(self.queue,self.engine))

    def test_retry_after_survives_new_queue_instance(self):
        error=ProviderFailure('provider_http_error',http_status=429)
        error.metadata={'retry_after_seconds':40}
        self.fake.plans={'mistral':error}
        job=self.enqueue();step(self.queue,self.engine)
        with self.queue.connect() as c:
            state=c.execute("SELECT * FROM provider_runtime_state WHERE provider='mistral'").fetchone()
        self.assertGreater(state['cooldown_until'],time.time()+35)
        second=self.enqueue(ticket='second')
        restarted=type(self.queue)(self.queue.connect,mode='cascade',cascade_enabled=True,cascade_kb=self.engine.kb)
        claimed=restarted.claim()
        self.assertEqual(claimed['provider'],'sol')
        self.assertEqual(claimed['job_id'],job)
        self.assertEqual(self.status(second)['state'],'queued')

    def test_free_provider_concurrency_is_bounded(self):
        self.queue.mistral_rpm = 0
        self.queue.mistral_running = 1
        self.enqueue(ticket='first')
        self.enqueue(ticket='second')
        self.assertEqual(self.queue.claim()['provider'], 'mistral')
        with self.queue.transaction() as connection:
            connection.execute("UPDATE provider_runtime_state SET next_call_at=0 WHERE provider='mistral'")
        self.assertIsNone(self.queue.claim())

    def test_receipt_requires_identity_or_complete_unambiguous_usage(self):
        receipt = dict(id='billing', model=MODELS['sol'], status='aborted', cost_usd='0.002',
                       created_at='2026-11-19T00:00:00Z')
        attempt = dict(reserved_at=1795046400)
        self.assertIsNone(matching_receipt(attempt, {'usage': {'total_tokens': 10}}, [receipt]))
        receipt.update(tokens_in=10, tokens_out=2)
        diagnostics = dict(usage=dict(prompt_tokens=10, completion_tokens=2))
        self.assertIsNotNone(matching_receipt(attempt, diagnostics, [receipt]))
        self.assertIsNone(matching_receipt(attempt, diagnostics, [receipt, dict(receipt, id='other')]))

    def test_http_headers_survive_rate_limit_and_body_failure(self):
        request = dict(request_id='trusted', ticket_text='Импорт падает', history=[], kb_version='v1')
        class TimeoutStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                raise httpx.ReadTimeout('synthetic timeout')
                yield b''

        for status, headers, body, reason in (
            (429, {'x-request-id': 'limited', 'retry-after': '40'}, b'', 'provider_http_error'),
            (200, {'x-request-id': 'timed-out'}, TimeoutStream(), 'provider_transport_or_timeout'),
            (200, {'request-id': 'malformed'}, b'{broken', 'invalid_provider_response'),
        ):
            with self.subTest(status=status, reason=reason):
                def handler(req):
                    if isinstance(body, bytes):
                        return httpx.Response(status, headers=headers, content=body)
                    return httpx.Response(status, headers=headers, stream=body)
                with patch.dict(os.environ, CASCADE_LIVE_ENABLED='true', MISTRAL_FREE_CONFIRMED='true',
                                MISTRAL_API_KEY='synthetic-only', LANGFUSE_PUBLIC_KEY='', LANGFUSE_SECRET_KEY=''), \
                     patch('ml.cascade.providers.httpx.AsyncHTTPTransport', return_value=httpx.MockTransport(handler)):
                    with self.assertRaises(ProviderFailure) as caught:
                        HTTPProvider()('mistral', request, [], fixtures.api.SCHEMA, 30, 'synthetic')
                self.assertEqual(caught.exception.reason, reason)
                self.assertEqual(caught.exception.metadata['provider_request_id'], next(iter(headers.values())))
                if status == 429:
                    self.assertEqual(caught.exception.metadata['retry_after_seconds'], 40)


if __name__=='__main__':unittest.main()
