# AI Engineer: запуск и проверка LAB03

Из корня репозитория, Python 3.11+:

```sh
python -m pip install -r ml/requirements.txt
python ml/experiments/check_baseline.py
python ml/experiments/cascade/check_submission.py
python ml/experiments/cascade/replay.py
```

Первые данные относятся к историческому baseline 15.10; replay отдельно проверяет
сохранённые каскадные серии 10–12.11. Ключи не нужны, внешних генераций нет.

Локальный AI endpoint для extractive baseline:

```sh
# Задать DEMO_OPERATOR_TOKEN локально; в PowerShell: $env:DEMO_OPERATOR_TOKEN='свой-код'
uvicorn backend.routers.ai:create_app --factory --host 127.0.0.1 --port 8012
```

GET /health, POST /v1/analyze с Bearer и полями request_id, ticket_ref, ticket_text,
history, kb_version. Версии: v1, demo-20261112, support-20261112. Текст и история
валидируются; inference идёт в рабочем потоке. BASELINE_MODE только extractive
или disabled. Этот инспекционный endpoint не выполняет платных вызовов.

Каскад исполняется через CascadeEngine.analyze_attempt с reservation/snapshot,
долговечной очередью и общим deadline из интеграции Delivery. analyze() намеренно
отклоняет обход reservation. Здесь не публикуются worker, SQL, Compose, UI,
секреты или чужой deployment. Общий runtime предоставляется PR Delivery отдельно.
Платные живые вызовы не входят в команды воспроизведения.

Исходный старый evaluator/schema и golden dataset сохранены, новая схема карточки
отдельно в ml/contracts/. Итоговый отчёт: docs/experiments/lab3.md.
