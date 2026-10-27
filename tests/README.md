# Проверки пакета LAB04 Delivery

Тесты приложения находятся рядом с текущим backend, отдельной устаревшей
копии runtime нет. Quality-проверки и неизменённые синтетические fixtures
взяты как зависимость из публичного Quality PR15, commit 56e8223.
Изменены только привязка к текущему приложению, manifest его хешей и выделенный
порт тестовой PostgreSQL. Пороги, случаи, сохранённые ответы и атаки не менялись.

Из корня пакета:

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
python tests/evals/evaluate_model.py --require-targets
python -m unittest discover -s tests -p quality_contracts.py -v
python tests/evals/runtime_checks.py --output-dir validation-output/events
python tests/evals/security_checks.py --output validation-output/security.json
```

PostgreSQL EVT использует только loopback:18532, database lab04_audit, user
audit, synthetic_lab04_only. Флаг --postgres очищает эту тестовую схему;
проверка имени БД и пользователя обязательна. Никогда не подставлять рабочую БД.
Суточные квоты отдельно проверяются в infra_audit на :18522.

49 записей / 38 независимых входов — replay настоящих сохранённых ответов,
а не новые платные вызовы модели. Исходные raw и системные метрики разделены.
Хеши fixtures и текущего приложения проверяются до импорта.

Для локальной проверки двух БД из корня пакета:

```sh
docker compose -p lab04-checks -f tests/compose.yml up -d --wait
python tests/evals/runtime_checks.py --postgres --output-dir validation-output/events-postgres
```

Суточные quotas проверяются теми же test_infrastructure.py с переменной
INFRA_TEST_DATABASE_URL=postgresql://audit:synthetic_infra_only@127.0.0.1:18522/infra_audit.
Остановка только тестового проекта: `docker compose -p lab04-checks -f tests/compose.yml down --volumes`.
