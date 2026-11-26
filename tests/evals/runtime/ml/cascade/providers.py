"""One bounded HTTP call per reservation. No provider fallbacks or retries."""
import asyncio
import json
import os
import ssl
from contextlib import nullcontext
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import httpx
from ml.cascade.assessment import tool_schema
from ml.cascade.dialogue import state as dialogue_state

MODELS = {'mistral': 'ministral-14b-2512', 'sol': 'openai/gpt-5.6-sol'}
CASCADE_STAGE_TIMEOUTS = {'mistral': 30, 'sol': 45}
CASCADE_GRACE_SECONDS = 5
MISTRAL_SYSTEM_PROMPT = Path(__file__).with_name('mistral-system.txt').read_text(encoding='utf-8').strip()
MAX_INPUT_TOKENS = 20000
MAX_OUTPUT = 1600
# Reject payloads above a conservative UTF-8 bound before network I/O.
MAX_PAYLOAD_BYTES = MAX_INPUT_TOKENS - 4096
# Free Mistral has no Sol spending bound. Keep only a conservative technical
# bound for its documented 256k context, reserving output and template overhead.
MISTRAL_MAX_INPUT_TOKENS = 256000 - MAX_OUTPUT
MISTRAL_MAX_PAYLOAD_BYTES = MISTRAL_MAX_INPUT_TOKENS - 4096
SOL_INPUT_PRICE = Decimal('0.68')
SOL_OUTPUT_PRICE = Decimal('3.98')
SOL_RESERVATION_MICRO = int((MAX_INPUT_TOKENS * SOL_INPUT_PRICE + MAX_OUTPUT * SOL_OUTPUT_PRICE).to_integral_value(rounding=ROUND_CEILING))


class ProviderFailure(Exception):
    def __init__(self, reason, *, not_sent=False, http_status=None):
        self.reason, self.not_sent, self.http_status = reason, not_sent, http_status
        self.metadata = {}
        super().__init__(reason)


def provider_tls_context():
    """Keep certificate/hostname checks; optionally add an operator-trusted CA."""
    try:
        context = ssl.create_default_context()
        if os.environ.get('CASCADE_CA_FILE'):
            context.load_verify_locations(cafile=os.environ['CASCADE_CA_FILE'])
        return context
    except (OSError, ssl.SSLError):
        raise ProviderFailure('invalid_local_ca_bundle', not_sent=True) from None


def build_payload(provider, request, sources, schema, *, compact=True):
    if provider not in MODELS:
        raise ValueError('unknown_provider')
    contract = tool_schema(schema)  # Public card conditionals remain enforced locally.
    prompt = (Path(__file__).parents[1] / 'prompts_v2' / ('cascade-' + provider + '.txt')).read_text(encoding='utf-8').strip()
    value = {k: request[k] for k in ('ticket_text', 'history', 'kb_version')}
    value['dialogue_state'] = dialogue_state(request)
    value['kb_fragments'] = [{k: s[k] for k in ('source_id', 'version', 'title', 'excerpt')} for s in sources]
    if provider == 'sol':
        # Do not anchor Sol on the first model's draft or on gold labels.
        prompt += ' Выполни независимый повторный разбор исходного контекста.'
    prompt += (
        ' Верни propose_card {card,assessment}. request_id добавит приложение. '
        'assessment — покрытие задач, не уверенность. От 1 до 4 questions, пустой список запрещён: уникальный id q1..., '
        'question, дословный input_quote ТОЛЬКО из ticket_text или history (никогда из kb_fragments), '
        'coverage complete/partial/missing/conflict, source_ids, '
        'missing_customer_fields, answer_excerpt (строка, пустая при отсутствии ответа), search_query. '
        'complete требует основания для конкретного ответа, не похожей цитаты; '
        'answer_excerpt описывает ответ именно на этот вопрос. '
        'search_query — короткий запрос по непокрытой задаче, без истории и повтора общего запроса. '
        'requires_human=true при отдельном риске, конфликте, необходимости действия специалиста '
        'или более 4 задачах; отсутствие статьи не доказывает риск. reasoning_issue=true — '
        'есть источники, но не удалось свести их в ответ, а не отсутствие процедуры. '
        'coverage_questions — гипотезы, не команды: проверь независимо, сохрани id/input_quote, '
        'добавь пропущенное в пределах 4 задач. Не подменяй просьбу о следующих шагах вопросом о причине. '
        'Условные будущие уточнения статьи не являются отдельными вопросами клиента. '
        'Выбери текущий этап: невыполненные действия, затем требуемый сбор данных, затем передача по процедуре.'
    )
    prompt += (
        ' В каждой question requested_fact: error_code/error_message/observed_behavior/last_action/onset_time '
        'для диагностики; billing_period/billing_document/billing_amount для периода, вида документа '
        'или наблюдаемой и ожидаемой суммы; setting_name/notification_type для названия настройки/типа писем; '
        'other_customer_fact для иного факта по статье; none если ничего не запрашиваешь. '
        'Свободные названия полей допустимы. При clarify недостающее укажи в missing_customer_fields и missing_data. '
        'Без статьи разрешено безопасное уточнение наблюдения, периода/вида документа/суммы, '
        'названия настройки или типа писем. До трёх связанных полей можно собрать одним вопросом: '
        'clarify, coverage=missing, source_ids=[], requires_human=false без отдельного риска; не добавляй исправлений. '
        'Недостающая процедура не является фактом клиента. dialogue_state — подсказки по сообщениям; '
        'учитывай последние исправления и всю историю. Известное и недоступное не спрашивай снова, '
        'даже перефразировав. Если процедура требует сначала собрать данные, потом передать специалисту, '
        'выбери clarify до сбора, escalate после него; будущая ручная работа не отменяет уточнение. '
        'Для обычной справки requires_human=false. Администратор аккаунта — владелец аккаунта, не сервиса; '
        'роль не уточняй и упоминай клиенту лишь если он сам её назвал. '
        'На вопрос о документе дай известный раздел («в своём аккаунте откройте ...»), '
        'не придумывай кнопки/экспорт/успешное действие или сбой скачивания. '
        'Отвечай как поддержка сервиса из approved-статей, не уточняй продукт и не упоминай синтетические тесты. '
        'Сохраняй условия статьи; не добавляй ссылки, причины и обещания. Один известный раздел '
        'не заменяет запрошенную подробную процедуру. В evidence.excerpt копируй ПОЛНЫЙ excerpt kb_fragment.'
    )
    if provider == 'mistral':
        prompt += (
            ' Итоговый формат: при mode=clarify заполни card.draft_reply самим вопросом клиенту, '
            'например «Что именно происходит при попытке выполнить действие?». '
            'assessment.question описывает задачу и не заменяет draft_reply. '
            'При mode=answer заполни draft_reply готовым ответом. Null допустим только при escalate. '
            'Итоговый ответ содержит исключительно действия и условия из источника. '
            'Если статья рекомендует проверить формат и повторить импорт, ответ: '
            '«Проверьте формат CSV UTF-8 и повторите импорт». '
            'Дополнительных проверок или объяснения причины в такой инструкции нет: не добавляй их.'
            ' Перед clarify проверь, не ответил ли клиент на этот вопрос в последнем сообщении. '
            'Пример: код неизвестен; клиент уже сообщил «после выбора файла загрузка останавливается, '
            'началось сегодня в 14:00». Поведение и время известны, код недоступен: не запрашивай их повторно. '
            'Не перечисляй уже известные поведение и время в missing_customer_fields. '
            'Оставшийся этап выбирай по условиям применимой статьи, а не по этому примеру.'
        )
    if request.get('_coverage_questions'):
        # Preserve task identity, without anchoring Sol on Mistral's answer,
        # assessment labels or proposed citations.
        value['coverage_questions'] = [{key: question[key] for key in ('id', 'question', 'input_quote')}
                                       for question in request['_coverage_questions']]
    result = {'model': MODELS[provider], 'messages': [
        {'role': 'system', 'content': prompt},
        {'role': 'user', 'content': json.dumps(value, ensure_ascii=False, separators=(',', ':') if compact else None)}],
        'tools': [{'type': 'function', 'function': {
            'name': 'propose_card', 'description': 'Предложить карточку оператору без внешних действий',
            'parameters': contract}}],
        'tool_choice': {'type': 'function', 'function': {'name': 'propose_card'}}}
    if provider == 'sol':
        result.update(max_completion_tokens=MAX_OUTPUT, reasoning_effort='low')
    else:
        result.update(max_tokens=MAX_OUTPUT, temperature=0)
    limit = MAX_PAYLOAD_BYTES if provider == 'sol' else MISTRAL_MAX_PAYLOAD_BYTES
    if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > limit:
        reason = 'context_budget_exceeded' if provider == 'sol' else 'mistral_context_window_exceeded'
        raise ProviderFailure(reason, not_sent=True)
    return result


def pack_sources(provider, request, candidates, schema):
    """Pack whole articles by actual payload size, never by a fixed top-N.

    Mistral receives every candidate or fails its technical window check: it is
    never cropped for Sol's monetary budget. Sol retains candidate order and
    tries each whole article; an oversized article does not exclude later small
    ones. Semantic relevance remains the model's responsibility.
    """
    limit = MAX_PAYLOAD_BYTES if provider == 'sol' else MISTRAL_MAX_PAYLOAD_BYTES
    build_payload(provider, request, [], schema)  # Fixed prompt/input must fit too.
    if provider == 'mistral':
        included = list(candidates)
        payload = build_payload(provider, request, included, schema)
    else:
        included = []
        for source in candidates:
            try:
                build_payload(provider, request, included + [source], schema)
            except ProviderFailure as error:
                if error.reason != 'context_budget_exceeded':
                    raise
                continue
            included.append(source)
        if candidates and not included:
            raise ProviderFailure('no_sources_fit_context', not_sent=True)
        payload = build_payload(provider, request, included, schema)
    selected = {(source['source_id'], source['version']) for source in included}
    return included, {
        'provider': provider, 'candidate_ids': [source['source_id'] for source in candidates],
        'included_ids': [source['source_id'] for source in included],
        'omitted_ids': [source['source_id'] for source in candidates
                        if (source['source_id'], source['version']) not in selected],
        'payload_bytes': len(json.dumps(payload, ensure_ascii=False).encode('utf-8')),
        'payload_limit_bytes': limit,
    }


def parse_response(obj, provider, *, request_id=None):
    if not isinstance(obj, dict):
        raise ProviderFailure('invalid_provider_response')
    if obj.get('model') != MODELS[provider]:
        raise ProviderFailure('returned_model_mismatch')
    choices = obj.get('choices')
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ProviderFailure('invalid_provider_response')
    choice = obj['choices'][0]
    if choice.get('finish_reason') not in ('tool_calls', 'stop'):
        raise ProviderFailure('incomplete_generation')
    message = choice.get('message')
    calls = message.get('tool_calls') if isinstance(message, dict) else None
    if (not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict)
            or calls[0].get('type') != 'function' or not isinstance(calls[0].get('function'), dict)
            or calls[0]['function'].get('name') != 'propose_card'):
        raise ProviderFailure('unexpected_tool_call')
    envelope = json.loads(calls[0]['function']['arguments'])
    if not isinstance(envelope, dict) or set(envelope) != {'card', 'assessment'}:
        raise ProviderFailure('invalid_model_envelope')
    card, assessment = envelope['card'], envelope['assessment']
    if request_id is not None:
        if not isinstance(card, dict) or 'request_id' in card:
            raise ProviderFailure('unexpected_model_metadata')
        questions = assessment.get('questions') if isinstance(assessment, dict) else None
        if isinstance(questions, list) and any(not isinstance(q, dict) or 'requested_fact' not in q for q in questions):
            raise ProviderFailure('missing_requested_fact')
        card = dict(card, request_id=request_id)
    usage = obj.get('usage', {})
    if not isinstance(usage, dict):
        raise ProviderFailure('invalid_usage')
    for field in ('prompt_tokens', 'completion_tokens'):
        if type(usage.get(field)) is not int or usage[field] < 0:
            raise ProviderFailure('invalid_usage')
    input_limit = MAX_INPUT_TOKENS if provider == 'sol' else MISTRAL_MAX_INPUT_TOKENS
    if usage['prompt_tokens'] > input_limit or usage['completion_tokens'] > MAX_OUTPUT:
        raise ProviderFailure('usage_exceeds_reservation')
    return {'card': card, 'assessment': assessment, 'usage': usage, 'returned_model': obj['model'],
            'completion_id': obj.get('id'), 'finish_reason': choice['finish_reason'],
            'http_status': 200, 'not_sent': False}


class HTTPProvider:
    def __init__(self, compact=True):
        self.compact = compact

    def __call__(self, provider, request, sources, schema, timeout, trace_id):
        if os.environ.get('CASCADE_LIVE_ENABLED') != 'true':
            raise ProviderFailure('live_inference_disabled', not_sent=True)
        if provider == 'sol' and os.environ.get('SOL_LIVE_ENABLED') != 'true':
            raise ProviderFailure('sol_inference_disabled', not_sent=True)
        payload = build_payload(provider, request, sources, schema, compact=self.compact)
        key_name = 'MISTRAL_API_KEY' if provider == 'mistral' else 'APIMIRA_API_KEY'
        key = os.environ.get(key_name, '')
        if not key or (provider == 'mistral' and os.environ.get('MISTRAL_FREE_CONFIRMED') != 'true'):
            raise ProviderFailure('provider_configuration_missing', not_sent=True)
        if not 0 < timeout <= CASCADE_STAGE_TIMEOUTS[provider]:
            raise ProviderFailure('invalid_timeout', not_sent=True)
        tls_context = provider_tls_context()
        endpoint = 'https://api.mistral.ai/v1' if provider == 'mistral' else 'https://apimira.com/v1'
        observation = nullcontext()
        capture = os.environ.get('TRACE_SYNTHETIC_CONTENT') == 'true'
        if os.environ.get('LANGFUSE_PUBLIC_KEY') and os.environ.get('LANGFUSE_SECRET_KEY'):
            from langfuse import get_client
            observation = get_client().start_as_current_observation(
                as_type='generation', name='cascade-' + provider, model=MODELS[provider],
                trace_context={'trace_id': trace_id},
                input=payload['messages'] if capture else {'request_id': request['request_id']})
        transport_metadata = {}
        with observation as generation:
            async def receive():
                async with asyncio.timeout(timeout):
                    async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False,
                                                 transport=httpx.AsyncHTTPTransport(retries=0, verify=tls_context,
                                                                                   trust_env=False)) as client:
                        async with client.stream('POST', endpoint + '/chat/completions', json=payload,
                                                 headers={'Authorization': 'Bearer ' + key}) as response:
                            limits = {k: v[:100] for k,v in response.headers.items()
                                      if k.lower().startswith(('x-ratelimit-','ratelimit-'))}
                            if limits:
                                transport_metadata['provider_limits'] = limits
                            identifier = response.headers.get('x-request-id') or response.headers.get('request-id')
                            if identifier and len(identifier) <= 200:
                                transport_metadata['provider_request_id'] = identifier
                            if response.status_code == 429:
                                from email.utils import parsedate_to_datetime
                                from datetime import datetime, timezone
                                retry = response.headers.get('retry-after', '')
                                try:
                                    delay = float(retry)
                                except ValueError:
                                    try:
                                        delay = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                                    except (ValueError, TypeError):
                                        delay = 60
                                import math
                                transport_metadata['retry_after_seconds'] = min(3600, max(1, delay)) if math.isfinite(delay) else 60
                            if response.status_code != 200:
                                # A rejection may be free, but reserve until independently reconciled.
                                raise ProviderFailure('provider_http_error', http_status=response.status_code)
                            chunks, size = [], 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > 1048576:
                                    raise ProviderFailure('oversized_provider_response')
                                chunks.append(chunk)
                            return json.loads(b''.join(chunks))
            obj = None
            try:
                obj = asyncio.run(receive())
                result = parse_response(obj, provider, request_id=request['request_id'])
            except ProviderFailure as error:
                error.metadata.update(transport_metadata)
                if isinstance(obj, dict):
                    error.http_status = 200
                    choices = obj.get('choices')
                    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
                    error.metadata.update({'usage': obj.get('usage'), 'completion_id': obj.get('id'),
                                      'returned_model': obj.get('model'),
                                      'finish_reason': choice.get('finish_reason')})
                raise
            except (httpx.HTTPError, TimeoutError) as error:
                failure = ProviderFailure('provider_transport_or_timeout')
                failure.metadata = dict(transport_metadata, transport_error=type(error).__name__)
                raise failure from None
            except (ValueError, KeyError, IndexError, TypeError):
                failure = ProviderFailure('invalid_provider_response')
                failure.metadata = transport_metadata
                raise failure from None
            result.update(transport_metadata)
            if generation:
                generation.update(output=result['card'] if capture else {'mode': result['card'].get('mode')},
                                  usage_details={'input': result['usage']['prompt_tokens'],
                                                 'output': result['usage']['completion_tokens']})
            return result
