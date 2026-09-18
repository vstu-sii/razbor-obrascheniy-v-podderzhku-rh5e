# Кандидаты моделей и токен-бюджет

Дата проверки источников и тарифов: 18.09.2026.

## Кандидат 1. GPT-5.6 Sol (high) со Structured Outputs

Роль: основной облачный baseline для проверки качества структурированной карточки.

Почему рассматриваем: официальный API OpenAI поддерживает Structured Outputs и позволяет получать результат по заданной JSON Schema. Это позволяет автоматически валидировать поля карточки: [OpenAI — Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

В качестве конкретной модели рассматривается GPT-5.6 Sol (high). По данным Artificial Analysis, модель имеет Artificial Analysis Intelligence Index 42, скорость генерации 60,1 токена в секунду, Time to First Token (TTFT) 11,91 секунды и контекст 1 млн токенов: [Artificial Analysis — GPT-5.6 Sol (high)](https://artificialanalysis.ai/models/gpt-5-6-sol-high).

Artificial Analysis указывает стоимость GPT-5.6 Sol (high) $4,00 за 1 млн входных токенов и $20,00 за 1 млн выходных токенов: [Artificial Analysis — GPT-5.6 Sol (high), цена и производительность](https://artificialanalysis.ai/models/gpt-5-6-sol-high).

Преимущества: высокий Intelligence Index, контекст 1 млн токенов, поддержка Structured Outputs и официальный облачный API.

Риски: внешний API, наиболее высокая цена входных и выходных токенов среди рассматриваемых кандидатов; доступность конкретной модели необходимо проверить для аккаунта команды.

Что измеряем при практическом эксперименте: качество на одном golden dataset, p95 latency, input/output tokens, стоимость одного triage и долю ответов, прошедших JSON Schema.

## Кандидат 2. Mistral Medium 3.5

Роль: альтернативный облачный кандидат с высокой скоростью генерации.

Почему рассматриваем: Mistral API поддерживает Structured Outputs с JSON Schema, что позволяет задавать требуемую структуру ответа и автоматически валидировать результат: [Mistral AI — Structured Outputs](https://docs.mistral.ai/capabilities/structured-output/structured_output_overview).

По данным Artificial Analysis, Mistral Medium 3.5 имеет Artificial Analysis Intelligence Index 15, скорость генерации 144,7 токена в секунду, TTFT 2,25 секунды и контекст 260 тыс. токенов: [Artificial Analysis — Mistral Medium 3.5](https://artificialanalysis.ai/models/mistral-medium-3-5).

Artificial Analysis указывает стоимость Mistral Medium 3.5 $1,50 за 1 млн входных токенов и $7,50 за 1 млн выходных токенов: [Artificial Analysis — Mistral Medium 3.5, цена и производительность](https://artificialanalysis.ai/models/mistral-medium-3-5).

Преимущества: самая высокая скорость генерации среди рассматриваемых кандидатов, небольшая TTFT, поддержка Structured Outputs и более низкая стоимость токенов по сравнению с GPT-5.6 Sol (high).

Риски: Intelligence Index ниже остальных рассматриваемых кандидатов; необходимо отдельно проверить качество русского языка, retrieval и соблюдение JSON Schema на нашем golden dataset.

Что измеряем при практическом эксперименте: качество на одном golden dataset, p95 latency, input/output tokens, стоимость одного triage и долю ответов, прошедших JSON Schema.

## Кандидат 3. Qwen3.8-Max через облачный API

Роль: мощный универсальный облачный кандидат на базе семейства Qwen.

Почему рассматриваем: Alibaba Cloud Model Studio предоставляет API для облачного вызова моделей Qwen. API поддерживает Structured Output с режимом `json_schema`, который позволяет задавать структуру ответа и получать результат, соответствующий указанной JSON Schema: [Alibaba Cloud Model Studio — Structured Output](https://docs.modelstudio.console.alibabacloud.com/en/model-studio/qwen-structured-output).

В качестве конкретной модели рассматривается Qwen3.8 Max (0902). По данным Artificial Analysis, модель имеет Artificial Analysis Intelligence Index 45, скорость генерации 39,6 токена в секунду, TTFT 2,71 секунды и контекст 980 тыс. токенов: [Artificial Analysis — Qwen3.8 Max (0902)](https://artificialanalysis.ai/models/qwen3-8-max).

Artificial Analysis указывает стоимость Qwen3.8 Max (0902) $2,00 за 1 млн входных токенов и $6,00 за 1 млн выходных токенов: [Artificial Analysis — Qwen3.8 Max (0902), цена и производительность](https://artificialanalysis.ai/models/qwen3-8-max).

Преимущества: самый высокий Artificial Analysis Intelligence Index среди рассматриваемых кандидатов, большой контекст, поддержка Structured Outputs и сравнительно невысокая стоимость выходных токенов.

Риски: для использования требуется внешний облачный провайдер; скорость генерации ниже остальных рассматриваемых кандидатов; необходимо отдельно проверить качество русского языка, retrieval и соблюдение схемы на нашем golden dataset.

В практическом smoke-тесте модель вызвана через OpenRouter с идентификатором `qwen/qwen3.8-max`. Это проверяет конкретный маршрут OpenRouter, а не прямое подключение к Alibaba Cloud Model Studio: [первичные данные smoke-теста](../notebooks/lab1_prompt_experiments.ipynb).

Результат smoke-теста: модель вернула читаемый JSON, но израсходовала 2419 токенов и показала максимальное время ответа среди четырёх кандидатов — 55,15 с. Поэтому для следующего этапа она не выбрана: [ответ модели и метрики запуска](../notebooks/lab1_prompt_experiments.ipynb).

## Кандидат 4. DeepSeek-V4-Pro-0813

Роль: альтернативный облачный кандидат для сложного анализа обращений и генерации структурированного результата.

Почему рассматриваем: DeepSeek-V4-Pro-0813 доступен через облачные API и ориентирован на reasoning-задачи. Для сравнения характеристик используется вариант DeepSeek V4 Pro 0813 (Reasoning, Max Effort), представленный в Artificial Analysis: [Artificial Analysis — DeepSeek V4 Pro 0813](https://artificialanalysis.ai/models/deepseek-v4-pro).

По данным Artificial Analysis, DeepSeek V4 Pro 0813 (Reasoning, Max Effort) имеет Artificial Analysis Intelligence Index 36, скорость генерации 79,6 токена в секунду, TTFT 1,70 секунды и контекст 1 млн токенов: [Artificial Analysis — DeepSeek V4 Pro 0813, производительность](https://artificialanalysis.ai/models/deepseek-v4-pro).

Artificial Analysis указывает стоимость DeepSeek V4 Pro 0813 $1,32 за 1 млн входных токенов и $3,96 за 1 млн выходных токенов: [Artificial Analysis — DeepSeek V4 Pro 0813, цена](https://artificialanalysis.ai/models/deepseek-v4-pro).

Преимущества: высокий Intelligence Index, самая низкая TTFT среди рассматриваемых кандидатов, высокая скорость генерации, контекст 1 млн токенов и самая низкая указанная Artificial Analysis цена входных и выходных токенов среди выбранных моделей.

Риски: внешний облачный API; модель в сравнении Artificial Analysis является reasoning-моделью и может использовать больше выходных токенов; необходимо отдельно проверить retrieval и соблюдение требуемой JSON Schema на нашем сценарии.

В практическом smoke-тесте модель вызвана через OpenRouter с идентификатором `deepseek/deepseek-v4-pro`: [первичные данные smoke-теста](../notebooks/lab1_prompt_experiments.ipynb).

Результат smoke-теста: 1783 токена и 27,69 с на запрос. Ответ содержал JSON внутри Markdown-блока и русские имена полей вместо ожидаемого машинного контракта, поэтому для следующего этапа модель не выбрана: [ответ модели и метрики запуска](../notebooks/lab1_prompt_experiments.ipynb).

## Сравнение по данным Artificial Analysis

| Модель | Intelligence Index | Скорость генерации | TTFT | Цена input / 1 млн | Цена output / 1 млн | Контекст |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| GPT-5.6 Sol (high) | 42 | 60,1 ток/с | 11,91 с | $4,00 | $20,00 | 1 млн |
| Mistral Medium 3.5 | 15 | 144,7 ток/с | 2,25 с | $1,50 | $7,50 | 260 тыс. |
| Qwen3.8 Max (0902) | 45 | 39,6 ток/с | 2,71 с | $2,00 | $6,00 | 980 тыс. |
| DeepSeek V4 Pro 0813 | 36 | 79,6 ток/с | 1,70 с | $1,32 | $3,96 | 1 млн |

Источник показателей: [Artificial Analysis — Models](https://artificialanalysis.ai/leaderboards/models). Показатели Artificial Analysis являются внешними бенчмарками и не заменяют проверку качества моделей непосредственно на нашем golden dataset.

Важно: в таблице выше приведены профили и цены Artificial Analysis, а в практическом тесте использованы точные идентификаторы моделей OpenRouter без отдельно заданного reasoning effort. Поэтому внешние показатели используются только как ориентир, а решение принимается по собственному тесту.

## Практический smoke-тест через API

18.09.2026 выполнено по одному потоковому API-вызову четырёх моделей через OpenRouter. Все модели получили один и тот же базовый prompt; фактические токены взяты из `api_usage`. Подробная методика и ограничения находятся в [`docs/experiments/lab1.md`](experiments/lab1.md), а первичные метрики, исходные ответы моделей и воспроизводимый расчёт — в [`notebooks/lab1_prompt_experiments.ipynb`](../notebooks/lab1_prompt_experiments.ipynb).

| Модель OpenRouter | Общее время | Input | Output | Всего | Формат ответа | Расчётная стоимость запроса* |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| `openai/gpt-5.6-sol` | 5,43 с | 214 | 252 | 466 | валидный JSON, 10 ожидаемых полей | $0,00590 |
| `mistralai/mistral-medium-3-5` | **1,34 с** | 274 | 76 | **350** | обычный текст, не JSON | **$0,00098** |
| `qwen/qwen3.8-max` | 55,15 с | 283 | 2136 | 2419 | валидный JSON, 10 ожидаемых полей | $0,01338 |
| `deepseek/deepseek-v4-pro` | 27,69 с | 243 | 1540 | 1783 | JSON в Markdown, несовместимые имена полей | $0,00642 |

\* Стоимость рассчитана по приведённым выше ставкам Artificial Analysis и не является суммой из биллинга OpenRouter. У OpenRouter итоговая цена зависит от выбранного маршрута и провайдера.

## Выбор для следующего этапа

Основной кандидат — `openai/gpt-5.6-sol`. В данном запуске это лучший компромисс между машинно-читаемым результатом, задержкой и расходом токенов: модель вернула сырой JSON со всеми ожидаемыми полями за 5,43 с и использовала 466 токенов. При отсутствии входных данных она безопасно выбрала уточнение и участие оператора: [первичные метрики и полный ответ](../notebooks/lab1_prompt_experiments.ipynb).

`mistralai/mistral-medium-3-5` остаётся резервом: модель была самой быстрой и дешёвой, но без принудительного `response_format: json_schema` не выполнила требование к формату: [первичные метрики и полный ответ](../notebooks/lab1_prompt_experiments.ipynb). Следующий честный этап — повторить сравнение на 10 одинаковых golden-примерах, передав реальную JSON Schema и выполнив не менее трёх прогонов на пример. Только после этого можно сравнивать качество и p95 latency.

## Токен-бюджет одного разбора

Инструкция занимает примерно 450 токенов.

Обращение занимает примерно 250 токенов.

История занимает примерно 600 токенов.

Найденные фрагменты занимают примерно 900 токенов.

Результат занимает примерно 500 токенов.

Итого один вызов использует примерно 2700 токенов. Один разрешённый повтор увеличивает верхнюю оценку до 5400 токенов.

Фактическая стоимость считается так: стоимость input-токенов плюс стоимость output-токенов плюс retrieval и embeddings плюс повторные вызовы. Полученную сумму делим на число завершённых разборов.

Рабочий продуктовый лимит — не больше 5 рублей за один завершённый triage.

## Деградация

Если API недоступен, оператор видит исходный тикет и ручной поиск.

Если превышается бюджет, система сокращает нерелевантную историю и явно отмечает сокращение.

Если ответ не соответствует JSON Schema, запрос повторяется один раз, затем тикет передаётся оператору.

Если retrieval не нашёл подтверждённый источник, система не генерирует процедуру и выбирает clarify или escalate.
