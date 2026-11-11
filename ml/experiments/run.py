"""Подготовка контекста эксперимента и проверка сохранённого прогона."""


def sources_for(case, kb):
    data = case['input']
    return [item for item in kb
            if item['source_id'] in data['kb_candidates']
            and item['status'] == 'approved'
            and item['version'] == data['kb_version']]


if __name__ == '__main__':
    from check_baseline import main
    main()
