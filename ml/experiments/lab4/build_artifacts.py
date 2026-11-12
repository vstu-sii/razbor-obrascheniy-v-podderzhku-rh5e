"""Build portable LAB04 notebook and result tables from immutable saved evidence."""
from pathlib import Path
import csv
import html
import json

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def main():
    import nbformat as nb
    notebook = nb.v4.new_notebook()
    notebook.metadata.kernelspec = dict(name='python3',display_name='Python 3',language='python')
    notebook.cells = [nb.v4.new_markdown_cell('''# LAB04: каскад, поиск и стоимость

Сохранённые реальные A/B-ответы и отдельно контролируемый replay. Этот notebook
не вызывает модели. Набор диагностический, после правок; независимая accuracy
и причинный эффект изменения общего числа output-токенов не заявляются.'''),
        nb.v4.new_code_cell('''from pathlib import Path
import json
root = next(candidate for p in [Path.cwd(), *Path.cwd().parents] for candidate in [p,p/'project'] if (candidate/'ml/experiments/lab4').exists())
data = root/'ml/experiments/lab4'
offline = json.loads((data/'offline-final-20261112/offline.json').read_text(encoding='utf-8'))
print('Replay:', offline['mode_matches'], '/', offline['cases'], '; external calls:', offline['external_model_calls'])
assert offline['mode_matches'] == offline['cases'] == 13
print('Historical prompt A/B:', offline['historical_ab'])'''),
        nb.v4.new_code_cell('''positive = [r for r in offline['retrieval'] if r['required_total']]
assert len(positive) == 9 and all(r['all_required_found'] for r in positive)
print('Required-source coverage:', len(positive), '/', len(positive))
for r in offline['retrieval']: print(r['case_id'], r['required'], r['retrieved'])
assert all(r['identical_decoded_context'] for r in offline['lossless_context'])
print('Context JSON decoded without loss; all articles retained.')
assert len(offline['cache_replay']) == 7
print('Cache: seven controlled repeated answer requests; 14 -> 7 generations in replay.')'''),
        nb.v4.new_code_cell('''live_dir = data/'live-context-verified-20261112'
live = json.loads((live_dir/'results.json').read_text(encoding='utf-8'))
assert len(live) == 2
for row in live:
    stage = row['stages'][-1]
    assert stage['provider'] == 'mistral' and stage['automatic_resolution']
    assert stage['final_card']['mode'] == 'answer'
    assert row['cache_repeat'] == {'cache_hit': True, 'attempts': 0}
    print(row['variant'], stage['usage'], stage['latency_ms'], 'ms')
    print(stage['final_card']['draft_reply'])
    trace = json.loads((live_dir/('langfuse-'+row['variant']+'-verified.json')).read_text(encoding='utf-8'))
    observations = [o for o in trace['data'] if o['type']=='GENERATION' and o['traceId']==row['trace_id']]
    assert len(observations)==1 and observations[0]['input'] and observations[0]['output']
print('Real Langfuse generations verified: 2. Cache hits are not generations.')
budget = json.loads((live_dir/'budget.json').read_text(encoding='utf-8'))
assert budget['new_accounted_micro_usd']==0
print('New Sol calls: 0. FX:', budget['fx'], '; prior accounted RUB:', budget['lifetime_accounted_rub'])'''),
        nb.v4.new_code_cell('''import sys
sys.path.insert(0,str(root))
from ml.contracts.validation import SCHEMA, provenance_gate
from jsonschema import Draft202012Validator
kb = [json.loads(line) for line in (root/'ml/data/cascade/kb.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
for row in live:
    for stage in row['stages']:
        assert Draft202012Validator(SCHEMA).is_valid(stage['final_card'])
        assert provenance_gate(stage['raw_card'],row['input']['request_id'],row['input']['kb_version'],stage['stage_context']['sources'])[0]
print('Both live raw/final cards satisfy the current cascade contract.')
from ml.cascade.engine import CascadeEngine
import copy
for row in live:
    original=row['stages'][0]
    def recorded(*args, original=original):
        return dict(card=copy.deepcopy(original['raw_card']),assessment=copy.deepcopy(original['assessment']),usage=original['usage'],http_status=200)
    replay=CascadeEngine(kb,SCHEMA,provenance_gate,provider=recorded).analyze_attempt(row['input'],'mistral',25,'current-replay','current-replay')
    assert replay['automatic_resolution'], replay['gate_reason']
print('Both new outputs also pass the final current engine; no external calls.')''')]
    notebook.cells.extend([
        nb.v4.new_markdown_cell("## История исправлений и вклад резервной модели\nНе новый независимый A/B: старые серии разработки и отключение этапа Sol на сохранённых первых ответах."),
        nb.v4.new_code_cell("""progress = json.loads((data/'progress-20261112.json').read_text(encoding='utf-8'))
import hashlib
for path, expected in progress['source_sha256_lf'].items():
    assert hashlib.sha256((data/path).read_text(encoding='utf-8').encode()).hexdigest() == expected
assert progress['ordinary_v2'] == dict(n=8,mode_matches=6,answer=5,clarify=2,escalate=1)
assert progress['ordinary_v3'] == dict(n=8,mode_matches=8,answer=7,clarify=1,escalate=0)
print('Итерация системы v2 → v3:',progress['ordinary_v2'],progress['ordinary_v3'])
for item in progress['prompt_examples']:
    print(item['case_id'],item['input']['ticket_text'])
    for side in ['before','after']:
        print(side, [(s['provider'], (s.get('raw_card') or {}).get('draft_reply')) for s in item[side]['stages']])"""),
        nb.v4.new_code_cell("""a=progress['ablation']
assert (a['cases'],a['mistral_only_automatic'],a['cascade_automatic'],a['cascade_human']) == (3,0,2,1)
print('Абляция тех же первых этапов:',a)
for item in progress['recovery']:
    print(item['case_id'],item['mistral_reason'],'→',item['cascade_mode'])
human = next(r['final_card'] for r in progress['recovery'] if not r['cascade_automatic'])
assert human['summary'] and human['known_facts'] and human['evidence'] and human['next_step']
assert human['draft_reply'] is None and human['needs_human_review'] is True
print('Специалист получает содержательную карточку:',human['summary'],human['known_facts'],human['next_step'])"""),
        nb.v4.new_code_cell("""delivery=json.loads((data/'delivery-d021/live-smoke.json').read_text(encoding='utf-8'))
assert delivery['passed'] and len(delivery['cases'])==2
ordinary,manual=delivery['cases']
assert ordinary['card_status']=='resolved' and not ordinary['needs_human_review']
assert [s['provider'] for s in manual['attempts']]==['mistral','sol']
assert manual['attempts'][1]['diagnostics']['http_status']==200 and manual['needs_human_review']
persist=json.loads((data/'delivery-d021/persistence-smoke.json').read_text(encoding='utf-8'))
assert persist['passed'] and all(c['card_preserved'] for c in persist['cases'])
print('Delivery LAB05: последующая интеграционная проверка, не наш A/B. Обе карточки пережили restart.')""")])
    notebook.cells.extend([
        nb.v4.new_markdown_cell('''## Дополнение 13.11: ранние ошибки и новый парный опыт
Один и тот же первый живой ответ Mistral используется в A и B; Sol вызывается
только по реальному решению текущего движка. Это парная абляция этапа, не две
независимые генерации. Исправленные входы истории и их расходы сохранены.
Режим/схема не заменяют независимую оценку содержания.'''),
        nb.v4.new_code_cell('''early=json.loads((data/'early-progress.json').read_text(encoding='utf-8'))
for name,expected_hash in early['source_sha256_lf'].items():
    assert hashlib.sha256((data/'historical-early'/name).read_text(encoding='utf-8').encode()).hexdigest()==expected_hash
assert len(early['recoveries'])==3 and len(early['unrecovered_examples'])==2
print('Ранние реальные восстановления Sol:')
for row in early['recoveries']:
    print(row['case_id'],[(a['provider'],a['diagnostics']['gate_reason']) for a in row['attempts']])
print('Выбранные примеры, не оценка общей частоты ошибок.')'''),
        nb.v4.new_code_cell('''comparison=json.loads((data/'comparison-20261113.json').read_text(encoding='utf-8'))
for name,expected_hash in comparison['source_sha256_lf'].items():
    assert hashlib.sha256((data/name).read_text(encoding='utf-8').encode()).hexdigest()==expected_hash
assert len(comparison['rows'])==16
for row in comparison['rows']:
    assert row['a']==row['stages'][0] and row['b']==row['stages'][-1]
    assert all(set(h)=={'speaker','text'} for h in row['input']['history'])
    for stage in row['stages']: assert Draft202012Validator(SCHEMA).is_valid(stage['final_card'])
print(json.dumps(comparison['summary'],ensure_ascii=False,indent=2))
print('Оценка человека:',comparison['human_review'])
from decimal import Decimal
spending=comparison['summary']['spending']
assert Decimal(spending['confirmed_rub'])+Decimal(spending['unknown_reserve_rub'])<=10
print('Стоимость включает первоначальные записи с исправленным затем полем истории; резерв не равен списанию.')''')])
    # Project keeps the same portable AI contract/data aliases as publication.
    target=ROOT/'docs/experiments/lab4.ipynb';target.parent.mkdir(parents=True,exist_ok=True)
    nb.write(notebook,target)
    live=json.loads((HERE/'live-context-verified-20261112/results.json').read_text(encoding='utf-8'))
    rows=[]
    for row in live:
        for stage in row['stages']:
            rows.append(dict(variant=row['variant'],provider=stage['provider'],mode=stage['final_card']['mode'],
                input_tokens=stage['usage']['prompt_tokens'],output_tokens=stage['usage']['completion_tokens'],
                latency_ms=stage['latency_ms'],paid_sol_rub='0',cache_repeat=row['cache_repeat']['cache_hit']))
    with (HERE/'live-context-verified-20261112/requests.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=rows[0].keys());writer.writeheader();writer.writerows(rows)
    cards=['<!doctype html><meta charset="utf-8"><title>LAB04: реальные карточки A/B</title><style>body{font:18px system-ui;max-width:1000px;margin:40px auto}article{padding:24px;border:1px solid #ccc;margin-bottom:25px}pre{white-space:pre-wrap}</style><h1>LAB04: живой A/B контекста</h1><p>Синтетический сценарий. Новая человеческая оценка не выставлена.</p>']
    for row in live:
        cards.append('<article><h2>'+html.escape(row['variant'])+'</h2><p>'+html.escape(row['input']['ticket_text'])+'</p><pre>'+html.escape(row['final_card']['draft_reply'] or row['final_card']['summary'])+'</pre><details><summary>Полная карточка</summary><pre>'+html.escape(json.dumps(row['final_card'],ensure_ascii=False,indent=2))+'</pre></details></article>')
    (HERE/'live-context-verified-20261112/cards.html').write_text('\n'.join(cards),encoding='utf-8')


if __name__=='__main__':main()
