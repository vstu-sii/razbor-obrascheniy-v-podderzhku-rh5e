import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
"""Article-count and provider-context regressions using synthetic data only."""
import copy
import json
import os
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

import test_cascade as fixtures
from backend.worker import step
from ml.cascade.assessment import expand_sources, validate_assessment
from ml.cascade.context import checked_context, make_context
from ml.cascade.engine import CascadeEngine
from ml.cascade.providers import (
    MAX_INPUT_TOKENS, MAX_OUTPUT, MAX_PAYLOAD_BYTES, MISTRAL_MAX_INPUT_TOKENS,
    MISTRAL_MAX_PAYLOAD_BYTES, MODELS, ProviderFailure, build_payload,
    pack_sources, parse_response,
)
from ml.cascade.retrieval import retrieve


def article(source_id, *, title='Настройки профиля', excerpt='Параметры профиля доступны в настройках.',
            version='v1', status='approved'):
    return {'source_id': source_id, 'version': version, 'status': status,
            'title': title, 'excerpt': excerpt}


def articles(count):
    return [article('ARTICLE-%02d' % index) for index in range(count)]


def request():
    return {'request_id': 'synthetic-article-context',
            'ticket_text': 'Где настройки профиля?', 'history': [], 'kb_version': 'v1'}


def ids(sources):
    return [source['source_id'] for source in sources]


def payload_bytes(payload):
    return len(json.dumps(payload, ensure_ascii=False).encode('utf-8'))


def response(provider, prompt_tokens):
    envelope = {'card': {}, 'assessment': {}}
    return {'model': MODELS[provider], 'choices': [
        {'finish_reason': 'tool_calls', 'message': {'tool_calls': [
            {'type': 'function', 'function': {'name': 'propose_card',
                                           'arguments': json.dumps(envelope)}}]}}],
        'usage': {'prompt_tokens': prompt_tokens, 'completion_tokens': 30}}


class RecordingProvider:
    """Controlled cards record exactly the fragments supplied by the engine."""
    def __init__(self, *, fail_mistral=False, cite_omitted=None):
        self.fail_mistral, self.cite_omitted = fail_mistral, cite_omitted
        self.calls = []

    def __call__(self, provider, payload, sources, schema, timeout, trace_id):
        self.calls.append((provider, copy.deepcopy(sources)))
        if provider == 'mistral' and self.fail_mistral:
            raise ProviderFailure('provider_transport_or_timeout')
        card = fixtures.api.Baseline([], schema, fixtures.api.provenance_gate).extractive(payload, sources)
        if self.cite_omitted is not None:
            card['evidence'] = [{key: self.cite_omitted[key]
                                 for key in ('source_id', 'version', 'title', 'excerpt')}]
        return {'card': card, 'assessment': fixtures.make_assessment(payload, sources, card),
                'usage': {'prompt_tokens': 100, 'completion_tokens': 30},
                'returned_model': MODELS[provider], 'http_status': 200,
                'completion_id': 'synthetic-article-context', 'not_sent': False}


class ArticleContextTests(unittest.TestCase):
    def test_retrieval_keeps_nine_ranked_hits_and_filters_unapproved_versions(self):
        current = articles(9)
        kb = list(reversed(current)) + [
            article('STALE', version='v0'), article('DRAFT', status='draft'),
            article('UNRELATED', title='Архив', excerpt='Документ перенесён.')]
        original = copy.deepcopy(kb)
        found = retrieve(request()['ticket_text'], [], 'v1', kb)
        self.assertEqual(ids(found), ids(current))
        self.assertEqual(kb, original)

    def test_expansion_keeps_seven_existing_and_all_new_whole_articles(self):
        original_sources = articles(7)
        original_sources[0]['excerpt'] = 'Настройки уведомления рассылка.'
        new_sources = [article('EXPANDED-%02d' % index, title='Уведомления рассылка',
                               excerpt='Параметры рассылки доступны.') for index in range(5)]
        supplied = original_sources + [copy.deepcopy(original_sources[0])]
        kb = original_sources + new_sources + [
            article('STALE', title='Уведомления рассылка', version='v0'),
            article('DRAFT', title='Уведомления рассылка', status='draft')]
        assessment = {'questions': [
            {'id': 'q1', 'coverage': 'missing', 'missing_customer_fields': [],
             'search_query': 'уведомления рассылка'}]}
        before = copy.deepcopy((supplied, assessment, kb))
        expanded = expand_sources(request(), supplied, assessment, kb)
        self.assertEqual(ids(expanded['sources'][:7]), ids(original_sources))
        self.assertEqual(set(ids(expanded['sources'])), set(ids(original_sources + new_sources)))
        self.assertEqual(len(expanded['sources']), 12)
        self.assertEqual(set(expanded['new_source_ids']), set(ids(new_sources)))
        self.assertEqual(expanded['queries'], ['уведомления рассылка'])
        self.assertEqual(expanded['query_outcomes'][0]['status'], 'new_candidates')
        self.assertEqual((supplied, assessment, kb), before)
        expanded['sources'][0]['excerpt'] = 'Изменённая копия'
        self.assertEqual((supplied, assessment, kb), before)

    def test_stage_context_roundtrips_more_than_six_and_rejects_seventh_source_tampering(self):
        payload = request()
        context = make_context(articles(9), payload)
        context['sources'] = copy.deepcopy(context['kb_snapshot'])
        checked = checked_context(context, payload)
        self.assertEqual(len(checked['sources']), 9)
        checked['sources'][0]['excerpt'] = 'Локальная копия'
        self.assertNotEqual(checked['sources'], context['sources'])
        forged = copy.deepcopy(context)
        forged['sources'][6]['excerpt'] = 'Подменённая седьмая статья'
        with self.assertRaises(ValueError):
            checked_context(forged, payload)

    def test_seven_citations_and_source_ids_keep_provenance_checks(self):
        payload, sources = request(), articles(7)
        card = fixtures.api.Baseline([], fixtures.api.SCHEMA,
                                     fixtures.api.provenance_gate).extractive(payload, sources)
        assessment = fixtures.make_assessment(payload, sources, card)
        self.assertEqual(len(card['evidence']), 7)
        self.assertEqual(len(assessment['questions'][0]['source_ids']), 7)
        self.assertTrue(Draft202012Validator(fixtures.api.SCHEMA).is_valid(card))
        self.assertTrue(fixtures.api.provenance_gate(card, payload['request_id'], 'v1', sources)[0])
        self.assertTrue(validate_assessment(assessment, card, payload, sources)[0])
        forged = copy.deepcopy(card)
        forged['evidence'][6]['excerpt'] = 'Неподтверждённая седьмая цитата'
        self.assertEqual(fixtures.api.provenance_gate(forged, payload['request_id'], 'v1', sources),
                         (False, 'evidence_provenance'))

    def test_mistral_accepts_larger_context_and_usage_while_sol_keeps_its_bounds(self):
        payload = request()
        sources = [article('LARGE-%02d' % index, excerpt='я' * 2500) for index in range(8)]
        built = build_payload('mistral', payload, sources, fixtures.api.SCHEMA)
        self.assertGreater(payload_bytes(built), MAX_PAYLOAD_BYTES)
        self.assertLessEqual(payload_bytes(built), MISTRAL_MAX_PAYLOAD_BYTES)
        self.assertEqual(json.loads(built['messages'][1]['content'])['kb_fragments'],
                         [{key: source[key] for key in ('source_id', 'version', 'title', 'excerpt')}
                          for source in sources])
        with self.assertRaises(ProviderFailure) as raised:
            build_payload('sol', payload, sources, fixtures.api.SCHEMA)
        self.assertTrue(raised.exception.not_sent)
        self.assertEqual(raised.exception.reason, 'context_budget_exceeded')
        used = MAX_INPUT_TOKENS + 1
        self.assertLessEqual(used, MISTRAL_MAX_INPUT_TOKENS)
        self.assertEqual(parse_response(response('mistral', used), 'mistral')['usage']['prompt_tokens'], used)
        with self.assertRaises(ProviderFailure) as raised:
            parse_response(response('sol', used), 'sol')
        self.assertEqual(raised.exception.reason, 'usage_exceeds_reservation')

    def test_sol_greedily_fits_whole_articles_after_an_oversized_first_candidate(self):
        payload = request()
        payload['_coverage_questions'] = [
            {'id': 'q1', 'question': 'Где настройки профиля?',
             'input_quote': payload['ticket_text'], 'coverage': 'partial',
             'source_ids': ['OVERSIZED-FIRST'], 'answer_excerpt': 'Черновик первой модели',
             'missing_customer_fields': [], 'search_query': 'Настройки'}]
        oversized = article('OVERSIZED-FIRST', excerpt='я' * MAX_PAYLOAD_BYTES)
        small = [article('SMALL-%02d' % index, excerpt='Текст.') for index in range(8)]
        candidates = [oversized] + small
        original = copy.deepcopy(candidates)
        included, metadata = pack_sources('sol', payload, candidates, fixtures.api.SCHEMA)
        self.assertEqual(included, small)
        self.assertEqual(candidates, original)
        self.assertEqual(metadata['candidate_ids'], ids(candidates))
        self.assertEqual(metadata['included_ids'], ids(small))
        self.assertEqual(metadata['omitted_ids'], ['OVERSIZED-FIRST'])
        self.assertEqual(metadata['provider'], 'sol')
        self.assertEqual(metadata['payload_limit_bytes'], MAX_PAYLOAD_BYTES)
        built = build_payload('sol', payload, included, fixtures.api.SCHEMA)
        expected_bytes = payload_bytes(built)
        self.assertEqual(metadata['payload_bytes'], expected_bytes)
        self.assertLessEqual(expected_bytes, MAX_PAYLOAD_BYTES)
        sent_questions = json.loads(built['messages'][1]['content'])['coverage_questions']
        self.assertEqual(sent_questions, [{'id': 'q1', 'question': 'Где настройки профиля?',
                                           'input_quote': payload['ticket_text']}])
        mistral_included, mistral_metadata = pack_sources('mistral', payload, candidates, fixtures.api.SCHEMA)
        self.assertEqual(mistral_included, candidates)
        self.assertEqual(mistral_metadata['omitted_ids'], [])
        self.assertEqual(mistral_metadata['payload_limit_bytes'], MISTRAL_MAX_PAYLOAD_BYTES)

    def test_engine_uses_only_packed_sources_for_provider_context_and_provenance(self):
        payload, candidates = request(), articles(8)
        included, omitted = candidates[:7], candidates[7]
        provider = RecordingProvider(cite_omitted=omitted)
        engine = CascadeEngine(candidates, fixtures.api.SCHEMA, fixtures.api.provenance_gate, provider)
        context = make_context(candidates, payload)
        context['sources'] = copy.deepcopy(candidates)
        metadata = {'candidate_ids': ids(candidates), 'included_ids': ids(included),
                    'omitted_ids': [omitted['source_id']], 'provider': 'sol',
                    'payload_bytes': payload_bytes(build_payload('sol', payload, included, fixtures.api.SCHEMA)),
                    'payload_limit_bytes': MAX_PAYLOAD_BYTES}
        with patch('ml.cascade.engine.pack_sources', return_value=(included, metadata)):
            result = engine.analyze_attempt(payload, 'sol', 15, 'synthetic-job', 'synthetic-attempt', context)
        self.assertEqual(provider.calls, [('sol', included)])
        self.assertEqual(result['retrieved_ids'], ids(included))
        self.assertEqual(result['stage_context']['sources'], included)
        self.assertEqual(result['context_packing'], metadata)
        self.assertFalse(result['gate_passed'])
        self.assertEqual(result['gate_reason'], 'evidence_provenance')
        self.assertFalse(result['cascade_next'])

    def test_followup_error_procedure_fits_with_realistic_history_and_coverage_questions(self):
        kb=fixtures.api.read_jsonl(fixtures.ROOT/'ml/data/cascade/kb.jsonl')
        payload=dict(request_id='followup',ticket_ref='ticket',kb_version='support-20261119',
                     ticket_text='CSV UTF-8 проверен, повторный импорт не помог: снова E42.',
                     history=[dict(speaker='customer',text='Импорт CSV показывает E42, что делать?'),
                              dict(speaker='operator',text='Проверьте, что CSV имеет формат UTF-8, и повторите импорт. Если формат уже проверен, повторная проверка не нужна. Если E42 останется, сообщите время последней попытки и текст сообщения об ошибке. Сам файл и секреты отправлять не нужно.')],
                     _coverage_questions=[dict(id='q1',question='Почему ошибка E42 сохраняется после UTF-8 и повторного импорта?',input_quote='CSV UTF-8 проверен, повторный импорт не помог: снова E42.'),
                                          dict(id='q2',question='Требуются ли дополнительные сведения до передачи специалисту?',input_quote='Импорт CSV показывает E42, что делать?')])
        candidates=[row for row in kb if row['source_id']=='KB-E42-REPEAT-support-20261119']
        self.assertEqual(len(candidates),1)
        included,metadata=pack_sources('sol',payload,candidates,fixtures.api.SCHEMA)
        self.assertEqual(included,candidates)
        self.assertLessEqual(metadata['payload_bytes'],MAX_PAYLOAD_BYTES)


class ArticleQueueTests(unittest.TestCase):
    enqueue = fixtures.CascadeTests.enqueue
    status = fixtures.CascadeTests.status
    rows = fixtures.CascadeTests.rows

    def setUp(self):
        sqlite_only = patch.dict(os.environ, CASCADE_TEST_DATABASE_URL='')
        sqlite_only.start()
        self.addCleanup(sqlite_only.stop)
        fixtures.CascadeTests.setUp(self)

    def configure(self, kb, provider):
        self.engine = CascadeEngine(kb, fixtures.api.SCHEMA, fixtures.api.provenance_gate, provider)
        self.client.app.state.engine = self.engine
        self.queue.cascade_kb = self.engine.kb

    def test_complete_mistral_answer_with_eight_articles_finishes_without_sol(self):
        provider, sources = RecordingProvider(), articles(8)
        self.configure(sources, provider)
        job = self.enqueue(request()['ticket_text'])
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'ready')
        self.assertEqual(provider.calls, [('mistral', sources)])
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]['reserved_micro_usd'], 0)
        with self.queue.connect() as connection:
            row = connection.execute('SELECT card_json FROM analysis_proposal WHERE proposal_id=?',
                                     (self.status(job)['proposal_id'],)).fetchone()
        self.assertEqual(len(json.loads(row['card_json'])['evidence']), 8)

    def test_sol_preflight_failures_do_not_send_and_empty_fit_settles_zero(self):
        payload = request()
        oversized = article('ONLY-OVERSIZED', excerpt='я' * (3 * MAX_PAYLOAD_BYTES))
        with self.assertRaises(ProviderFailure) as raised:
            pack_sources('sol', payload, [oversized], fixtures.api.SCHEMA)
        self.assertEqual(raised.exception.reason, 'no_sources_fit_context')
        self.assertTrue(raised.exception.not_sent)
        oversized_input = dict(payload, ticket_text='я' * MAX_PAYLOAD_BYTES)
        with self.assertRaises(ProviderFailure) as raised:
            pack_sources('sol', oversized_input, [article('SMALL')], fixtures.api.SCHEMA)
        self.assertEqual(raised.exception.reason, 'context_budget_exceeded')
        self.assertTrue(raised.exception.not_sent)
        provider = RecordingProvider(fail_mistral=True)
        self.configure([oversized], provider)
        job = self.enqueue(payload['ticket_text'])
        snapshot_bytes = len(json.dumps([oversized], ensure_ascii=False).encode('utf-8'))
        self.assertGreater(snapshot_bytes, 65536)
        original_budget = self.queue.budget
        self.queue.budget = snapshot_bytes
        step(self.queue, self.engine)
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.status(job)['state'], 'queued')
        self.queue.budget = original_budget
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'queued')
        self.assertGreaterEqual(self.rows()[0]['reserved_tokens'], snapshot_bytes + MAX_OUTPUT)
        self.assertLessEqual(self.rows()[0]['reserved_tokens'], MISTRAL_MAX_INPUT_TOKENS + MAX_OUTPUT)
        step(self.queue, self.engine)
        self.assertEqual(self.status(job)['state'], 'manual_review')
        self.assertEqual([name for name, _ in provider.calls], ['mistral'])
        self.assertEqual(len(self.rows()), 2)
        sol = self.rows()[1]
        self.assertEqual(sol['billed_micro_usd'], 0)
        diagnostics = json.loads(sol['diagnostics_json'])
        self.assertTrue(diagnostics['not_sent'])
        self.assertEqual(diagnostics['route_reason'], 'no_sources_fit_context')
