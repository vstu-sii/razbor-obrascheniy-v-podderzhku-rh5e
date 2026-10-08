# Разбор обращений в поддержку — LAB02 / Delivery

Сдача четырёх результатов Delivery: C4 и архитектурные ADR, UI-прототип с
реальными скриншотами, ERD и SQL, Compose-скелет будущей системы.
Основание: [официальная LAB02](https://sii.sergeivolchkov.ru/labs/lab2)
и предоставленный файл задания. Подготовлено 08.10.2026.

## Файлы для проверки

| Результат | Файлы |
|---|---|
| C4 context/container и ERD | [Архитектура: PlantUML + SVG](docs/architecture/README.md) |
| Правила aact | [aact.config.ts](aact.config.ts) |
| Архитектурные решения с альтернативами и ценой | [ADR-001](docs/adr/ADR-001-boundaries.md), [ADR-002](docs/adr/ADR-002-human-approval.md) |
| UI: код, четыре сценария, пять скриншотов | [Описание прототипа](frontend/prototype/README.md) |
| Начальные SQL-миграции 001 | [Triage schema](database/schema.sql), [Knowledge schema](database/knowledge-schema.sql) |
| Сервисы, сети, volumes и health checks | [compose.dev.yml](compose.dev.yml) |
| Временный интерфейс AI-заглушки | [Описание](docs/architecture/ai-stub-boundary.md) |

## Запуск из корня репозитория

Требуется работающий Docker Engine с Linux containers.

```sh
docker compose -f compose.dev.yml up --build -d --wait
```

UI: http://127.0.0.1:8080/; backend health: http://127.0.0.1:8000/health.
Публикуются только эти локальные порты. Внутренние AI и БД не имеют host ports.
При занятых портах задайте BACKEND_PORT и UI_PORT в окружении.
Учебные значения из [.env.example](.env.example) уже имеют defaults;
создавать .env не обязательно.

```sh
python tests/smoke.py
python tests/backend.py
docker compose -f compose.dev.yml down
```

`down` сохраняет данные в volumes. SQL выполняется автоматически только при
первом старте с пустыми volumes. Дальнейшие изменения схемы потребуют новых
миграций, а не перезаписи существующих данных.

## Проверка архитектуры и UI

Требуется Node.js 22+; Python 3.12+ для Python-тестов.

```sh
npm ci --ignore-scripts
npm run architecture:check
npm run architecture:test
npx playwright install chromium
npm run ui:test
```

Для установленного Edge в PowerShell задайте `$env:PLAYWRIGHT_CHANNEL='msedge'`
вместо загрузки Chromium. UI-тест сам запускает локальный HTTP-сервер, закрывает
его и обновляет пять скриншотов рядом с кодом. Отчёты создаются в игнорируемой
папке `test-results/`.

Рендер диаграмм из корня, PowerShell:

```powershell
$labPath = (Get-Location).Path
docker run --rm --network none --mount "type=bind,source=$labPath,target=/work" -w /work/docs/architecture plantuml/plantuml:1.2026.8 -charset UTF-8 -tsvg -failfast2 context.puml container.puml erd.puml
```

## Результаты проверки и границы готовности

На 08.10.2026 проверены: рендер трёх диаграмм; aact — 0 нарушений по четырём
включённым правилам; четыре отрицательных архитектурных контроля; UI UC-01–04,
правка, подтверждение/отмена и отклонение; мобильная ширина 390 px; запуск
девяти healthy сервисов; обе начальные миграции; ограничения SQL; проверка
входа API; отказ AI с 503/manual_review и восстановление. Два регрессионных
теста проверяют JSON и HTML при ошибке AI.

Это учебный проект системы и работающий каркас. UI использует синтетические
сценарии и журнал в памяти страницы. Реальная модель, отправка клиенту,
авторизация, CRUD сервисов и постоянный UI audit ещё не реализованы.
Окончательный AI-API и уточнение архитектуры ожидают командного согласования;
статусы ADR указаны явно. Полная командная приёмка не заявляется.

Публикация выполнена в указанную участником ветку `lab2-delivery-initiation`.
Формат курса предусматривает PR роли и имя ветки `lab2-delivery-design`;
текущая ветка содержит материалы для проверки, PR отдельно не создан.

Папка `lab2 docker/` сохранена от прежнего состояния ветки. Её старый Compose
не является точкой запуска этой сдачи: используйте корневой `compose.dev.yml`.
