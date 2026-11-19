"""Keep topic validation separate from accepting a proposed answer.

A valid label is a model classification, not approval of its advice. Hard-stop
topics come only from existing input-risk rules, never from a general keyword
classifier for ordinary questions.
"""
import re
import unicodedata

from jsonschema import Draft202012Validator


_RISK_RULES = (
    ('instruction_override', r'игнорир\w*.{0,80}(инструкц|правил)', 'other'),
    ('instruction_override', r'ignore.{0,80}(instructions|rules)', 'other'),
    ('public_secret', r'(токен|парол|секрет).{0,80}(публич|утек|раскры|опубликов)', None),
    ('public_secret', r'публич\w*.{0,80}(токен|парол|секрет)', None),
    ('mass_incident', r'у всех.{0,80}(недоступ|ошиб|пропал)', 'product_error'),
    ('third_party_access', r'чуж\w*.{0,50}(аккаунт|парол|данн)', None),
    ('refund_action', r'верни.{0,20}деньги', 'payment'),
)


def input_risk(request):
    text = unicodedata.normalize('NFKC', '\n'.join(
        [request['ticket_text']] + [m['text'] for m in request['history']])).casefold()
    for rule, pattern, category in _RISK_RULES:
        if not re.search(pattern, text):
            continue
        if category is None:
            if rule == 'public_secret':
                category = 'integrations' if re.search(r'токен', text) else ('access' if re.search(r'парол', text) else 'other')
            elif rule == 'third_party_access':
                category = ('payment' if re.search(r'чуж\w*.{0,30}карт', text) else
                            'access' if re.search(r'чуж\w*.{0,50}(?:аккаунт|парол)', text) else 'other')
        return dict(reason='risk_requires_human', category=category, rule=rule)
    return None


def validated_model_category(card, request, schema):
    """Validate the original card and request binding independently of assessment.

No field is repaired, no source check is waived, and this function never
authorizes a draft reply. The caller records the provider as the label's origin.
"""
    if (not isinstance(card, dict) or card.get('request_id') != request['request_id']
            or card.get('status') != 'proposed' or card.get('needs_human_review') is not True
            or not Draft202012Validator(schema).is_valid(card)):
        return None
    return card['category']
