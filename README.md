# AI Support Triage

AI-помощник для операторов первой линии русскоязычного B2B SaaS-сервиса.

Система анализирует текстовое обращение и готовит карточку с категорией, срочностью, summary, найденными источниками, заполненными полями, недостающими данными и следующим шагом. Финальный ответ подтверждает оператор.

## Быстрый запуск

Для стандартного запуска не требуется заранее создавать .env:

~~~bash
docker compose -f compose.dev.yml up --build
~~~

Переменные из .env.example уже имеют безопасные значения по умолчанию в compose.dev.yml. Если нужен другой порт или провайдер, скопируйте .env.example в .env и измените значения.

После запуска:

- hello-world: GET http://localhost:8000/;
- health-check: GET http://localhost:8000/health;
- triage-заглушка: POST http://localhost:8000/triage.

Ожидаемый ответ hello-world:

~~~json
{"message":"AI Support Triage is running"}
~~~

## Структура репозитория

~~~text
.
├── .github/
│   ├── pull_request_template.md   # шаблон PR
│   └── workflows/
│       └── ci.yml                 # lint, format, tests и Docker build
├── app/
│   ├── __init__.py
│   └── main.py                    # FastAPI и контракт triage
├── data/
│   └── sample/
│       └── golden.jsonl           # первые 10 golden-примеров
├── docs/
│   ├── brief.md                   # итоговая картина продукта
│   ├── deploy.md                  # запуск и публичный deploy
│   ├── experiments/
│   │   └── lab1.md                # план и результаты проб
│   ├── glossary.md                # единый язык команды
│   ├── hypotheses.md              # фальсифицируемые гипотезы
│   ├── insights.md                # результаты интервью
│   ├── model-candidates.md        # модели и токен-бюджет
│   ├── presentation.md            # «Я понял проект как…»
│   ├── prd.md                     # сегмент, боли, MVP и North Star
│   ├── quality/
│   │   ├── dod-draft.md
│   │   ├── golden-dataset-plan.md
│   │   ├── success-criteria.md
│   │   ├── team-actions.md
│   │   └── threat-model.md
│   └── research.md                # research и ADR-001
├── notebooks/
│   └── lab1_prompt_experiments.ipynb
├── tests/
│   └── test_api.py
├── .env.example                   # список переменных без секретов
├── .gitignore
├── compose.dev.yml                # dev-среда одной командой
├── Dockerfile
└── requirements.txt
~~~

Дерево выше соответствует фактической структуре проекта. При добавлении нового верхнеуровневого каталога README обновляется в том же PR.

## CI

Workflow .github/workflows/ci.yml запускается на каждом pull request и push в main. Он проверяет lint, формат, компиляцию, тесты, зависимости и сборку Docker-образа.

Рабочие ветки Lab 1:

- lab1-product-initiation;
- lab1-ai-initiation;
- lab1-delivery-initiation;
- lab1-quality-initiation.

Заголовок PR: Lab1: [Role] — Initiation Deliverables.

## Публичный deploy

Публичная точка входа: [https://lab1-sii-zxc.masikpupuna.chatgpt.site](https://lab1-sii-zxc.masikpupuna.chatgpt.site).

После deploy адрес должен открываться вне локальной сети, а GET <PUBLIC_URL>/health должен возвращать HTTP 200 и {"status":"ok"}.

Инструкция размещена в [docs/deploy.md](docs/deploy.md).

## НУЖНО ДОБАВИТЬ КОМАНДЕ

- Delivery: открыть PR и вставить сюда ссылку на успешный CI run и commit SHA.
- Delivery: deploy выполнен; публичный HTTPS-адрес добавлен выше.
- Delivery: заменить следующий badge адресом настоящего GitHub-репозитория.

![CI](https://github.com/ORG/REPO/actions/workflows/ci.yml/badge.svg)
