# Трассировка baseline

SDK: `langfuse==4.0.0`. Каждая внешняя попытка в `Baseline.call_model` обёрнута
в generation observation. Повтор создаёт отдельное наблюдение. Локальный
request ID связывает попытку с карточкой и метриками eval.

`LANGFUSE_BASE_URL`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` передаются
окружением. Только при `TRACE_SYNTHETIC_CONTENT=true` SDK получает prompt
и ответ; режим предназначен для вымышленных примеров этого задания.
По умолчанию SDK получает лишь request ID и итоговый mode.

Dashboard размещается в доступном команде Langfuse Cloud/self-hosted сервисе.
В Compose не запускается фиктивный dashboard и не записывается выдуманный URL.
После настройки проверить trace каждого вызова, request ID, модель, токены,
latency, отсутствие ключей и правильную политику доступа. Сейчас это не проверено.

Источник API SDK проверен 2026-10-13:
[официальная документация Langfuse](https://langfuse.com/docs/observability/sdk/overview).
