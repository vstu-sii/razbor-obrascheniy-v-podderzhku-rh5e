# LAB04 Delivery: окружения, выпуск и проверка

Все команды выполняются из корня пакета (в рабочем репозитории это папка
project/, в ветке сдачи — корень репозитория). Используются одинаковые Dockerfile,
миграции, baseline и протокол событий. Проверки и инструкции не требуют
доступа к закрытому рабочему репозиторию.

| Окружение | Процессы | БД | Наблюдаемость |
|---|---|---|---|
| Локальная разработка | compose.yml: API, worker, AI, ingress | PostgreSQL | JSON logs, метаданные в БД |
| Staging | те же compose.yml + compose.staging.yml | отдельный volume PostgreSQL | Prometheus, Loki, Tempo, Alloy, Alertmanager, Grafana |
| Render LAB04 | тот же образ, встроенный worker/AI | существующая Neon | JSON stdout + приватные /metrics + event traces в БД; внешний OTLP только при настройке |

Staging и обычный compose используют один собранный образ для четырёх процессов,
одинаковые SQL-миграции и политику квот. Render Free не запускает Compose-стек:
в нём очередь/лимиты те же, worker/AI входят в процесс API. Это реальное отличие
топологии, ресурсов и доступности, а не заявление об одинаковом SLA.
Локальные Loki/Tempo не объявляются публичным мониторингом Render. Для их
постоянного production размещения нужен отдельный контролируемый сервер или
настроенные внешние endpoints мониторинга. Участник выбрал оставить мониторинг
в Docker, без Grafana Cloud и новых платных ресурсов. Пока компьютер или стек
выключен, локальный мониторинг недоступен; он наблюдает локальный staging,
а не объявляется непрерывным внешним монитором публичного Render.

## Запуск staging

Из корня пакета:

```sh
docker compose -p lab04-staging -f compose.yml -f compose.staging.yml up -d --build --wait
```

Порты по умолчанию: API 8000, ingress 8001, Prometheus 18300, Grafana 18301,
Alertmanager 18302, Loki 18500, Tempo 18600. Все опубликованные порты — loopback.
При занятых портах задать PORT, INGRESS_PORT, PROMETHEUS_PORT, GRAFANA_PORT,
ALERTMANAGER_PORT, LOKI_PORT, TEMPO_PORT. Секреты генерирует credentials в volume;
в Git они не попадают. Остановка: compose down без --volumes.

Grafana требует admin-пароль, анонимный доступ выключен. Получить локальный
пароль в своём терминале, не публиковать результат:

```sh
docker compose -p lab04-staging -f compose.yml exec -T app python -c "from pathlib import Path; print(Path('/run/secrets/grafana-password').read_text())"
```

Главный дашборд: /d/lab04-triage. На нём очередь, HTTP, резервы, отказы/деградация,
активные алерты, логи и трассы. Loki/Tempo используют минимальный static probe
для настоящих /ready checks в distroless-образах. Alloy читает только volume
JSON логов, Docker socket ему не предоставляется. Метрики защищены отдельным
metrics-token, код оператора не даёт доступ к ним. Логи ротируются по 5 MB,
3 файла на сервис; Loki/Tempo хранят 7 дней. Это учебная одноузловая конфигурация.

## Суточная политика

USER_REQUESTS_PER_DAY=100: новые уникальные события на authenticated actor за UTC
сутки. Дубликат не расходует лимит; чтение и review не блокируются из-за бюджета.
USER_MODEL_CALLS_PER_DAY=100: попытки платного LLM на actor за UTC сутки.
USER_TOKENS_PER_DAY=1000000: резерв платных токенов того же actor. Все три
счётчика находятся в БД, общий lock исключает превышение при конкуренции.
Время суток считается по UTC, резерв — в день начала попытки, admission —
в день приёма события. Неизвестное списание после сбоя не возвращается в бюджет.

OPERATOR_TOKENS может содержать JSON map внутренних actor ID → приватные коды.
Значения задаются только в закрытых env, не в примерах Git. По умолчанию один
demo-operator с сохраняемым случайным кодом. JSON/header actor ID не принимается.
Интеграция имеет отдельный service actor. Сервисные, операторские и metrics-коды
не взаимозаменяемы. Paid inference требует действующий job/claim и совпадающий
снимок; повтор claim запрещён. Старый ручной /api/analyses не допускает LLM обход.

При исчерпании платных квот используется существующий extractive baseline без
вызова провайдера, а не другая неутверждённая платная модель. Это фиксируется
security_event и логом policy; см. ADR-004. Первичный запросный лимит даёт 429.
Глобальные MAX_RUNNING/MAX_PENDING/RPM и lifetime TOKEN_BUDGET также сохраняются.
ANOMALY_TOKENS_PER_MINUTE=200000 — порог алерта по консервативным резервам,
не по неизвестному реальному долларному биллингу. Локальная проверка ставит
порог 1 только в изолированном staging; production defaults не занижаются.

## CI/CD и откат

.github/workflows/deploy.yml запускает quality gates и сборку на PR/main.
Deploy job выполняется автоматически только после push/merge в main и успеха
обоих jobs. Он вызывает hook с точным SHA и ждёт /health с тем же revision,
status=ok и infrastructure=lab04. Старый успешный /health не принимается.
Повторный workflow_dispatch на main с rollback_sha использует только совместимый
ancestor, у которого были успешные quality gates и имеются новые daily guards.
Deploy jobs сериализованы; текущий deploy не отменяется новым push.

GitHub production configuration: secret RENDER_DEPLOY_HOOK и variable PUBLIC_URL.
Настроено 27.10.2026 с разрешения участника. Render использует main, root project/,
Dockerfile ./Dockerfile и /health. Независимый Render Auto-Deploy выключен:
автоматический выпуск после merge main выполняет GitHub CI через hook,
поэтому обновление не обходит тесты. Тариф Free и существующая Neon сохранены.

Публичный URL: https://siiski-volchkova-shared-repository-ic7x.onrender.com/.
Первый автоматический main release 600c194 прошёл. Затем workflow_dispatch
с rollback_sha=5a2050056e64204523fa7e7d578babb361f3c001 выпустил совместимый
проверенный guard release, затем обычный workflow_dispatch вернул main.
Публичная синтетическая карточка и решение оператора доступны во всех трёх
состояниях с теми же идентификаторами. Оба SHA содержат одинаковую версию
runtime: это проверка механизма выпуска/отката и сохранности, не моделирование
неисправной миграции или откат на старый LAB03. Старый LAB03 без guards
по-прежнему запрещён production gate. Доказательства: deployment-lab04.json.

Команды из корня пакета (секрет hook хранится только в окружении):

```sh
python ops/release.py deploy --sha FULL_COMMIT_SHA
python ops/release.py rollback --sha PREVIOUS_VERIFIED_FULL_SHA
python ops/release.py compose-rollback --image VERIFIED_PREVIOUS_IMAGE --project lab04-staging
```

Compose rollback использует --no-build, сохраняет БД/volumes и принудительно
выбирает extractive. Проверен реальный переход с нового образа на D-014 84d544e
и возврат, карточка/review и код доступа сохранились. Старый D-014 не проходит
production rollback gate новой daily-политики; этот локальный тест доказывает
совместимость SQL, не разрешает откат публичной LLM-конфигурации на слабые guards.
Для production нужен совместимый ранее проверенный LAB04 guard release.

Документация интерфейсов: [Render deploy hooks](https://render.com/docs/deploy-hooks),
[Grafana Alloy file collection](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.file/).
Техническое review/локальные проверки не являются принятием Product или преподавателем.

## CI в ветке сдачи

Пакет самодостаточен: deploy.yml вызывает включённый quality-gates.yml,
сборка использует Dockerfile в корне. Проверяются текущий backend и его manifest,
полный неизменённый replay, контракты, события и атаки. Отдельной устаревшей
копии приложения нет. Чужие ролевые отчёты не являются сдачей Delivery.

Рабочий автодеплой настроен в SIISKI/siiski_volchkova_shared_repository.
Секреты между репозиториями не переносятся. Для активации deploy job после merge
в другом репозитории нужны его собственные production secret/variable и Render,
подключённый к этому исходнику; без них выпуск завершается безопасным отказом.
PR в репозитории преподавателя выполняет проверки без доступа к production.
