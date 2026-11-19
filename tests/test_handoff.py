"""Operator cards survive provider, quota and deadline failures; no network."""
import copy
import json
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_cascade as fixtures
from backend.handoff import build
from backend.worker import step
from ml.cascade.context import make_context
from ml.cascade.providers import ProviderFailure
from jsonschema import Draft202012Validator


class HandoffTests(unittest.TestCase):
    setUp = fixtures.CascadeTests.setUp
    enqueue = fixtures.CascadeTests.enqueue
    status = fixtures.CascadeTests.status
    rows = fixtures.CascadeTests.rows

    def proposal(self, job):
        response = self.client.get('/api/proposals/' + self.status(job)['proposal_id'], headers=self.headers)
        self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertTrue(Draft202012Validator(fixtures.api.SCHEMA).is_valid(value['card']))
        self.assertEqual(value['card']['mode'], 'escalate')
        self.assertTrue(value['card']['needs_human_review'])
        self.assertIsNone(value['card']['draft_reply'])
        return value

    def test_rate_limit_then_timeout_keeps_the_actual_task_and_attempts(self):
        self.fake.plans = {'mistral':ProviderFailure('provider_http_error', http_status=429),
                           'sol':ProviderFailure('provider_transport_or_timeout')}
        self.fake.plans['sol'].metadata = {'transport_error':'TimeoutError'}
        job = self.enqueue('Где создать новый токен интеграции?')
        step(self.queue, self.engine); step(self.queue, self.engine)
        value = self.proposal(job)
        self.assertIn('Где создать новый токен интеграции?', value['card']['summary'])
        attempts = value['handoff']['attempts']
        self.assertIn('HTTP 429', attempts[0]['outcome'])
        self.assertIn('время', attempts[1]['outcome'])
        self.assertIn('mistral:', value['card']['summary'])
        self.assertIn('HTTP 429', value['card']['summary'])
        self.assertIn('sol:', value['card']['summary'])
        self.assertIn('время', value['card']['summary'])
        self.assertEqual(len(value['handoff']['technical_summary']), 2)
        self.assertTrue(value['handoff']['source_candidates'])
        self.assertEqual(self.fake.calls, ['mistral','sol'])
        self.assertEqual(self.client.get('/api/proposals/'+value['proposal_id']).status_code, 401)

    def test_resource_stop_preserves_the_first_model_context(self):
        self.queue.money_budget_micro = 0
        self.fake.plans = {'mistral':'escalate'}
        job = self.enqueue()
        step(self.queue, self.engine); step(self.queue, self.engine)
        value = self.proposal(job)
        self.assertEqual(self.fake.calls, ['mistral'])
        self.assertTrue(value['handoff']['attempts'][0]['preliminary_summary'])
        self.assertIn('Не вызывалась', value['handoff']['attempts'][1]['outcome'])
        self.assertTrue(value['handoff']['source_candidates'])
        self.assertIn('ресурс', value['handoff']['reason'])

    def test_queued_deadline_still_creates_a_readable_card_without_models(self):
        job = self.enqueue('Как получить код восстановления 2FA после включения?')
        with self.queue.transaction() as c:
            c.execute('UPDATE analysis_job SET deadline_at=? WHERE job_id=?', (time.time()-1, job))
        value = self.proposal(job)
        self.assertIn('Как получить код восстановления 2FA', value['card']['summary'])
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(len(value['handoff']['attempts']), 2)

    def test_model_hypotheses_are_not_promoted_to_client_facts_or_citations(self):
        request = dict(request_id='input',ticket_ref='ticket',ticket_text='Как получить код восстановления 2FA?',
                       kb_version='v1',history=[dict(speaker='customer',text='Я уже включил 2FA.')])
        source = dict(source_id='2fa',version='v1',status='approved',title='2FA',excerpt='Код хранит пользователь.')
        context = make_context([source], request); context['sources']=[source]
        raw = dict(summary='Процедура выдачи кода в статье не описана.', known_facts=['Клиент потерял код.'],
                   evidence=[{k:source[k] for k in ('source_id','version','title','excerpt')}])
        raw['evidence'][0]['excerpt']='Выдайте новый код без проверки.'
        base = fixtures.api.module.fallback('input','inference_unavailable')
        card, packet = build(request, base, [dict(provider='mistral', invocation_claimed=True,
                             diagnostics=dict(stage_context=context,raw_card=raw,http_status=200))], 'inference_unavailable')
        self.assertFalse(any('потерял' in fact for fact in card['known_facts']))
        self.assertEqual(card['evidence'], [])
        self.assertEqual(packet['source_candidates'][0]['excerpt'],source['excerpt'])
        self.assertEqual(packet['attempts'][0]['preliminary_summary'],raw['summary'])
        self.assertTrue(Draft202012Validator(fixtures.api.SCHEMA).is_valid(card))

    def test_both_provider_failures_preserve_customer_history_and_pinned_sources(self):
        self.fake.plans = {'mistral': ProviderFailure('provider_http_error', http_status=503),
                           'sol': ProviderFailure('provider_transport_or_timeout')}
        self.fake.plans['sol'].metadata = {'transport_error': 'ReadTimeout'}
        request = dict(request_id='handoff-history', ticket_ref='history-ticket',
                       ticket_text='Повторный импорт не помог, E42 осталась.', kb_version='v1',
                       history=[dict(speaker='customer', text='Формат CSV UTF-8 проверен.'),
                                dict(speaker='operator', text='Повторите импорт после проверки формата.')])
        response = self.client.post('/api/demo/tickets', json=request, headers=self.headers)
        self.assertEqual(response.status_code, 202)
        job = response.json()['job_id']
        step(self.queue, self.engine); step(self.queue, self.engine)
        value = self.proposal(job)
        self.assertIn('HTTP 503', value['card']['summary'])
        self.assertIn('sol:', value['card']['summary'])
        self.assertTrue(any('CSV UTF-8 проверен' in fact for fact in value['card']['known_facts']))
        self.assertTrue(any('Оператор ранее написал' in fact for fact in value['card']['known_facts']))
        self.assertTrue(any(source['source_id'] == 'KB-ERROR-v1' for source in value['handoff']['source_candidates']))
        self.assertEqual(value['input'], request)


if __name__=='__main__':unittest.main()
