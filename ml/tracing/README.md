# Трассировка вызовов

В `Baseline.call_model` используется Langfuse SDK 4.0.0. Каждая внешняя
попытка создаёт generation observation с названием `support-baseline`,
идентификатором модели и статистикой входных и выходных токенов.
Повторный вызов создаёт отдельное наблюдение.

Настройки перечислены в [.env.example](.env.example): `LANGFUSE_BASE_URL`,
`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`. При
`TRACE_SYNTHETIC_CONTENT=true` в наблюдение передаются prompt и ответ.
Этот режим используется с синтетическими обращениями. При значении `false`
передаются идентификатор запроса и выбранный режим ответа.

Локальный журнал `TRACE_PATH` содержит request_id, хеш входа, найденные
источники, время обработки, usage, число попыток и результат проверки.

Внешняя модель настраивается через `MODEL_BASE_URL`, `MODEL_NAME`,
`MODEL_API_KEY`; для её вызова требуется `BASELINE_MODE=llm`.
В `MODEL_BASE_URL` указывается адрес API до `/chat/completions`, например
`https://api.mistral.ai/v1`. В личном окружении задаются значения ключей.

Чтобы запускать API с настройками из файла без дополнительных модулей:

```powershell
Copy-Item ml/tracing/.env.example .env
Get-Content .env | ForEach-Object {
    if ($_ -match '^([A-Z][A-Z0-9_]*)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}
python -m uvicorn backend.routers.ai:create_app --factory --host 127.0.0.1 --port 8001
```

Swagger UI: `http://127.0.0.1:8001/docs`. Для `/v1/analyze` используется
Bearer-токен из `DEMO_OPERATOR_TOKEN`. При подключении к общему backend
вызывается `create_router(AnalyzeRequest, operator_dependency, engine)`.

Документация SDK: [Langfuse Python SDK](https://langfuse.com/docs/observability/sdk/overview).
