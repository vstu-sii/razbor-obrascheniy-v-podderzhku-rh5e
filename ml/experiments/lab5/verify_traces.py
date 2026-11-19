"""Rate-limited, cached trace/billing verification. No model generation."""
import argparse
import json
import os
from pathlib import Path
import time
from decimal import Decimal
import httpx
import run_final as run


def wait(seconds):
    until=time.monotonic()+seconds
    while time.monotonic()<until:time.sleep(min(1,until-time.monotonic()))


def verify(evidence,local,env_file,interval):
    cfg=json.loads((evidence/'config.json').read_text(encoding='utf-8'));run.frozen(cfg);run.configure(env_file,local)
    app,queue,_,_=run.app_for(cfg,local)
    with run.TestClient(app):
        for _ in range(3):
            queue.reconcile_billing();rows,budget=run.export(evidence,queue,cfg)
            if Decimal(budget['new_unknown_reserve_rub'])==0:break
            wait(5)
    cached_path=evidence/'langfuse-readback.json'
    old=json.loads(cached_path.read_text(encoding='utf-8')) if cached_path.exists() else []
    cached={row['case_id']:row for row in old};observations=[]
    with httpx.Client(timeout=15,trust_env=False,verify=run.provider_tls_context(),follow_redirects=False) as client:
        for row in rows:
            expected=sum(not s['not_sent'] for s in row['stages'])
            known=cached.get(row['case_id'])
            if known and known.get('trace_id')==row['trace_id'] and known['expected']==known['observed']==expected:
                observations.append(known);continue
            if expected==0:
                observations.append(dict(case_id=row['case_id'],trace_id=row['trace_id'],expected=0,observed=0,entries=[],scope='No HTTPProvider generation attempted'))
                run.save(cached_path,observations);continue
            item=None
            for attempt in range(6):
                response=client.get(os.environ['LANGFUSE_BASE_URL']+'/api/public/v2/observations',
                    auth=(os.environ['LANGFUSE_PUBLIC_KEY'],os.environ['LANGFUSE_SECRET_KEY']),
                    params=dict(fields='core,basic,usage',limit=100,filter=json.dumps([dict(type='string',column='traceId',operator='=',value=row['trace_id'])])))
                if response.status_code==429:
                    try:pause=float(response.headers.get('Retry-After','60'))
                    except ValueError:pause=60
                    print(json.dumps(dict(case=row['case_id'],read_only_rate_limit=True,wait_seconds=pause)),flush=True)
                    wait(max(1,min(pause,120)));continue
                response.raise_for_status()
                entries=[{k:r.get(k) for k in ('id','traceId','type','name','model','usageDetails','level')} for r in response.json().get('data',[]) if r.get('type')=='GENERATION']
                item=dict(case_id=row['case_id'],trace_id=row['trace_id'],expected=expected,observed=len(entries),entries=entries)
                if len(entries)==expected:break
                wait(interval)
            if item is None:raise RuntimeError('Trace endpoint remains rate-limited; rerun read-only verification later.')
            observations.append(item);run.save(cached_path,observations)
            print(json.dumps(dict(checked=len(observations),planned=len(rows),case=row['case_id'],expected=expected,observed=item['observed'])),flush=True)
            wait(interval)
    result=dict(runtime_hashes=True,release_tag=cfg['release_tag'],expected_generations=sum(r['expected'] for r in observations),observed_generations=sum(r['observed'] for r in observations),
                missing=[r['case_id'] for r in observations if r['expected']!=r['observed']],new_model_calls=0,budget_verified=True,
                verifier_hash=run.digest(Path(__file__)),read_interval_seconds=interval)
    run.save(evidence/'verification.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--local',type=Path,required=True);p.add_argument('--env-file',type=Path,required=True);p.add_argument('--interval',type=float,default=6)
    a=p.parse_args();verify(a.evidence,a.local,a.env_file,a.interval)
