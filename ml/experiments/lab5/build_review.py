"""Render measured requests/cards for human review without model calls."""
import argparse
import hashlib
import html
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent


def build(evidence,destination):
    raw=(evidence/'results.jsonl').read_bytes();rows=[json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    cases={r['case_id']:r for r in (json.loads(line) for line in (HERE/'fixtures/cases.jsonl').read_text(encoding='utf-8').splitlines() if line.strip())}
    budget=json.loads((evidence/'budget.json').read_text(encoding='utf-8'));esc=lambda x:html.escape(str(x))
    digest=hashlib.sha256(raw).hexdigest();sections=[];table=[]
    for row in rows:
        key=row['case_id'];card=row['final_card'];expected=cases[key]['expected'];answer=card.get('draft_reply') or card['summary']
        route=' → '.join(stage['provider']+': '+stage['gate_reason'] for stage in row['stages'])
        observed=card['mode']+'/'+card['category'];gold=expected['mode']+'/'+expected['category']
        table.append('<tr><td><a href="#'+esc(key)+'">'+esc(key)+'</a></td><td>'+esc(row['input']['ticket_text'])+'</td><td>'+esc(gold)+'</td><td>'+esc(observed)+'</td><td>'+esc(answer)+'</td></tr>')
        history='\n'.join(('Клиент: ' if m['speaker']=='customer' else 'Поддержка: ')+m['text'] for m in row['input']['history']) or 'Нет предыдущей переписки.'
        section='<section id="'+esc(key)+'"><h2>'+esc(key)+' · '+esc(card['mode'])+'</h2><p>Группа: '+esc(row['suite'])+'. Gold: '+esc(gold)+'. Факт: '+esc(observed)+'.</p><h3>Запрос</h3><pre>'+esc(row['input']['ticket_text'])+'</pre><h3>История</h3><pre>'+esc(history)+'</pre><p>'+esc(route)+'</p>'
        if card['mode']=='escalate':
            section+='<h3>Только специалисту: draft_reply=null</h3><pre>'+esc(card['summary'])+'</pre><h3>Сведения</h3><pre>'+esc('\n'.join(card['known_facts']))+'</pre><h3>Следующий шаг</h3><pre>'+esc(card['next_step'])+'</pre>'
            for note in (row.get('handoff') or {}).get('attempts',[]):
                section+='<p><b>'+esc(note['provider'])+': '+esc(note['outcome'])+'</b></p><pre>'+esc(note['preliminary_summary'])+'</pre>'
        else:section+='<h3>Текст для клиента</h3><pre>'+esc(card['draft_reply'])+'</pre>'
        section+='<details><summary>Источники, исходные этапы и карточка</summary><pre>'+esc(json.dumps(row,ensure_ascii=False,indent=2))+'</pre></details>'
        for trace in row.get('trace_ids',[row['trace_id']]):
            section+='<p><a href="https://cloud.langfuse.com/project/cmwamj1yl0198ad0j974csoi9/traces/'+esc(trace)+'">Trace Langfuse</a></p>'
        section+='<label>Твоя оценка: <select data-id="'+esc(key)+'"><option value="pending">Не оценено</option><option value="correct">Верно</option><option value="partial">Частично</option><option value="incorrect">Неверно</option></select></label><textarea data-id="'+esc(key)+'" placeholder="Комментарий"></textarea></section>'
        sections.append(section)
    page='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LAB05: финальный полный прогон</title><style>body{font:17px system-ui;background:#edf2f8;color:#172b42;margin:24px}main{max-width:1500px;margin:auto}section{background:white;padding:24px;margin:24px 0;border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#e5ecf5;padding:16px}table{border-collapse:collapse;background:white;width:100%}th,td{padding:14px;border:1px solid #bfcddb;vertical-align:top;text-align:left;overflow-wrap:anywhere}.scroll{overflow:auto}textarea{display:block;width:95%;min-height:90px;margin-top:12px}button{padding:14px}p{line-height:1.6}</style><main><h1>Финальный прогон LAB05:44 входа</h1><p>Все показанные обращения обработаны согласованной версией. Исходные эталоны38 случаев сохранены,6 новых регрессий считаются отдельно. Совпадение с gold не заменяет человеческую проверку текста. После увеличения денежного допуска один остановленный первый этап использован из записи; его новое уточнение/решение Sol реально получено. История и исходные фазы сохранены.</p><p>Это локальный тест: внешним клиентам сообщения не отправлялись. Технический текст escalate видит специалист. Прежние6 положительных оценок не перенесены на эти новые ответы.</p><button onclick="download()">Скачать оценки JSON</button>'''
    page+='<pre>'+esc(json.dumps(budget,ensure_ascii=False,indent=2))+'</pre><div class="scroll"><table><thead><tr><th>Случай</th><th>Запрос</th><th>Gold</th><th>Факт</th><th>Ответ / специалисту</th></tr></thead><tbody>'+''.join(table)+'</tbody></table></div>'+''.join(sections)
    page+='''<script>const sourceHash='''+json.dumps(digest)+''';const key='lab5-final-review-'+sourceHash;function values(){return [...document.querySelectorAll('select[data-id]')].map(e=>({case_id:e.dataset.id,grade:e.value,comment:document.querySelector(`textarea[data-id="${e.dataset.id}"]`).value}));}try{for(const r of JSON.parse(localStorage.getItem(key)||'[]')){const s=document.querySelector(`select[data-id="${r.case_id}"]`),t=document.querySelector(`textarea[data-id="${r.case_id}"]`);if(s&&t){s.value=r.grade;t.value=r.comment;}}}catch{}document.addEventListener('input',()=>{try{localStorage.setItem(key,JSON.stringify(values()));}catch{}});function download(){const u=URL.createObjectURL(new Blob([JSON.stringify({protocol:'lab5-ai-engineer-final',reviewed_source_sha256:sourceHash,reviewed_at:new Date().toISOString(),reviews:values()},null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download='lab5-final-human-review.json';a.click();URL.revokeObjectURL(u);}</script></main></html>'''
    destination.parent.mkdir(parents=True,exist_ok=True);destination.write_text(page,encoding='utf-8')
    print(json.dumps(dict(cards=len(rows),grades='pending',new_model_calls=0)))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--evidence',type=Path,default=HERE/'evidence/final');p.add_argument('--destination',type=Path,default=HERE/'evidence/review.html');a=p.parse_args();build(a.evidence,a.destination)
