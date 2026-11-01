"""Execute a read-only evidence notebook with ordinary Python; no Jupyter/API."""
import contextlib,io,os
from pathlib import Path
import nbformat
ROOT=Path(__file__).resolve().parents[1]
sources=[
 ('# LAB02: новый прогон Sol и Mistral\n\n09.10.2026. Только результаты текущего эксперимента. '
  'Синтетические данные, черновая разметка. Ноутбук повторно вычисляет сохранённые результаты без вызова API.',None),
 (None,"from pathlib import Path\nimport sys,json\nroot=Path.cwd().resolve()\n"
  "if root.name in ('notebooks','experiments'): root=root.parent\n"
  "sys.path.insert(0,str(root/'experiments'))\nfrom analyze import analysis\nreport=analysis()\n"
  "rows=[json.loads(x) for x in (root/'evidence/results.jsonl').read_text(encoding='utf-8').splitlines()]\n"
  "config=json.loads((root/'evidence/experiment-config.json').read_text(encoding='utf-8'))\n"
  "print('Models:',{n:m['model'] for n,m in report['models'].items()})\nprint('Same max output:',config['max_output_tokens'])"),
 ('## Структура, режим и доступность\nСовпадение режима с авторской разметкой не доказывает смысловую верность. '
  'HTTP-отказ не оценивает формат или качество модели. Успешные общие входы показаны отдельно.',None),
 (None,"print('Shared successful cases:',report['shared_successful_cases'])\n"
  "for name,m in report['models'].items():\n    print(name,json.dumps(m,indent=2))"),
 ('## Retrieval\nПоиск идёт по полному корпусу approved/version, без подсказок разметки. '
  'Контрпример с настоящей цитатой показывает, что provenance недостаточен для grounding.',None),
 (None,"offline=json.loads((root/'evidence/offline-results.json').read_text(encoding='utf-8'))\n"
  "print('Offline:',json.dumps({'valid_requests':offline['valid_synthetic_requests'],'contract':offline['contract']['passed']},indent=2))\n"
  "for name,m in offline['retrieval']['methods'].items():\n"
  "    print(name,'Recall@3:',m['mean_recall_at_3'],'misses:',m['misses'])"),
 ('## Проверка маршрута и угроз\nПоказываем текущие карточки по утечке токена и инструкции выполнить внешнее действие. '
  'Ни одна функция отправки или изменения тикета не исполняется.',None),
 (None,"for r in rows:\n"
  "    if r['case_id'] in ('UC03-05','UC03-09') and r['context_mode']=='bm25':\n"
  "        print(r['provider'],r['case_id'],json.dumps(r['raw_card'],ensure_ascii=False,indent=2))\n"
  "print('Comparison scope: selected 20 attempts; availability checks are budget-only')"),
 ('## Стоимость\nРублёвые суммы пересчитаны по заданному курсу 89 ₽/$. Исторический cap $0.20 не изменён. Неизвестные списания сохраняют резерв. '
  'Цифры предыдущих прогонов не участвуют в метриках текущей модели.',None),
 (None,"print('Current upper USD:',report['current_run_cost_upper_usd'])\n"
  "print('Prior cost/setup reserve USD:',report['prior_cost_and_setup_reserve_usd'])\n"
  "print('Shared total upper USD:',report['total_cost_upper_usd'])\n"
  "print('RUB/USD:',report['rub_per_usd_assumption'])\n"
  "print('Shared total upper RUB:',report['total_cost_upper_rub'])\n"
  "print((root/'evidence/request-costs-rub.csv').read_text(encoding='utf-8'))\n"
  "assert report['total_cost_upper_usd']<=.20\n"
  "print('Independent product acceptance: pending')"),
 ('## Решение\nОбоснование и текущие ограничения описаны в docs/model-candidates.md. '
  'Автоматическая отправка ответов не разрешается: operator review обязателен.',None)]
cells=[nbformat.v4.new_markdown_cell(md) if md is not None else nbformat.v4.new_code_cell(code) for md,code in sources]
namespace={'__name__':'__main__'}; old=Path.cwd()
try:
 os.chdir(ROOT); n=0
 for cell in cells:
  if cell.cell_type!='code': continue
  n+=1; out=io.StringIO()
  with contextlib.redirect_stdout(out): exec(compile(cell.source,'<notebook-cell>','exec'),namespace)
  cell.execution_count=n; cell.outputs=[nbformat.v4.new_output('stream',name='stdout',text=out.getvalue())]
finally: os.chdir(old)
nb=nbformat.v4.new_notebook(cells=cells,metadata={'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'},
 'language_info':{'name':'python','version':'3.11'},'execution_method':'sequential Python exec; stored evidence only'})
nbformat.validate(nb); nbformat.write(nb,ROOT/'notebooks/lab2_sol_mistral.ipynb')
print('PASS: five read-only cells executed; nbformat validated')
