"""Bounded model assessments and one lexical expansion, without semantic certainty.

The public card contract remains separate. These checks verify the declared
questions and their references; they cannot prove that a model found every
meaningful question in the customer's text or that a source entails an answer.
"""
import copy
import re
import unicodedata

from jsonschema import Draft202012Validator

from ml.models.baseline import retrieve, tokenize


QUESTION_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['id', 'question', 'input_quote', 'coverage', 'source_ids',
                 'missing_customer_fields', 'answer_excerpt', 'search_query'],
    'properties': {
        'id': {'type': 'string', 'pattern': r'^q[1-9][0-9]*$', 'maxLength': 16},
        'question': {'type': 'string', 'minLength': 1, 'maxLength': 300},
        'input_quote': {'type': 'string', 'minLength': 1, 'maxLength': 500},
        'coverage': {'type': 'string', 'enum': ['complete', 'partial', 'missing', 'conflict']},
        'source_ids': {
            'type': 'array', 'uniqueItems': True,
            'items': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        },
        'missing_customer_fields': {
            'type': 'array', 'maxItems': 3, 'uniqueItems': True,
            'items': {'type': 'string', 'minLength': 1, 'maxLength': 200},
        },
        'answer_excerpt': {'type': 'string', 'maxLength': 1000},
        'search_query': {'type': 'string', 'maxLength': 250},
    },
}
ASSESSMENT_SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['questions', 'requires_human', 'reasoning_issue'],
    'properties': {
        'questions': {'type': 'array', 'minItems': 1, 'maxItems': 4,
                      'items': QUESTION_SCHEMA},
        'requires_human': {'type': 'boolean'},
        'reasoning_issue': {'type': 'boolean'},
    },
}
_ASSESSMENT_VALIDATOR = Draft202012Validator(ASSESSMENT_SCHEMA)


def reference_tokens(text):
    """Input references tolerate typography; KB evidence remains exact elsewhere."""
    return tuple(tokenize(unicodedata.normalize('NFKC', text).casefold()))


def input_reference_present(quote, texts):
    needle=reference_tokens(quote)
    # Caller supplies current text first; the dialog itself is history then current.
    haystack=reference_tokens(' '.join(texts[1:]+texts[:1]))
    return bool(needle) and any(haystack[i:i+len(needle)]==needle
                               for i in range(len(haystack)-len(needle)+1))


def safe_error_code_question(card, request, question):
    """Narrow exception: ask for an unknown error code, without inventing a fix.

    This does not authorize arbitrary source-free clarification or requests for
    account credentials. The existing independent secret/authority guard runs first.
    """
    context=' '.join([request.get('ticket_text','')]+[m.get('text','') for m in request.get('history',[])]).casefold()
    draft=(card.get('draft_reply') or '').casefold()
    fields=question['missing_customer_fields']
    def asks_code(text):
        return bool(re.search(r'\bкод\w*.{0,65}\bошиб\w*',text.casefold()))
    code=r'\b[a-zа-я]{1,5}[-_]?\d{2,}\b|\bкод\w*\s*[:№]?\s*\d+\b'
    return (card.get('mode')=='clarify' and bool(fields)
            and bool(re.search(r'ошиб\w*|сбо[йя]',context))
            and not re.search(code,context) and not re.search(code,draft)
            and all(asks_code(field) for field in fields) and asks_code(draft))


def tool_schema(card_schema):
    """Return the internal provider envelope without changing the public schema.

    Only the provider's deep copy drops top-level card conditionals; the caller
    must still run the original card schema and provenance/safety gates locally.
    """
    card_contract = copy.deepcopy(card_schema)
    card_contract.pop('allOf', None)
    # Only the backend can resolve a candidate without operator approval.
    card_contract['properties']['status'] = {'const': 'proposed'}
    card_contract['properties']['needs_human_review'] = {'const': True}
    return {
        'type': 'object',
        'additionalProperties': False,
        'required': ['card', 'assessment'],
        'properties': {'card': card_contract,
                       'assessment': copy.deepcopy(ASSESSMENT_SCHEMA)},
    }


def validate_assessment(assessment, card, request, sources, expected_questions=None):
    """Check bounded structure, declared-question continuity and local references.

    A valid incomplete assessment is usable for routing, even beside an answer
    card. It does not authorize accepting that card. ``expected_questions`` may
    be a prior question list or an assessment dict; all prior IDs and exact input
    quotes must remain present. A second pass may add missed questions within
    the same four-question bound; it may not drop or replace earlier questions.
    This function does not replace validation of the public card itself.
    """
    if not _ASSESSMENT_VALIDATOR.is_valid(assessment):
        return False, 'assessment_schema'
    if not isinstance(card, dict) or not isinstance(request, dict):
        return False, 'assessment_context'
    questions = assessment['questions']
    identifiers = [question['id'] for question in questions]
    if len(identifiers) != len(set(identifiers)):
        return False, 'assessment_question_ids'

    if expected_questions is not None:
        expected = (expected_questions.get('questions')
                    if isinstance(expected_questions, dict) else expected_questions)
        if (not isinstance(expected, list) or not expected
                or any(not isinstance(question, dict)
                       or not isinstance(question.get('id'), str)
                       or not isinstance(question.get('input_quote'), str)
                       for question in expected)):
            return False, 'assessment_question_set'
        expected_pairs = [(question['id'], reference_tokens(question['input_quote'])) for question in expected]
        pairs = [(question['id'], reference_tokens(question['input_quote'])) for question in questions]
        if (len(expected_pairs) != len(set(pair[0] for pair in expected_pairs))
                or not set(expected_pairs).issubset(pairs)):
            return False, 'assessment_question_set'

    texts = [request.get('ticket_text', '')]
    history = request.get('history', [])
    if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
        return False, 'assessment_context'
    texts.extend(item.get('text', '') for item in history)
    if any(not isinstance(text, str) for text in texts):
        return False, 'assessment_context'
    allowed = {source['source_id'] for source in sources
               if isinstance(source, dict) and isinstance(source.get('source_id'), str)
               and source.get('status') == 'approved'
               and source.get('version') == request.get('kb_version')}
    all_customer_fields = []
    for question in questions:
        if not question['question'].strip() or not question['input_quote'].strip():
            return False, 'assessment_input_quote'
        if not input_reference_present(question['input_quote'], texts):
            return False, 'assessment_input_quote'
        if any(source_id not in allowed for source_id in question['source_ids']):
            return False, 'assessment_source_provenance'
        customer_fields = question['missing_customer_fields']
        if any(not field.strip() for field in customer_fields):
            return False, 'assessment_customer_fields'
        if question['coverage'] == 'complete' and not question['source_ids']:
            return False, 'assessment_complete_without_sources'
        if customer_fields and not question['source_ids'] and not safe_error_code_question(card,request,question):
            return False, 'assessment_customer_fields_without_sources'
        if card.get('mode') == 'answer' and question['coverage'] == 'complete':
            draft = card.get('draft_reply')
            excerpt = question['answer_excerpt']
            # This explanatory field is not a second literal copy of draft_reply.
            # No string comparison can establish that the answer is semantically sound.
            if not excerpt.strip() or not isinstance(draft, str) or not draft.strip():
                return False, 'assessment_answer_excerpt'
            evidence = card.get('evidence', [])
            visible_ids = set()
            if isinstance(evidence, list):
                visible_ids = {item['source_id'] for item in evidence
                               if isinstance(item, dict)
                               and isinstance(item.get('source_id'), str)}
            if not set(question['source_ids']).intersection(visible_ids):
                return False, 'assessment_answer_evidence'
        all_customer_fields.extend(customer_fields)

    if card.get('mode') == 'clarify':
        if not all_customer_fields:
            return False, 'assessment_clarify_fields'
        missing_data = card.get('missing_data')
        # The schema and safety guard validate both lists. Requiring verbatim
        # duplication rejected ordinary paraphrases without improving provenance.
        if not isinstance(missing_data, list) or not missing_data:
            return False, 'assessment_clarify_fields'
        if any(question['coverage'] != 'complete'
               and not (question['coverage'] in ('partial', 'missing')
                        and question['missing_customer_fields'])
               for question in questions):
            return False, 'assessment_clarify_coverage'
    return True, 'assessment_structure_and_references_only'


def _query_fingerprint(query):
    """Ignore token order/case and duplicates, matching retrieval's token set."""
    return frozenset(tokenize(unicodedata.normalize('NFKC', query).casefold()))


def expand_sources(request, sources, assessment, kb):
    """Run at most three new BM25 queries once without a source-count limit.

    Only incomplete questions without missing customer facts can expand context.
    ``queries`` contains searches actually run; ``query_outcomes`` also explains
    skipped duplicates and exhausted limits. A returned new source is a lexical
    candidate, not a claim that its content is relevant or sufficient. The caller
    supplies the same immutable approved-KB snapshot used for the first stage.
    Inputs are never modified and no model calls occur here.
    """
    version = request['kb_version']
    combined = []
    seen_sources = set()
    for source in sources:
        key = (source['source_id'], source['version'])
        if (source.get('status') == 'approved' and source['version'] == version
                and key not in seen_sources):
            combined.append(copy.deepcopy(source))
            seen_sources.add(key)
    original_query = request['ticket_text'] + ' ' + ' '.join(
        message['text'] for message in request['history'])
    original_fingerprint = _query_fingerprint(original_query)
    seen_queries = {original_fingerprint}
    queries, new_source_ids, outcomes = [], [], []
    for question in assessment['questions']:
        if (question['coverage'] not in ('partial', 'missing')
                or question['missing_customer_fields']):
            continue
        query = question['search_query'].strip()
        fingerprint = _query_fingerprint(query)
        outcome = {'question_id': question['id'], 'query': query,
                   'source_ids': [], 'new_source_ids': []}
        if not fingerprint:
            outcome['status'] = 'empty_query'
        elif fingerprint == original_fingerprint:
            outcome['status'] = 'same_original_query'
        elif fingerprint in seen_queries:
            outcome['status'] = 'duplicate_query'
        elif len(queries) >= 3:
            outcome['status'] = 'query_limit'
        else:
            seen_queries.add(fingerprint)
            queries.append(query)
            candidates = retrieve(query, [], version, kb)
            outcome['source_ids'] = [source['source_id'] for source in candidates]
            for source in candidates:
                key = (source['source_id'], source['version'])
                if key not in seen_sources:
                    combined.append(copy.deepcopy(source))
                    seen_sources.add(key)
                    new_source_ids.append(source['source_id'])
                    outcome['new_source_ids'].append(source['source_id'])
            outcome['status'] = ('new_candidates' if outcome['new_source_ids']
                                 else 'no_new_candidates')
        outcomes.append(outcome)
    return {'sources': combined, 'queries': queries,
            'new_source_ids': new_source_ids, 'query_outcomes': outcomes}
