"""Pure proposal contract. It has no network, storage or external-action rights."""
import re


def normalized(text):
    return ' '.join(text.split())


def grounded_reply_reason(card, request, sources):
    """Conservative MVP: an answer draft is an exact approved cited excerpt.

    This restricts paraphrasing. It does not prove applicability of an article,
    truth of the source, or semantic correctness of other model fields.
    """
    if not isinstance(card, dict):
        return 'missing_proposal'
    if card.get('needs_human_review') is not True:
        return 'operator_control_required'
    if card.get('mode') == 'answer':
        cited = {item['source_id'] for item in card.get('evidence', [])}
        permitted = {normalized(item['excerpt']) for item in sources
                     if item['source_id'] in cited and item['status'] == 'approved'
                     and item['version'] == request['kb_version']}
        if normalized(card.get('draft_reply') or '') not in permitted:
            return 'unsupported_reply_text'
    if card.get('mode') == 'clarify':
        question = (card.get('draft_reply') or '').casefold()
        # A question may ask for an error code; not credentials or payment secrets.
        if re.search(r'(?:пришл|сообщ|напиш|отправ|укаж).{0,40}(?:парол|cvv|cvc|значение токена|код 2fa)', question):
            return 'secret_request_forbidden'
    return None


def tool_contract(schema):
    return {'name': 'propose_card', 'description': 'Только предложить карточку для проверки оператором',
            'parameters': schema, 'side_effects': False, 'external_actions': []}
