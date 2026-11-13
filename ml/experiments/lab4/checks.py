"""Offline regression of production AI boundaries; no external generation."""
import copy
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from ml.contracts.validation import SCHEMA, provenance_gate, read_jsonl
from ml.cascade.engine import CascadeEngine
from ml.cascade.cache import AnswerCache
from ml.cascade.context import make_context
from ml.cascade.providers import build_payload, pack_sources, ProviderFailure
from ml.tools.propose_card import execute_proposal

HERE = Path(__file__).resolve().parent
KB = read_jsonl(ROOT / 'ml/data/cascade/kb.jsonl')


def fixture():
    rows = read_jsonl(HERE/'historical-cascade/ordinary-v3-remaining-20261110/results.jsonl')
    row = next(r for r in rows if r['case_id'] == 'invoice')
    stage = row['attempts'][0]['diagnostics']
    return copy.deepcopy(row['input']), copy.deepcopy(stage)


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.request, self.stage = fixture()
        self.calls = []
        self.clock = [0]
        self.cache = AnswerCache(clock=lambda: self.clock[0])
        def provider(name, request, sources, schema, timeout, trace):
            self.calls.append(name)
            card = copy.deepcopy(self.stage['raw_card'])
            card['request_id'] = request['request_id']
            return dict(card=card, assessment=copy.deepcopy(self.stage['assessment']),
                        usage=self.stage['usage'], http_status=200)
        self.engine = CascadeEngine(KB, SCHEMA, provenance_gate, provider=provider, answer_cache=self.cache)

    def call(self, request=None, scope='authenticated-synthetic-user'):
        return self.engine.analyze_attempt(request or self.request, 'mistral', 25, 'job', 'attempt', cache_scope=scope)

    def test_validated_answer_reuses_no_generation(self):
        before = self.call()
        self.assertTrue(before['automatic_resolution'], before['gate_reason'])
        request = dict(self.request, request_id='cache-new-request')
        after = self.call(request)
        self.assertTrue(after['cache_hit'])
        self.assertEqual(after['attempts'], 0)
        self.assertEqual(after['billed_micro_usd'], 0)
        self.assertIsNone(after['completion_id'])
        self.assertEqual(after['final_card']['request_id'], request['request_id'])
        self.assertEqual(self.calls, ['mistral'])
        after['final_card']['summary'] = 'mutated'
        self.assertNotEqual(self.call()['final_card']['summary'], 'mutated')

    def test_identity_and_changed_context_do_not_hit(self):
        self.call()
        self.assertFalse(self.call(scope='different-authenticated-user')['cache_hit'])
        for change in [dict(ticket_text=self.request['ticket_text']+' подробнее'),
                       dict(history=[{'speaker':'customer','text':'Новый вопрос'}]),
                       dict(kb_version='support-20261112')]:
            self.assertFalse(self.call(dict(self.request, **change))['cache_hit'])

    def test_kb_content_and_prompt_invalidate_cache(self):
        self.call()
        altered = copy.deepcopy(KB)
        for source in altered:
            source['title'] += ' revised'
        self.engine.kb = altered
        self.assertFalse(self.call()['cache_hit'])
        from unittest.mock import patch
        with patch('ml.cascade.engine.build_payload', return_value={'policy':'changed'}):
            self.assertFalse(self.call()['cache_hit'])

    def test_expiration_and_no_scope_disable_cache(self):
        self.call(); self.clock[0] = 301
        self.assertFalse(self.call()['cache_hit'])
        self.assertFalse(self.call(scope=None)['cache_hit'])

    def test_risk_is_not_served_from_cache(self):
        self.call()
        before = len(self.calls)
        result = self.call(dict(self.request, ticket_text='Токен опубликован в публичном чате'))
        self.assertFalse(result['cache_hit'])
        self.assertEqual(result['final_card']['mode'], 'escalate')
        self.assertEqual(len(self.calls), before)

    def test_tool_permission_deadline_and_fabricated_quote(self):
        card = self.stage['raw_card']; sources = self.stage['stage_context']['sources']
        kwargs = dict(card=card, request=self.request, sources=sources, schema=SCHEMA, gate=provenance_gate)
        self.assertEqual(execute_proposal(**kwargs, permissions=frozenset(), deadline=time.monotonic()+1)[1], 'tool_permission_denied')
        self.assertEqual(execute_proposal(**kwargs, permissions=frozenset({'propose_card'}), deadline=0)[1], 'tool_timeout')
        times = iter([0, 2])
        self.assertEqual(execute_proposal(**kwargs, permissions=frozenset({'propose_card'}), deadline=1, clock=lambda:next(times))[1], 'tool_timeout')
        bad = copy.deepcopy(card); bad['evidence'][0]['excerpt'] = 'Invented evidence'
        self.assertFalse(execute_proposal(**dict(kwargs, card=bad), permissions=frozenset({'propose_card'}), deadline=time.monotonic()+1)[0])

    def test_transport_timeout_routes_to_sol_without_autoanswer(self):
        def timeout(*args):
            raise ProviderFailure('provider_transport_or_timeout')
        self.engine.provider = timeout
        result = self.call()
        self.assertTrue(result['cascade_next'])
        self.assertFalse(result['automatic_resolution'])
        self.assertEqual(len(self.cache.entries), 0)
        result = self.engine.analyze_attempt(self.request, 'sol', 25, 'job', 'attempt', stage_context=result['stage_context'])
        self.assertFalse(result['cascade_next'])
        self.assertEqual(result['final_card']['mode'], 'escalate')

    def test_cache_scope_comes_from_stored_actor_only(self):
        from contextlib import contextmanager
        from ml.cascade.service_cache import authenticated_cache_scope
        class Connection:
            def execute(self, query, params):
                self.params=params;return self
            def fetchone(self):return {'actor_ref':'authenticated-actor'}
        class Queue:
            @contextmanager
            def connect(self):yield Connection()
        scope=authenticated_cache_scope(Queue(),'job-id')
        self.assertEqual(len(scope),64)
        self.assertNotIn('authenticated-actor',scope)

    def test_context_compaction_is_lossless_and_has_no_top_n(self):
        request = self.request
        context = make_context(KB, request)
        sources = context['kb_snapshot']
        for provider in ['mistral','sol']:
            old = build_payload(provider, request, sources, SCHEMA, compact=False)
            new = build_payload(provider, request, sources, SCHEMA)
            self.assertEqual(json.loads(old['messages'][1]['content']), json.loads(new['messages'][1]['content']))
            self.assertLess(len(new['messages'][1]['content'].encode()), len(old['messages'][1]['content'].encode()))
            packed, _ = pack_sources(provider, request, sources, SCHEMA)
            self.assertEqual(len(packed), len(sources))
            self.assertGreater(len(packed), 3)


if __name__ == '__main__':
    unittest.main()
