"""Build an auditable development history; no model calls or invented human grades."""
from collections import Counter
import hashlib
import html
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent


def load(series):
    p=HERE/'historical-cascade'/series/'results.jsonl'
    return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]


def stage(row,provider):
    return next(a['diagnostics'] for a in row['attempts'] if a['provider']==provider)


def ordinary(rows):
    rows=[r for r in rows if r['expected']['group']=='ordinary']
    modes=Counter(stage(r,r['attempts'][-1]['provider'])['final_card']['mode'] for r in rows)
    return dict(n=len(rows),mode_matches=sum(stage(r,r['attempts'][-1]['provider'])['final_card']['mode'] in r['expected']['expected_modes'] for r in rows),
                answer=modes['answer'],clarify=modes['clarify'],escalate=modes['escalate'])


def main():
    v2=load('ordinary-v2-live-20261110')
    v3=load('ordinary-v3-followup-20261110')+load('ordinary-v3-remaining-20261110')
    first=load('prompt-followup-live-20261112')
    support=load('support-policy-live-20261112')
    examples=[]
    for case_id in ['history','two_questions']:
        before=next(r for r in v2 if r['case_id']==case_id)
        after=next(r for r in v3 if r['case_id']==case_id)
        for key in ['ticket_text','history','kb_version']:
            assert before['input'][key]==after['input'][key]
        examples.append(dict(case_id=case_id,input=before['input'],
            before=dict(series='ordinary-v2-live-20261110',job=before['job'],stages=[a['diagnostics'] for a in before['attempts']]),
            after=dict(series='ordinary-v3-followup-20261110' if case_id in ['history','two_questions'] else 'ordinary-v3-remaining-20261110',job=after['job'],stages=[a['diagnostics'] for a in after['attempts']])))
    recovery=[]
    for row in support:
        m=stage(row,'mistral');s=stage(row,'sol')
        # Same recorded first-stage output. This is an ablation, not a second live run.
        recovery.append(dict(case_id=row['case_id'],input=row['input'],
            mistral_reason=m['gate_reason'],mistral_http=m['http_status'],
            mistral_only_automatic=m['automatic_resolution'],
            cascade_automatic=s['automatic_resolution'],cascade_mode=s['final_card']['mode'],
            final_card=s['final_card'],raw_sol=s['raw_card'],cost_micro_usd=row['attempts'][-1]['billed_micro_usd']))
    ablation=dict(method='Disable stage two on the SAME saved first-stage outcomes; not a new live A/B or independent quality score',
        cases=len(recovery),mistral_only_automatic=sum(r['mistral_only_automatic'] for r in recovery),
        cascade_automatic=sum(r['cascade_automatic'] for r in recovery),
        mistral_only_human=sum(not r['mistral_only_automatic'] for r in recovery),
        cascade_human=sum(not r['cascade_automatic'] for r in recovery))
    followup=[]
    for row in first:
        after=next(r for r in support if r['case_id']==row['case_id'])
        assert row['input']['ticket_text']==after['input']['ticket_text']
        followup.append(dict(case_id=row['case_id'],before_version=row['input']['kb_version'],after_version=after['input']['kb_version'],
            before_reason=stage(row,'sol')['gate_reason'],after_reason=stage(after,'sol')['gate_reason'],
            before_final=stage(row,'sol')['final_card'],after_final=stage(after,'sol')['final_card'],
            before_raw_sol=stage(row,'sol')['raw_card'],after_raw_sol=stage(after,'sol')['raw_card']))
    sources={str(p.relative_to(HERE)).replace('\\','/'):hashlib.sha256(p.read_text(encoding='utf-8').encode()).hexdigest()
             for p in (HERE/'historical-cascade').glob('*/results.jsonl')}
    result=dict(scope='development_history_and_saved_stage_ablation; external_model_calls=0',
        ordinary_v2=ordinary(v2),ordinary_v3=ordinary(v3),prompt_examples=examples,
        recovery=recovery,ablation=ablation,followup=followup,source_sha256_lf=sources)
    assert result['ordinary_v2']==dict(n=8,mode_matches=6,answer=5,clarify=2,escalate=1)
    assert result['ordinary_v3']==dict(n=8,mode_matches=8,answer=7,clarify=1,escalate=0)
    assert ablation['mistral_only_automatic']==0 and ablation['cascade_automatic']==2
    (HERE/'progress-20261112.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    parts=['<!doctype html><meta charset="utf-8"><title>LAB04: ошибки и исправления</title><style>body{font:17px system-ui;max-width:1100px;margin:32px auto;color:#182231}article{border:1px solid #ccd3dc;border-radius:12px;padding:22px;margin:20px 0}pre{white-space:pre-wrap;background:#f3f5f8;padding:15px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}@media(max-width:700px){.pair{display:block}}</style><h1>LAB04: реальные ошибки, правки и результат</h1><p>История разработки и абляция сохранённых этапов. Новых вызовов нет. Оценки человека не добавлены.</p>']
    parts.append('<p><a href="early-cards.html">Больше ранних ошибок и реальных восстановлений Sol</a> · <a href="comparison-cards-20261113.html">Новое парное сравнение на текущей версии</a></p>')
    esc=lambda value:html.escape(str(value)).replace('\n','&#10;').replace('\r','&#13;')
    for item in examples:
        parts.append('<article><h2>'+esc(item['case_id'])+'</h2><pre>'+esc(item['input']['ticket_text']+'\nИстория: '+json.dumps(item['input']['history'],ensure_ascii=False))+'</pre><div class="pair">')
        for side,label in [('before','До правки'),('after','После правки')]:
            parts.append('<section><h3>'+label+'</h3><p>'+esc(item[side]['job']['state'])+'</p>')
            for d in item[side]['stages']:
                c=d.get('raw_card') or {}
                parts.append('<h4>'+esc(d['provider'])+' · '+esc(d['gate_reason'])+'</h4><pre>'+esc(c.get('draft_reply') or c.get('summary') or 'Нет ответа')+'</pre>')
            parts.append('</section>')
        parts.append('</div></article>')
    for item in recovery:
        c=item['final_card']
        parts.append('<article><h2>Резервный этап: '+esc(item['case_id'])+'</h2><pre>'+esc(item['input']['ticket_text'])+'</pre><p>Mistral: '+esc(item['mistral_reason'])+' → Sol: '+esc(item['cascade_mode'])+'</p><pre>'+esc(c['draft_reply'] or c['summary'])+'</pre><details><summary>Карточка для приложения/сотрудника</summary><pre>'+esc(json.dumps(c,ensure_ascii=False,indent=2))+'</pre></details></article>')
    parts.append('<p>E42: после правки лишний вопрос устранён, но утверждение о точной причине ошибки остаётся чрезмерно категоричным. Совпадение режима не равно безошибочному содержанию.</p>')
    (HERE/'progress-cards-20261112.html').write_text('\n'.join(parts),encoding='utf-8')
    print(json.dumps(dict(ordinary_v2=result['ordinary_v2'],ordinary_v3=result['ordinary_v3'],ablation=ablation),ensure_ascii=False))


if __name__=='__main__':main()
