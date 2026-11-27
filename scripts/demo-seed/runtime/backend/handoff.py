"""Build an operator handoff from persisted input and attributed model attempts.

No generation, new classification, or promotion of model hypotheses to facts.
The public card schema is unchanged; optional operator metadata is separate.
"""
import copy
from jsonschema import Draft202012Validator

from ml.cascade.context import checked_context
from ml.contracts.validation import SCHEMA
from ml.cascade.category import input_risk, validated_model_category

EVIDENCE = Draft202012Validator(SCHEMA['properties']['evidence']['items'])


def explanation(reason):
    if 'budget' in reason or 'allowance' in reason or 'quota' in reason:
        return 'Автоматический разбор остановлен ограничением ресурсов; требуется продолжить рассмотрение обращения.'
    if 'deadline' in reason or 'timeout' in reason or reason == 'inference_unavailable':
        return 'Надёжный итог автоматического разбора не получен за отведённое время.'
    if reason in ('semantic_requires_human', 'model_escalation', 'important_case', 'risk_requires_human'):
        return 'Для продолжения требуется проверка процедуры, риска или полномочий специалистом.'
    if 'knowledge' in reason or 'evidence' in reason or 'coverage' in reason:
        return 'Автоматический разбор не подтвердил достаточность оснований для решения.'
    if 'assessment' in reason or 'schema' in reason or 'arguments' in reason or 'request_id' in reason:
        return 'Ответ модели получен, но не прошёл проверку согласованности карточки.'
    if 'provider' in reason:
        return 'Сервис модели не вернул пригодный ответ; требуется продолжить рассмотрение обращения.'
    return 'Автоматическое решение не принято; требуется рассмотрение специалистом.'


def attempt_outcome(stage, claimed):
    if stage.get('not_sent'):
        return 'Запрос к модели не отправлялся: ' + explanation(stage.get('gate_reason', ''))
    if stage.get('http_status') == 429:
        return 'Провайдер ограничил частоту запросов (HTTP 429).'
    if stage.get('transport_error') in ('TimeoutError', 'ReadTimeout', 'ConnectTimeout'):
        return 'Ответ модели не получен за отведённое время.'
    if stage.get('http_status') and stage['http_status'] != 200:
        return 'Провайдер вернул ошибку HTTP ' + str(stage['http_status']) + '.'
    if isinstance(stage.get('raw_card'), dict):
        return ('Ответ получен и принят автоматически.' if stage.get('automatic_resolution') else
                'Ответ получен; автоматическое решение не принято. ' + explanation(stage.get('gate_reason', '')))
    return 'Запрос начат, подтверждённого результата нет.' if claimed else 'Зарезервировано; отправка запроса не подтверждена.'


def build(request, base, attempts, reason):
    """Return a manual card and an explicitly attributed operator packet."""
    card = copy.deepcopy(base)
    # A backend-prepared operator proposal is distinct from a failed model's
    # not_analyzed result (that status requires empty facts and citations).
    card.update(request_id=request['request_id'], status='proposed', mode='escalate', needs_human_review=True, draft_reply=None)
    detail = explanation(reason)
    card['summary'] = ('Запрос клиента: «' + request['ticket_text'][:1400] + '». ' + detail)[:2000]
    # Quotations remain attributed to their speakers, including contradictions
    # and corrected periods. Never copy raw_card.known_facts into this list.
    facts = ['Текущее сообщение клиента: ' + request['ticket_text'][:2000]]
    seen = {request['ticket_text']}
    for message in reversed(request.get('history', [])):
        if message['text'] not in seen:
            speaker = 'Клиент ранее сообщил: ' if message['speaker'] == 'customer' else 'Оператор ранее написал: '
            facts.append(speaker + message['text'][:1500])
            seen.add(message['text'])
        if len(facts) >= 12:
            break
    card['known_facts'] = facts
    if not card.get('missing_data'):
        card['missing_data'] = ['Проверенное решение обращения и применимый следующий шаг специалиста.']
    existing_next = card.get('next_step', '')
    generic = ('Оператор проверяет исходное обращение', 'Специалист проверяет исходное обращение и основания эскалации.')
    card['next_step'] = ((existing_next + ' ') if existing_next and existing_next not in generic else '') + \
        'Проверить текущий запрос и переписку, сопоставить найденные статьи с ситуацией и определить следующий шаг. ' + detail
    card['next_step'] = card['next_step'][:2000]
    notes, sources, evidence, classifications = [], {}, {}, []
    for attempt in attempts:
        stage = attempt.get('diagnostics') or {}
        provider = attempt.get('provider') or stage.get('provider') or 'unknown'
        allowed = {}
        try:
            context = checked_context(stage['stage_context'], request)
            allowed = {s['source_id']: s for s in context['kb_snapshot']}
            for source in context['sources']:
                sources[source['source_id']] = {k: source[k] for k in ('source_id', 'version', 'title', 'excerpt')}
        except (ValueError, KeyError, TypeError):
            pass
        raw = stage.get('raw_card')
        # Revalidate persisted raw data against this input. Do not trust a
        # caller-supplied category_result, failed assessment, or base fallback.
        category = validated_model_category(raw, request, SCHEMA)
        if allowed and category is not None and provider in ('mistral', 'sol'):
            classifications.append(dict(value=category, source=provider,
                                        validation='card_schema_and_request_binding'))
        model_summary = raw.get('summary', '') if isinstance(raw, dict) else ''
        if isinstance(raw, dict) and isinstance(raw.get('evidence'), list):
            for item in raw['evidence']:
                if not isinstance(item, dict):
                    continue
                if not EVIDENCE.is_valid(item):
                    continue
                source = allowed.get(item['source_id'])
                keys = ('source_id', 'version', 'title', 'excerpt')
                if source and all(item.get(k) == source[k] for k in keys):
                    evidence[source['source_id']] = {k: source[k] for k in keys}
        notes.append(dict(provider=provider, outcome=attempt_outcome(stage, attempt.get('invocation_claimed', False)),
                          preliminary_summary=model_summary[:2000] if isinstance(model_summary, str) else ''))
    for provider in ('mistral', 'sol'):
        if not any(note['provider'] == provider for note in notes):
            notes.append(dict(provider=provider, outcome='Не вызывалась. ' + detail, preliminary_summary=''))
    # Surface technical failures in the card itself, not just in folded metadata.
    # Only trusted transport/status fields are used, never raw exception text.
    technical = []
    for attempt in attempts:
        stage = attempt.get('diagnostics') or {}
        if (stage.get('not_sent') or stage.get('transport_error')
                or (stage.get('http_status') and stage['http_status'] != 200)
                or str(stage.get('gate_reason', '')).startswith('provider_')):
            provider = attempt.get('provider') or stage.get('provider') or 'unknown'
            if provider in ('mistral', 'sol'):
                technical.append(provider + ': ' + attempt_outcome(stage, attempt.get('invocation_claimed', False)))
    if technical:
        technical_text = ' '.join(technical)
        card['summary'] = ('Запрос клиента: «' + request['ticket_text'][:900] + '». '
                           + technical_text + ' ' + detail)[:2000]
    # All citations must match the pinned approved snapshot exactly. They are
    # operator evidence candidates, not proof that a proposed answer is correct.
    card['evidence'] = list(evidence.values())
    risk = input_risk(request)
    classification = (dict(value=risk['category'], source='input_policy', rule=risk['rule'])
                      if risk else classifications[-1] if classifications else None)
    if classification is not None:
        card['category'] = classification['value']
    packet = dict(version=1, origin='backend', reason=detail,
                  reason_code=reason, technical_summary=technical,
                  task=request['ticket_text'], attempts=notes,
                  classification=classification,
                  source_candidates=list(sources.values()),
                  caution='Предварительные описания моделей требуют проверки. Найденная статья сама по себе не подтверждает применимость решения.')
    return card, packet
