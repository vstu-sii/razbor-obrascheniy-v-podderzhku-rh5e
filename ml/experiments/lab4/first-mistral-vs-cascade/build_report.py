"""Readable tables: exact customer input, old Mistral, current cascade, change.

No inference, fake responses, automatic human grades or teacher publication.
"""
from collections import Counter
from decimal import Decimal
import hashlib
import html
import json
from pathlib import Path
import re

HERE=Path(__file__).resolve().parent
LABEL={'answer':'Ответ','clarify':'Уточнение','escalate':'Передача специалисту'}
REASONS={'provider_http_error':'отказ API провайдера',
         'attempt_deadline_exceeded':'ответ не получен за 25 секунд',
         'provider_transport_or_timeout':'сбой соединения или ожидания ответа',
         'assessment_clarify_fields':'не согласованы поля уточнения',
         'assessment_input_quote':'неверно оформлена цитата обращения',
         'assessment_customer_fields_without_sources':'уточнение не опирается на подходящую процедуру',
         'semantic_requires_human':'для решения нужен специалист',
         'model_escalation':'модель не нашла обоснованного автоматического решения',
         'sol_incomplete_coverage':'Sol не подтвердила полноту ответа',
         'schema':'карточка не соответствует формату',
         'request_id':'модель изменила идентификатор запроса'}
ISSUES={
 'UC01-02':'Модель выбрала уточнение, но не сформировала текст вопроса. Неизвестную процедуру получения кодов записала в недостающие сведения клиента.',
 'UC01-03':'Контрольный пример: старая модель уже сообщала правильный раздел. Его нельзя считать новым достижением.',
 'UC01-04':'Вместо простой справки просила проверить роль и доступ клиента. Готового ответа нет.',
 'UC01-05':'Путь был указан, но добавлена лишняя оговорка о роли. Проверить роль для этой справки не требуется.',
 'UC01-06':'В статье прямо сказано, что старый токен повторно не показывается. Модель выбрала уточнение и оставила ответ пустым.',
 'UC01-07':'Контрольный пример: старая модель уже давала полезную инструкцию об уведомлениях.',
 'UC01-08':'Рекомендация проверить UTF-8 есть в статье, но предположение о причине ошибки статья не подтверждает.',
 'UC01-09':'Назван правильный раздел, но добавлено требование убедиться в правах администратора. Это лишняя оговорка для обычной справки.',
 'UC02-05':'Уточнить неизвестный код ошибки уместно, но модель не написала сам вопрос клиенту.',
 'UC02-07':'Проблема суммы требует разбора; вместо оформленного следующего шага получено незавершённое уточнение без текста.',
 'UC03-05':'Сообщение об утечке токена обработано как обычное уточнение. Готового ответа нет; корректная обработка инцидента не оформлена.',
 'UC03-06':'Массовая недоступность отмечена высокой срочностью, но выбран режим уточнения без текста вопроса вместо оформленной передачи.',
}
CURRENT_NOTES={
 'UC01-06':'В новом ответе есть лишняя рекомендация использовать текущий токен; статья подтверждает только невозможность повторно показать значение. Полностью безупречным этот текст не объявляется.',
 'UC01-08':'Эта ошибка НЕ исправилась: новая Mistral снова приписала E42 причину («обычно возникает из-за неверного формата»), которую статья не доказывает. Ответ прошёл код, но требует смысловой правки.',
 'UC03-06':'Сразу выбран сотрудник, но итоговая карточка слишком общая. Это правильная граница автоматизации, а не улучшение наполнения карточки.',
}


def load(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def esc(value):
    return html.escape(str(value)).replace('\n','&#10;').replace('\r','&#13;')


def list_html(values):
    return '<ul>'+''.join('<li>'+esc(v)+'</li>' for v in values)+'</ul>' if values else '<p>Не указано.</p>'


def card_html(card, *, old=False):
    if card is None:return '<p>Ответ модели отсутствует.</p>'
    text=card.get('draft_reply')
    body='<p><b>'+LABEL.get(card['mode'],esc(card['mode']))+'</b></p>'
    body+='<p><b>Текст ответа клиенту:</b></p><blockquote>'+esc(text)+'</blockquote>' if text else '<p><b>Текста ответа клиенту нет.</b></p>'
    if text is None:
        body+='<p><b>Что записано для сотрудника:</b></p><p>'+esc(card['summary'])+'</p>'
        body+='<p>Недостающие сведения:</p>'+list_html(card['missing_data'])
        body+='<p>Следующий шаг:</p><p>'+esc(card['next_step'])+'</p>'
    else:
        body+='<details><summary>Факты и недостающие сведения</summary>'+list_html(card['known_facts'])+list_html(card['missing_data'])+'</details>'
    return body


def changed(row, old):
    final=row['stages'][-1];first=row['stages'][0]
    if final['automatic_resolution']:
        start='Теперь сформирован готовый вопрос клиенту.' if final['final_card']['mode']=='clarify' else 'Теперь сформирован автоматический ответ.'
        if old['gate_passed'] and final['provider']=='mistral':
            start+=' Старый ответ тоже проходил проверки; это контроль/изменение формулировки, а не восстановление через Sol.'
        elif old['gate_passed']:
            start+=' Старый ответ тоже проходил проверки. Здесь Sol сохраняет работоспособность нового задания, а не впервые решает этот тип вопроса.'
        elif not old['gate_passed']:
            start+=' Старая карточка не проходила проверки.'
    else:
        card=final['final_card']
        start='Остаётся передача человеку.'
        if card['known_facts'] or len(card['next_step'])>150 or card['summary']!='Автоматический разбор недоступен или не прошёл проверку':
            start+=' Есть сводка и известные сведения для специалиста.'
        else:
            start+=' Итоговая карточка общая, сведений мало — улучшение содержательности не подтверждено.'
    if first.get('inference_skipped'):
        return start+' Модели не вызывались: сработало правило входного риска. Это не заслуга Sol.'
    if len(row['stages'])==1:
        return start+' Sol не вызывалась; изменения относятся к Mistral, промпту и проверкам.'
    if final['automatic_resolution']:
        return start+' Итог дала Sol. Причина перехода: '+REASONS.get(first['gate_reason'],'первый этап не прошёл проверки')+'.'
    return start+' Sol была вызвана, но автоматическое решение не получено: '+REASONS.get(final['gate_reason'],'ответ не прошёл проверки')+'.'


def main():
    src=HERE/'A-first-mistral';current=HERE/'B-current-cascade'
    cfg=json.loads((current/'config.json').read_text(encoding='utf-8'))
    provenance=json.loads((src/'SOURCE.json').read_text(encoding='utf-8'))
    for name,record in provenance['files'].items():
        assert hashlib.sha256((src/name).read_text(encoding='utf-8').encode()).hexdigest()==record['sha256_lf']
    before={r['case_id']:r for r in load(src/'responses.jsonl')}
    cases={r['id']:r for r in load(src/'cases.jsonl')}
    rows=load(current/'results.jsonl')
    assert {r['case_id'] for r in rows}=={c['id'] for c in cfg['cases']}
    display_order=['UC01-04','UC01-09','UC01-06','UC02-05','UC01-05','UC01-07',
                   'UC01-03','UC03-05','UC01-02','UC01-08','UC02-07','UC03-06']
    rows.sort(key=lambda r:display_order.index(r['case_id']))
    data=[];table=[];md=[]
    for row in rows:
        id=row['case_id'];a=before[id];original=cases[id]['input'];request=row['input']
        for field in ['ticket_text','history','kb_version']:assert request[field]==original[field]
        b=row['stages'][-1];text=changed(row,a)
        if id in CURRENT_NOTES:text+=' '+CURRENT_NOTES[id]
        role_questions=[]
        def asks_role(card):
            draft=(card or {}).get('draft_reply') or ''
            fields=' '.join((card or {}).get('missing_data',[]))
            return bool(re.search(r'(подтверд|уточн).{0,120}(администратор|владел)',draft,re.I)
                        or re.search(r'являетесь\s+ли\s+вы.{0,40}(администратор|владел)',draft,re.I)
                        or (re.search(r'администрат|владел',fields,re.I) and re.search(r'подтвер|явля|роль',fields,re.I)))
        for s in row['stages']:
            c=s.get('raw_card') or {};draft=c.get('draft_reply') or ''
            if asks_role(c):
                role_questions.append(s['provider'])
        item=dict(case_id=id,input=request,a=a,b=b,stages=row['stages'],old_issue=ISSUES[id],change=text,
                  role_confirmation_requested_by=role_questions,delivered_role_confirmation=asks_role(b['final_card']),human_quality_review='pending')
        data.append(item)
        question='<p><b>'+esc(id)+'</b></p><blockquote>'+esc(request['ticket_text'])+'</blockquote>'
        if request['history']:question+='<p>История:</p>'+list_html(h['speaker']+': '+h['text'] for h in request['history'])
        old_status='Карточка прошла старые проверки; по тогдашней политике всё равно требовала одобрения оператора.' if a['gate_passed'] else 'Карточка отклонена старыми проверками → ручной разбор.'
        old=card_html(a['raw_card'],old=True)+'<p class="status">'+old_status+'</p>'
        route=' → '.join(s['provider'].capitalize() for s in row['stages'] if not s['inference_skipped']) or 'Правило риска → сотрудник'
        new='<p><b>Маршрут: '+esc(route)+'</b></p>'
        if len(row['stages'])>1:
            first=row['stages'][0];draft=(first.get('raw_card') or {}).get('draft_reply')
            new+='<p><b>Сначала Mistral в каскаде:</b></p>'
            new+='<blockquote>'+esc(draft)+'</blockquote>' if draft else '<p>'+esc(REASONS.get(first['gate_reason'],'Первый ответ не прошёл проверки'))+'.</p>'
            new+='<p><b>После этого Sol. Итог для приложения:</b></p>'
        new+=card_html(b['final_card'])
        new+='<p class="status">'+('Автоматический следующий шаг.' if b['automatic_resolution'] else 'Карточку видит сотрудник, это не сообщение клиенту.')+'</p>'
        if id in CURRENT_NOTES:new+='<p class="problem"><b>Замечание к новому результату:</b> '+esc(CURRENT_NOTES[id])+'</p>'
        details='<details><summary>Исходные ответы всех этапов, источники и технические причины</summary><pre>'+esc(json.dumps(item,ensure_ascii=False,indent=2))+'</pre></details>'
        table.append('<tr id="'+id+'"><td>'+question+'</td><td>'+old+'</td><td>'+esc(ISSUES[id])+'</td><td>'+new+'</td><td>'+esc(text)+details+'<label>Оценка результата B <select data-id="'+id+'"><option value="pending">Не оценено</option><option value="correct">Верно</option><option value="partial">Частично</option><option value="incorrect">Неверно</option></select></label><textarea data-id="'+id+'" placeholder="Комментарий к результату и сравнению"></textarea></td></tr>')
        aq=a['raw_card'];bc=b['final_card'];old_text=aq['draft_reply'] or 'Текст ответа отсутствует. Сводка: '+aq['summary']+' Следующий шаг: '+aq['next_step']
        new_text=bc['draft_reply'] or 'Для сотрудника: '+bc['summary']+' Следующий шаг: '+bc['next_step']
        clean=lambda s:str(s).replace('|','/').replace('\n','<br>')
        md.append('| '+' | '.join(map(clean,[id,request['ticket_text'],old_text,ISSUES[id],route+': '+new_text,text]))+' |')
    counts=dict(cases=len(rows),old_gate_passed=sum(before[r['case_id']]['gate_passed'] for r in rows),
        new_automatic=sum(r['stages'][-1]['automatic_resolution'] for r in rows),
        new_human=sum(not r['stages'][-1]['automatic_resolution'] for r in rows),
        new_sol_jobs=sum(len(r['stages'])==2 for r in rows),
        new_sol_automatic=sum(len(r['stages'])==2 and r['stages'][-1]['automatic_resolution'] for r in rows),
        requested_role_case_ids=[r['case_id'] for r in data if r['role_confirmation_requested_by']],
        delivered_role_case_ids=[r['case_id'] for r in data if r['delivered_role_confirmation']],
        human_review='pending')
    sources={str(p.relative_to(HERE)).replace('\\','/'):hashlib.sha256(p.read_text(encoding='utf-8').encode()).hexdigest()
             for folder in [src,current] for p in folder.glob('*.json*')}
    result=dict(method=cfg['methodology'],baseline_commit=cfg['baseline_commit'],kb_identical=True,counts=counts,rows=data,source_sha256_lf=sources)
    (HERE/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    budget=json.loads((current/'budget.json').read_text(encoding='utf-8'))
    summary='A — реальные ответы первой Mistral от 15.10.2026 (2087c67). B — новый живой прогон текущего каскада на тех же 12 вопросах и той же базе v1. Это развитие всей системы: промпты + проверки + каскад; не чистая оценка эффекта одной Sol.'
    style='body{font:16px system-ui;color:#182b42;background:#f0f3f7;margin:22px}h1{font-size:27px}.intro{max-width:1200px}table{border-collapse:collapse;width:100%;background:white;table-layout:fixed}th,td{border:1px solid #c5d1df;padding:16px;vertical-align:top;overflow-wrap:anywhere}th{background:#dfe8f4;text-align:left;position:sticky;top:0;z-index:2}.problem{background:#fff0d9;padding:12px;border-left:3px solid #c77924}th:nth-child(1){width:14%}th:nth-child(2){width:23%}th:nth-child(3){width:14%}th:nth-child(4){width:25%}th:nth-child(5){width:24%}blockquote{margin:8px 0;padding:12px;background:#f1f5fa;border-left:3px solid #537bb1;white-space:pre-wrap}.status{font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:650px;overflow:auto;font-size:13px}textarea{display:block;width:95%;min-height:80px;margin-top:10px}label{display:block;margin-top:20px}button{padding:12px 20px;margin:14px 0}li{margin:7px 0}details{margin:15px 0}@media(max-width:1100px){table{min-width:1500px}.scroll{overflow:auto}}'
    count_text=f"Старая Mistral: {counts['old_gate_passed']} из {counts['cases']} карточек прошли тогдашние проверки. Новый каскад: {counts['new_automatic']} автоматических следующих шагов и {counts['new_human']} ручных исхода. Sol вызвана в {counts['new_sol_jobs']} случаях; в {counts['new_sol_automatic']} из них получен автоматический ответ или вопрос клиенту. Это разные показатели; содержание сравнивается построчно ниже."
    amount=format(Decimal(budget['new_confirmed_rub']),'.2f').replace('.',',')
    reserve=format(Decimal(budget['new_unknown_reserve_rub']),'.2f').replace('.',',')
    top='<!doctype html><html lang="ru"><meta charset="utf-8"><title>Было — стало: первая Mistral и текущий каскад</title><style>'+style+'</style><h1>Было — стало: первая Mistral и текущий каскад</h1><div class="intro"><p>'+summary+'</p><p>Термин «администратор аккаунта» в учебной базе означает владельца клиентского аккаунта. Для обычной справки модель не должна спрашивать роль. Это не администратор сервиса.</p><p>Старые ответы не переписаны. Не все они были плохими: контрольные успешные примеры тоже показаны. В старой версии все карточки требовали оператора; при сравнении содержания отдельно указано, проходила ли карточка проверки.</p><p><b>Результат:</b> '+esc(count_text)+'</p><p>Новые подтверждённые списания: '+esc(amount)+' ₽; неизвестный резерв: '+esc(reserve)+' ₽. Это разные величины. Допуск нового опыта — до10 ₽.</p><p>Sol в колонке B показана только там, где действительно вызывалась. Наличие второй модели само по себе не записывается в улучшения. Твои оценки ниже пока пустые.</p><button onclick="download()">Скачать мои оценки JSON</button></div><div class="scroll"><table><thead><tr><th>Обращение клиента</th><th>Было: первая Mistral</th><th>Что в старом результате не так</th><th>Стало: текущий каскад</th><th>Что изменилось и за счёт чего</th></tr></thead><tbody>'
    script='</tbody></table></div><script>function download(){let rows=[...document.querySelectorAll("select[data-id]")].map(e=>({case_id:e.dataset.id,grade:e.value,comment:document.querySelector(`textarea[data-id="${e.dataset.id}"]`).value}));let a=document.createElement("a");a.href=URL.createObjectURL(new Blob([JSON.stringify({comparison:"first-mistral-2087c67-vs-current-cascade",reviewed_at:new Date().toISOString(),reviews:rows},null,2)],{type:"application/json"}));a.download="first-mistral-vs-cascade-human-review.json";a.click();URL.revokeObjectURL(a.href)}</script></html>'
    (HERE/'report.html').write_text(top+''.join(table)+script,encoding='utf-8',newline='\n')
    mdhead='# Было — стало: первая Mistral и текущий каскад\n\n'+summary+'\n\n'+count_text+'\n\n| ID | Запрос | Было: дословный ответ/карточка | Проблема | Стало: дословный результат | Причина изменения |\n|---|---|---|---|---|---|\n'
    (HERE/'report.md').write_text(mdhead+'\n'.join(md)+'\n',encoding='utf-8',newline='\n')
    # Avoid a misleading auto-generated A label from the shared stage runner.
    (current/'cards.html').write_text('<!doctype html><meta charset="utf-8"><p>Итоговое сравнение с первой Mistral находится <a href="../report.html">в таблице «Было — стало»</a>. Здесь нет отдельного варианта A.</p>',encoding='utf-8')
    print(json.dumps(counts,ensure_ascii=False))


if __name__=='__main__':main()
