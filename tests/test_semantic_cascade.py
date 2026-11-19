import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
"""Semantic routing regressions with synthetic KB, providers, and real SQLite/HTTP."""
import copy
import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

import test_cascade as fixtures
from ml.cascade.engine import CascadeEngine
from ml.cascade.providers import MODELS, ProviderFailure, SOL_RESERVATION_MICRO
from ml.models.baseline import retrieve
from backend.worker import step


def article(source_id, title, excerpt, *, version='v1', status='approved'):
    return {'source_id': source_id, 'version': version, 'status': status,
            'title': title, 'excerpt': excerpt, 'provenance': 'synthetic_semantic_test'}


INVOICE = article('TEST-INVOICE', 'Счёт', 'Счёт доступен в разделе Биллинг.')
NOTIFY = article('TEST-NOTIFY', 'Уведомления',
                 'Email-уведомления меняются в личных настройках.')
MULTI_TEXT = 'Скачать счёт и изменить рассылку'
MULTI_QUOTES = ('Скачать счёт', 'изменить рассылку')
NOTIFY_QUERY = 'Email уведомления личные настройки'


def multi_questions(sources, card, *, partial=False, reasoning=False):
    available = {source['source_id']: source for source in sources}
    questions = []
    for index, (quote, document) in enumerate(zip(MULTI_QUOTES, (INVOICE, NOTIFY)), 1):
        complete = index == 1 or not partial
        supported = document['source_id'] in available
        questions.append({'id': 'q'+str(index), 'question': quote, 'input_quote': quote,
                          'coverage': 'complete' if complete else 'partial' if reasoning else 'missing',
                          'source_ids': [document['source_id']] if supported else [],
                          'missing_customer_fields': [],
                          'answer_excerpt': document['excerpt'] if complete else '',
                          'search_query': '' if complete else NOTIFY_QUERY})
    return questions


class SemanticProvider:
    """Scripted replies record the actual sources each model receives."""
    def __init__(self, *, partial=False, reasoning=False, sol_partial=False,
                 sol_identity=None, mistral_error=None, clarify=False,
                 unsafe=False, conflict=False):
        self.partial, self.reasoning = partial, reasoning
        self.sol_partial, self.sol_identity = sol_partial, sol_identity
        self.mistral_error, self.clarify = mistral_error, clarify
        self.unsafe, self.conflict = unsafe, conflict
        self.calls, self.source_calls = [], []

    def __call__(self, provider, request, sources, schema, timeout, trace_id):
        self.calls.append(provider)
        self.source_calls.append((provider, copy.deepcopy(sources)))
        if provider == 'mistral' and isinstance(self.mistral_error, Exception):
            raise self.mistral_error
        card = fixtures.api.Baseline([], schema, fixtures.api.provenance_gate).extractive(request, sources)
        if request['ticket_text'] == MULTI_TEXT:
            partial = self.partial if provider == 'mistral' else self.sol_partial
            by_id = {source['source_id']: source for source in sources}
            response_sources = [by_id[INVOICE['source_id']]]
            if not partial and NOTIFY['source_id'] in by_id:
                response_sources.append(by_id[NOTIFY['source_id']])
            card['draft_reply'] = ' '.join(source['excerpt'] for source in response_sources)
            # Extra evidence alone does not make the omitted second answer complete.
            questions = multi_questions(sources, card, partial=partial, reasoning=self.reasoning)
            if provider == 'sol' and self.sol_identity == 'id':
                questions[1]['id'] = 'q3'
            elif provider == 'sol' and self.sol_identity == 'quote':
                questions[1]['input_quote'] = MULTI_QUOTES[0]
            assessment = fixtures.make_assessment(request, sources, card, questions,
                                                  reasoning_issue=self.reasoning and provider == 'mistral')
        else:
            assessment = fixtures.make_assessment(request, sources, card)
        if self.clarify or self.unsafe:
            field = 'Значение токена' if self.unsafe else 'Код ошибки импорта'
            draft = 'Сообщите значение токена?' if self.unsafe else 'Какой код ошибки импорта показан?'
            card.update(mode='clarify', missing_data=[field], draft_reply=draft, evidence=[])
            assessment = fixtures.make_assessment(request, sources, card)
        if self.conflict:
            assessment['questions'][0]['coverage'] = 'conflict'
        if provider == 'mistral' and self.mistral_error == 'format':
            card['next_step'] = None
        return {'card': card, 'assessment': assessment,
                'usage': {'prompt_tokens': 100, 'completion_tokens': 30},
                'returned_model': MODELS[provider], 'http_status': 200,
                'completion_id': 'semantic-synthetic', 'not_sent': False}


class AssessmentEdgeProvider(SemanticProvider):
    def __init__(self, *, billing_procedure=None, malformed_unsafe=False):
        super().__init__()
        self.billing_procedure, self.malformed_unsafe = billing_procedure, malformed_unsafe

    def __call__(self, provider, request, sources, schema, timeout, trace_id):
        reply = super().__call__(provider, request, sources, schema, timeout, trace_id)
        card = reply['card']
        if self.billing_procedure:
            complete = provider == 'sol'
            by_id = {source['source_id']: source for source in sources}
            supporting = by_id[self.billing_procedure['source_id'] if complete else INVOICE['source_id']]
            card['draft_reply'] = supporting['excerpt']
            question = {'id': 'q1', 'question': 'Как проверить неправильную сумму счёта?',
                        'input_quote': request['ticket_text'],
                        'coverage': 'complete' if complete else 'partial',
                        'source_ids': [supporting['source_id']], 'missing_customer_fields': [],
                        'answer_excerpt': supporting['excerpt'] if complete else '',
                        'search_query': '' if complete else 'проверка начислений расчёт суммы'}
            reply['assessment'] = fixtures.make_assessment(request, sources, card, [question])
        elif self.malformed_unsafe:
            card.update(mode='clarify', missing_data=['Пароль'],
                        draft_reply='Сообщите пароль для входа?', evidence=[])
            reply['assessment'] = {'questions': None}
        return reply


class SemanticCascadeTests(unittest.TestCase):
    setUp = fixtures.CascadeTests.setUp
    enqueue = fixtures.CascadeTests.enqueue
    status = fixtures.CascadeTests.status
    rows = fixtures.CascadeTests.rows

    def configure(self, provider, kb=None):
        self.fake = provider
        self.engine = CascadeEngine(copy.deepcopy(kb or [INVOICE, NOTIFY]), fixtures.api.SCHEMA,
                                    fixtures.api.provenance_gate, provider)
        self.client.app.state.engine = self.engine
        self.queue.cascade_kb = self.engine.kb

    def final_card(self, job):
        with self.queue.connect() as connection:
            row = connection.execute('SELECT card_json FROM analysis_proposal WHERE proposal_id=?',
                                     (self.status(job)['proposal_id'],)).fetchone()
        return json.loads(row['card_json'])

    def run_to_completion(self, job):
        for _ in range(5):
            step(self.queue, self.engine)
        self.assertLessEqual(len(self.fake.calls), 2)
        self.assertLessEqual(len([row for row in self.rows() if row['job_id'] == job]), 2)
        return self.status(job)

    def test_repeated_question_marks_are_one_complete_task(self):
        self.configure(SemanticProvider())
        job = self.enqueue('Где счёт???')
        with patch('ml.cascade.engine.retrieve', wraps=retrieve) as search:
            self.run_to_completion(job)
        self.assertEqual(self.status(job)['state'], 'ready')
        self.assertEqual(self.fake.calls, ['mistral'])
        self.assertEqual(search.call_count, 1)
        self.assertEqual(sum(row['reserved_micro_usd'] for row in self.rows()), 0)

    def test_two_complete_tasks_without_questions_need_no_sol(self):
        # Both procedures are retrieved initially; punctuation is irrelevant.
        kb = [INVOICE, article(NOTIFY['source_id'], 'Рассылку изменить', NOTIFY['excerpt'])]
        self.configure(SemanticProvider(), kb)
        job = self.enqueue(MULTI_TEXT)
        self.run_to_completion(job)
        self.assertEqual(self.status(job)['state'], 'ready')
        self.assertEqual(self.fake.calls, ['mistral'])
        card = self.final_card(job)
        self.assertIn(INVOICE['excerpt'], card['draft_reply'])
        self.assertIn(NOTIFY['excerpt'], card['draft_reply'])

    def test_partial_answer_with_sources_uses_reasoning_fallback(self):
        kb = [INVOICE, article(NOTIFY['source_id'], 'Рассылку изменить', NOTIFY['excerpt'])]
        self.configure(SemanticProvider(partial=True, reasoning=True), kb)
        job = self.enqueue(MULTI_TEXT)
        self.run_to_completion(job)
        self.assertEqual(self.fake.calls, ['mistral', 'sol'])
        self.assertEqual(self.status(job)['state'], 'ready')
        self.assertEqual(self.rows()[1]['reserved_micro_usd'], SOL_RESERVATION_MICRO)

    def test_customer_clarification_with_procedure_is_final(self):
        procedure = article('TEST-IMPORT', 'Импорт',
                            'При ошибке импорта уточните код ошибки и формат файла.')
        self.configure(SemanticProvider(clarify=True), [procedure])
        job = self.enqueue('Импорт не завершается')
        with patch('ml.cascade.engine.retrieve', wraps=retrieve) as search:
            self.run_to_completion(job)
        self.assertEqual(self.status(job)['state'], 'ready')
        self.assertEqual(self.fake.calls, ['mistral'])
        self.assertEqual(search.call_count, 1)
        self.assertEqual(self.final_card(job)['missing_data'], ['Код ошибки импорта'])

    def test_billing_procedure_missing_from_initial_hits_can_be_retrieved(self):
        procedure = article('TEST-BILLING', 'Проверка начислений',
                            'Сверьте начисления: расчёт суммы должен соответствовать тарифу; '
                            'при расхождении передайте расчёт оператору биллинга.')
        self.configure(AssessmentEdgeProvider(billing_procedure=procedure), [INVOICE, procedure])
        job = self.enqueue('Счёт скачал, но сумма в нём неправильная.')
        with patch('ml.cascade.assessment.retrieve', wraps=retrieve) as expansion_search:
            step(self.queue, self.engine)
            self.assertEqual(self.fake.calls, ['mistral'])
            self.assertEqual([source['source_id'] for source in self.fake.source_calls[0][1]],
                             [INVOICE['source_id']])
            self.assertEqual(self.status(job)['state'], 'queued')
            first = json.loads(self.rows()[0]['diagnostics_json'])
            self.assertEqual(first['route_reason'], 'additional_evidence')
            self.assertEqual(first['stage_context']['retrieval']['new_source_ids'],
                             [procedure['source_id']])
            self.run_to_completion(job)
        self.assertEqual([call.args[0] for call in expansion_search.call_args_list],
                         ['проверка начислений расчёт суммы'])
        self.assertEqual(self.fake.calls, ['mistral', 'sol'])
        self.assertEqual(self.status(job)['state'], 'ready')
        self.assertIn(procedure['source_id'],
                      [source['source_id'] for source in self.fake.source_calls[1][1]])
        self.assertEqual(self.final_card(job)['draft_reply'], procedure['excerpt'])

    def test_unsafe_card_is_blocked_before_malformed_assessment_is_parsed(self):
        self.configure(AssessmentEdgeProvider(malformed_unsafe=True))
        job = self.enqueue('Где счёт?')
        with patch('ml.cascade.assessment.retrieve', wraps=retrieve) as expansion_search:
            self.run_to_completion(job)
        self.assertEqual(self.fake.calls, ['mistral', 'sol'])
        self.assertEqual(self.status(job)['state'], 'manual_review')
        self.assertEqual(expansion_search.call_count, 0)
        diagnostics = json.loads(self.rows()[0]['diagnostics_json'])
        self.assertTrue(Draft202012Validator(fixtures.api.SCHEMA).is_valid(diagnostics['raw_card']))
        self.assertEqual(diagnostics['raw_card']['missing_data'], ['Пароль'])
        self.assertEqual(diagnostics['assessment'], {'questions': None})
        self.assertEqual(diagnostics['route_reason'], 'unsafe_secret_request')
        self.assertTrue(diagnostics['cascade_next'])
        self.assertIsNone(self.final_card(job)['draft_reply'])
        self.assertEqual(sum(row['reserved_micro_usd'] for row in self.rows()), SOL_RESERVATION_MICRO)

    def test_missing_context_expands_once_with_new_query_before_sol(self):
        # Only invoice matches the initial vocabulary; the missing task supplies a new query.
        stale = article('TEST-OLD', 'Уведомления', 'Email уведомления настройки', version='v0')
        draft = article('TEST-DRAFT', 'Уведомления', 'Email уведомления настройки', status='draft')
        self.configure(SemanticProvider(partial=True), [INVOICE, NOTIFY, stale, draft])
        job = self.enqueue(MULTI_TEXT)
        with patch('ml.cascade.engine.retrieve', wraps=retrieve) as initial_search, \
                patch('ml.cascade.assessment.retrieve', wraps=retrieve) as expansion_search:
            step(self.queue, self.engine)
            self.assertEqual(self.status(job)['state'], 'queued')
            self.assertEqual(self.fake.calls, ['mistral'])
            self.assertEqual([source['source_id'] for source in self.fake.source_calls[0][1]],
                             [INVOICE['source_id']])
            self.run_to_completion(job)
        queries = [call.args[0] for call in initial_search.call_args_list]
        expansion_queries = [call.args[0] for call in expansion_search.call_args_list]
        self.assertEqual(expansion_queries, [NOTIFY_QUERY])
        self.assertEqual(queries.count(MULTI_TEXT), 1)
        self.assertLessEqual(expansion_search.call_count, 3)
        self.assertEqual(self.fake.calls, ['mistral', 'sol'])
        self.assertEqual(self.status(job)['state'], 'ready')
        paid_sources = self.fake.source_calls[1][1]
        self.assertEqual({source['source_id'] for source in paid_sources},
                         {INVOICE['source_id'], NOTIFY['source_id']})
        self.assertTrue(all(source['status'] == 'approved' and source['version'] == 'v1'
                            for source in paid_sources))

    def test_no_new_sources_still_get_sol_before_final_manual(self):
        for title in ('Счёт', 'Счёт Email уведомления личные настройки'):
            with self.subTest(expansion='empty' if title == 'Счёт' else 'same_source'):
                old_source = article(INVOICE['source_id'], title, INVOICE['excerpt'])
                self.configure(SemanticProvider(partial=True), [old_source])
                job = self.enqueue(MULTI_TEXT)
                with patch('ml.cascade.assessment.retrieve', wraps=retrieve) as search:
                    self.run_to_completion(job)
                self.assertEqual(self.fake.calls, ['mistral', 'sol'])
                self.assertEqual(self.status(job)['state'], 'manual_review')
                self.assertIsNone(self.final_card(job)['draft_reply'])
                attempts = [row for row in self.rows() if row['job_id'] == job]
                self.assertEqual(sum(row['reserved_micro_usd'] for row in attempts), SOL_RESERVATION_MICRO)
                queries = [call.args[0] for call in search.call_args_list]
                self.assertEqual(queries.count(NOTIFY_QUERY), 1)

    def test_sol_must_cover_original_tasks_without_third_attempt(self):
        for kind in ('partial', 'id', 'quote'):
            with self.subTest(kind=kind):
                self.fake = SemanticProvider(partial=True, sol_partial=kind == 'partial',
                                             sol_identity=None if kind == 'partial' else kind)
                self.engine.provider = self.fake
                self.engine.kb = copy.deepcopy([INVOICE, NOTIFY])
                self.queue.cascade_kb = self.engine.kb
                job = self.enqueue(MULTI_TEXT)
                for _ in range(5):
                    step(self.queue, self.engine)
                self.assertEqual(self.fake.calls, ['mistral', 'sol'])
                self.assertEqual(self.status(job)['state'], 'manual_review')
                self.assertIsNone(self.final_card(job)['draft_reply'])
                with self.queue.connect() as connection:
                    count = connection.execute('SELECT COUNT(*) AS n FROM model_attempt WHERE job_id=?',
                                               (job,)).fetchone()['n']
                self.assertEqual(count, 2)

    def test_transport_and_format_failures_keep_reserved_sol_backup(self):
        for error in (ProviderFailure('provider_transport_or_timeout'), 'format'):
            with self.subTest(error=str(error)):
                provider = SemanticProvider(mistral_error=error)
                self.configure(provider)
                job = self.enqueue('Где счёт?')
                step(self.queue, self.engine)
                self.assertEqual(self.status(job)['state'], 'queued')
                step(self.queue, self.engine)
                self.assertEqual(provider.calls, ['mistral', 'sol'])
                self.assertEqual(self.status(job)['state'], 'ready')

    def test_verified_input_risk_stops_but_model_unsafe_or_conflict_gets_sol(self):
        for kind in ('risk', 'unsafe', 'conflict'):
            with self.subTest(kind=kind):
                provider = SemanticProvider(unsafe=kind == 'unsafe', conflict=kind == 'conflict')
                self.configure(provider)
                text = 'Токен опубликован в публичном чате' if kind == 'risk' else 'Где счёт?'
                job = self.enqueue(text)
                for _ in range(3):
                    step(self.queue, self.engine)
                self.assertEqual(provider.calls, [] if kind == 'risk' else ['mistral', 'sol'])
                self.assertEqual(self.status(job)['state'], 'manual_review')
                self.assertIsNone(self.final_card(job)['draft_reply'])

    def test_durable_context_survives_restart_and_cannot_be_supplied_by_caller(self):
        self.configure(SemanticProvider(partial=True))
        job = self.enqueue(MULTI_TEXT)
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'queued')
        first_diagnostics = json.loads(self.rows()[0]['diagnostics_json'])
        self.assertIn('stage_context', first_diagnostics)
        self.assertEqual({source['source_id'] for source in first_diagnostics['stage_context']['sources']},
                         {INVOICE['source_id'], NOTIFY['source_id']})
        restarted = fixtures.api.Queue(self.queue.connect, fixtures.api.module.fallback,
                                       cascade_enabled=True)
        reserved = restarted.claim()
        self.assertEqual(reserved['provider'], 'sol')
        payload = json.loads(reserved['input_json'])
        context = restarted.inference_context(job, reserved['claim_id'], payload)
        self.assertEqual(context['questions'], first_diagnostics['stage_context']['questions'])
        self.assertEqual({source['source_id'] for source in context['sources']},
                         {INVOICE['source_id'], NOTIFY['source_id']})
        replacement_provider = SemanticProvider()
        # A deployment may load another KB while this job still owns its pinned snapshot.
        replacement_kb = [article('TEST-REPLACEMENT', 'Другая статья', 'Новая процедура.')]
        replacement_engine = CascadeEngine(replacement_kb, fixtures.api.SCHEMA,
                                           fixtures.api.provenance_gate, replacement_provider)
        postgres = self.target.startswith('postgresql://')
        env = {'SERVICE_TOKEN': 'synthetic-service',
               'DATABASE_URL': self.target if postgres else '', 'DATABASE_PATH': self.target}
        body = {'input': payload, 'timeout': 15, 'provider': 'sol', 'job_id': job,
                'attempt_id': reserved['claim_id']}
        from backend.ai_service import create_app
        with patch.dict(os.environ, env), patch('backend.ai_service.build_engine', return_value=replacement_engine):
            with TestClient(create_app()) as client:
                headers = {'Authorization': 'Bearer synthetic-service'}
                forged = dict(body, stage_context={'questions': [], 'sources': []})
                self.assertEqual(client.post('/internal/inference', json=forged, headers=headers).status_code, 422)
                self.assertEqual(replacement_provider.calls, [])
                response = client.post('/internal/inference', json=body, headers=headers)
                self.assertEqual(response.status_code, 200)
                result = response.json()
                self.assertEqual(client.post('/internal/inference', json=body, headers=headers).status_code, 409)
        restarted.finish(reserved, result)
        self.assertEqual(replacement_provider.calls, ['sol'])
        self.assertEqual(self.status(job)['state'], 'ready')
        self.assertEqual({source['source_id'] for source in replacement_provider.source_calls[0][1]},
                         {INVOICE['source_id'], NOTIFY['source_id']})
        card = self.final_card(job)
        self.assertEqual(set(card), set(fixtures.api.SCHEMA['required']))
        self.assertTrue(Draft202012Validator(fixtures.api.SCHEMA).is_valid(card))
        self.assertNotIn('assessment', card)
        self.assertEqual(len(self.rows()), 2)
