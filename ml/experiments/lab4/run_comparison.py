"""Predeclared paired-stage experiment. Offline preparation is the default.

One real Mistral result is shared by A (stop after stage one) and B (cascade).
Sol is called only when the unchanged engine requests it. No retries, no cache,
no labels sent to models, no public helpdesk actions. A persistent reservation
is committed BEFORE every HTTP call; uncertain paid calls retain their reserve.
"""
import argparse
import copy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import html
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from uuid import uuid4

ROOT = next((p if (p/'ml/cascade').is_dir() else p/'project')
            for p in Path(__file__).resolve().parents
            if (p/'ml/cascade').is_dir() or (p/'project/ml/cascade').is_dir())
sys.path.insert(0, str(ROOT))
from ml.cascade.engine import CascadeEngine
from ml.cascade.context import make_context
from ml.cascade.providers import HTTPProvider, SOL_RESERVATION_MICRO, provider_tls_context
from ml.contracts.validation import SCHEMA, provenance_gate, read_jsonl

FX = Decimal(89)
PREVIOUS_MICRO = 164803
OLD_CAP_RUB = Decimal('18.407652')
PREVIOUS_TECHNICAL = 5276257
TECHNICAL_CAP = 10000000
KB_VERSION = 'support-20261112'


def cases():
    def item(id, text, modes, articles=(), history=(), note=''):
        return dict(id=id, text=text, history=list(history), expected_modes=list(modes),
                    required_sources=['KB-'+s+'-'+KB_VERSION for s in articles],
                    review_note=note)
    return [
        item('invoice', 'Где администратору скачать счёт и акт?', ['answer'], ['INVOICE'], note='Условие доступа не подтверждает роль автора.'),
        item('invoice_notify', 'Где скачать счёт и как отключить email-уведомления?', ['answer'], ['INVOICE','NOTIFY']),
        item('two_factor', 'Как включить 2FA через приложение-аутентификатор? Нужны шаги настройки.', ['answer'], ['2FA'], note='Не запрашивать QR, одноразовый или резервный код в поддержке.'),
        item('history_e42', 'Код E42. Что попробовать дальше?', ['answer'], ['ERROR'],
             [{'role':'customer','text':'Импорт CSV завершился ошибкой.'}, {'role':'operator','text':'Уточните код ошибки импорта.'}],
             'Не спрашивать код повторно и не объявлять диагностическую рекомендацию доказанной причиной.'),
        item('notifications', 'Как поменять настройки email-уведомлений?', ['answer'], ['NOTIFY']),
        item('invoice_two_factor', 'Подскажите, где скачать счёт и как по шагам настроить 2FA на телефоне.', ['answer'], ['INVOICE','2FA']),
        item('observer_role', 'В каком разделе администратор может выдать коллеге роль наблюдателя?', ['answer'], ['ROLE'], note='Справка о разделе, без обещания выполнить выдачу прав.'),
        item('three_topics', 'Нужно скачать акт, отключить email-уведомления и настроить 2FA через приложение. Как это сделать?', ['answer'], ['INVOICE','NOTIFY','2FA']),
        item('new_token', 'Где администратору создать новый токен интеграции и можно ли снова посмотреть старый?', ['answer'], ['API'], note='Не просить прислать значение токена, не обещать показать старый.'),
        item('role_notify', 'Где администратор добавляет наблюдателя и где пользователь отключает email-уведомления?', ['answer'], ['ROLE','NOTIFY']),
        item('history_invoice', 'А акт находится там же?', ['answer'], ['INVOICE'],
             [{'role':'customer','text':'Где администратору скачать счёт?'}, {'role':'operator','text':'Счёт доступен в разделе Биллинг → Документы.'}]),
        item('four_topics', 'Где скачать счёт, отключить email-уведомления, настроить 2FA через приложение и выдать роль наблюдателя?', ['answer'], ['INVOICE','NOTIFY','2FA','ROLE']),
        item('missing_error', 'Импорт завершился ошибкой, код пока не знаю. Что вам уточнить?', ['clarify'], note='Спросить диагностические сведения, не придумать E42.'),
        item('unknown_region', 'Как перенести проект в другой регион хранения данных?', ['escalate'], note='Процедуры нет в KB. Не утверждать, что перенос невозможен, и не выдумывать шаги.'),
        item('lost_phone', 'Телефон с приложением 2FA потерян, резервных кодов нет. Как восстановить доступ?', ['escalate'], ['2FA'], note='Специалист с карточкой, без обхода 2FA и запроса кодов.'),
        item('wrong_amount', 'Счёт уже скачал, но сумма в нём неправильная. Нужен разбор списания.', ['escalate'], note='Нет процедуры проверки платежа: не повторять инструкцию скачивания и не обещать возврат.'),
    ]


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')


def hashes():
    paths=[p for folder in ['ml/cascade','ml/tools','ml/prompts_v2','ml/contracts']
           for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    paths += [ROOT/'ml/data/cascade/kb.jsonl', Path(__file__)]
    return {str(p.relative_to(ROOT)).replace('\\','/') if p.is_relative_to(ROOT) else 'run_comparison.py':
            hashlib.sha256(p.read_text(encoding='utf-8').encode()).hexdigest() for p in paths}


def prepare(out, new_allowance_rub=None):
    out.mkdir(parents=True, exist_ok=False)
    cap=OLD_CAP_RUB if new_allowance_rub is None else Decimal(PREVIOUS_MICRO)/1000000*FX+new_allowance_rub
    cfg=dict(created_utc=datetime.now(timezone.utc).isoformat(), protocol='paired live stage ablation',
             methodology='A and B share the exact same first Mistral attempt; B alone may invoke Sol. Not two independent generations and not held-out accuracy.',
             cases=cases(), kb_version=KB_VERSION, models={'mistral':'ministral-14b-2512','sol':'openai/gpt-5.6-sol'},
             source_sha256_lf=hashes(), fx_rub_per_usd=89, previous_accounted_micro_usd=PREVIOUS_MICRO,
             lifetime_cap_rub=str(cap), old_lifetime_cap_rub=str(OLD_CAP_RUB),
             new_experiment_allowance_rub=str(new_allowance_rub) if new_allowance_rub is not None else None,
             previous_technical_reserve=PREVIOUS_TECHNICAL, technical_cap=TECHNICAL_CAP,
             max_attempts_per_case=2, attempt_timeout_seconds=25, retries=0, cache_enabled=False,
             seconds_between_cases=8, human_quality_review='pending',
             execution='AI engine with persistent local reservations, not a public queue/end-to-end deployment test')
    save(out/'config.json',cfg)
    print(json.dumps(dict(prepared_cases=len(cfg['cases']), lifetime_cap_rub=str(cap),new_calls=0)),flush=True)


def reserved_call(db, cfg, request, name, job, engine, context, case_id):
    reserve=SOL_RESERVATION_MICRO if name=='sol' else 0
    snapshot=make_context(engine.kb,request)
    technical=21600 if name=='sol' else min(256000,2*len(json.dumps(snapshot,ensure_ascii=False).encode())+65536)
    cap=int((Decimal(cfg['lifetime_cap_rub'])/FX*1000000).to_integral_value(rounding=ROUND_CEILING))
    db.execute('BEGIN IMMEDIATE')
    accounted=PREVIOUS_MICRO+db.execute('SELECT COALESCE(SUM(COALESCE(billed,reserve)),0) FROM attempts').fetchone()[0]
    tokens=PREVIOUS_TECHNICAL+db.execute('SELECT COALESCE(SUM(tokens),0) FROM attempts').fetchone()[0]
    if accounted+reserve>cap or tokens+technical>TECHNICAL_CAP:
        db.rollback()
        return None,dict(reason='budget_stop',provider=name,accounted_micro_usd=accounted,technical_reserved=tokens)
    attempt=str(uuid4());at=time.time()
    db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)',(attempt,name,reserve,None,None,technical));db.commit()
    stage=engine.analyze_attempt(request,name,25,job,attempt,stage_context=copy.deepcopy(context))
    stage['reservation_utc']=datetime.fromtimestamp(at,timezone.utc).isoformat()
    if stage['not_sent']:
        stage['billed_micro_usd']=0
    elif name=='mistral':
        stage['billed_micro_usd']=0
        stage['billing_basis']='participant-confirmed Free tier, no individual paid receipt'
    elif stage.get('usage'):
        import httpx
        try:
            response=httpx.get('https://apimira.com/v1/account/usage',headers={'Authorization':'Bearer '+os.environ['APIMIRA_API_KEY']},timeout=10,trust_env=False,verify=provider_tls_context())
            response.raise_for_status();u=stage['usage']
            records=[r for r in response.json()['data'] if r.get('status')=='success' and r.get('model')==stage['model']
                     and r.get('tokens_in')==u['prompt_tokens'] and r.get('tokens_out')==u['completion_tokens']
                     and abs(datetime.fromisoformat(r['created_at'].replace('Z','+00:00')).timestamp()-at)<=60]
            if len(records)==1:
                # Keep receipt fields needed for audit; no account identity or secrets.
                stage['billing_record']={k:records[0][k] for k in ['id','model','status','tokens_in','tokens_out','cost_usd','created_at'] if k in records[0]}
                stage['billed_micro_usd']=int((Decimal(str(records[0]['cost_usd']))*1000000).to_integral_value(rounding=ROUND_CEILING))
        except (httpx.HTTPError,ValueError,KeyError):
            stage['billing_reconciliation']='unavailable; conservative reserve retained'
    with db:db.execute('UPDATE attempts SET billed=?,result=? WHERE id=?',(stage['billed_micro_usd'],json.dumps(stage,ensure_ascii=False),attempt))
    print(json.dumps(dict(case=case_id,provider=name,http=stage['http_status'],reason=stage['gate_reason'],automatic=stage['automatic_resolution'])),flush=True)
    return stage,None


def render(out, rows, cfg):
    paired=[r for r in rows if r['comparison_complete']]
    summary=dict(planned=len(cfg['cases']),observed=len(rows),paired_complete=len(paired),
        mistral_http_200=sum(r['stages'][0]['http_status']==200 for r in rows),
        sol_invocations=sum(len(r['stages'])==2 and not r['stages'][1]['not_sent'] for r in rows),
        a_automatic=sum(r['a']['automatic_resolution'] for r in paired),
        b_automatic=sum(r['b']['automatic_resolution'] for r in paired),
        a_human=sum(not r['a']['automatic_resolution'] for r in paired),
        b_human=sum(not r['b']['automatic_resolution'] for r in paired),
        recovered_answer_ids=[r['case_id'] for r in paired if not r['a']['automatic_resolution'] and r['b']['automatic_resolution'] and r['b']['final_card']['mode']=='answer'],
        recovered_clarify_ids=[r['case_id'] for r in paired if not r['a']['automatic_resolution'] and r['b']['automatic_resolution'] and r['b']['final_card']['mode']=='clarify'],
        mode_matches_a=sum(r['a']['final_card']['mode'] in r['expected']['expected_modes'] for r in paired),
        mode_matches_b=sum(r['b']['final_card']['mode'] in r['expected']['expected_modes'] for r in paired),
        human_quality_review='pending; mode/schema counts are not semantic correctness')
    save(out/'summary.json',summary)
    esc=lambda x:html.escape(str(x)).replace('\n','&#10;').replace('\r','&#13;')
    cards=[]
    for r in rows:
        a,b=r['a'],r['b']
        def panel(label,s):
            c=s['final_card']
            return '<section><h3>'+label+'</h3><b>'+esc(c['mode'])+'</b> · '+esc(s['gate_reason'])+'<pre>'+esc(c.get('draft_reply') or c['summary'])+'</pre><details><summary>Полная карточка</summary><pre>'+esc(json.dumps(c,ensure_ascii=False,indent=2))+'</pre></details></section>'
        cards.append('<article><h2>'+esc(r['case_id'])+'</h2><p>'+esc(r['input']['ticket_text'])+'</p><p>'+esc(r['expected']['review_note'])+'</p><div class="pair">'+panel('A: только Mistral',a)+panel('B: каскад',b)+'</div><p>Маршрут: '+esc(' → '.join(s['provider']+': '+str(s['gate_reason']) for s in r['stages']))+'</p><p>Полное сравнение: '+esc(r['comparison_complete'])+' · <a href="https://cloud.langfuse.com/trace/'+esc(r['trace_id'])+'">Langfuse trace</a></p><details><summary>История, исходные ответы и этапы</summary><pre>'+esc(json.dumps(r,ensure_ascii=False,indent=2))+'</pre></details><label>Оценка человека <select data-grade="'+esc(r['case_id'])+'"><option value="pending">Не оценено</option><option value="correct">Верно</option><option value="partial">Частично</option><option value="incorrect">Неверно</option></select></label><textarea data-comment="'+esc(r['case_id'])+'" placeholder="Оцените правильность, полноту, необходимость человека; укажите A или B"></textarea></article>')
    head='<!doctype html><meta charset="utf-8"><title>LAB04: Mistral и каскад</title><style>body{font:17px system-ui;background:#eef2f7;margin:24px;color:#16304a}main{max-width:1300px;margin:auto}article{background:white;border-radius:12px;padding:24px;margin:20px 0}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}section{min-width:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f7;padding:16px}textarea{display:block;width:95%;min-height:70px;margin:12px 0}button{padding:12px}@media(max-width:800px){.pair{grid-template-columns:1fr}}</style><main><h1>Только Mistral и каскад на одном первом ответе</h1><p>Новые живые вызовы. A и B используют один ответ Mistral. Режим и прохождение проверок не заменяют оценку содержания человеком. Пустые оценки не считаются одобрением.</p><pre>'+esc(json.dumps(summary,ensure_ascii=False,indent=2))+'</pre><button onclick="download()">Скачать оценки JSON</button>'
    script='<script>function download(){const data={protocol:"lab4-paired-stage-20261113",reviewed_at:new Date().toISOString(),reviews:[...document.querySelectorAll("select[data-grade]")].map(e=>({case_id:e.dataset.grade,grade:e.value,comment:document.querySelector(`textarea[data-comment="${e.dataset.grade}"]`).value}))};const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json"}));a.download="lab4-paired-stage-human-review.json";a.click();URL.revokeObjectURL(a.href)}</script></main>'
    (out/'cards.html').write_text(head+''.join(cards)+script,encoding='utf-8',newline='\n')


def live(out, env_file, ca_file):
    cfg=json.loads((out/'config.json').read_text(encoding='utf-8'))
    assert cfg['source_sha256_lf']==hashes(), 'Source changed after predeclaration'
    if (out/'results.jsonl').exists() or (out/'started.json').exists():
        raise SystemExit('Do not overwrite/resume a live series; inspect its persisted reservations first')
    for line in env_file.read_text(encoding='utf-8-sig').splitlines():
        key,sep,value=line.partition('=')
        if sep and key.strip() in ('MISTRAL_API_KEY','APIMIRA_API_KEY','LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY','LANGFUSE_BASE_URL'):
            os.environ[key.strip()]=value.strip().strip('"\'')
    os.environ.update(CASCADE_CA_FILE=str(ca_file.resolve()),CASCADE_LIVE_ENABLED='true',SOL_LIVE_ENABLED='true',MISTRAL_FREE_CONFIRMED='true',TRACE_SYNTHETIC_CONTENT='true')
    provider_tls_context()
    assert all(os.environ.get(k) for k in ['MISTRAL_API_KEY','APIMIRA_API_KEY','LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY'])
    ledger=ROOT/'validation-output/lab4-budget.sqlite';ledger.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(ledger,timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY,provider TEXT,reserve INTEGER,billed INTEGER,result TEXT,tokens INTEGER)')
    start_ids={r[0] for r in db.execute('SELECT id FROM attempts')}
    save(out/'started.json',dict(utc=datetime.now(timezone.utc).isoformat(),previous_local_attempts=len(start_ids)))
    kb=read_jsonl(ROOT/'ml/data/cascade/kb.jsonl');rows=[]
    for index,case in enumerate(cfg['cases']):
        req=dict(request_id=str(uuid4()),ticket_ref='lab4-paired-'+case['id'],ticket_text=case['text'],history=case['history'],kb_version=cfg['kb_version'])
        engine=CascadeEngine(kb,SCHEMA,provenance_gate,provider=HTTPProvider())
        job=str(uuid4());trace=hashlib.sha256(job.encode()).hexdigest()[:32]
        first,stop=reserved_call(db,cfg,req,'mistral',job,engine,None,case['id'])
        if first is None:
            save(out/'stopped.json',stop);break
        stages=[first];complete=True
        if first['cascade_next']:
            second,stop=reserved_call(db,cfg,req,'sol',job,engine,first['stage_context'],case['id'])
            if second is None:
                complete=False
                save(out/('stop-'+case['id']+'.json'),stop)
            else:stages.append(second)
        row=dict(case_id=case['id'],input=req,expected={k:case[k] for k in ['expected_modes','required_sources','review_note']},
                 trace_id=trace,stages=stages,a=copy.deepcopy(first),b=copy.deepcopy(stages[-1]),comparison_complete=complete)
        with (out/'results.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        rows.append(row);render(out,rows,cfg)
        from langfuse import get_client
        get_client().flush()
        if index+1<len(cfg['cases']):time.sleep(cfg['seconds_between_cases'])
    exported=[dict(id=r[0],provider=r[1],reserve=r[2],billed=r[3],tokens=r[5],this_series=r[0] not in start_ids) for r in db.execute('SELECT * FROM attempts')]
    save(out/'ledger-export.json',exported)
    new=[r for r in exported if r['this_series']]
    paid=sum((r['billed'] or 0) for r in new if r['provider']=='sol')
    accounted=sum(r['billed'] if r['billed'] is not None else r['reserve'] for r in exported)
    save(out/'budget.json',dict(fx_rub_per_usd=89,new_confirmed_rub=str(Decimal(paid)/1000000*FX),
         new_unknown_reserve_rub=str(Decimal(sum(r['reserve'] for r in new if r['billed'] is None))/1000000*FX),
         lifetime_accounted_rub=str(Decimal(PREVIOUS_MICRO+accounted)/1000000*FX),lifetime_cap_rub=cfg['lifetime_cap_rub'],
         technical_reserved=PREVIOUS_TECHNICAL+sum(r['tokens'] for r in exported),technical_cap=TECHNICAL_CAP))
    db.close()
    # Read traces independently after all generations were flushed; failures stay visible.
    import httpx
    for row in rows:
        try:
            resp=httpx.get(os.environ['LANGFUSE_BASE_URL']+'/api/public/v2/observations',auth=(os.environ['LANGFUSE_PUBLIC_KEY'],os.environ['LANGFUSE_SECRET_KEY']),
                params=dict(fields='core,basic,io,usage',limit=100,filter=json.dumps([dict(type='string',column='traceId',operator='=',value=row['trace_id'])])),timeout=15,trust_env=False,verify=provider_tls_context())
            resp.raise_for_status();save(out/('langfuse-'+row['case_id']+'.json'),resp.json())
        except (httpx.HTTPError,ValueError):save(out/('langfuse-'+row['case_id']+'.json'),dict(verified=False))
    render(out,rows,cfg)
    print(json.dumps(json.loads((out/'summary.json').read_text(encoding='utf-8'))),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--ca-file',type=Path)
    parser.add_argument('--new-allowance-rub',type=Decimal)
    args=parser.parse_args()
    if args.live:
        if not args.env_file or not args.ca_file:parser.error('Live requires local env and trusted CA files')
        live(args.output,args.env_file,args.ca_file)
    else:prepare(args.output,args.new_allowance_rub)
