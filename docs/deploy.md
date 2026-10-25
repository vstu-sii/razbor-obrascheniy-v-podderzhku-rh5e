# Развёртывание LAB03

## Локальная проверка без настройки переменных

Из корня LAB-ветки (в DEVELOPMENT — project/): `docker compose up -d --build --wait`.
Открыть http://localhost:8000; `/health` возвращает 200.
Код оператора генерируется случайно в persistent volume. Получить локально:
`docker compose exec app cat /data/operator-token`.
Ввести код в UI. Для `/api/proposals` нужен Authorization: Bearer с этим кодом;
без него возвращается 401. Нельзя снимать авторизацию ради статуса 200 проверки.
При заданном DEMO_OPERATOR_TOKEN используется он, файл остаётся резервом для
запусков без env. Параметры можно задать через .env, но для локального старта
это необязательно. Не коммитить файл .env или содержимое operator-token.

## Render Free и Neon PostgreSQL

Настройки Render: Language Docker, Root Directory `project`, Dockerfile Path
`./Dockerfile`, Branch `lab03/delivery/d-010-render-postgres`, Instance Free.
Health Check Path — `/health`. Docker Command оставьте пустым, используется CMD.

Environment Variables:

- `DATABASE_URL`: целая строка PostgreSQL из Neon, включая параметры SSL.
- `BASELINE_MODE=extractive`: локальный baseline, не генеративная LLM.
- `DEMO_OPERATOR_TOKEN`: необязательно; можно задать собственный длинный код.

При DATABASE_URL приложение использует PostgreSQL. Локальный файл SQLite не
создаётся как запасная замена при ошибке подключения. Миграция
`migrations/postgresql/001_init.sql` применяется транзакционно при старте.
Данные и review находятся в Neon и сохраняются после сна/деплоя Render.
Это адаптация LAB03 JSON-storage, не полный перенос нормализованной схемы LAB02.

Если DEMO_OPERATOR_TOKEN не задан, случайный код сохраняется в приватной
таблице app_settings Neon. Чтобы получить свой код, выполните в Neon SQL Editor:

```sql
SELECT setting_value FROM app_settings WHERE setting_key = 'operator_token';
```

Скопируйте результат в поле кода оператора в UI. Не публикуйте результат запроса,
скриншоты с кодом, DATABASE_URL или паролем. Если код задан в Render env,
используется именно он. API остаётся защищённым, без кода /api/proposals — 401.

Bootstrap слушает `0.0.0.0:$PORT`. Не передавайте DATABASE_URL через build ARG.
SQLite предназначен для локального режима без DATABASE_URL, не для Render Free.
Локальный JSONL tracing на Render ephemeral; persistent dashboard требует
Langfuse. Проверка сохранения карточек не доказывает работу внешних трасс/LLM.

## Действующая публичная версия

https://siiski-volchkova-shared-repository.onrender.com/

Health URL: https://siiski-volchkova-shared-repository.onrender.com/health

Проверено 25.10.2026: UI 200, health 200/status=ok, режим extractive.
API /api/proposals доступен с Bearer-кодом; без него 401 является ожидаемой
защитой, а не отсутствующим endpoint. Для преподавателя код доступа передаётся
участником отдельным разрешённым каналом; в публичный репозиторий он не пишется.
Отдельная read-only health проверка не доказывает аутентифицированную запись.
Скриншоты участника подтверждают создание/чтение карточки, но независимый
публичный restart-test агент не выполнял.

Исходник развёртывания — DEVELOPMENT fb19515 (Render настроен участником).
Фактический commit в панели Render агент не прочитал; это expected source,
не независимое подтверждение deployment SHA. В этой LAB-ветке дополнительно
сохранены функции подтверждения и SQLite migration 002 из исходного PR #12.
Новая PostgreSQL migration 002 переносит эти поля на Neon без удаления строк.
Обновление LAB само по себе не меняет Render, привязанный к DEVELOPMENT-ветке.

Старый lab1-sii-zxc.masikpupuna.chatgpt.site относится к LAB01 и не используется
как свидетельство публичного backend текущей лабораторной.
