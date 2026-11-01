# LAB02 / Delivery — событийная архитектура

Обновлено 25.10.2026 под Product: создание обращения и новое сообщение клиента
автоматически ставят разбор в очередь; оператор просматривает и подтверждает.

- [C4, ERD и инструкция](docs/architecture/README.md)
- [ADR очереди](docs/adr/ADR-003-event-analysis.md)
- [UI: исходник](frontend/prototype/index.html), [актуальный скриншот](frontend/prototype/screen-events.png)
- [Базовая SQL-схема](database/schema.sql), [очередь и версии](database/002_event_queue.sql)
- [Compose](compose.dev.yml), [параметры](.env.example), [aact](aact.config.ts)

## Запуск

```sh
docker compose -f compose.dev.yml up --build -d --wait
```

UI http://localhost:18086, backend health http://localhost:18085/health.
Это десять health-заглушек LAB02, не runtime LAB03. Начальные SQL выполняются
на пустом volume. Старые volumes автоматически не обновляются; не удаляйте
данные, для обновления нужна отдельная проверенная миграция.

В UI кнопки событий моделируют внешний источник. «Открыть карточку» не запускает
AI. Первая кнопка завершения переводит queued→running, вторая running→ready.
Новое сообщение делает старую карточку устаревшей. Повтор доставки и заметка
не создают разбор. Подтверждение устаревшей версии блокируется.
Симуляция в памяти страницы; реальные webhook, worker и квоты не реализованы.

## Проверки

```sh
npm ci --ignore-scripts
npm run architecture:check
```

Установленный Edge используется для `npm run ui:test` при работающем Compose.
SQL regression:

```powershell
Get-Content tests/database.sql -Raw | docker compose -f compose.dev.yml exec -T db psql -U triage -d triage -v ON_ERROR_STOP=1
```

Проверены aact (четыре правила, 0 нарушений), рендер четырёх диаграмм,
10 healthy сервисов, SQL и browser-симуляция событий. Это не доказательство
реальной доставки/квот/lease или готовой модели. AI-контракт и Quality coverage
остаются зависимостями командного review. Физическое разделение Knowledge DB
ещё требует миграции; пока базовая knowledge_article находится в Triage DB.

Старые скриншоты screen*.png, кроме screen-events.png, относятся к предыдущему
ручному прототипу. Актуальная демонстрация — screen-events.png. Старый lab2 docker/
сохранён как история, запускать следует корневой compose.dev.yml.
LAB03-ветка не менялась. PR и merge при этом обновлении не создаются.

## Материалы LAB01

Помощник оператору первой линии русскоязычного B2B SaaS. В этой версии работает
каркас FastAPI с безопасной заглушкой: модель и поиск по БЗ не подключены,
ответ клиенту автоматически не отправляется. Заглушка возвращает
`status=not_analyzed`, `mode=escalate`, `urgency=unassessed`,
`needs_human_review=true`, пустые источники и `draft_reply=null`.

## Запуск из корня репозитория

Требуется Docker Compose. Создание `.env` необязательно: в Compose есть
безопасные значения по умолчанию.

```powershell
docker compose -f compose.lab01.yml up --build
```

После запуска ожидаются `GET http://localhost:8000/`,
`GET http://localhost:8000/health` и `POST http://localhost:8000/triage`.

```powershell
Invoke-RestMethod -Method Post -Uri 'http://localhost:8000/triage' -ContentType 'application/json' -Body '{"message":"Не могу войти"}'
```

## Реализация — Delivery

- `app/main.py` — HTTP API и явная передача обращения оператору.
- `tests/test_api.py` — проверки hello-world, health и безопасного ответа заглушки.
- `Dockerfile`, `requirements.txt`, `compose.lab01.yml`, `.env.example` — сборка и окружение.
- `.github/workflows/ci.yml` — lint, формат, pytest, проверка зависимостей и Docker build на PR; результат CI нужно смотреть на текущем коммите.
- [docs/deploy.md](docs/deploy.md) — условия проверки публичной версии.
- [data/sample/golden.jsonl](data/sample/golden.jsonl) — 10 синтетических черновиков Quality & Safety. Это примеры формата, а не утверждённый набор для измерения качества.

## Публичный адрес и ограничения

Исторический адрес: https://lab1-sii-zxc.masikpupuna.chatgpt.site.
Его доступность и соответствие текущему коду не подтверждены.
Публичный deploy и полный запуск Compose должны быть проверены отдельно.
Каркас не определяет категорию, не оценивает срочность, не создаёт ответ
по БЗ и не выполняет действий в аккаунте клиента.

## Качество и безопасность — Quality & Safety

- [Критерии успеха](docs/quality/success-criteria.md): метрики, предложенные пороги и правила измерения.
- [План golden dataset](docs/quality/golden-dataset-plan.md): источники, покрытие, независимая разметка и разделение набора.
- [Черновик DoD](docs/quality/dod-draft.md): условия готовности артефактов команды.
- [Первые 10 примеров](data/sample/golden.jsonl): существующий файл с синтетическими черновиками; набором владеет Quality & Safety.

Примеры ожидают независимой проверки; качество модели на них не измерялось.
Условия DoD и пороги остаются предложениями до review команды.
