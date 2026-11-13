# propose_card: контракт инструмента

LLM получает tools[].function с name=propose_card, schema {card, assessment}
из ml/cascade/assessment.py. tool_choice фиксирует этот инструмент. Другие имена,
несколько вызовов и невалидные аргументы отвергаются provider.parse_response.

Вход execute_proposal: card, request, approved sources, публичная schema,
локальный provenance gate, permissions=frozenset({'propose_card'}), абсолютный
monotonic deadline. Выход (passed: bool, reason: str). Успех не исполняет
действие: он разрешает последующие assessment/safety-проверки каскада.

Права проверяются в коде. Нет сети, записи в БД/helpdesk, выдачи прав или
вызова произвольной функции из текста. Пустые/лишние права → tool_permission_denied,
невалидная схема → tool_invalid_arguments, истёкший deadline → tool_timeout.
Deadline проверяется до и после чистой валидации, без фонового зависшего worker;
это не механизм прерывания произвольного стороннего кода. HTTP timeout
поставщика ограничен отдельно (≤25 с) и проверен в каскаде.

Отказ Mistral идёт к Sol, окончательный отказ Sol — человеку. Для входных
политик высокого риска модели вообще не вызываются. Тесты:
python ml/experiments/lab4/checks.py; существующие runtime-тесты PR39 отдельно.
