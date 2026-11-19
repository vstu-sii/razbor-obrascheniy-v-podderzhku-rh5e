# LAB05: проверка категорий AI-033

20 исходных обращений с неизменёнными эталонами. Это адресная живая проверка
новой версии после полного офлайн-replay 44 сохранённых входов.
Драйвер run.py использует реальную очередь, worker и провайдеров; отдельный
допуск до 10 ₽ разрешён участником 20.11.2026. История расходов и резервов
из AI-032 перенесена без сброса. Курс 89 ₽/$, Mistral Free.

Перед генерацией фиксируются commit/tag, исходники, промпты, входы и параметры.
Промпты или результаты во время серии не редактируются, платных повторов нет.
Ключи берутся из отдельного локального env-файла, в сдаваемых файлах их нет.
verify_traces.py только читает биллинг и Langfuse.

## Воспроизведение без новых API-вызовов

Из корня проекта:

```text
python -m pip install -r requirements.txt -r ml/experiments/lab5/requirements-analysis.txt
python ml/experiments/lab5-category/analysis.py
python -m unittest discover -s tests -p "test_*.py"
```

Ноутбук docs/experiments/lab5.ipynb выполняется в Python 3 и читает сохранённые
данные. Полные 44 генерации 19.11 и свежие адресные генерации 20.11 разделены.
Для точного исторического runtime используйте соответствующий локальный тег
в отдельном checkout; не запускайте старый эксперимент как новый живой вызов.

Новая генерация требует отдельного разрешённого бюджета и локального env-файла:

```text
python ml/experiments/lab5-category/run.py --prepare --tag lab5-ai-engineer-categories-2026-11-20 --output validation-output/category-live
python ml/experiments/lab5-category/run.py --live --output validation-output/category-live --local validation-output/category-runtime --env-file .env.cascade
```

Драйвер отказывается повторять уже начатую серию. Он не меняет эталоны,
не передаёт их моделям и не повторяет платные запросы автоматически.
