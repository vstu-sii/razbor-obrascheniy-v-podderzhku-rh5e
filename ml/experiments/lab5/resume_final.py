"""Finish only interrupted/unseen inputs after an explicitly authorized budget amendment."""
import argparse
import copy
from decimal import Decimal,ROUND_FLOOR
import json
from pathlib import Path
import shutil
import time

import run_final as run
from backend.handoff import build as handoff_build


def prepare(initial,out,allowance):
    cfg=json.loads((initial/'config.json').read_text(encoding='utf-8'));run.frozen(cfg)
    old_budget=json.loads((initial/'budget.json').read_text(encoding='utf-8'))
    rows=run.read_jsonl(initial/'results.jsonl');plan=json.loads((initial/'plan.json').read_text(encoding='utf-8'))
    assert rows[-1]['reason']=='cascade_money_budget_exhausted'
    assert Decimal(allowance)>Decimal(str(cfg['new_allowance_rub']))
    out.mkdir(parents=True,exist_ok=True)
    if (out/'resume-config.json').exists():raise SystemExit('Resume already prepared.')
    phase_cfg=dict(cfg,new_allowance_rub=allowance,previous_accounted_rub=old_budget['cumulative_accounted_rub'],
                   technical_previous=old_budget['technical_reserved'])
    resume_cfg=dict(created_utc=run.utc(),new_total_allowance_rub=allowance,
                    remaining_phase_allowance_rub=str(Decimal(allowance)-Decimal(old_budget['new_accounted_rub'])),
                    stopped_case_id=rows[-1]['case_id'],resume_runner_hash=run.digest(Path(__file__)),
                    authorization='Participant explicitly approved the new total allowance before this phase.',
                    original_config_hash=run.digest(initial/'config.json'),model_runtime_unchanged=True)
    start=next(i for i,c in enumerate(plan) if c['case_id']==rows[-1]['case_id'])
    run.save(out/'config.json',phase_cfg);run.save(out/'resume-config.json',resume_cfg);run.save(out/'plan.json',plan[start:])
    run.save(out/'saved-first-stage.json',rows[-1]);run.save(out/'initial-budget.json',old_budget)
    print(json.dumps(dict(remaining_inputs=len(plan[start:]),recorded_mistral=rows[-1]['case_id'],new_total_allowance_rub=allowance)),flush=True)


def live(out,local,env_file):
    cfg=json.loads((out/'config.json').read_text(encoding='utf-8'));run.frozen(cfg)
    rcfg=json.loads((out/'resume-config.json').read_text(encoding='utf-8'))
    assert rcfg['resume_runner_hash']==run.digest(Path(__file__))
    if (out/'started.json').exists():raise SystemExit('Already resumed; never repeat completed model calls.')
    local.mkdir(parents=True,exist_ok=True);assert not (local/'runtime.sqlite').exists()
    run.configure(env_file,local);app,queue,engine,guard=run.app_for(cfg,local)
    queue.money_budget_micro=int((Decimal(rcfg['remaining_phase_allowance_rub'])/89*1000000).to_integral_value(rounding=ROUND_FLOOR))
    saved=json.loads((out/'saved-first-stage.json').read_text(encoding='utf-8'))
    class ResumeProvider:
        def __call__(self,provider,*args):
            if guard.case_id==saved['case_id'] and provider=='mistral':
                old=saved['stages'][0]
                if old.get('raw_card') is None:
                    raise run.ProviderFailure(old['gate_reason'],not_sent=True,http_status=old.get('http_status'))
                return dict(card=copy.deepcopy(old['raw_card']),assessment=copy.deepcopy(old.get('assessment')),usage=None,not_sent=True,http_status=old.get('http_status'))
            return guard(provider,*args)
    engine.provider=ResumeProvider();plan=json.loads((out/'plan.json').read_text(encoding='utf-8'))
    run.save(out/'started.json',dict(utc=run.utc(),recorded_first_stage=saved['case_id']))
    stop=None;replay_at=None
    with run.TestClient(app) as api:
        with queue.transaction() as c:
            c.execute('CREATE TABLE experiment_network_call(id TEXT PRIMARY KEY,case_id TEXT,provider TEXT,technical INTEGER,utc TEXT)')
            c.execute('CREATE TABLE experiment_case(case_id TEXT PRIMARY KEY,job_id TEXT,suite TEXT,ordinal INTEGER,wall_ms REAL)')
        for ordinal,case in enumerate(plan):
            if replay_at is not None:
                # Expire the replay-only first-stage quota claim outside the next job.
                while time.monotonic()-replay_at<60.5:time.sleep(.5)
                replay_at=None
            run.pace(queue);guard.case_id=case['case_id'];started=time.perf_counter()
            request=dict(case['request'],request_id='final-'+case['case_id'],ticket_ref='lab5-final-'+case['case_id'])
            response=api.post('/api/demo/tickets',json=request,headers={'Authorization':'Bearer synthetic-final-eval'});assert response.status_code==202
            job=response.json()['job_id']
            with queue.transaction() as c:c.execute('INSERT INTO experiment_case VALUES (?,?,?,?,NULL)',(case['case_id'],job,case['suite'],ordinal))
            while queue.status(job)['state'] in ('queued','running'):
                if not run.step(queue,engine):time.sleep(.5)
                if guard.stop_reason:stop=guard.stop_reason;break
            with queue.transaction() as c:c.execute('UPDATE experiment_case SET wall_ms=? WHERE case_id=?',(round((time.perf_counter()-started)*1000,3),case['case_id']))
            queue.reconcile_billing();rows,budget=run.export(out,queue,cfg);row=rows[-1]
            if case['case_id']==saved['case_id']:replay_at=time.monotonic()
            from langfuse import get_client
            get_client().flush()
            print(json.dumps(dict(case=row['case_id'],phase_observed=len(rows),mode=row['final_card']['mode'],reasons=[s['gate_reason'] for s in row['stages']],phase_accounted_rub=budget['new_accounted_rub'])),flush=True)
            if row['reason']=='cascade_money_budget_exhausted' or stop:stop=stop or row['reason'];break
        run.frozen(cfg);run.save(out/'completion.json',dict(utc=run.utc(),planned=len(plan),observed=len(rows),stop=stop))


def consolidate(initial,resumed,dest):
    old=run.read_jsonl(initial/'results.jsonl');new=run.read_jsonl(resumed/'results.jsonl')
    rcfg=json.loads((resumed/'resume-config.json').read_text(encoding='utf-8'))
    assert run.digest(initial/'config.json')==rcfg['original_config_hash']
    assert old[-1]['case_id']==new[0]['case_id']==rcfg['stopped_case_id']
    a=json.loads((initial/'budget.json').read_text(encoding='utf-8'));b=json.loads((resumed/'budget.json').read_text(encoding='utf-8'))
    cfg=json.loads((initial/'config.json').read_text(encoding='utf-8'));run.frozen(cfg)
    new[0]['interrupted_phase_wall_ms']=old[-1]['end_to_end_ms'];new[0]['completion_phase_wall_ms']=new[0]['end_to_end_ms'];new[0]['end_to_end_ms']=None
    new[0]['latency_note']='Interrupted by budget amendment; no single contiguous end-to-end latency observation.'
    original_m=old[-1]['stages'][0];new[0]['stages'][0]=original_m
    new[0]['trace_ids']=[old[-1]['trace_id'],new[0]['trace_id']]
    if new[0].get('handoff'):
        card,packet=handoff_build(new[0]['input'],new[0]['stages'][-1]['final_card'],
            [dict(provider=s['provider'],invocation_claimed=True,diagnostics=s) for s in new[0]['stages']],new[0]['stages'][-1]['route_reason'])
        new[0]['final_card']=card;new[0]['handoff']=packet;new[0]['handoff_origin']='Backend assembly from original real Mistral and fresh Sol metadata; raw replay packet is preserved.'
    rows=old[:-1]+new;assert len({r['case_id'] for r in rows})==len(rows)
    dest.mkdir(parents=True,exist_ok=True)
    cfg['new_allowance_rub']=rcfg['new_total_allowance_rub'];cfg['budget_amendment']=dict(original_allowance=10,new_allowance=rcfg['new_total_allowance_rub'],input_or_model_changes=False)
    budget=dict(a)
    for key in ('new_confirmed_rub','new_unknown_reserve_rub','new_accounted_rub'):budget[key]=str(Decimal(a[key])+Decimal(b[key]))
    budget.update(new_allowance_rub=rcfg['new_total_allowance_rub'],cumulative_accounted_rub=b['cumulative_accounted_rub'],
                  technical_new=a['technical_new']+b['technical_new'],technical_reserved=b['technical_reserved'])
    assert Decimal(budget['new_accounted_rub'])<=Decimal(str(rcfg['new_total_allowance_rub']))
    run.save(dest/'config.json',cfg);run.save(dest/'budget.json',budget);run.save(dest/'plan.json',json.loads((initial/'plan.json').read_text(encoding='utf-8')))
    (dest/'results.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    for name in ('ledger.json','network-calls.json','billing-receipts.json'):
        merged=json.loads((initial/name).read_text(encoding='utf-8'))+json.loads((resumed/name).read_text(encoding='utf-8'))
        if name=='billing-receipts.json':merged=list({r['receipt_id']:r for r in merged}.values())
        run.save(dest/name,merged)
    for phase,source in [('initial',initial),('resume',resumed)]:
        target=dest/'phases'/phase;target.mkdir(parents=True,exist_ok=True)
        for name in ('config.json','completion.json','budget.json','results.jsonl','verification.json','langfuse-readback.json'):
            shutil.copy2(source/name,target/name)
    v=[json.loads((source/'verification.json').read_text(encoding='utf-8')) for source in (initial,resumed)]
    run.save(dest/'verification.json',dict(runtime_hashes=True,release_tag=cfg['release_tag'],expected_generations=sum(i['expected_generations'] for i in v),
        observed_generations=sum(i['observed_generations'] for i in v),missing=[x for i in v for x in i['missing']],
        phases=2,new_model_calls=0,budget_verified=True))
    done=json.loads((resumed/'completion.json').read_text(encoding='utf-8'))
    run.save(dest/'completion.json',dict(planned=44,observed=len(rows),stop=done['stop'],phases=2,financial_amendment_only=True,interrupted_latency_cases=[new[0]['case_id']]))
    print(json.dumps(dict(consolidated=len(rows),new_accounted_rub=budget['new_accounted_rub'],allowance_rub=budget['new_allowance_rub'])),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--prepare',action='store_true');p.add_argument('--live',action='store_true');p.add_argument('--verify',action='store_true');p.add_argument('--consolidate',action='store_true')
    p.add_argument('--initial',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--destination',type=Path);p.add_argument('--local',type=Path);p.add_argument('--env-file',type=Path);p.add_argument('--allowance',type=int)
    a=p.parse_args()
    if a.prepare:prepare(a.initial,a.output,a.allowance)
    elif a.live:live(a.output,a.local,a.env_file)
    elif a.verify:run.verify(a.output,a.local,a.env_file)
    elif a.consolidate:consolidate(a.initial,a.output,a.destination)
