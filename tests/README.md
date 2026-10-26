# LAB04: Quality & Safety

В этой ветке только тесты, доказательства, два отчёта и workflow Quality.
Код приложения других ролей сюда не скопирован. CI получает публичную реализацию
SIISKI по фиксированному коммиту `84d544ed8a1194e91b27fdae5cc60578975e785a`, только каталог `project/`.
`runtime-manifest.json` проверяет содержимое используемых файлов до импорта.

Проверяются 49 записей / 38 групп, 14 сценариев очереди на SQLite и PostgreSQL,
16 сценариев безопасности и шесть защитных контрактов. Записанные HTTP-ответы
Mistral сохранены: CI делает полный replay через текущую зафиксированную систему,
а не новую генерацию. Ключ API не нужен. Результаты LLM и системы разделены.

Для локального запуска укажите `LAB04_RUNTIME_ROOT` — абсолютный путь к
`project/` указанной ревизии SIISKI. Установите её `requirements.txt`.
Из корня этой ветки:

```sh
python tests/evals/evaluate_model.py --require-targets
python -m unittest discover -s tests -p 'test_*.py' -v
python tests/evals/runtime_checks.py --output-dir /tmp/lab4-events
python tests/evals/security_checks.py --output /tmp/lab4-security.json
```

Для PostgreSQL: выделенная синтетическая БД lab04_audit, пользователь audit,
пароль synthetic_lab04_only, loopback-порт 18093. Команда runtime_checks.py
с `--postgres` очищает именно эту тестовую схему между сценариями. Рабочую БД
приложения не использовать. В CI сервис PostgreSQL создаётся отдельно.

`evidence/history/` содержит исходные сбои, `security-current.json` и
`live-current/` — повторение. `events-*/` — фактические трассы SQLite/PG.
Метрики model-v2.1 относятся к development-набору. Человеческая приёмка
LAB04 и готовность к реальным клиентам этим CI не подтверждаются.
