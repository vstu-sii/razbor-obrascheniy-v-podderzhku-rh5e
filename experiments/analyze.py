"""Recompute the completed design spikes offline; no provider/runtime imports."""
import csv,hashlib,json,math
from decimal import Decimal
from pathlib import Path
from jsonschema import Draft202012Validator
from lab2_checks import expanded_schema,gate,read_jsonl,retrieve
ROOT=Path(__file__).resolve().parents[1]
MODELS={'sol':'openai/gpt-5.6-sol','mistral_free':'ministral-14b-2512'}

def analysis():
 rows=[json.loads(line) for line in (ROOT/'evidence/results.jsonl').read_text(encoding='utf-8').splitlines()]
 cases={c['id']:c for c in read_jsonl('cases.jsonl')};kb=read_jsonl('kb.jsonl')
 config=json.loads((ROOT/'evidence/experiment-config.json').read_text(encoding='utf-8'))
 budget=json.loads((ROOT/'evidence/budget-summary.json').read_text(encoding='utf-8'))
 rate=Decimal(str(budget['rub_per_usd_assumption']))
 validator=Draft202012Validator(expanded_schema('AnalyzeResponse'))
 assert len(rows)==20 and {r['provider'] for r in rows}==set(MODELS)
 for r in rows:
  case=cases[r['case_id']];data=case['input']
  ids=retrieve(data['ticket_text'],data['history'],data['kb_version'],kb)
  sources=[d for sid in ids for d in kb if d['source_id']==sid and d['version']==data['kb_version'] and d['status']=='approved']
  inp={'request_id':r['request_id'],'ticket_text':data['ticket_text'],'history':data['history'],
       'kb_version':data['kb_version'],'kb_fragments':[{k:d[k] for k in ('source_id','version','title','excerpt')} for d in sources]}
  assert hashlib.sha256(json.dumps(inp,ensure_ascii=False).encode()).hexdigest()==r['input_context_sha256']
  assert ids==r['retrieved_ids']
  assert r['expected_mode']==case['expected']['mode'] and r['expected_category']==case['expected']['category']
  validator.validate(r['final_card'])
  if r['raw_card'] is not None:
   assert validator.is_valid(r['raw_card'])==r['schema_valid']
   allowed,_=gate(r['raw_card'],r['request_id'],data['kb_version'],sources)
   assert allowed==r['gate_passed']
  if not r['gate_passed']:
   assert r['final_card']['status']=='not_analyzed' and r['final_card']['draft_reply'] is None
 shared=set.intersection(*[{r['case_id'] for r in rows if r['provider']==name and r['http_status']==200} for name in MODELS])
 report={'models':{},'shared_successful_cases':sorted(shared),'valid_final_cards':20,
         'current_run_cost_upper_usd':float(Decimal(str(budget['current_selected_attempts_upper_usd']))+Decimal(str(budget['current_availability_checks_upper_usd']))),
         'prior_cost_and_setup_reserve_usd':budget['prior_cost_and_setup_reserve_usd'],'total_cost_upper_usd':budget['total_upper_usd']}
 for name,model in MODELS.items():
  group=[r for r in rows if r['provider']==name];cards=[r for r in group if r['raw_card'] is not None]
  lat=sorted(r['latency_ms'] for r in group if r['http_status']==200)
  report['models'][name]={'model':model,'n':len(group),'http_200':sum(r['http_status']==200 for r in group),
   'schema_valid':sum(r['schema_valid'] for r in group),'gate_passed':sum(r['gate_passed'] for r in group),
   'raw_cards':len(cards),'mode_matches_raw':sum(r['raw_card']['mode']==r['expected_mode'] for r in cards),
   'category_matches_raw':sum(r['raw_card']['category']==r['expected_category'] for r in cards),
   'paired_http_200_n':len(shared),'paired_schema_valid':sum(r['schema_valid'] for r in group if r['case_id'] in shared),
   'generation_p50_ms':lat[math.ceil(len(lat)*.5)-1],'generation_empirical_p95_ms':lat[math.ceil(len(lat)*.95)-1]}
 for cid in config['case_ids']:
  pair=[r for r in rows if r['case_id']==cid]
  assert len(pair)==2 and pair[0]['input_context_sha256']==pair[1]['input_context_sha256']
 assert sum(Decimal(str(r['cost_upper_usd'])) for r in rows)==Decimal(str(budget['current_selected_attempts_upper_usd']))
 assert Decimal(str(report['current_run_cost_upper_usd']))+Decimal(str(report['prior_cost_and_setup_reserve_usd']))==Decimal(str(report['total_cost_upper_usd']))<=Decimal(str(budget['original_run_cap_usd']))
 assert Decimal(str(report['total_cost_upper_usd']))*rate<=Decimal(str(budget['authorized_total_rub']))
 report['rub_per_usd_assumption']=float(rate)
 report['total_cost_upper_rub']=float(Decimal(str(report['total_cost_upper_usd']))*rate)
 for name in MODELS:
  group=[r for r in rows if r['provider']==name]
  report['models'][name]['cost_upper_rub']=float(sum(Decimal(str(r['cost_upper_usd'])) for r in group)*rate)
  confirmed=[Decimal(str(r['billed_usd'])) for r in group if r.get('billed_usd') is not None]
  report['models'][name]['confirmed_billed_rub']=float(sum(confirmed)*rate) if confirmed else None
 with (ROOT/'evidence/request-costs-rub.csv').open('w',encoding='utf-8',newline='') as out:
  writer=csv.DictWriter(out,fieldnames=['case_id','provider','billing_source','rub_per_usd','cost_upper_usd','cost_upper_rub','billed_usd','billed_rub'])
  writer.writeheader()
  for cid in config['case_ids']:
   for r in (r for r in rows if r['case_id']==cid):
    billed=r.get('billed_usd')
    writer.writerow({'case_id':cid,'provider':r['provider'],'billing_source':r['billing_source'],
     'rub_per_usd':str(rate),'cost_upper_usd':r['cost_upper_usd'],
     'cost_upper_rub':format(Decimal(str(r['cost_upper_usd']))*rate,'.6f'),
     'billed_usd':'' if billed is None else billed,
     'billed_rub':'' if billed is None else format(Decimal(str(billed))*rate,'.6f')})
 for p,record in config['frozen_input_sha256'].items():
  raw=(ROOT/p).read_bytes().replace(b'\r\n',b'\n')
  assert hashlib.sha256(raw).hexdigest()==record['git_lf_sha256']
 (ROOT/'evidence/analysis-summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
 return report
if __name__=='__main__': print(json.dumps(analysis(),indent=2))
