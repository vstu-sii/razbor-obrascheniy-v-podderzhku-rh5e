"""Small evidence-based dialogue hints, not a semantic summary or new memory."""
import re
import unicodedata


def norm(text):
    return ' '.join(unicodedata.normalize('NFKC',text).casefold().replace('ё','е').split())


def state(request):
    facts, asked = {}, set()
    turns = list(request.get('history', [])) + [dict(speaker='customer',text=request['ticket_text'])]
    for index, turn in enumerate(turns):
        text = norm(turn['text'])
        if turn['speaker'] == 'operator':
            if re.search(r'код.{0,15}ошиб',text):
                asked.add('error_code')
            continue
        code = re.search(r'\b[a-zа-я]{1,5}[-_]?\d{2,}\b|\bкод(?: ошибки)?\s*[:№]?\s*\d+\b',text)
        unavailable = re.search(r'код.{0,30}(?:неизвест|не извест|не знаю|пока нет|нет)|(?:не знаю|неизвестен).{0,20}код|без кода ошибки',text)
        if code:
            facts['error_code'] = dict(status='known',turn=index,value=code.group(0))
        elif unavailable:
            facts['error_code'] = dict(status='unavailable',turn=index)
    return dict(facts=facts, previously_requested=sorted(asked), latest_customer_turn=len(turns)-1)


def repeated_fact(request, kind):
    hints = state(request)
    fact = hints['facts'].get(kind, {})
    return fact.get('status') in ('known','unavailable')
