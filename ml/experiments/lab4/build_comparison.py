"""Audit paired live records offline and build a review page; never generate."""
import copy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from statistics import median

from run_comparison import render, ROOT
from ml.contracts.validation import SCHEMA
from jsonschema import Draft202012Validator

HERE=Path(__file__).resolve().parent


def read(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def main():
    initial=HERE/'paired-live-20261113'
    corrected=HERE/'paired-history-correction-20261113'
    targeted=HERE/'targeted-retrieval-20261113'
    cfg=json.loads((initial/'config.json').read_text(encoding='utf-8'))
    original=read(initial/'results.jsonl')
    # Explicit fixture correction: API history entries use speaker, not role.
    # The original calls/receipts remain intact and remain in total spending.
    repairs=read(corrected/'results.jsonl')
    targeted_rows=read(targeted/'results.jsonl')
    assert len(targeted_rows)==2
    replacements={r['case_id']:r for r in repairs}
    assert set(replacements)=={'history_e42','history_invoice'}
    rows=[replacements.get(r['case_id'],r) for r in original]
    assert len(rows)==len({r['case_id'] for r in rows})==len(cfg['cases'])==16
    for r in rows:
        assert all(set(h)=={'speaker','text'} for h in r['input']['history'])
        assert r['a']==r['stages'][0] and r['b']==r['stages'][-1]
        assert len(r['stages']) in (1,2)
        if len(r['stages'])==2:
            assert r['stages'][0]['cascade_next']
            assert r['stages'][0]['provider']=='mistral' and r['stages'][1]['provider']=='sol'
        for s in r['stages']:
            assert Draft202012Validator(SCHEMA).is_valid(s['final_card'])
            assert s['request_id']==r['input']['request_id']
    # Keep the original per-series pages; produce one portable reviewed view.
    out=HERE/'comparison-20261113'
    out.mkdir(exist_ok=True)
    render(out,rows,cfg)
    summary=json.loads((out/'summary.json').read_text(encoding='utf-8'))
    paired=[r for r in rows if r['comparison_complete']]
    recovery=[r for r in paired if not r['a']['automatic_resolution'] and r['b']['automatic_resolution']]
    summary.update(
        methodology=cfg['methodology'],
        input_fixture_correction='Two initial history records used role instead of API speaker; repeated only these two with speaker. Original records excluded from primary counts but retained in spending and raw evidence.',
        live_mistral_calls_primary=sum(not r['stages'][0]['not_sent'] for r in rows),
        broad_live_calls_including_fixture_correction=sum(not s['not_sent'] for r in original+repairs for s in r['stages']),
        total_live_calls_including_targeted=sum(not s['not_sent'] for r in original+repairs+targeted_rows for s in r['stages']),
        sol_recovery_after_http_200=sum(r['a']['http_status']==200 for r in recovery),
        sol_recovery_after_transport_failure=sum(r['a']['http_status']!=200 for r in recovery),
        a_latency_median_ms=median(r['a']['latency_ms'] for r in paired),
        b_latency_median_ms=median(sum(s['latency_ms'] for s in r['stages']) for r in paired),
        raw_sol_200=sum(len(r['stages'])==2 and r['stages'][1]['http_status']==200 for r in rows),
        failed_sol_reasons={reason:sum(len(r['stages'])==2 and not r['b']['automatic_resolution'] and r['b']['gate_reason']==reason for r in rows)
                            for reason in sorted({r['b']['gate_reason'] for r in rows if len(r['stages'])==2 and not r['b']['automatic_resolution']})})
    all_stages=[s for r in original+repairs+targeted_rows for s in r['stages']]
    confirmed=sum(s['billed_micro_usd'] or 0 for s in all_stages if s['provider']=='sol')
    from ml.cascade.providers import SOL_RESERVATION_MICRO
    unknown=sum(SOL_RESERVATION_MICRO for s in all_stages if s['provider']=='sol' and s['billed_micro_usd'] is None)
    summary['spending']=dict(fx_rub_per_usd=89,confirmed_rub=str(Decimal(confirmed)/1000000*89),
        unknown_reserve_rub=str(Decimal(unknown)/1000000*89),authorized_this_experiment_rub='10',
        meaning='Confirmed provider receipts and conservative unknown reserves, including both corrected fixture calls; no reserve presented as actual charge.')
    assert Decimal(summary['spending']['confirmed_rub'])+Decimal(summary['spending']['unknown_reserve_rub'])<=10
    sources={str(p.relative_to(HERE)).replace('\\','/'):hashlib.sha256(p.read_text(encoding='utf-8').encode()).hexdigest()
             for folder in [initial,corrected,targeted] for p in folder.glob('*.json*')}
    targeted_summary=json.loads((targeted/'summary.json').read_text(encoding='utf-8'))
    trace_counts=dict(expected_generations=0,read_back_generations=0,generations_with_input_output=0)
    for folder,records in [(initial,original),(corrected,repairs),(targeted,targeted_rows)]:
        for row in records:
            trace_counts['expected_generations']+=sum(not s['not_sent'] for s in row['stages'])
            verified=folder/('langfuse-'+row['case_id']+'-verified.json')
            trace_file=verified if verified.exists() else folder/('langfuse-'+row['case_id']+'.json')
            observations=json.loads(trace_file.read_text(encoding='utf-8'))
            generations=[o for o in observations.get('data',[]) if o.get('type')=='GENERATION' and o.get('traceId')==row['trace_id']]
            trace_counts['read_back_generations']+=len(generations)
            trace_counts['generations_with_input_output']+=sum(bool(o.get('input')) and bool(o.get('output')) for o in generations)
    summary['langfuse']=trace_counts
    payload=dict(summary=summary,rows=rows,source_sha256_lf=sources,human_review='pending',
                 targeted_summary=targeted_summary,targeted_rows=targeted_rows)
    (HERE/'comparison-20261113.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    # Re-render the page with the audited summary, while retaining blank grades.
    page=(out/'cards.html').read_text(encoding='utf-8')
    page=page.replace('https://cloud.langfuse.com/trace/',
                      'https://cloud.langfuse.com/project/cmwamj1yl0198ad0j974csoi9/traces/')
    page=page.replace('<h1>','<p><a href="targeted-retrieval-20261113/cards.html">Два отдельных направленных опыта: бытовые синонимы и полнота контекста</a> · <a href="early-cards.html">Ранние ошибки и восстановления</a></p><h1>',1)
    target_page=(targeted/'cards.html').read_text(encoding='utf-8').replace('https://cloud.langfuse.com/trace/',
                      'https://cloud.langfuse.com/project/cmwamj1yl0198ad0j974csoi9/traces/')
    labels={'complete_coverage':'Ответ прошёл проверки покрытия',
            'needs_customer_data':'Нужно уточнение клиента',
            'request_id':'Модель вернула другой ID запроса',
            'assessment_input_quote':'Недословная цитата обращения в служебном поле',
            'assessment_customer_fields_without_sources':'Уточнение без подходящей процедуры',
            'provider_http_error':'Отказ API провайдера',
            'sol_incomplete_coverage':'Sol не подтвердила полноту ответа',
            'attempt_deadline_exceeded':'Модель не ответила в пределах 25 секунд',
            'semantic_requires_human':'Нужен специалист по оценке модели',
            'model_escalation':'Модель предлагает передачу специалисту'}
    def explain(value):
        value=value.replace('Оценка человека <select','Оценка итоговой карточки B <select')
        for code,label in labels.items():
            value=value.replace(' · '+code+'<pre>',' · '+label+' ('+code+')<pre>')
        return value
    page=explain(page)
    target_page=explain(target_page)
    (targeted/'cards.html').write_text(target_page,encoding='utf-8',newline='\n')
    import html
    extra=html.escape(json.dumps(summary,ensure_ascii=False,indent=2)).replace('\n','&#10;')
    page=page.replace('<button onclick="download()">','<details><summary>Аудит, расходы и коррекция формата истории</summary><pre>'+extra+'</pre></details><button onclick="download()">')
    (HERE/'comparison-cards-20261113.html').write_text(page,encoding='utf-8',newline='\n')
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
