# Deploy

## Локальная проверка

Dev-среда поднимается одной командой:

~~~powershell
docker compose -f compose.dev.yml up --build
~~~

Файл .env не обязателен: compose.dev.yml содержит безопасные значения для stub-режима. Для переопределения настроек можно скопировать .env.example в .env.

После запуска проверяются:

- GET http://localhost:8000/ — hello-world;
- GET http://localhost:8000/health — статус приложения;
- POST http://localhost:8000/triage — контракт заглушки.

Ожидаемый health-ответ: HTTP 200 и {"status":"ok"}.

## Публичный deploy

Приложение собирается из Dockerfile. На хостинге нужно создать web service, подключить репозиторий, выбрать Docker deployment и задать переменные окружения.

Секреты задаются только в настройках хостинга. Реальный .env не коммитится.

Публичный URL: https://lab1-sii-zxc.masikpupuna.chatgpt.site

Проверки публичной версии:

- `GET https://lab1-sii-zxc.masikpupuna.chatgpt.site/` — Hello World;
- `GET https://lab1-sii-zxc.masikpupuna.chatgpt.site/health` — health-страница.

## НУЖНО ДОБАВИТЬ КОМАНДЕ

1. Выбрать хостинг и подключить репозиторий.
2. Выполнить deploy с текущим Dockerfile.
3. Открыть публичный URL вне локальной сети.
4. Проверить GET <PUBLIC_URL>/health и сохранить HTTP 200.
5. URL уже добавлен здесь и в README.
6. Добавить ссылку на успешный deploy или скрин проверки в PR.
