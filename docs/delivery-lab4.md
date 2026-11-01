# LAB04: Delivery, материалы для проверки

Роль отвечает за воспроизводимый запуск, выпуск, staging, мониторинг и бюджетные
ограничения. Приложение и тестовые fixtures других ролей включены как необходимые
зависимости, их ролевые отчёты не выдаются за работу Delivery.

| Требование | Где находится | Как проверено |
|---|---|---|
| Тесты → сборка → выпуск после merge main | .github/workflows/deploy.yml | Успешный автоматический public release |
| Откат одной командой | ops/release.py, docs/environments.md | Compatible SHA rollback и возврат, те же карточка/review |
| Staging из того же compose | compose.yml, compose.staging.yml | 11 healthy сервисов, одинаковые миграции и образ |
| Метрики, логи, трассы, дашборд | monitoring/ | Prometheus, Loki, Tempo, Grafana, Alloy; 8 panels |
| Лимиты на пользователя/сутки | backend/limits/ | PostgreSQL/SQLite, конкуренция, restart, UTC |
| Деградация и границы полномочий | backend/guards/, docs/adr/ADR-004-production-guardrails.md | Одноразовый claim, отсутствие обхода, human confirmation |
| Алерт аномального расхода | monitoring/alerts.yml | Реальный alert в локальном Alertmanager |

Публичный прототип: https://siiski-volchkova-shared-repository-ic7x.onrender.com/.
Точная проверенная версия приложения и результаты публичного создания карточки,
review, отката и возврата: [deployment-lab04.json](deployment-lab04.json).
Позднейший выпуск main 11e5400 также проверен точным /health; он добавляет
итоговую документацию, runtime идентичен базовой проверке 600c194.
Код доступа оператору передаётся отдельно; в Git его нет.

Запуск из корня: `docker compose up -d --build --wait`.
Staging: `docker compose -p lab04-staging -f compose.yml -f compose.staging.yml up -d --build --wait`.
Grafana: http://127.0.0.1:18301/d/lab04-triage. Вход и пароль из volume описаны
в [environments.md](environments.md). Мониторинг оставлен локальным Docker по
выбору участника; при выключенном компьютере он недоступен. Render Free может
засыпать. Постоянный внешний Grafana/SLA не заявляется.

Проверки: 37 тестов приложения, 11 проверок квот на PostgreSQL, 6 контрактов,
14 EVT SQLite/PostgreSQL и 16 атак. Replay: 49 записей / 38 независимых входов,
mode 35/38, category 36/38, false answer 0/28, прежние пороги сохранены.
Это повторная обработка сохранённых ответов, не новые платные вызовы LLM.
[Команды и происхождение проверок](../tests/README.md).

Рабочий автодеплой живёт в DEVELOPMENT. Ветка преподавателя самодостаточна для
сборки и тестов; секреты хостинга сюда не переносились. Для своего deploy job
нужны отдельные настройки production и Render, подключённый к этому репозиторию.
После merge main без такой конфигурации deploy завершается безопасным отказом.
На PR этот job не выполняется; все тесты и сборка выполняются.
