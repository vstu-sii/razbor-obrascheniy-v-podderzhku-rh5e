"""Show selected early real failures and recoveries; no model calls."""
import hashlib
import html
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent


def main():
    folder=HERE/'historical-early'
    def load(name):
        return [json.loads(line) for line in (folder/name).read_text(encoding='utf-8').splitlines() if line.strip()]
    def stage(row, provider):
        return next(a['diagnostics'] for a in row['attempts'] if a['provider']==provider)
    before,after=load('role-before.jsonl')[0],load('role-after.jsonl')[0]
    assert all(before['input'][k]==after['input'][k] for k in ['ticket_text','history','kb_version'])
    b,a=stage(before,'mistral'),stage(after,'mistral')
    assert 'Клиент — администратор аккаунта.' in b['raw_card']['known_facts']
    assert 'Клиент спрашивает, где администратору скачать счёт.' in a['raw_card']['known_facts']
    recovery=load('invoice-recovery.jsonl')+[r for r in load('paced-recovery.jsonl') if r['case_id'] in ['punctuation','history']]
    assert len(recovery)==3
    for r in recovery:
        m,s=stage(r,'mistral'),stage(r,'sol')
        assert not m['gate_passed'] and s['gate_passed'] and s['raw_card']['mode']=='answer'
    problems=[r for r in load('paced-recovery.jsonl') if r['case_id']=='wrong_amount']+load('notification-rejection.jsonl')
    result=dict(scope='selected historical examples, not a representative sample, not new calls',
        role_rule=dict(before=before,after=after),recoveries=recovery,unrecovered_examples=problems,
        source_sha256_lf={p.name:hashlib.sha256(p.read_text(encoding='utf-8').encode()).hexdigest() for p in folder.iterdir() if p.is_file()})
    (HERE/'early-progress.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    esc=lambda x:html.escape(str(x)).replace('\n','&#10;').replace('\r','&#13;')
    parts=['<!doctype html><meta charset="utf-8"><title>Ранние ошибки Mistral</title><style>body{font:17px system-ui;max-width:1150px;margin:30px auto;color:#16304a}article{padding:22px;border:1px solid #ccd;border-radius:12px;margin:20px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f7;padding:16px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px}@media(max-width:750px){.pair{display:block}}section{min-width:0}</style><h1>Ранние реальные ошибки и исправления</h1><p>Выбранные примеры из LAB03. Исторические проверки могли быть строже нынешних. Ошибка служебного поля не всегда означает неправильный совет. Реальные права модель не выдавала. Новых человеческих оценок нет.</p>']
    def panel(label,d):
        c=d.get('raw_card') or {}
        return '<section><h3>'+esc(label)+'</h3><p>'+esc(d['gate_reason'])+'</p><pre>'+esc(c.get('draft_reply') or c.get('summary'))+'</pre><p>known_facts:</p><pre>'+esc(json.dumps(c.get('known_facts'),ensure_ascii=False,indent=2))+'</pre><details><summary>Полный исходный этап</summary><pre>'+esc(json.dumps(d,ensure_ascii=False,indent=2))+'</pre></details></section>'
    parts.append('<article><h2>Не подтверждённая роль клиента → правило промпта</h2><p>'+esc(before['input']['ticket_text'])+'</p><div class="pair">'+panel('Mistral до правила',b)+panel('Mistral после правила',a)+'</div><p>Один повтор; не доказательство общей устойчивости. Sol здесь не нужна.</p></article>')
    for r in recovery:
        parts.append('<article><h2>'+esc(r['case_id'])+'</h2><p>'+esc(r['input']['ticket_text'])+'</p><div class="pair">'+panel('Первый ответ Mistral',stage(r,'mistral'))+panel('Второй этап Sol',stage(r,'sol'))+'</div></article>')
    for r in problems:
        parts.append('<article><h2>Сохранённый нерешённый пример: '+esc(r['case_id'])+'</h2><p><strong>Запрос клиента:</strong> '+esc(r['input']['ticket_text'])+'</p><pre>История: '+esc(json.dumps(r['input']['history'],ensure_ascii=False))+'</pre>'+panel('Mistral',stage(r,'mistral'))+'<p>Успешный второй этап в этой записи отсутствует; не приписываем Sol исправление.</p></article>')
    (HERE/'early-cards.html').write_text(''.join(parts),encoding='utf-8',newline='\n')
    print('Historical evidence: one role before/after pair, three actual Sol recoveries, two unrecovered examples')


if __name__=='__main__':main()
