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
