# LAB02: событийная архитектура

Product UC из PR #21 интегрированы в main: события создания/сообщения клиента
готовят карточку автоматически. Оператор только просматривает и решает.
AI PR #22 и Quality PR #24 прочитаны как зависимости, не приняты за merged state.

- [Context](context.puml) / [SVG](context.svg)
- [Container](container.puml) / [SVG](container.svg)
- [Базовая ERD](erd.puml) / [SVG](erd.svg)
- [ERD очереди](event-erd.puml) / [SVG](event-erd.svg)
- [ADR-003](../adr/ADR-003-event-analysis.md)
- [Базовая схема](../../database/schema.sql) и [расширение](../../database/002_event_queue.sql)

Конфиг aact в корне проверяет acl, acyclic, crud, dbPerService; остальные правила
не заявлены политикой. Event ingress отделён от исходящего adapter, чтобы
входящие события не создавали цикл зависимостей. Только triage-store обращается
к Triage DB; только knowledge обращается к Knowledge DB. В Compose эти процессы
являются health-заглушками: границы показаны, бизнес-вызовы ещё не реализованы.
Историческая базовая SQL-схема пока содержит knowledge_article в Triage DB;
knowledge-db в скелете пустая. Перенос БЗ и FK требует отдельной миграции.

Запуск: `docker compose -f compose.dev.yml up --build -d --wait` из project/.
UI localhost:18086, backend health localhost:18085. На пустой Triage DB применяются
обе схемы; существующий volume автоматически не обновляется. Не удалять реальные
данные: применить расширение один раз отдельной проверенной миграцией.
UI демонстрирует очередь, версию, дедупликацию, устаревший результат и подтверждение.
Стендовые кнопки событий — симуляция источника, не ручной запуск AI оператором.
Первая кнопка завершения берёт ожидающую задачу, вторая завершает её.
