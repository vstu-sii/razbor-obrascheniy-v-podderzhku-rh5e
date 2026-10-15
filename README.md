# LAB03 / Delivery — промежуточный прототип

Снимок текущей работы от 15.10.2026. **Лабораторная ещё не завершена.**
Основание: [задание LAB03](https://sii.sergeivolchkov.ru/labs/lab3).
Доработки намеренно отложены по просьбе участника.

## Что находится в ветке

- `frontend/`, `backend/`: UI вызывает API, результаты и решения сохраняются.
- `backend/models/`, `migrations/`: SQLite, миграции 001/002, сохранённый контекст,
  отдельный review, подтверждение и очередь эскалации.
- `compose.yml`, `Dockerfile`, `.env.example`: запускаемый общий прототип.
- [Отчёт агентной разработки](docs/agent-workflow.md).
- `ml/`, `backend/contracts/`, `data/kb.jsonl`: необходимые зависимости
  текущего baseline. Это синтетический корпус, не утверждённая производственная БЗ.
- `tests/test_delivery.py`, `tests/ui.cjs`: проверки; реальные скриншоты в `frontend/screenshots/`.

## Запуск из корня этой ветки

Docker Desktop / Linux Engine. В PowerShell:

```powershell
Copy-Item .env.example .env
```

Задайте в `.env` собственный `DEMO_OPERATOR_TOKEN` — код доступа для интерфейса.
Не добавляйте `.env` в Git. Затем:

```powershell
docker compose --env-file .env -f compose.yml up --build -d --wait
```

Откройте http://localhost:8000, введите код оператора и обращение.
При занятом порте задайте PORT в `.env`, например 18084.
Проверьте источник, укажите причину, выберите решение и подтвердите его.
Для эскалации сначала выберите очередь. Решения видны после перезапуска.
Внешних отправок клиентам нет. Используйте только синтетические данные.

```powershell
docker compose --env-file .env -f compose.yml down
```

Тома сохраняются. SQL применяется один раз по версиям; версия 002 обновляет
старую БД без удаления строк. Самостоятельно удалять volume для обновления не нужно.

## Проверки

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p test_delivery.py -v
npm ci --ignore-scripts
$env:DEMO_OPERATOR_TOKEN='ваш-код-из-env'
$env:AUDIT_URL='http://127.0.0.1:8000'
npm run test:ui
```

UI-тест использует установленный Edge через Playwright. Он создаёт синтетические
карточки и reviews. Данные нужны для теста и не отправляются клиентам.

## Что ещё не выполнено

1. Публичный деплой: подтверждённого прод-URL нет.
2. Живая LLM и проверка prompts/responses в Langfuse dashboard не подтверждены.
   По умолчанию `BASELINE_MODE=extractive` — BM25 и извлечение статьи, не генеративная LLM.
   Параметры будущего подключения перечислены в `.env.example` и `ml/tracing/README.md`.
3. SQLite/JSON и файловая БЗ упрощают схему PostgreSQL из LAB02.
   Полный перенос нормализованной схемы и командное согласование остаются впереди.
4. Независимый walkthrough и итоговая приёмка не выполнены.

GitHub-ветка предназначена для просмотра текущей работы и не доказывает сдачу
всех требований. PR не создан. Прежние файлы LAB01 в ветке сохранены;
точка запуска LAB03 — `compose.yml`, а не прежний `compose.dev.yml`.
