"""Synthetic tests of identity-bound quota, pre-call reservation and safe degradation."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('infra_api',ROOT/'backend/main.py')
api=importlib.util.module_from_spec(spec);spec.loader.exec_module(api)
from worker import step


class InfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'infra.db'
        target=os.environ.get('INFRA_TEST_DATABASE_URL')
        if target:
            assert target=='postgresql://audit:synthetic_infra_only@127.0.0.1:18522/infra_audit'
            import psycopg
            with psycopg.connect(target) as c:
                c.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public')
            self.path=target
        self.env=patch.dict(os.environ,{'OPERATOR_TOKENS':json.dumps({'alice':'a'*32,'bob':'b'*32}),
            'USER_REQUESTS_PER_DAY':'2','USER_TOKENS_PER_DAY':'1','TOKEN_BUDGET':'10000000',
            'METRICS_TOKEN':'metrics-synthetic-only','SERVICE_TOKEN':'service-synthetic-only',
            'BASELINE_MODE':'llm','AI_BASE_URL':'','OTEL_EXPORTER_OTLP_ENDPOINT':''})
        self.env.start();self.addCleanup(self.env.stop)
        self.calls=[]
        def transport(request,sources,timeout):
            self.calls.append(request['request_id'])
            return self.engine.extractive(request,sources), {'total_tokens':3}
        self.engine=api.Baseline(api.read_jsonl(ROOT/'data/kb.jsonl'),api.SCHEMA,api.provenance_gate,mode='llm',transport=transport)
        self.client=TestClient(api.create_app(self.path,self.engine,'synthetic-default',start_worker=False))
        self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        self.queue=self.client.app.state.queue

    def body(self):
        return {'request_id':str(uuid4()),'ticket_ref':str(uuid4()),'ticket_text':'Где администратору скачать счёт и акт?', 'kb_version':'v1','history':[]}

    def post(self,body=None,user='alice',**headers):
        return self.client.post('/api/demo/tickets',json=body or self.body(),headers={'Authorization':'Bearer '+('a'*32 if user=='alice' else 'b'*32),**headers})

    def test_daily_requests_are_per_verified_user_and_duplicates_are_free(self):
        body=self.body()
        self.assertEqual(self.post(body).status_code,202)
        self.assertTrue(self.post(body).json()['duplicate'])
        self.assertEqual(self.post().status_code,202)
        self.assertEqual(self.post(**{'X-User':'bob'}).status_code,429)
        self.assertEqual(self.post(user='bob').status_code,202)
        with self.queue.connect() as c:
            rows=c.execute('SELECT actor_ref,requests FROM daily_usage ORDER BY actor_ref').fetchall()
            self.assertEqual([(r['actor_ref'],r['requests']) for r in rows],[('alice',2),('bob',1)])
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM analysis_job').fetchone()['n'],3)

    def test_daily_token_limit_selects_cheap_algorithm_before_paid_call(self):
        self.post();step(self.queue,self.engine)
        self.assertEqual(self.calls,[])
        with self.queue.connect() as c:
            job=c.execute('SELECT * FROM analysis_job').fetchone()
            self.assertEqual(job['inference_mode'],'extractive')
            self.assertEqual(c.execute('SELECT reserved_tokens FROM daily_usage').fetchone()['reserved_tokens'],0)
            self.assertEqual(c.execute("SELECT COUNT(*) AS n FROM security_event WHERE event_type='degradation'").fetchone()['n'],1)

    def test_paid_reservation_is_durable_and_failed_call_does_not_refund_unknown_cost(self):
        self.queue.daily.tokens=1000000
        self.post()
        job=self.queue.claim()
        with self.queue.connect() as c:
            before=c.execute('SELECT reserved_tokens FROM daily_usage').fetchone()['reserved_tokens']
        self.assertGreater(before,0)
        self.queue.finish(job)
        with self.queue.connect() as c:
            self.assertEqual(c.execute('SELECT reserved_tokens FROM daily_usage').fetchone()['reserved_tokens'],before)
        with TestClient(api.create_app(self.path,self.engine,'synthetic-default',start_worker=False)) as restarted:
            with restarted.app.state.queue.connect() as c:
                self.assertEqual(c.execute('SELECT reserved_tokens FROM daily_usage').fetchone()['reserved_tokens'],before)

    def test_request_limit_survives_restart_and_next_utc_day_has_separate_counter(self):
        self.post();self.post()
        with TestClient(api.create_app(self.path,self.engine,'synthetic-default',start_worker=False)) as restarted:
            self.assertEqual(restarted.post('/api/demo/tickets',json=self.body(),headers={'Authorization':'Bearer '+'a'*32}).status_code,429)
        with patch.object(self.queue.daily,'day',return_value='2099-01-01'):
            self.assertEqual(self.post().status_code,202)

    def test_daily_model_call_cap_counts_attempts_before_transport(self):
        self.queue.daily.tokens=1000000
        self.queue.daily.calls=1
        self.post();step(self.queue,self.engine)
        self.assertEqual(len(self.calls),1)
        self.post();step(self.queue,self.engine)
        self.assertEqual(len(self.calls),1)
        with self.queue.connect() as c:
            self.assertEqual(c.execute('SELECT model_calls FROM daily_usage').fetchone()['model_calls'],1)

    def test_global_budget_remains_a_hard_cap_without_provider_call(self):
        self.queue.budget=1
        self.queue.daily.tokens=1000000
        self.post();step(self.queue,self.engine)
        self.assertEqual(self.calls,[])
        with self.queue.connect() as c:
            self.assertEqual(c.execute('SELECT state FROM analysis_job').fetchone()['state'],'queued')
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM model_attempt').fetchone()['n'],0)

    def test_concurrent_requests_cannot_exceed_daily_limit(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            responses=list(pool.map(lambda _:self.post().status_code,range(5)))
        self.assertEqual(responses.count(202),2)
        self.assertEqual(responses.count(429),3)

    def test_operator_cannot_access_metrics_or_select_actor_in_body(self):
        self.assertEqual(self.client.get('/metrics',headers={'Authorization':'Bearer '+'a'*32}).status_code,401)
        self.assertEqual(self.post(self.body()|{'actor_ref':'bob'}).status_code,422)
        response=self.client.get('/metrics',headers={'Authorization':'Bearer metrics-synthetic-only'})
        self.assertEqual(response.status_code,200)
        self.assertIn('triage_policy_events_total',response.text)
        self.assertNotIn('alice',response.text)
        self.assertNotIn('a'*32,response.text)

    def test_legacy_route_cannot_bypass_paid_quotas(self):
        with patch.dict(os.environ,{'ENABLE_LEGACY_ANALYZE':'true'}):
            response=self.client.post('/api/analyses',json=self.body(),headers={'Authorization':'Bearer '+'a'*32})
        self.assertEqual(response.status_code,410)
        self.assertEqual(self.calls,[])

    def test_ai_reservation_is_single_use_and_input_bound(self):
        from backend.guards.inference import consume
        from types import SimpleNamespace
        self.queue.daily.tokens=1000000
        body=self.body();self.post(body);job=self.queue.claim()
        request=SimpleNamespace(job_id=job['job_id'],claim_id=job['claim_id'],mode=None,input=api.AnalyzeRequest(**body))
        with patch.dict(os.environ,{'DATABASE_PATH':str(self.path),'DATABASE_URL':''}):
            consume(api.store_module,request)
            with self.assertRaises(api.HTTPException): consume(api.store_module,request)

    def test_json_logs_omit_payload_credentials_and_use_route_templates(self):
        from backend.observability import LOG
        with patch.object(LOG,'info') as captured:
            body=self.body();body['ticket_text']='Private synthetic marker must stay out of logs'
            self.post(body)
            self.client.get('/api/jobs/'+str(uuid4()),headers={'Authorization':'Bearer '+'a'*32})
        logs='\n'.join(call.args[0] for call in captured.call_args_list)
        self.assertNotIn(body['ticket_text'],logs)
        self.assertNotIn('a'*32,logs)
        self.assertIn('/api/jobs/{job_id}',logs)


if __name__=='__main__':unittest.main()
