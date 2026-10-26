import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evaluate import DATASET, EVIDENCE, SCHEMA, check_floors, provenance_gate, read_jsonl, score
from free_api import load_key, parse_response, payload_for, verify_free_model, mistral_payload
from run import sources_for

class StandaloneTests(unittest.TestCase):
    def setUp(self):
        self.cases = read_jsonl(DATASET / 'cases.jsonl')
        self.kb = read_jsonl(DATASET / 'kb.jsonl')
        self.rows = read_jsonl(EVIDENCE / 'historical-responses.jsonl')
        self.model = 'test/model:free'
        self.catalog = {'data': [{'id': self.model, 'pricing': {'prompt': '0', 'completion': '0'}, 'supported_parameters': ['tools','tool_choice']}]}

    def test_free_only_price_guard_rejects_missing_paid_and_nonfree(self):
        self.assertEqual(verify_free_model(self.model, self.catalog)['model'], self.model)
        for field in ('prompt', 'completion', 'request'):
            catalog = copy.deepcopy(self.catalog)
            catalog['data'][0]['pricing'][field] = '0.0001'
            with self.assertRaises(ValueError):
                verify_free_model(self.model, catalog)
        with self.assertRaises(ValueError):
            verify_free_model('test/model', self.catalog)
        self.catalog['data'][0]['pricing'].pop('prompt')
        with self.assertRaises(ValueError):
            verify_free_model(self.model, self.catalog)

    def test_payload_has_zero_price_cap_no_fallback_or_key(self):
        request = dict(self.cases[0]['input'], request_id='test')
        payload = payload_for(self.model, SCHEMA, request, sources_for(self.cases[0], self.kb))
        self.assertFalse(payload['provider']['allow_fallbacks'])
        self.assertTrue(payload['provider']['require_parameters'])
        self.assertTrue(all(value == 0 for value in payload['provider']['max_price'].values()))
        self.assertNotIn('Authorization', json.dumps(payload))
        with self.assertRaises(ValueError):
            payload_for('paid-model', SCHEMA, request, [])

    def test_key_not_required_for_offline_and_missing_is_explicit(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                load_key()
        with tempfile.TemporaryDirectory() as temporary:
            file = Path(temporary) / '.env'
            file.write_text('UNRELATED=test\nOPENROUTER_API_KEY="synthetic-test-only"\n')
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_key(file), 'synthetic-test-only')

    def test_bad_http_body_is_not_logged(self):
        result = parse_response(401, b'private error body must be discarded')
        self.assertEqual(result['error'], 'http_401')
        self.assertNotIn('private', json.dumps(result))

    def test_nonzero_observed_cost_raises_cost_violation(self):
        value = {'choices': [{'message': {'content': json.dumps(self.rows[0]['raw_card'])}}], 'usage': {'cost': .01}}
        self.assertTrue(parse_response(200, json.dumps(value).encode())['cost_violation'])

    def test_mistral_requires_free_account_confirmation(self):
        request = dict(self.cases[0]['input'], request_id='test')
        with self.assertRaises(ValueError):
            mistral_payload('ministral-14b-2512', SCHEMA, request, [])
        payload = mistral_payload('ministral-14b-2512', SCHEMA, request, [], True)
        self.assertNotIn('provider', payload)
        self.assertEqual(payload['model'], 'ministral-14b-2512')
        self.assertIn('allOf', SCHEMA)

    def test_invalid_json_keeps_usage_and_failure_visible(self):
        value = {'choices': [{'message': {'content': 'invalid json'}}], 'usage': {'total_tokens': 7}}
        result = parse_response(200, json.dumps(value).encode())
        self.assertIsNone(result['raw_card'])
        self.assertEqual(result['error'], 'invalid_model_json')
        self.assertEqual(result['usage']['total_tokens'], 7)

    def test_candidate_context_filters_stale_version(self):
        stale = next(case for case in self.cases if case['id'] == 'EDGE-STALE-01')
        self.assertEqual(sources_for(stale, self.kb), [])

    def test_dataset_and_independent_call_count(self):
        self.assertEqual(len(self.cases), 43)
        self.assertEqual(len({case['id'] for case in self.cases}), 43)
        self.assertEqual(sum(case['id'] == case['group_id'] for case in self.cases), 38)

    def test_historical_scores_and_red_regression_threshold(self):
        floors = json.loads(Path(__file__).with_name('ci-floors.json').read_text())
        self.assertEqual(check_floors(score(self.rows, self.cases, self.kb), floors), [])
        broken = [dict(row, raw_card=None) for row in self.rows]
        self.assertTrue(check_floors(score(broken, self.cases, self.kb), floors))

    def test_gate_blocks_forged_evidence_and_no_human_review(self):
        row = self.rows[0]
        card = row['raw_card']
        self.assertTrue(provenance_gate(card, row['request_id'], 'v1', self.kb)[0])
        for changes in ({'evidence': []}, {'needs_human_review': False}, {'request_id': 'wrong'}):
            self.assertFalse(provenance_gate(dict(card, **changes), row['request_id'], 'v1', self.kb)[0])
        forged = copy.deepcopy(card)
        forged['evidence'][0]['excerpt'] = 'invented'
        self.assertFalse(provenance_gate(forged, row['request_id'], 'v1', self.kb)[0])

if __name__ == '__main__':
    unittest.main()
