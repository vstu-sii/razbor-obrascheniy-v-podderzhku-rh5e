# Research и ADR-001

Первичный ресёрч: 18.09.2026. Уточнение источников и цены решения: 01.10.2026.

Ниже отделены возможности API, подтверждённые документацией, от проектных решений команды. Продуктовые основания взяты из [PRD, зафиксированная версия `4357bd7`](https://github.com/vstu-sii/razbor-obrascheniy-v-podderzhku-rh5e/blob/4357bd7c416b5cd20443d34be7c61a43ac616111/docs/prd.md). Ссылки на OpenAI Retrieval иллюстрируют устройство поиска, но не означают, что команда уже выбрала OpenAI Vector Store.

## Подход 1. Прямой prompt

Прямой запрос к LLM подходит как минимальный baseline: в инструкции можно явно задать ожидаемый результат и критерии успеха. Это соответствует рекомендациям официальной документации OpenAI по prompting: [Model guidance — Prompting](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.5#prompting).

Ограничение для нашего продукта: модель не получает актуальные статьи внутренней базы знаний, если приложение отдельно не передало их во входе или не подключило инструмент поиска. Официальное описание Responses API указывает, что собственные данные добавляются во вход ответа через custom code или `file_search`; вывод о недостаточности изолированного direct prompt для изменяемой БЗ является архитектурным выводом команды: [OpenAI Responses API — использование собственных данных](https://developers.openai.com/api/reference/cli/resources/responses/methods/create).

## Подход 2. RAG

Retrieval позволяет искать релевантные фрагменты по запросу, ограничивать поиск фильтрами и получать найденный текст вместе с оценкой релевантности. Это подтверждает официальный метод поиска по vector store: [OpenAI Vector Store Search](https://developers.openai.com/api/reference/cli/resources/vector_stores/methods/search).

Проектное решение: хранить идентификатор и версию статьи в метаданных, возвращать источники оператору, а при недостаточных основаниях выбирать clarify или escalate. Основания: требование поиска и эскалации в [PRD, раздел MVP](https://github.com/vstu-sii/razbor-obrascheniy-v-podderzhku-rh5e/blob/4357bd7c416b5cd20443d34be7c61a43ac616111/docs/prd.md#mvp) и возможность фильтрации по атрибутам в [OpenAI Retrieval](https://developers.openai.com/api/docs/guides/retrieval). Сам retrieval не гарантирует актуальность статьи: её версию должна поддерживать команда.

Цена подхода: команда принимает на себя загрузку и обновление индекса и контроль версий БЗ. Например, в managed retrieval загрузка включает обработку и индексацию, а удаление файлов применяется не мгновенно; это требует контроля готовности и актуальности данных: [OpenAI Retrieval — Vector stores и File operations](https://developers.openai.com/api/docs/guides/retrieval).

План проверки retrieval: измерять Recall@3 на размеченных релевантных фрагментах и отдельно проверять обращения без ответа в БЗ. Для запроса с непустым эталонным набором Recall@3 — доля эталонных фрагментов, найденных среди первых трёх результатов; по таким запросам усредняем показатель. Примеры без эталонных фрагментов оцениваем отдельно по корректности clarify/escalate. Именно `k=3` — проектный выбор из [бюджета трёх фрагментов](model-candidates.md#токен-бюджет-одного-разбора), а не требование провайдера. Основание для отдельной оценки поиска — рекомендации по context recall/precision в [OpenAI Evaluation best practices, Q&A over docs](https://developers.openai.com/api/docs/guides/evaluation-best-practices#example-qa-over-docs). Это план измерений, а не уже полученный результат.

## Подход 3. Fine-tuning

Supervised fine-tuning требует подготовленных примеров корректных входов и выходов и отдельного цикла оценки. Формат и процесс обучения описаны в официальной документации: [OpenAI Supervised fine-tuning](https://developers.openai.com/api/docs/guides/supervised-fine-tuning).

Решение команды — отложить fine-tuning: текущая практика подтверждает только smoke-тест, а сравнение на 10 golden-примерах ещё запланировано; это не подготовленный обучающий корпус: [методика и ограничения эксперимента](experiments/lab1.md). Документация рекомендует сначала получить baseline и подготовить репрезентативные обучающие и отдельные проверочные примеры: [OpenAI — Optimizing LLM Accuracy, Fine-tuning](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy#fine-tuning).

Fine-tuning сам по себе не синхронизирует модель с изменившейся БЗ. В документации актуальные и внутренние знания относятся к задаче предоставления контекста, а fine-tuning — к обучению поведению на примерах. Поэтому для обновляемых фактов выбираем retrieval; в будущем его можно сочетать с fine-tuning: [OpenAI — Optimizing LLM Accuracy](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy).

## Подход 4. Агент

Агентная архитектура полезна для многошаговых процессов с инструментами, состоянием и действиями. Возможности агентов и подключаемых инструментов описаны в официальной документации: [OpenAI Agents](https://developers.openai.com/api/docs/guides/agents).

Наш MVP не выполняет внешние действия и не изменяет данные, а финальный ответ подтверждает оператор: [PRD, раздел MVP](https://github.com/vstu-sii/razbor-obrascheniy-v-podderzhku-rh5e/blob/4357bd7c416b5cd20443d34be7c61a43ac616111/docs/prd.md#mvp). Поэтому команда выбирает фиксированный pipeline поиска и заполнения карточки без автономного выбора инструментов. Это проектное решение, а не утверждение, что агенты неприменимы к поддержке. В агентном варианте пришлось бы дополнительно проверять выбор инструментов и аргументов, а при нескольких агентах — передачу задачи; такие дополнительные зоны ошибок описаны в [OpenAI Evaluation best practices — архитектуры и области оценки](https://developers.openai.com/api/docs/guides/evaluation-best-practices#identify-where-you-need-evals).

## Структурированный результат

Карточка должна соответствовать JSON Schema, чтобы API и автоматические проверки могли надёжно читать поля. В API режим json_schema используется для Structured Outputs и обеспечивает соответствие переданной схеме: [OpenAI Responses — Structured Outputs](https://developers.openai.com/api/reference/cli/resources/beta/subresources/responses#responses-beta_response_format_text_config).

Соответствие схеме не гарантирует истинность содержания: документация прямо предупреждает, что Structured Outputs может содержать ошибки: [OpenAI Structured Outputs — Handling mistakes](https://developers.openai.com/api/docs/guides/structured-outputs#handling-mistakes). Поэтому команда планирует отдельно проверять значения по golden dataset и БЗ: [план следующего эксперимента](experiments/lab1.md). Отказ модели и незавершённый ответ также обрабатываются отдельно, а не считаются успешной карточкой: [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## ADR-001. RAG и структурированная карточка

Решение команды: использовать retrieval по утверждённой базе знаний и передавать найденные фрагменты LLM для заполнения фиксированной JSON Schema. Если подтверждённого фрагмента нет, система выбирает clarify или escalate. Основания — [продуктовые требования PRD](https://github.com/vstu-sii/razbor-obrascheniy-v-podderzhku-rh5e/blob/4357bd7c416b5cd20443d34be7c61a43ac616111/docs/prd.md#mvp) и рассмотренные выше [возможности retrieval](https://developers.openai.com/api/docs/guides/retrieval) и [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

Почему выбран RAG: продукт должен отвечать по изменяемой БЗ и показывать оператору источники. Vector Store Search возвращает релевантные фрагменты и поддерживает фильтры, что позволяет учитывать версию и область статьи: [источник](https://developers.openai.com/api/reference/cli/resources/vector_stores/methods/search).

Почему не direct prompt: он остаётся baseline, но без переданного контекста не даёт модели доступа к актуальной внутренней БЗ. Недостающие, устаревшие и внутренние знания требуют предоставления контекста: [OpenAI — Context optimization](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy).

Почему не fine-tuning: текущий [smoke-тест](experiments/lab1.md) не является обучающим корпусом; сначала нужны репрезентативные примеры и оценка baseline: [OpenAI — Fine-tuning](https://developers.openai.com/api/docs/guides/optimizing-llm-accuracy#fine-tuning).

Почему не агент: в [MVP](https://github.com/vstu-sii/razbor-obrascheniy-v-podderzhku-rh5e/blob/4357bd7c416b5cd20443d34be7c61a43ac616111/docs/prd.md#mvp) нет автономных внешних действий; команда оставляет поиск фиксированным шагом приложения. Введение выбора инструментов моделью потребовало бы дополнительных проверок: [OpenAI — Single-agent и Multi-agent evaluation](https://developers.openai.com/api/docs/guides/evaluation-best-practices#identify-where-you-need-evals).

### Цена решения и привязка к провайдеру

- Деньги и задержка: поиск добавляет шаг к pipeline; хранение индекса и обработка запросов требуют ресурсов. Например, managed vector store имеет отдельный учёт хранения: [OpenAI Retrieval — Pricing](https://developers.openai.com/api/docs/guides/retrieval#pricing). Для нашего проекта итоговые стоимость и latency ещё нужно измерить на полном pipeline; лимит и состав затрат взяты из [PRD, North Star](https://github.com/vstu-sii/razbor-obrascheniy-v-podderzhku-rh5e/blob/4357bd7c416b5cd20443d34be7c61a43ac616111/docs/prd.md#north-star).
- Сопровождение: обновлять индекс и версии статей, проверять Recall@3, неверные источники и содержательные ошибки даже при валидном JSON. Основания и проектная методика приведены в разделах [RAG](#подход-2-rag) и [Структурированный результат](#структурированный-результат).
- Привязка к LLM-провайдеру: эксперимент использует маршрут OpenRouter и конкретный model ID, а не прямой OpenAI API: [параметры запуска](experiments/lab1.md). Контракт запроса, поддержка `response_format`, подмножества JSON Schema и обработка отказов зависят от выбранного API; пример таких ограничений — [OpenAI Structured Outputs, Supported schemas](https://developers.openai.com/api/docs/guides/structured-outputs#supported-schemas). Поэтому смену модели или провайдера нельзя считать простой заменой строки без проверки.
- Привязка к векторному хранилищу: конкретный backend в ADR-001 ещё не выбран. При выборе managed vector store появится зависимость от его API загрузки, поиска, фильтров и идентификаторов файлов; пример контракта — [OpenAI Retrieval](https://developers.openai.com/api/docs/guides/retrieval). Это условный риск выбранного класса решений, а не утверждение об уже внедрённом OpenAI Vector Store.
- Цена переезда — инженерная оценка команды на основании перечисленных API-контрактов: адаптировать клиент модели и обработку ошибок; заново проверить prompt и JSON Schema; перенести исходные статьи с версиями и метаданными и пересоздать индекс в новом backend. Если меняется embedding-модель, потребуется пересчитать векторы корпуса и запросов. Затем повторить retrieval-оценку, проверки карточек, стоимости и latency на одном контрольном наборе; необходимость повторных оценок при изменениях описана в [OpenAI Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices). Трудозатраты в часах и стоимость миграции пока не измерены.

Чтобы снизить эту привязку, команда планирует хранить исходную БЗ и версии вне managed store, изолировать вызовы модели и retrieval адаптерами и сохранять собственную схему карточки и контрольный набор. Это предлагаемые меры по перечисленным рискам, а не уже реализованные возможности или доказанная переносимость.
