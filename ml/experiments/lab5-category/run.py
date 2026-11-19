"""Frozen final eval through the real queue. No model calls without --live."""
import argparse
from datetime import datetime,timezone
from decimal import Decimal,ROUND_FLOOR
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
import httpx
from backend.main import create_app
from backend.worker import step
from ml.cascade.engine import CascadeEngine
from ml.cascade.providers import HTTPProvider,MODELS,MAX_OUTPUT,build_payload,provider_tls_context,ProviderFailure
from ml.contracts.validation import SCHEMA,provenance_gate,read_jsonl


def utc():return datetime.now(timezone.utc).isoformat()
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def digest(path):return hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()
def runtime_hashes():
    files=[p for folder in ('backend','migrations','ml/cascade','ml/prompts_v2','ml/contracts','ml/tools') for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix in ('.py','.sql','.json','.txt')]
    files.extend([ROOT/'ml/models/baseline.py',ROOT/'ml/data/cascade/kb.jsonl'])
    return {p.relative_to(ROOT).as_posix():digest(p) for p in sorted(files)}
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()


def prepare(out,tag):
    out.mkdir(parents=True,exist_ok=True)
    if (out/'config.json').exists():raise SystemExit('Already prepared; do not overwrite the frozen plan.')
    commit=git('rev-parse',tag+'^{commit}')
    # All model runtime sources must be precisely those of the tagged release.
    hashes=runtime_hashes()
    for name,value in hashes.items():
        tagged=subprocess.check_output(['git','show',commit+':'+git('rev-parse','--show-prefix')+name],cwd=ROOT).decode('utf-8').replace('\r\n','\n')
        if hashlib.sha256(tagged.encode()).hexdigest()!=value:raise ValueError('Runtime differs from tag: '+name)
    prior=json.loads((HERE/'history/prior-budget.json').read_text(encoding='utf-8'))
    cfg=dict(created_utc=utc(),release_tag=tag,runtime_commit=commit,source_hashes=hashes,runner_hash=digest(Path(__file__)),
             cases_hash=digest(HERE/'fixtures/cases.jsonl'),new_allowance_rub=10,fx_rub_per_usd=89,
             previous_accounted_rub=prior['total_accounted_rub'],previous_unknown_reserve_rub=prior['total_unknown_reserve_rub'],
             technical_previous=prior['technical_reserved'],technical_cap=20000000,
             models=MODELS,stage_timeout_seconds=dict(mistral=30,sol=45),mistral_rpm=2,cache_enabled=False,
             authorization='Participant authorized a separate new allowance of up to 10 RUB for the complete final run.',
             scope='20 frozen diagnostic input snapshots, real queue/API/worker and real providers; no deployment or external helpdesk delivery.',
             policy='No source/prompt/label changes during run; no paid retries; uncertain charges retain reservations.')
    save(out/'config.json',cfg);save(out/'plan.json',read_jsonl(HERE/'fixtures/cases.jsonl'));save(out/'previous-budget.json',prior)
    print(json.dumps(dict(prepared=len(read_jsonl(HERE/'fixtures/cases.jsonl')),new_allowance_rub=10,release_tag=tag,new_calls=0)),flush=True)


def frozen(cfg):
    assert cfg['source_hashes']==runtime_hashes() and cfg['runner_hash']==digest(Path(__file__))
    assert cfg['cases_hash']==digest(HERE/'fixtures/cases.jsonl')


def configure(env_file,local):
    allowed={'MISTRAL_API_KEY','APIMIRA_API_KEY','LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY','LANGFUSE_BASE_URL','CASCADE_CA_FILE','SSL_CERT_FILE'}
    for line in env_file.read_text(encoding='utf-8-sig').splitlines():
        key,sep,value=line.partition('=')
        if sep and key.strip() in allowed:os.environ[key.strip()]=value.strip().strip('\"\'')
    ca=os.environ.get('CASCADE_CA_FILE')
    if ca and not Path(ca).is_file():
        candidate=env_file.parent/'validation-output'/Path(ca).name
        assert candidate.is_file(),'Trusted CA bundle unavailable';os.environ['CASCADE_CA_FILE']=str(candidate)
    if os.environ.get('SSL_CERT_FILE') and not Path(os.environ['SSL_CERT_FILE']).is_file():
        import ssl
        local.mkdir(parents=True,exist_ok=True);bundle=local/'trusted-roots.pem'
        bundle.write_text(''.join(ssl.DER_cert_to_PEM_cert(c) for c in provider_tls_context().get_ca_certs(binary_form=True)),encoding='ascii')
        os.environ['SSL_CERT_FILE']=str(bundle)
    assert all(os.environ.get(k) for k in ('MISTRAL_API_KEY','APIMIRA_API_KEY','LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY'))
    os.environ.update(CASCADE_LIVE_ENABLED='true',SOL_LIVE_ENABLED='true',MISTRAL_FREE_CONFIRMED='true',
        TRACE_SYNTHETIC_CONTENT='true',BASELINE_MODE='cascade',AI_BASE_URL='',CASCADE_BUDGET_RUB='10',
        MISTRAL_REQUESTS_PER_MINUTE='2',MISTRAL_MAX_RUNNING='1',TOKEN_BUDGET='20000000',USER_TOKENS_PER_DAY='20000000')


class Guard:
    def __init__(self,cfg):self.cfg=cfg;self.queue=None;self.case_id=None;self.stop_reason=None
    def __call__(self,provider,request,sources,schema,timeout,trace_id):
        frozen(self.cfg)
        payload=build_payload(provider,request,sources,schema)
        technical=len(json.dumps(payload,ensure_ascii=False).encode())+4096+MAX_OUTPUT
        with self.queue.transaction() as c:
            used=c.execute('SELECT COALESCE(SUM(technical),0) AS n FROM experiment_network_call').fetchone()['n']
            if used+technical+self.cfg['technical_previous']>self.cfg['technical_cap']:
                self.stop_reason='technical_allowance'
                raise ProviderFailure('experiment_technical_allowance',not_sent=True)
            claim=c.execute("SELECT request_id FROM model_attempt WHERE provider=? AND state='reserved' AND invocation_claimed=1",(provider,)).fetchall()
            assert len(claim)==1
            c.execute('INSERT INTO experiment_network_call VALUES (?,?,?,?,?)',(claim[0]['request_id'],self.case_id,provider,technical,utc()))
        return HTTPProvider()(provider,request,sources,schema,timeout,trace_id)


def app_for(cfg,local):
    guard=Guard(cfg)
    engine=CascadeEngine(read_jsonl(ROOT/'ml/data/cascade/kb.jsonl'),SCHEMA,provenance_gate,guard,answer_cache=None)
    app=create_app(local/'runtime.sqlite',engine,'synthetic-final-eval',start_worker=False)
    queue=app.state.queue;guard.queue=queue
    queue.money_budget_micro=int((Decimal(cfg['new_allowance_rub'])/89*1000000).to_integral_value(rounding=ROUND_FLOOR))
    return app,queue,engine,guard


def export(out,queue,cfg):
    rows=[]
    with queue.connect() as c:
        for registration in c.execute('SELECT * FROM experiment_case ORDER BY ordinal').fetchall():
            job=c.execute('SELECT * FROM analysis_job WHERE job_id=?',(registration['job_id'],)).fetchone()
            attempts=c.execute('SELECT diagnostics_json FROM model_attempt WHERE job_id=? ORDER BY attempt_no',(job['job_id'],)).fetchall()
            stages=[json.loads(a['diagnostics_json']) for a in attempts if a['diagnostics_json'] and 'final_card' in json.loads(a['diagnostics_json'])]
            proposal=c.execute('SELECT * FROM analysis_proposal WHERE proposal_id=?',(job['proposal_id'],)).fetchone() if job['proposal_id'] else None
            rows.append(dict(case_id=registration['case_id'],suite=registration['suite'],input=json.loads(job['input_json']),stages=stages,
                final_card=json.loads(proposal['card_json']) if proposal else None,handoff=json.loads(proposal['diagnostics_json']).get('handoff') if proposal else None,
                state=job['state'],reason=job['reason'],end_to_end_ms=registration['wall_ms'],
                complete=job['state'] in ('ready','manual_review') and job['reason']!='cascade_money_budget_exhausted',
                trace_id=hashlib.sha256(job['job_id'].encode()).hexdigest()[:32]))
        ledger=[dict(r) for r in c.execute('SELECT request_id,provider,reserved_micro_usd,billed_micro_usd FROM model_attempt').fetchall()]
        calls=[dict(r) for r in c.execute('SELECT * FROM experiment_network_call').fetchall()]
        receipts=[dict(r) for r in c.execute('SELECT * FROM provider_billing_receipt').fetchall()]
    confirmed=sum(r['billed_micro_usd'] or 0 for r in ledger);unknown=sum(r['reserved_micro_usd'] for r in ledger if r['billed_micro_usd'] is None)
    budget=dict(new_allowance_rub=cfg['new_allowance_rub'],new_confirmed_rub=str(Decimal(confirmed)*89/1000000),
                new_unknown_reserve_rub=str(Decimal(unknown)*89/1000000),new_accounted_rub=str(Decimal(confirmed+unknown)*89/1000000),
                previous_accounted_rub=cfg['previous_accounted_rub'],cumulative_accounted_rub=str(Decimal(cfg['previous_accounted_rub'])+Decimal(confirmed+unknown)*89/1000000),
                previous_unknown_reserve_rub=cfg['previous_unknown_reserve_rub'],technical_previous=cfg['technical_previous'],technical_new=sum(r['technical'] for r in calls),technical_cap=cfg['technical_cap'])
    budget['technical_reserved']=budget['technical_previous']+budget['technical_new']
    assert Decimal(budget['new_accounted_rub'])<=cfg['new_allowance_rub'] and budget['technical_reserved']<=cfg['technical_cap']
    (out/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    for name,value in [('budget',budget),('ledger',ledger),('network-calls',calls),('billing-receipts',receipts)]:save(out/(name+'.json'),value)
    return rows,budget


def pace(queue):
    with queue.connect() as c:recent=c.execute("SELECT utc FROM experiment_network_call WHERE provider='mistral' ORDER BY utc DESC LIMIT 2").fetchall()
    if len(recent)==2:
        while (datetime.now(timezone.utc)-datetime.fromisoformat(recent[-1]['utc'])).total_seconds()<60.5:time.sleep(.5)


def live(out,local,env_file):
    cfg=json.loads((out/'config.json').read_text(encoding='utf-8'));frozen(cfg)
    if (out/'started.json').exists():raise SystemExit('Already started; inspect existing results rather than repeating paid calls.')
    local.mkdir(parents=True,exist_ok=True);assert not (local/'runtime.sqlite').exists()
    configure(env_file,local);app,queue,engine,guard=app_for(cfg,local)
    plan=json.loads((out/'plan.json').read_text(encoding='utf-8'));save(out/'started.json',dict(utc=utc(),new_allowance_rub=cfg['new_allowance_rub']))
    stop=None
    with TestClient(app) as api:
        with queue.transaction() as c:
            c.execute('CREATE TABLE experiment_network_call(id TEXT PRIMARY KEY,case_id TEXT,provider TEXT,technical INTEGER,utc TEXT)')
            c.execute('CREATE TABLE experiment_case(case_id TEXT PRIMARY KEY,job_id TEXT,suite TEXT,ordinal INTEGER,wall_ms REAL)')
        for ordinal,case in enumerate(plan):
            pace(queue);guard.case_id=case['case_id'];started=time.perf_counter()
            request=dict(case['request'],request_id='ai033-'+case['case_id'],ticket_ref='lab5-ai033-'+case['case_id'])
            response=api.post('/api/demo/tickets',json=request,headers={'Authorization':'Bearer synthetic-final-eval'});assert response.status_code==202
            job=response.json()['job_id']
            with queue.transaction() as c:c.execute('INSERT INTO experiment_case VALUES (?,?,?,?,NULL)',(case['case_id'],job,case['suite'],ordinal))
            while queue.status(job)['state'] in ('queued','running'):
                if not step(queue,engine):time.sleep(.5)
                if guard.stop_reason:stop=guard.stop_reason;break
            wall=round((time.perf_counter()-started)*1000,3)
            with queue.transaction() as c:c.execute('UPDATE experiment_case SET wall_ms=? WHERE case_id=?',(wall,case['case_id']))
            queue.reconcile_billing();rows,budget=export(out,queue,cfg);row=rows[-1]
            from langfuse import get_client
            get_client().flush()
            print(json.dumps(dict(case=row['case_id'],observed=len(rows),state=row['state'],mode=(row['final_card'] or {}).get('mode'),
                  reasons=[s['gate_reason'] for s in row['stages']],new_accounted_rub=budget['new_accounted_rub'])),flush=True)
            if row['reason']=='cascade_money_budget_exhausted' or stop:stop=stop or row['reason'];break
        frozen(cfg);save(out/'completion.json',dict(utc=utc(),planned=len(plan),observed=len(rows),stop=stop))


def verify(out,local,env_file):
    cfg=json.loads((out/'config.json').read_text(encoding='utf-8'));frozen(cfg);configure(env_file,local)
    app,queue,_,_=app_for(cfg,local)
    with TestClient(app):
        for index in range(7):
            queue.reconcile_billing();rows,budget=export(out,queue,cfg)
            if Decimal(budget['new_unknown_reserve_rub'])==0:break
            time.sleep(5)
    observations=[]
    with httpx.Client(timeout=10,trust_env=False,verify=provider_tls_context(),follow_redirects=False) as client:
        for row in rows:
            response=client.get(os.environ['LANGFUSE_BASE_URL']+'/api/public/v2/observations',auth=(os.environ['LANGFUSE_PUBLIC_KEY'],os.environ['LANGFUSE_SECRET_KEY']),
                params=dict(fields='core,basic,usage',limit=100,filter=json.dumps([dict(type='string',column='traceId',operator='=',value=row['trace_id'])])))
            response.raise_for_status()
            entries=[{k:r.get(k) for k in ('id','traceId','type','name','model','usageDetails','level')} for r in response.json().get('data',[]) if r.get('type')=='GENERATION']
            observations.append(dict(case_id=row['case_id'],expected=sum(not s['not_sent'] for s in row['stages']),observed=len(entries),entries=entries))
    save(out/'langfuse-readback.json',observations)
    result=dict(runtime_hashes=True,release_tag=cfg['release_tag'],expected_generations=sum(r['expected'] for r in observations),observed_generations=sum(r['observed'] for r in observations),
                missing=[r['case_id'] for r in observations if r['expected']!=r['observed']],new_model_calls=0,budget_verified=True)
    save(out/'verification.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepare',action='store_true');p.add_argument('--live',action='store_true');p.add_argument('--verify',action='store_true')
    p.add_argument('--output',type=Path,default=HERE/'evidence/final');p.add_argument('--local',type=Path,default=HERE/'validation-output')
    p.add_argument('--env-file',type=Path);p.add_argument('--tag',default='lab5-ai-engineer-categories-2026-11-20')
    a=p.parse_args()
    if a.prepare:prepare(a.output,a.tag)
    elif a.live:live(a.output,a.local,a.env_file)
    elif a.verify:verify(a.output,a.local,a.env_file)
    else:p.print_help()
