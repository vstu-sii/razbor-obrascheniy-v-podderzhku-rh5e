# Модель данных AI-предложения

ERD заимствована без изменения из артефакта Delivery (commit 9aa47a2): [PlantUML](erd.puml), [SVG](erd.svg). Это общая модель хранения, не второй вариант схемы базы от AI Engineer. Владельцы SQL и миграций — Delivery / triage-store / knowledge.

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
