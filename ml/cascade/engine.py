"""Rule-based cascade: each invocation executes at most ONE model attempt."""
import copy
import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path
from jsonschema import Draft202012Validator
from ml.models.baseline import fallback
from ml.cascade.security import untrusted_instruction_reason
from ml.cascade.retrieval import retrieve
from ml.cascade.providers import HTTPProvider, ProviderFailure, MODELS, pack_sources
from ml.cascade.assessment import validate_assessment, expand_sources
from ml.cascade.context import make_context, checked_context
from ml.cascade.cache import AnswerCache
from ml.tools.propose_card import execute_proposal
from ml.cascade.providers import build_payload


TOKEN_EXPOSURE_GUIDANCE = (
    'Передать инцидент уполномоченному специалисту. Памятка для ответа клиенту: '
    'удалите сообщение с токеном из публичного чата и доступные вам копии; '
    'попросите уполномоченного администратора немедленно отозвать раскрытый токен '
    'и создать замену, затем обновить интеграции. Удаление сообщения не отменяет '
    'компрометацию и не заменяет отзыв. Проверьте доступные журналы использования '
    'вместе со специалистом. Не присылайте значение токена в поддержку. '
    'Это рекомендации, а не сообщение о выполненных действиях; оператор проверяет '
    'порядок отзыва у поставщика. Клиенту автоматически ничего не отправлено.'
)


def token_exposure(request):
    text = unicodedata.normalize('NFKC', '\n'.join(
        [request['ticket_text']] + [m['text'] for m in request['history']])).casefold()
    return bool(re.search(r'токен\w*.{0,80}(публич|утек|раскры|опубликов)|публич\w*.{0,80}токен', text))


def input_policy(request):
    text = unicodedata.normalize('NFKC', '\n'.join([request['ticket_text']] + [m['text'] for m in request['history']])).casefold()
    patterns = (r'игнорир\w*.{0,80}(инструкц|правил)', r'ignore.{0,80}(instructions|rules)',
                r'(токен|парол|секрет).{0,80}(публич|утек|раскры|опубликов)',
                r'публич\w*.{0,80}(токен|парол|секрет)',
                r'у всех.{0,80}(недоступ|ошиб|пропал)',
                r'чуж\w*.{0,50}(аккаунт|парол|данн)', r'верни.{0,20}деньги')
    return 'risk_requires_human' if any(re.search(p, text) for p in patterns) else None


def missing_billing_procedure(request, sources):
    text = request['ticket_text'].casefold()
    money_issue = re.search(r'(сумм|начисл|списан).{0,50}(неправ|невер|ошиб|лишн)|неправ\w*.{0,30}сумм', text)
    relevant = ' '.join(s['excerpt'] for s in sources).casefold()
    return bool(money_issue and not re.search(r'расч[её]т|сумм|начисл|списан', relevant))


def semantic_rule(card, request, sources, assessment=None, check_coverage=True):
    """Narrow checks; not a general semantic verifier or confidence score."""
    missing = ' '.join(card['missing_data']).casefold()
    if isinstance(assessment, dict) and isinstance(assessment.get('questions'), list):
        for question in assessment['questions']:
            if isinstance(question, dict) and isinstance(question.get('missing_customer_fields'), list):
                missing += ' ' + ' '.join(str(field) for field in question['missing_customer_fields']).casefold()
    draft = (card['draft_reply'] or '').casefold()
    claims = '\n'.join([draft] + [fact.casefold() for fact in card['known_facts']
                                   if not fact.casefold().startswith(('клиент сообщает', 'со слов клиента'))])
    authority = (r'(?:вам|клиенту).{0,25}(?:предоставлен|выдан|назначен).{0,30}(?:доступ|прав|администратор)',
                 r'клиент является администратором', r'права администратора.{0,20}(?:выданы|предоставлены)',
                 r'я (?:назначил|выдал|предоставил).{0,30}(?:доступ|прав|роль)')
    if any(re.search(pattern, claims) for pattern in authority):
        return 'unsafe_authority_claim'
    secret = r'значени[ея] токена|сам токен|полный токен|парол[ья]|код (?:2fa|двухфактор)|cvv'
    if re.search(secret, missing) or re.search(r'(пришлите|укажите|сообщите|назовите).{0,80}(?:' + secret + ')', draft):
        return 'unsafe_secret_request'
    if not check_coverage:
        return None
    if card['mode'] != 'answer':
        return None
    if missing_billing_procedure(request, sources):
        return 'no_approved_billing_procedure'
    if not sources:
        return 'no_answer_evidence'
    return None


def supplement_sol_sources(context):
    """Keep ranked hits first, then other sources from the same approved snapshot."""
    combined = list(context['sources'])
    seen = {(item['source_id'], item['version']) for item in combined}
    supplemented = []
    for source in context['kb_snapshot']:
        key = (source['source_id'], source['version'])
        if key not in seen:
            combined.append(copy.deepcopy(source))
            seen.add(key)
            supplemented.append(source['source_id'])
    context['sources'] = combined
    if supplemented:
        context['retrieval'].setdefault('sol_snapshot_supplement_ids', []).extend(supplemented)


class CascadeEngine:
    mode = 'cascade'

    def __init__(self, kb, schema, gate, provider=None, trace_path=None, answer_cache=None):
        self.kb, self.schema, self.gate = kb, schema, gate
        self.provider = provider or HTTPProvider()
        self.trace_path = trace_path
        self.answer_cache = answer_cache

    def analyze_attempt(self, request, provider, timeout, job_id, attempt_id, stage_context=None, cache_scope=None):
        started = time.perf_counter()
        context = checked_context(stage_context, request) if stage_context is not None else make_context(self.kb, request)
        kb = context['kb_snapshot']
        sources = (context['sources'] if provider == 'sol' and context['sources'] else
                   retrieve(request['ticket_text'], request['history'], request['kb_version'], kb))
        context['sources'] = sources
        if provider == 'sol':
            # Also cover a recovered worker failure that never saved a Mistral result.
            supplement_sol_sources(context)
            sources = context['sources']
        expected = context['questions'] if provider == 'sol' and context['questions'] else None
        raw, assessment, usage, passed, reason = None, None, None, False, (input_policy(request) or untrusted_instruction_reason(request, sources))
        reply = {}
        packing = None
        skip = bool(reason)
        if not kb:
            reason, skip = 'no_current_knowledge_version', True
        if provider == 'sol' and not sources and not skip:
            reason, skip = 'no_sources_for_second_stage', True
        if provider not in MODELS:
            raise ValueError('unknown_provider')
        blocked = skip
        assessment_valid = False
        safe_candidate = False
        cache_key = None
        if not skip and self.answer_cache is not None and cache_scope is not None:
            policy_sources = [Path(__file__), *[Path(__file__).with_name(name) for name in
                ('assessment.py','providers.py','retrieval.py','cache.py','security.py')],
                Path(__file__).parents[1]/'tools/propose_card.py',
                Path(__file__).parents[1]/'models/baseline.py']
            policy = hashlib.sha256(b''.join(p.read_bytes() for p in policy_sources) +
                json.dumps(build_payload(provider, dict(request, request_id=""), [], self.schema),
                           ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            cache_key = AnswerCache.key(cache_scope, provider, request, context, self.schema, policy)
            cached = self.answer_cache.get(cache_key)
            if cached is not None:
                for field in ('raw_card', 'final_card'):
                    cached[field]['request_id'] = request['request_id']
                # Revalidate references and output against the current reservation.
                valid, _ = self.gate(cached['raw_card'], request['request_id'], request['kb_version'],
                                    cached['stage_context']['sources'])
                if valid and not semantic_rule(cached['raw_card'], request,
                                               cached['stage_context']['sources'], cached['assessment']):
                    cached.update(request_id=request['request_id'], job_id=job_id, attempt_id=attempt_id,
                                  usage=None, attempts=0, not_sent=True, cache_hit=True,
                                  latency_ms=round((time.perf_counter()-started)*1000,3),
                                  completion_id=None, http_status=None, billed_micro_usd=0)
                    cached['stage_context']['input_hash'] = context['input_hash']
                    return cached
        if not skip:
            try:
                trace_id = hashlib.sha256(job_id.encode()).hexdigest()[:32]
                provider_request = dict(request)
                if expected:
                    provider_request['_coverage_questions'] = expected
                sources, packing = pack_sources(provider, provider_request, sources, self.schema)
                context['sources'] = sources
                reply = self.provider(provider, provider_request, sources, self.schema, timeout, trace_id)
                raw, usage = reply['card'], reply['usage']
                assessment = reply.get('assessment')
                passed, reason = execute_proposal(raw, request, sources, self.schema, self.gate,
                    permissions=frozenset({'propose_card'}), deadline=started+timeout)
                if passed and (raw['status'] != 'proposed' or raw['needs_human_review'] is not True):
                    passed, reason = False, 'model_cannot_resolve_candidate'
                if passed:
                    problem = semantic_rule(raw, request, sources, assessment, check_coverage=False)
                    if problem:
                        passed, reason = False, problem
                    else:
                        safe_candidate = True
                if passed:
                    passed, reason = validate_assessment(assessment, raw, request, sources, expected)
                if passed:
                    assessment_valid = True
                    questions = assessment['questions']
                    context['questions'] = questions
                    conflict = any(question['coverage'] == 'conflict' for question in questions)
                    complete = all(question['coverage'] == 'complete' for question in questions)
                    customer_missing = any(question['missing_customer_fields'] for question in questions)
                    if assessment['requires_human'] or conflict:
                        passed, reason = False, 'semantic_requires_human'
                    elif raw['urgency'] in ('high', 'critical'):
                        passed, reason = False, 'important_case'
                    elif raw['mode'] == 'escalate':
                        passed, reason = False, 'model_escalation'
                    elif raw['mode'] == 'clarify':
                        reason = 'needs_customer_data'
                    elif complete and not customer_missing and not assessment['reasoning_issue']:
                        problem = semantic_rule(raw, request, sources, assessment)
                        if problem:
                            passed, reason = False, problem
                        else:
                            reason = 'complete_coverage' if raw['mode'] == 'answer' else 'proposed_human_review'
                    elif customer_missing:
                        passed, reason = False, 'missing_customer_data_without_clarify'
                    elif provider == 'sol':
                        passed, reason = False, 'sol_incomplete_coverage'
                    else:
                        reasoning = (assessment['reasoning_issue']
                                     and all(question['source_ids'] and question['coverage'] in ('complete', 'partial')
                                             for question in questions))
                        passed, reason = False, 'reasoning_review' if reasoning else 'incomplete_coverage'
            except ProviderFailure as error:
                reason = error.reason
                reply = dict(error.metadata, not_sent=error.not_sent, http_status=error.http_status)
                usage = reply.get('usage')
            except (ValueError, KeyError, IndexError, TypeError):
                reason = 'invalid_model_card'
        latency = round((time.perf_counter() - started) * 1000, 3)
        if latency > timeout * 1000:
            passed, reason = False, 'attempt_deadline_exceeded'
        # A model's escalation is advisory. Sol gets a chance even if Mistral
        # produced malformed metadata, found no lexical hits, or requested human review.
        # Verified input/access guards, an absent approved KB and resource limits remain hard stops.
        next_stage = provider == 'mistral' and not blocked and not passed
        if next_stage:
            if assessment_valid:
                expanded = expand_sources(request, sources, assessment, kb)
                context['sources'] = expanded['sources']
                context['retrieval'] = {key: value for key, value in expanded.items() if key != 'sources'}
                if reason == 'incomplete_coverage':
                    reason = 'additional_evidence' if expanded['new_source_ids'] else 'no_new_evidence'
            # Retain ranked/new hits first, then let Sol inspect the rest of the
            # pinned approved snapshot within its existing payload-size bound.
            supplement_sol_sources(context)

        latency = round((time.perf_counter() - started) * 1000, 3)
        automatic = passed and raw['mode'] in ('answer', 'clarify')
        final_card = fallback(request['request_id'], reason or 'manual_review')
        if blocked and reason == 'risk_requires_human' and token_exposure(request):
            # Trusted incident guidance for the operator, not a model answer or KB quote.
            # Keep escalation and draft_reply=null; never claim revocation was executed.
            final_card['summary'] = 'Сообщение о публичной утечке токена: требуется специалист.'
            final_card['next_step'] = TOKEN_EXPOSURE_GUIDANCE
        if automatic:
            final_card = dict(raw, status='resolved', needs_human_review=False)
        elif (provider == 'sol' and safe_candidate and assessment_valid
              and reason in ('semantic_requires_human', 'model_escalation', 'important_case')):
            final_card = copy.deepcopy(raw)
            final_card.update(mode='escalate', draft_reply=None,
                              next_step='Специалист проверяет исходное обращение и основания эскалации.')
            final_card['warnings'] = (final_card['warnings'][:19] + [reason])
        result = {'provider': provider, 'model': MODELS[provider], 'request_id': request['request_id'],
                  'attempt_id': attempt_id, 'job_id': job_id, 'raw_card': raw,
                  'final_card': final_card, 'automatic_resolution': automatic,
                  'retrieved_ids': [s['source_id'] for s in sources], 'usage': usage,
                  'latency_ms': latency, 'schema_valid': isinstance(raw, dict) and Draft202012Validator(self.schema).is_valid(raw),
                  'gate_passed': passed, 'gate_reason': reason, 'cascade_next': next_stage,
                  'route_reason': reason, 'assessment': assessment, 'stage_context': context,
                  'context_packing': packing,
                  'retrieval': context['retrieval'], 'attempts': 1, 'http_status': reply.get('http_status'),
                  'returned_model': reply.get('returned_model'), 'completion_id': reply.get('completion_id'),
                  'finish_reason': reply.get('finish_reason'), 'not_sent': blocked or reply.get('not_sent', False),
                  'transport_error': reply.get('transport_error'), 'attempt_timeout_seconds': timeout,
                  'billed_micro_usd': None, 'inference_skipped': blocked}
        result['cache_hit'] = False
        if cache_key is not None:
            self.answer_cache.put(cache_key, result)
        return result

    def analyze(self, request, **kwargs):
        # Prevent accidental unreserved /v1/analyze or in-process paid calls.
        raise ValueError('Cascade requires a durable queue reservation and provider stage')
