"""Reproduce LAB04 evidence offline; --live is a bounded synthetic A/B opt-in."""
import argparse
import copy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from ml.contracts.validation import SCHEMA, provenance_gate, read_jsonl
from ml.cascade.engine import CascadeEngine
from ml.cascade.cache import AnswerCache
from ml.cascade.context import make_context
from ml.cascade.retrieval import retrieve
from ml.cascade.providers import (build_payload, HTTPProvider, SOL_RESERVATION_MICRO,
                                  provider_tls_context)

HERE = Path(__file__).resolve().parent
FX = Decimal(89)
PREVIOUS_ACCOUNTED_MICRO = 164803  # 14.667467 RUB, including old unknown reserves.
LIFETIME_CAP_RUB = Decimal('18.407652')


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def historical():
    rows = []
    for series in sorted((HERE/'historical-cascade').iterdir()):
        rows.extend(read_jsonl(series/'results.jsonl'))
    return rows


def offline(out):
    kb = read_jsonl(ROOT/'ml/data/cascade/kb.jsonl')
    metrics, payloads, replays, caching = [], [], [], []
    # Deduplicate disjoint v3 plans, include support-policy cases; v2 stays historical.
    selected = []
    for series in ['ordinary-v3-followup-20261110','ordinary-v3-remaining-20261110','support-policy-live-20261112']:
        selected.extend(read_jsonl(HERE/'historical-cascade'/series/'results.jsonl'))
    for row in selected:
        request = row['input']; expected = row['expected']; context = make_context(kb, request)
        sources = retrieve(request['ticket_text'],request['history'],request['kb_version'],context['kb_snapshot'])
        needed = set(expected['required_sources']); found = {s['source_id'] for s in sources}
        metrics.append(dict(case_id=row['case_id'], kb_version=request['kb_version'],
                            required=sorted(needed), retrieved=sorted(found),
                            required_found=len(needed & found), required_total=len(needed),
                            all_required_found=needed <= found))
        for provider in ['mistral','sol']:
            before = build_payload(provider,request,sources,SCHEMA,compact=False)
            after = build_payload(provider,request,sources,SCHEMA,compact=True)
            assert json.loads(before['messages'][1]['content']) == json.loads(after['messages'][1]['content'])
            payloads.append(dict(case_id=row['case_id'],provider=provider,
                before_context_bytes=len(before['messages'][1]['content'].encode()),
                after_context_bytes=len(after['messages'][1]['content'].encode()),
                identical_decoded_context=True, source_ids=[s['source_id'] for s in sources]))
        count = [0]
        def replay(provider, req, candidates, schema, timeout, trace):
            count[0] += 1
            stage = next(a['diagnostics'] for a in row['attempts'] if a['provider']==provider)
            if stage.get('raw_card') is None:
                from ml.cascade.providers import ProviderFailure
                raise ProviderFailure(stage['gate_reason'],http_status=stage.get('http_status'))
            card=copy.deepcopy(stage['raw_card']);card['request_id']=req['request_id']
            return dict(card=card,assessment=copy.deepcopy(stage['assessment']),usage=stage['usage'],http_status=200)
        engine=CascadeEngine(kb,SCHEMA,provenance_gate,provider=replay,answer_cache=AnswerCache())
        first=engine.analyze_attempt(request,'mistral',25,'offline-job','offline-first',cache_scope='synthetic-fixture')
        if first['cascade_next']:
            final=engine.analyze_attempt(request,'sol',25,'offline-job','offline-second',stage_context=first['stage_context'],cache_scope='synthetic-fixture')
        else:final=first
        replays.append(dict(case_id=row['case_id'],expected_modes=expected['expected_modes'],
                            mode=final['final_card']['mode'],reason=final['gate_reason'],
                            expected_mode=final['final_card']['mode'] in expected['expected_modes'],
                            automatic=final['automatic_resolution'],scope='saved-response replay, not new model quality'))
        if first['automatic_resolution'] and first['final_card']['mode']=='answer':
            n=count[0]
            duplicate=engine.analyze_attempt(dict(request,request_id=request['request_id']+'-repeat'),
                          'mistral',25,'offline-repeat','offline-repeat',cache_scope='synthetic-fixture')
            assert duplicate['cache_hit'] and count[0]==n
            caching.append(dict(case_id=row['case_id'],uncached_generations=2,cached_generations=1,
                                duplicate_same_card=duplicate['final_card']['draft_reply']==first['final_card']['draft_reply'],
                                accounting='controlled saved-response replay; not live billing'))
    ab=json.loads((HERE/'historical-ab/report.json').read_text(encoding='utf-8'))
    ab_rows=read_jsonl(HERE/'historical-ab/responses.jsonl')
    labels={r['id']:r['expected'] for r in json.loads((HERE/'historical-ab/expected.json').read_text(encoding='utf-8'))['cases']}
    recomputed={}
    for version in ('v1','v2'):
        group=[r for r in ab_rows if r['prompt_version']==version]
        assert len(group)==8
        recomputed[version]=dict(n=len(group),valid_quote_gate=sum(r['gate_passed'] for r in group),
            mode_correct=sum(r['raw_card']['mode']==labels[r['case_id']]['mode'] for r in group),
            category_correct=sum(r['raw_card']['category']==labels[r['case_id']]['category'] for r in group),
            total_tokens=sum(r['usage']['total_tokens'] for r in group))
    assert recomputed == ab['versions']
    report=dict(external_model_calls=0,historical_ab=ab['versions'],retrieval=metrics,
                lossless_context=payloads,system_replay=replays,cache_replay=caching,
                mode_matches=sum(r['expected_mode'] for r in replays),cases=len(replays))
    save(out/'offline.json',report)
    print(json.dumps(dict(cases=report['cases'],mode_matches=report['mode_matches'],
                          positive_retrieval_cases=sum(bool(r['required_total']) for r in metrics),
                          complete_positive_retrieval=sum(bool(r['required_total']) and r['all_required_found'] for r in metrics),
                          cache_verified=len(caching),new_model_calls=0)))


def live(out, env_file, ca_file=None):
    """Two variants of ONE predeclared ordinary case; no retries or old ledger reset."""
    import httpx
    for line in env_file.read_text(encoding='utf-8-sig').splitlines():
        key,sep,value=line.partition('=')
        if sep and key.strip() in ('MISTRAL_API_KEY','APIMIRA_API_KEY','LANGFUSE_PUBLIC_KEY',
                                  'LANGFUSE_SECRET_KEY','LANGFUSE_BASE_URL','CASCADE_CA_FILE'):
            os.environ[key.strip()]=value.strip().strip('"\'')
    if ca_file is not None:
        os.environ['CASCADE_CA_FILE']=str(ca_file.resolve())
    provider_tls_context()  # Fail preflight before reserving or calling any model.
    os.environ.update(CASCADE_LIVE_ENABLED='true',SOL_LIVE_ENABLED='true',MISTRAL_FREE_CONFIRMED='true',TRACE_SYNTHETIC_CONTENT='true')
    # Absolute approved cap and the prior accounting are pinned before generation.
    cap=int((LIFETIME_CAP_RUB/FX*1000000).to_integral_value(rounding=ROUND_CEILING))
    ledger=ROOT/'validation-output/lab4-budget.sqlite'
    ledger.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(ledger,timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY,provider TEXT,reserve INTEGER,billed INTEGER,result TEXT,tokens INTEGER)')
    kb=read_jsonl(ROOT/'ml/data/cascade/kb.jsonl')
    request=dict(ticket_ref='lab4-context-pair',ticket_text='Где администратору скачать счёт и как пошагово включить двухфакторную аутентификацию через приложение?',history=[],kb_version='support-20261112')
    config=dict(protocol='paired lossless JSON context; one case, two independent cascade jobs, no retries',
                request=request,expected_mode='answer',variants=['spaced','compact'],fx_rub_per_usd=89,
                previous_accounted_micro_usd=PREVIOUS_ACCOUNTED_MICRO,lifetime_cap_rub=str(LIFETIME_CAP_RUB),
                max_new_sol_calls=2,previous_technical_reserved=5276257,technical_limit=10000000,
                model_policy='Mistral first; Sol only after non-passing Mistral',
                source_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                               for folder in ['ml/cascade','ml/tools','ml/prompts_v2'] for p in (ROOT/folder).glob('*') if p.is_file()})
    save(out/'config.json',config)
    results=[]
    for variant in config['variants']:
        req=dict(request,request_id=str(uuid4()));job=str(uuid4());trace=hashlib.sha256(job.encode()).hexdigest()[:32]
        provider=HTTPProvider(compact=variant=='compact')
        engine=CascadeEngine(kb,SCHEMA,provenance_gate,provider=provider,answer_cache=AnswerCache())
        stages=[];context=None
        for name in ['mistral','sol']:
            reserved=SOL_RESERVATION_MICRO if name=='sol' else 0
            snapshot=make_context(kb,req)
            tokens=21600 if name=='sol' else min(256000,2*len(json.dumps(snapshot,ensure_ascii=False).encode())+65536)
            technical=5276257+db.execute('SELECT COALESCE(SUM(tokens),0) FROM attempts').fetchone()[0]
            if technical+tokens>10000000:
                save(out/'stopped.json',dict(reason='technical_token_cap'));break
            db.execute('BEGIN IMMEDIATE')
            accounted=PREVIOUS_ACCOUNTED_MICRO+db.execute('SELECT COALESCE(SUM(COALESCE(billed,reserve)),0) FROM attempts').fetchone()[0]
            technical=5276257+db.execute('SELECT COALESCE(SUM(tokens),0) FROM attempts').fetchone()[0]
            if accounted+reserved>cap or technical+tokens>10000000:
                db.rollback()
                save(out/'stopped.json',dict(reason='approved_lifetime_budget',accounted_micro_usd=accounted))
                break
            attempt=str(uuid4());at=time.time()
            with db:db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)',(attempt,name,reserved,None,None,tokens))
            incoming_context=copy.deepcopy(context)
            stage=engine.analyze_attempt(req,name,25,job,attempt,stage_context=context,cache_scope='synthetic-lab4-fixture')
            stage['reserved_at']=at
            if stage['not_sent']:
                stage['billed_micro_usd']=0
                stage['billing_reconciliation']='local pre-send stop; no HTTP request issued'
            # Only reconcile unique actual successful provider records, never estimates.
            if name=='sol' and stage.get('usage'):
                try:
                    resp=httpx.get('https://apimira.com/v1/account/usage',headers={'Authorization':'Bearer '+os.environ['APIMIRA_API_KEY']},timeout=10,trust_env=False,verify=provider_tls_context());resp.raise_for_status()
                    records=resp.json()['data'];u=stage['usage']
                    match=[r for r in records if r.get('status')=='success' and r.get('model')==stage['model'] and r.get('tokens_in')==u['prompt_tokens'] and r.get('tokens_out')==u['completion_tokens'] and abs(datetime.fromisoformat(r['created_at'].replace('Z','+00:00')).timestamp()-at)<=60]
                    if len(match)==1:
                        stage['billing_record']=match[0]
                        stage['billed_micro_usd']=int((Decimal(str(match[0]['cost_usd']))*1000000).to_integral_value(rounding=ROUND_CEILING))
                except (httpx.HTTPError,KeyError,ValueError):stage['billing_reconciliation']='unavailable; reserve retained'
            with db:db.execute('UPDATE attempts SET billed=?,result=? WHERE id=?',(stage['billed_micro_usd'],json.dumps(stage,ensure_ascii=False),attempt))
            stages.append(stage);context=stage['stage_context']
            print(json.dumps(dict(variant=variant,provider=name,http=stage['http_status'],reason=stage['gate_reason'],automatic=stage['automatic_resolution'])),flush=True)
            if not stage['cascade_next']:break
        result=dict(variant=variant,input=req,trace_id=trace,stages=stages)
        if stages:
            result['final_card']=stages[-1]['final_card']
            # Repeated validated response should use cache, not another paid call.
            last=stages[-1]
            if last['automatic_resolution'] and last['final_card']['mode']=='answer':
                repeat_req=dict(req,request_id=str(uuid4()))
                if incoming_context is not None:
                    incoming_context['input_hash']=make_context(kb,repeat_req)['input_hash']
                def no_network(*args):
                    raise AssertionError('cache miss; external call prohibited')
                engine.provider=no_network
                try:
                    repeated=engine.analyze_attempt(repeat_req,last['provider'],25,str(uuid4()),str(uuid4()),
                        stage_context=incoming_context,cache_scope='synthetic-lab4-fixture')
                    result['cache_repeat']=dict(cache_hit=repeated.get('cache_hit'),attempts=repeated['attempts'])
                except AssertionError:
                    result['cache_repeat']=dict(cache_hit=False,external_call_blocked=True)
        results.append(result);save(out/'results.json',results)
        if os.environ.get('LANGFUSE_PUBLIC_KEY'):
            from langfuse import get_client
            get_client().flush()
            params=dict(fields='core,basic,io,usage',limit=100,filter=json.dumps([dict(type='string',column='traceId',operator='=',value=trace)]))
            try:
                resp=httpx.get(os.environ['LANGFUSE_BASE_URL']+'/api/public/v2/observations',auth=(os.environ['LANGFUSE_PUBLIC_KEY'],os.environ['LANGFUSE_SECRET_KEY']),params=params,timeout=10,trust_env=False);resp.raise_for_status()
                save(out/('langfuse-'+variant+'.json'),resp.json())
            except httpx.HTTPError:save(out/('langfuse-'+variant+'.json'),dict(verified=False))
    confirmed=sum(s['billed_micro_usd'] or 0 for r in results for s in r['stages'])
    accounted=db.execute('SELECT COALESCE(SUM(COALESCE(billed,reserve)),0) FROM attempts').fetchone()[0]
    save(out/'budget.json',dict(fx=89,previous_accounted_micro_usd=PREVIOUS_ACCOUNTED_MICRO,new_confirmed_micro_usd=confirmed,
              new_accounted_micro_usd=accounted,new_confirmed_rub=str(Decimal(confirmed)/1000000*FX),
              lifetime_accounted_rub=str(Decimal(PREVIOUS_ACCOUNTED_MICRO+accounted)/1000000*FX),cap_rub=str(LIFETIME_CAP_RUB)))
    save(out/'ledger-export.json',[dict(id=row[0],provider=row[1],reserve=row[2],billed=row[3],tokens=row[5]) for row in db.execute('SELECT * FROM attempts')])
    db.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--ca-file',type=Path)
    args=parser.parse_args()
    if args.output.exists():raise SystemExit('Use a new output directory; no evidence overwrite')
    args.output.mkdir(parents=True)
    if args.live:
        if not args.env_file:raise SystemExit('--env-file required for live run')
        live(args.output,args.env_file,args.ca_file)
    else:offline(args.output)
