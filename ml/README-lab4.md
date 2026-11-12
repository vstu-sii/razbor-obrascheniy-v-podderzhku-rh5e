# AI Engineer LAB04: воспроизведение

Python 3.11+. Из корня LAB (в DEVELOPMENT сначала cd project):

```sh
python -m pip install -r ml/requirements-lab4.txt
python ml/experiments/lab4/checks.py
python ml/experiments/lab4/build_progress.py
python ml/experiments/lab4/run.py --output validation-output/lab4-replay
```

Эти команды не вызывают модели и не требуют ключей. Повтору нужна новая папка.
[Отчёт](../docs/experiments/lab4.md), [notebook](../docs/experiments/lab4.ipynb),
[расходы](../docs/ml-cost.md), [инструмент](tools/README.md).

При явном живом запуске --live --env-file <локальный-файл> [--ca-file <публичный-CA>]
используются локальные MISTRAL_API_KEY/APIMIRA_API_KEY и Langfuse env.
Секретов в пакете нет. По умолчанию выполнение всегда офлайн. Live ограничен
двумя Sol, без retry; общий бюджетный журнал validation-output/lab4-budget.sqlite
нельзя удалять ради обхода остатка. Разрешённая старая история включена в cap.

Каскад вызовов в product runtime по-прежнему требует single-use reservation
Delivery. Самостоятельный CascadeEngine.analyze() запрещает обход очереди.
Кэш подключается в AI service после claim; scope берётся из actor_ref в БД,
никакие значения из текста/assessment не задают права. Полные изменения
AI endpoint находятся в интеграционном PR к Delivery PR39. В teacher AI-пакете
они представлены в ml/integration/ как reference, не подменяют чужой worker.
Старые baseline-данные и LAB03 отчёт не переписываются.
