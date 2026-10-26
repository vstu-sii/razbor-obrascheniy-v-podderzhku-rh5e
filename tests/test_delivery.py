"""Delivery regression checks: migration compatibility and human review boundaries."""
import importlib.util
from contextlib import closing
import json
import os
from unittest.mock import patch
from pathlib import Path
import sqlite3
import tempfile
import unittest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT
spec = importlib.util.spec_from_file_location('delivery_audit_api', RUNTIME / 'backend/main.py')
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        legacy = patch.dict(os.environ, {'ENABLE_LEGACY_ANALYZE':'true'})
        legacy.start()
        self.addCleanup(legacy.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'triage.db'
        self.engine = api.Baseline(api.read_jsonl(api.ROOT / 'data/kb.jsonl'), api.SCHEMA,
                                   api.provenance_gate, mode='extractive')
        self.app = api.create_app(self.path, self.engine, 'test-synthetic-only')
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'Authorization': 'Bearer test-synthetic-only'}
        self.request = {'request_id':'audit-1', 'ticket_ref':'audit-ticket',
                        'ticket_text':'Р“РґРµ Р°РґРјРёРЅРёСЃС‚СЂР°С‚РѕСЂСѓ СЃРєР°С‡Р°С‚СЊ СЃС‡С‘С‚?', 'history':[], 'kb_version':'v1'}

    def post(self, path, body):
        return self.client.post(path, json=body, headers=self.headers)

    def proposal(self, **patch):
        response = self.post('/api/analyses', self.request | patch)
        self.assertEqual(response.status_code, 200)
        return '/api/proposals/' + response.json()['proposal_id']

    def test_full_input_and_original_proposal_survive_new_app_instance(self):
        path = self.proposal()
        with TestClient(api.create_app(self.path, self.engine, 'test-synthetic-only')) as restarted:
            result = restarted.get(path, headers=self.headers).json()
            self.assertEqual(result['input'], self.request)
            self.assertTrue(result['card']['evidence'])

    def test_new_review_requires_confirmation(self):
        path = self.proposal()
        body = {'decision':'accepted', 'reason':'РџСЂРѕРІРµСЂРµРЅРѕ РѕРїРµСЂР°С‚РѕСЂРѕРј'}
        self.assertEqual(self.post(path+'/reviews', body).status_code, 422)
        self.assertEqual(self.post(path+'/reviews', body | {'confirmed':'yes'}).status_code, 422)
        self.assertEqual(self.post(path+'/reviews', body | {'confirmed':True}).status_code, 200)

    def test_escalation_requires_queue_and_persists_it(self):
        path = self.proposal(kb_version='v2')
        body = {'decision':'accepted', 'reason':'РџРµСЂРµРґР°С‚СЊ СЃРїРµС†РёР°Р»РёСЃС‚Сѓ', 'confirmed':True}
        self.assertEqual(self.post(path+'/reviews', body).status_code, 422)
        self.assertEqual(self.post(path+'/reviews', body | {'destination':'unknown'}).status_code, 422)
        self.assertEqual(self.post(path+'/reviews', body | {'destination':'technical'}).status_code, 200)
        saved = self.client.get(path, headers=self.headers).json()
        self.assertEqual(saved['reviews'][0]['destination'], 'technical')
        self.assertIsNotNone(saved['reviews'][0]['confirmed_at'])

    def test_whitespace_reason_and_extra_inputs_rejected(self):
        path = self.proposal()
        self.assertEqual(self.post(path+'/reviews', {'decision':'rejected','reason':'   '}).status_code, 422)
        self.assertEqual(self.post('/api/analyses', self.request | {'unexpected':True}).status_code, 422)
        self.assertEqual(self.post('/api/analyses', self.request | {'ticket_text':'  '}).status_code, 422)

    def test_review_cannot_rewrite_model_result(self):
        path = self.proposal()
        before = self.client.get(path, headers=self.headers).json()['card']
        self.assertEqual(self.post(path+'/reviews', {'decision':'edited','reason':'РЈС‚РѕС‡РЅРµРЅРёРµ',
            'final_text':'РџСЂРѕРІРµСЂРµРЅРЅС‹Р№ С‚РµРєСЃС‚', 'confirmed':True}).status_code, 200)
        after = self.client.get(path, headers=self.headers).json()
        self.assertEqual(before, after['card'])
        with closing(sqlite3.connect(self.path)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE analysis_proposal SET card_json='{}'")

    def test_sql_rejects_missing_confirmation_without_api(self):
        path = self.proposal()
        with closing(sqlite3.connect(self.path)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('INSERT INTO operator_review(review_id,proposal_id,actor_ref,decision,reason) VALUES (?,?,?,?,?)',
                                   ('bad', path.split('/')[-1], 'operator', 'accepted', 'test'))

    def test_version_one_upgrade_preserves_existing_data_and_is_repeatable(self):
        old = Path(self.temp.name) / 'legacy.db'
        with closing(sqlite3.connect(old)) as connection:
            connection.executescript((RUNTIME/'migrations/001_init.sql').read_text())
            connection.execute('INSERT INTO analysis_proposal(proposal_id,request_id,input_hash,ticket_ref,card_json,diagnostics_json) VALUES (?,?,?,?,?,?)',
                               ('old','old-request','hash','old-ticket','{}','{}'))
            connection.execute('INSERT INTO operator_review(review_id,proposal_id,actor_ref,decision,final_text,reason) VALUES (?,?,?,?,?,?)',
                               ('old-review','old','actor','edited','old text','old reason'))
            connection.commit()
            api.store_module.migrate(connection, RUNTIME/'migrations')
            api.store_module.migrate(connection, RUNTIME/'migrations')
            self.assertEqual(connection.execute('SELECT version FROM schema_version ORDER BY version').fetchall(), [(1,),(2,),(3,)])
            self.assertEqual(connection.execute('SELECT input_json FROM analysis_proposal').fetchone()[0], '{}')
            self.assertEqual(connection.execute('SELECT final_text FROM operator_review').fetchone()[0], 'old text')


if __name__ == '__main__':
    unittest.main()
