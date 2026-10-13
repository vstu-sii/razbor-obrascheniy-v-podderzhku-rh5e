# AI Support Triage — LAB01 / Delivery

Помощник оператору первой линии русскоязычного B2B SaaS. В этой версии работает
каркас FastAPI с безопасной заглушкой: модель и поиск по БЗ не подключены,
ответ клиенту автоматически не отправляется. Заглушка возвращает
`status=not_analyzed`, `mode=escalate`, `urgency=unassessed`,
`needs_human_review=true`, пустые источники и `draft_reply=null`.

## Запуск из корня репозитория

Требуется Docker Compose. Создание `.env` необязательно: в Compose есть
безопасные значения по умолчанию.

```powershell
docker compose -f compose.dev.yml up --build
```

После запуска ожидаются `GET http://localhost:8000/`,
`GET http://localhost:8000/health` и `POST http://localhost:8000/triage`.

```powershell
Invoke-RestMethod -Method Post -Uri 'http://localhost:8000/triage' -ContentType 'application/json' -Body '{"message":"Не могу войти"}'
```

## Файлы этой ветки

- `app/main.py` — HTTP API и явная передача обращения оператору.
- `tests/test_api.py` — проверки hello-world, health и безопасного ответа заглушки.
- `Dockerfile`, `requirements.txt`, `compose.dev.yml`, `.env.example` — сборка и окружение.
- `.github/workflows/ci.yml` — lint, формат, pytest, проверка зависимостей и Docker build на PR; результат CI нужно смотреть на текущем коммите.
- [docs/deploy.md](docs/deploy.md) — условия проверки публичной версии.
- [data/sample/golden.jsonl](data/sample/golden.jsonl) — 10 синтетических черновиков Quality & Safety. Это примеры формата, а не утверждённый набор для измерения качества.

## Публичный адрес и ограничения

Исторический адрес: https://lab1-sii-zxc.masikpupuna.chatgpt.site.
Его доступность и соответствие текущему коду не подтверждены.
Публичный deploy и полный запуск Compose должны быть проверены отдельно.
Каркас не определяет категорию, не оценивает срочность, не создаёт ответ
по БЗ и не выполняет действий в аккаунте клиента.
