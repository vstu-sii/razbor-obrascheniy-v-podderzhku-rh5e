# LAB03: проверки Quality & Safety

## Сохранённый baseline

Результаты Mistral ministral-14b-2512 от 15.10.2026:

- 43 записи основного набора покрыты 38 разными ответами; 5 записей имеют одинаковый вход и эталон.
- Верный режим: 33/38. Верная категория: 36/38.
- Валидная схема и цитаты: 38/38. Ложный answer: 0/28 ожидаемых non-answer.

[Отчёт и ограничения](REPORT.md). Исходные ответы, хеши и покрытие сохранены
в `evidence/baseline-*.json` и `evidence/baseline-*.jsonl`.
Это замер генерации с заданным контекстом, а не всего приложения.

## Проверка из корня пакета

```powershell
python -m pip install -r tests/evals/requirements.txt
python docs/quality/golden-dataset/validate.py
python -m unittest discover -s tests/evals -p 'test_*.py' -v
python tests/evals/check_baseline.py
python tests/evals/check_iteration.py
```

Команды проверяют сохранённые результаты без обращения к API.
11 автономных unit-тестов прошли. `check_iteration.py` проверяет сохранённую итерацию;
недостигнутые инженерные цели перечислены в её выводе.

## Событийная архитектура

Baseline выше относится к генерации от 15.10.2026 и не измеряет очередь.
Выполнена отдельная изолированная проверка приложения Delivery `fb19515`:
health и прямой анализ возвращают 200; событийного API и worker в этой версии
нет. Просмотр и отклонение не вызывают повторный анализ. Проверялся TestClient,
временная SQLite и инструментированный engine, без внешней модели.

Полный прогон EVT-01–14 не выполнен. Зелёный CI проверяет сохранённые модельные
результаты и автономные тесты, а не работу событийной очереди.

История экспериментов находится в [PREVIOUS-REPORT.md](PREVIOUS-REPORT.md),
[ITERATION-REPORT.md](ITERATION-REPORT.md) и [OUTPUT-REVIEW.md](OUTPUT-REVIEW.md).
