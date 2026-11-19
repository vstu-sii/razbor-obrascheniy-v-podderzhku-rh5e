"""Offline final metrics and one-component ablations; never invokes model APIs."""
import argparse
from collections import Counter
import copy
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
from unittest.mock import patch

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2];sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from backend.main import create_app
from backend.worker import step
from backend.handoff import build as handoff_build
from ml.cascade.engine import CascadeEngine
from ml.cascade.cache import AnswerCache
from ml.cascade.providers import ProviderFailure
from ml.cascade.retrieval import retrieve
from ml.contracts.validation import SCHEMA,provenance_gate,read_jsonl

LABELS=['access','payment','settings','integrations','product_error','other']
def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def percentile(values,q):
    if not values:return None
    values=sorted(values);pos=(len(values)-1)*q;lo=math.floor(pos);hi=math.ceil(pos)
    return values[lo]+(values[hi]-values[lo])*(pos-lo)
def fraction(a,b):return dict(numerator=a,denominator=b,value=a/b if b else None)
def macro_f1(gold,pred):
    labels=sorted(set(gold)|set(pred));parts=[]
    for label in labels:
        tp=sum(a==b==label for a,b in zip(gold,pred));fp=sum(a!=label and b==label for a,b in zip(gold,pred));fn=sum(a==label and b!=label for a,b in zip(gold,pred))
        parts.append(dict(label=label,tp=tp,fp=fp,fn=fn,f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else 0))
    return dict(value=statistics.mean(p['f1'] for p in parts) if parts else None,per_class=parts)


def metrics(rows,cases):
    expected={r['case_id']:r['expected'] for r in cases};observed=[r for r in rows if r['case_id'] in expected and r.get('final_card')]
    gold=[expected[r['case_id']]['category'] for r in observed];pred=[r['final_card']['category'] for r in observed]
    nonanswer=[r for r in observed if expected[r['case_id']]['mode']!='answer']
    raw_m=[s for r in observed for s in r['stages'] if s['provider']=='mistral' and not s['not_sent']]
    schema=Draft202012Validator(SCHEMA)
    kb=read_jsonl(ROOT/'ml/data/cascade/kb.jsonl');hit3=hitall=eligible=0
    for row in observed:
        target=set(expected[row['case_id']].get('evidence_ids',[]))
        if not target:continue
        eligible+=1;req=row['input'];ranked=retrieve(req['ticket_text'],req['history'],req['kb_version'],kb)
        hit3+=bool(target & {s['source_id'] for s in ranked[:3]});hitall+=bool(target & {s['source_id'] for s in ranked})
    times=[r['end_to_end_ms'] for r in observed if r.get('end_to_end_ms') is not None]
    tokens=Counter()
    for row in observed:
        for stage in row['stages']:
            usage=stage.get('usage') or {}
            tokens['input']+=usage.get('prompt_tokens',usage.get('input_tokens',0)) or 0
            tokens['output']+=usage.get('completion_tokens',usage.get('output_tokens',0)) or 0
            tokens['reasoning']+=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens',0) or 0
    final_provenance=0
    for row in observed:
        req=row['input'];snapshot=[s for s in kb if s['version']==req['kb_version'] and s['status']=='approved']
        known={s['source_id']:s for s in snapshot}
        if all(s.get('source_id') in known and all(s.get(k)==known[s['source_id']][k] for k in ('version','title','excerpt')) for s in row['final_card']['evidence']):final_provenance+=1
    return dict(planned=len(cases),observed=len(observed),completed=sum(r.get('complete',True) for r in observed),
        mode_accuracy=fraction(sum(r['final_card']['mode']==expected[r['case_id']]['mode'] for r in observed),len(observed)),
        category_accuracy=fraction(sum(a==b for a,b in zip(gold,pred)),len(observed)),category_macro_f1=macro_f1(gold,pred),
        valid_final_schema=fraction(sum(schema.is_valid(r['final_card']) for r in observed),len(observed)),
        exact_final_citations=fraction(final_provenance,len(observed)),
        false_answer_on_nonanswer=fraction(sum(r['final_card']['mode']=='answer' for r in nonanswer),len(nonanswer)),
        automatic=fraction(sum(not r['final_card']['needs_human_review'] for r in observed),len(observed)),
        raw_mistral_schema=fraction(sum(isinstance(s.get('raw_card'),dict) and schema.is_valid(s['raw_card']) for s in raw_m),len(raw_m)),
        recall_at_3=fraction(hit3,eligible),recall_all_lexical_hits=fraction(hitall,eligible),
        latency=dict(n=len(times),p50_ms=percentile(times,.5),p95_ms=percentile(times,.95),scope='Actual local queue/worker/API wall time; pacing before enqueue excluded'),
        tokens=dict(tokens),gates=dict(Counter(s['gate_reason'] for r in observed for s in r['stages'])),
        not_proven=['independent generalization','semantic atomic-fact accuracy','human North Star','production 2-percent bound','production uptime'])


class RecordedProvider:
    def __init__(self,row):self.row=row;self.calls=[]
    def __call__(self,name,*args):
        self.calls.append(name);stage=next((s for s in self.row['stages'] if s['provider']==name),None)
        if stage is None:raise AssertionError('Replay requested a model stage absent from the live record')
        if stage.get('raw_card') is None:
            error=ProviderFailure(stage['gate_reason'],not_sent=True,http_status=stage.get('http_status'))
            error.metadata={k:stage[k] for k in ('transport_error','provider_request_id') if stage.get(k)}
            raise error
        # not_sent is true in this offline replay: transport and billing do not occur.
        return dict(card=copy.deepcopy(stage['raw_card']),assessment=copy.deepcopy(stage.get('assessment')),usage=None,not_sent=True,http_status=stage.get('http_status'))


class NoSecondStage(CascadeEngine):
    def analyze_attempt(self,*args,**kwargs):
        result=super().analyze_attempt(*args,**kwargs)
        if result['provider']=='mistral' and result['cascade_next']:
            result['cascade_next']=False;result['route_reason']='ablation_without_sol'
        return result


def replay_rows(rows,no_sol=False):
    result=[]
    env=dict(CASCADE_LIVE_ENABLED='false',SOL_LIVE_ENABLED='false',AI_BASE_URL='',APIMIRA_API_KEY='',
             MISTRAL_REQUESTS_PER_MINUTE='0',MISTRAL_MAX_RUNNING='2',CASCADE_BUDGET_RUB='10',TOKEN_BUDGET='20000000',USER_TOKENS_PER_DAY='20000000')
    with patch.dict(os.environ,env):
        for row in rows:
            provider=RecordedProvider(row);kind=NoSecondStage if no_sol else CascadeEngine
            engine=kind(read_jsonl(ROOT/'ml/data/cascade/kb.jsonl'),SCHEMA,provenance_gate,provider)
            with tempfile.TemporaryDirectory() as folder:
                app=create_app(Path(folder)/'replay.sqlite',engine,'offline-replay',start_worker=False)
                with TestClient(app) as api:
                    response=api.post('/api/demo/tickets',json=row['input'],headers={'Authorization':'Bearer offline-replay'})
                    if response.status_code!=202:raise AssertionError('Replay request rejected')
                    job=response.json()['job_id'];queue=app.state.queue
                    for _ in range(4):
                        if queue.status(job)['state'] not in ('queued','running'):break
                        if not step(queue,engine):raise AssertionError('Offline replay made no progress')
                    status=queue.status(job)
                    value=api.get('/api/proposals/'+status['proposal_id'],headers={'Authorization':'Bearer offline-replay'}).json()
                    result.append(dict(case_id=row['case_id'],final_card=value['card'],handoff=value.get('handoff'),calls=provider.calls))
    return result


def handoff_ablation(rows):
    checks=[]
    for row in rows:
        if not row.get('handoff'):continue
        facts=row['final_card']['known_facts'];packet=row['handoff']
        checks.append(dict(case_id=row['case_id'],has_current_quote=any(row['input']['ticket_text'][:100] in f for f in facts),
                           history_messages_preserved=sum(any(m['text'][:100] in f for f in facts) for m in row['input']['history']),
                           input_history_messages=len(row['input']['history']),provider_notes=len(packet['attempts']),sources=len(packet['source_candidates'])))
    return dict(scope='Deterministic backend output enrichment; no model quality attribution',manual_cards=len(checks),
                with_enrichment=dict(current_question=sum(c['has_current_quote'] for c in checks),provider_notes=sum(c['provider_notes']>=2 for c in checks),source_candidates=sum(c['sources']>0 for c in checks)),
                without_enrichment=dict(current_question=0,provider_notes=0,source_candidates=0),
                counterfactual='Original generic fallback has no attributed input/history/provider packet; input_json remains in storage in both variants.',cases=checks)


def cache_ablation(rows):
    chosen=next((r for r in rows if r.get('final_card') and r['final_card']['mode']=='answer' and len(r['stages'])==1 and r['stages'][0]['provider']=='mistral'),None)
    if chosen is None:return dict(available=False,reason='No first-stage ordinary answer in this run')
    calls=[]
    for enabled in (False,True):
        provider=RecordedProvider(chosen)
        engine=CascadeEngine(read_jsonl(ROOT/'ml/data/cascade/kb.jsonl'),SCHEMA,provenance_gate,provider,
                             answer_cache=AnswerCache() if enabled else None)
        outputs=[engine.analyze_attempt(chosen['input'],'mistral',30,'cache-replay',str(i),cache_scope='trusted-offline-fixture') for i in range(2)]
        calls.append(dict(cache_enabled=enabled,recorded_provider_invocations=len(provider.calls),cache_hits=sum(r['cache_hit'] for r in outputs)))
    return dict(available=True,case_id=chosen['case_id'],variants=calls,new_model_calls=0,
                scope='Same validated recorded ordinary answer; measures cache path and avoided adapter invocation, not new network latency or paid savings')


def analyze(evidence,output,ablation=True):
    output.mkdir(parents=True,exist_ok=True)
    cases=read_jsonl(HERE/'fixtures/cases.jsonl');rows=read_jsonl(evidence/'results.jsonl')
    cfg=json.loads((evidence/'config.json').read_text(encoding='utf-8'))
    summary=dict(release_tag=cfg['release_tag'],runtime_commit=cfg['runtime_commit'],new_model_calls=0,
                 suites={suite:metrics(rows,[c for c in cases if c['suite']==suite]) for suite in sorted({c['suite'] for c in cases})},
                 budget=json.loads((evidence/'budget.json').read_text(encoding='utf-8')))
    save(output/'metrics.json',summary)
    if ablation:
        full=replay_rows(rows);without=replay_rows(rows,no_sol=True)
        byid={r['case_id']:r for r in rows};mismatches=[];details=[]
        for f,w in zip(full,without):
            real=byid[f['case_id']]
            fields=('mode','category','status','needs_human_review')
            if any(f['final_card'][k]!=real['final_card'][k] for k in fields):mismatches.append(f['case_id'])
            details.append(dict(case_id=f['case_id'],with_sol={k:f['final_card'][k] for k in fields},without_sol={k:w['final_card'][k] for k in fields},
                                with_sol_calls=f['calls'],without_sol_calls=w['calls']))
        sol=dict(scope='Production queue/engine replay of frozen real first-stage outputs; only second-stage availability disabled; acceptance rules not relaxed',
                 full_replay_matches_live=not mismatches,replay_mismatches=mismatches,
                 automatic_with_sol=sum(not f['final_card']['needs_human_review'] for f in full),
                 automatic_without_sol=sum(not w['final_card']['needs_human_review'] for w in without),
                 details=details,new_model_calls=0)
        gold={c['case_id']:c for c in cases};quality={}
        for suite in sorted({c['suite'] for c in cases}):
            group=[r for r in details if gold[r['case_id']]['suite']==suite]
            quality[suite]={}
            for variant in ('with_sol','without_sol'):
                quality[suite][variant]=dict(
                    mode_accuracy=fraction(sum(r[variant]['mode']==gold[r['case_id']]['expected']['mode'] for r in group),len(group)),
                    category_accuracy=fraction(sum(r[variant]['category']==gold[r['case_id']]['expected']['category'] for r in group),len(group)),
                    false_answer=fraction(sum(r[variant]['mode']=='answer' and gold[r['case_id']]['expected']['mode']!='answer' for r in group),sum(gold[r['case_id']]['expected']['mode']!='answer' for r in group)))
        sol['quality_comparison']=quality
        save(output/'ablation.json',dict(sol=sol,handoff=handoff_ablation(rows),cache=cache_ablation(rows),
             exclusions=['Prompt replacement is not estimated by fixed-output replay','New KB causal effect is not attributed without new model inputs'],new_model_calls=0))
    print(json.dumps(dict(cases=len(rows),new_model_calls=0,output_files=['metrics.json','ablation.json'] if ablation else ['metrics.json'])),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,default=HERE/'evidence/final');p.add_argument('--output',type=Path,default=HERE/'evidence/analysis');p.add_argument('--metrics-only',action='store_true')
    a=p.parse_args();analyze(a.evidence,a.output,not a.metrics_only)
