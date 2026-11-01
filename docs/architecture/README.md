# LAB02: событийная архитектура

Product, AI Engineer и Quality LAB02 интегрированы в main. События создания обращения и сообщения клиента готовят карточку автоматически. Оператор просматривает результат и принимает решение.

- [Context](context.puml) / [SVG](context.svg)
- [Container](container.puml) / [SVG](container.svg)
- [Целевая ERD хранения](erd.puml) / [SVG](erd.svg)
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

Запуск: `docker compose -f compose.dev.yml up --build -d --wait` из корня репозитория.
UI localhost:18086, backend health localhost:18085. На пустой Triage DB применяются
обе схемы; существующий volume автоматически не обновляется. Не удалять реальные
данные: применить расширение один раз отдельной проверенной миграцией.
UI демонстрирует очередь, версию, дедупликацию, устаревший результат и подтверждение.
Стендовые кнопки событий — симуляция источника, не ручной запуск AI оператором.
Первая кнопка завершения берёт ожидающую задачу, вторая завершает её.

# Модель данных AI-предложения

Целевая ERD из интегрированного main: [PlantUML](erd.puml), [SVG](erd.svg). Это общая целевая модель хранения. Историческая database/schema.sql ещё содержит общий FK и требует миграции к этой модели. Владельцы SQL и миграций — Delivery / triage-store / knowledge.

| AI-API | Хранение и связь |
|---|---|
| ticket_ref | ticket_ref.external_ref; внутренний ticket_id связывает тикет с analysis_proposal |
| request_id | analysis_proposal.request_id, уникальная корреляция предложения |
| kb_version | analysis_proposal.kb_version и knowledge_article.version фильтруют одну версию БЗ |
| evidence.source_id / version | proposal_evidence.source_key / source_version; provenance на knowledge_article.source_key + version |
| evidence.title / excerpt | неизменяемый снимок источника в proposal_evidence |
| status / mode / known_facts / draft_reply | поля analysis_proposal; предложение не равно отправленному ответу |
| решение человека | operator_review.proposal_id → analysis_proposal; accepted/edited/rejected, action и confirmed_at отдельно |

Knowledge article имеет статус draft/approved/retired; модель видит только approved нужной версии. Между Knowledge DB и Triage DB нет общего FK: пунктир на ERD означает происхождение снимка. Внешнее действие требует решения оператора и полномочий backend. Поля warnings/usage/prompt hash относятся к проекту логирования и требуют согласования хранения с Delivery; диаграмма не выдаётся за выполненную production миграцию.
