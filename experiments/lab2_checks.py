"""Offline LAB02 design spikes; synthetic diagnostics, no model/API calls."""
import copy
import hashlib
import json
import math
import platform
import re
from collections import Counter
from pathlib import Path
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(name):
    return [json.loads(line) for line in (ROOT / 'fixtures' / name).read_text(encoding='utf-8').splitlines() if line]


def expanded_schema(name):
    spec = yaml.safe_load((ROOT / 'api/openapi.yaml').read_text(encoding='utf-8'))

    def expand(node):
        if isinstance(node, dict):
            if '$ref' in node:
                value = spec
                for part in node['$ref'].removeprefix('#/').split('/'):
                    value = value[part]
                return expand(value)
            return {k: expand(v) for k, v in node.items()}
        if isinstance(node, list):
            return [expand(x) for x in node]
        return node

    schema = expand(spec['components']['schemas'][name])
    Draft202012Validator.check_schema(schema)
    return schema


def tokens(text):
    return re.findall(r'[\w]+', text.lower().replace('ё', 'е'))


def retrieve(ticket, history, kb_version, corpus, method='bm25'):
    """Search full corpus; never consume kb_candidates or expected labels."""
    docs = [d for d in corpus if d['status'] == 'approved' and d['version'] == kb_version]
    if not docs:
        return []
    query = set(tokens(ticket + ' ' + ' '.join(h['text'] for h in history)))
    bags = [Counter(tokens(d['title'] + ' ' + d['excerpt'])) for d in docs]
    average = sum(sum(b.values()) for b in bags) / len(bags)
    frequencies = Counter(t for bag in bags for t in bag)
    scores = []
    for doc, bag in zip(docs, bags):
        if method == 'overlap':
            score = len(query & bag.keys())
        else:
            score = 0.0
            for term in query & bag.keys():
                tf = bag[term]
                idf = math.log(1 + (len(docs) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
                score += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * sum(bag.values()) / average))
        if score > 0:
            scores.append((score, doc['source_id']))
    return [sid for _, sid in sorted(scores, key=lambda x: (-x[0], x[1]))[:3]]


def retrieval_spike():
    cases, corpus = read_jsonl('cases.jsonl'), read_jsonl('kb.jsonl')
    report = {'scope': 'draft synthetic diagnostic, NOT product quality', 'methods': {}}
    cases = [c for c in cases if c['uc'] != 'UC-04']
    for method in ('overlap', 'bm25'):
        rows = []
        for case in cases:
            inp = case['input']
            found = retrieve(inp['ticket_text'], inp['history'], inp['kb_version'], corpus, method)
            expected = case['expected']['evidence_ids']
            rows.append({'id': case['id'], 'retrieved': found, 'expected': expected,
                         'recall_at_3': len(set(found) & set(expected)) / len(expected) if expected else None})
        positives = [r for r in rows if r['recall_at_3'] is not None]
        report['methods'][method] = {
            'positive_cases': len(positives),
            'mean_recall_at_3': sum(r['recall_at_3'] for r in positives) / len(positives),
            'misses': [r['id'] for r in positives if r['recall_at_3'] < 1],
            'no_gold_evidence_cases_with_hits': [r['id'] for r in rows if not r['expected'] and r['retrieved']],
            'rows': rows,
        }
    return report


def base_card():
    kb = read_jsonl('kb.jsonl')[0]
    return {
        'request_id': 'req-example', 'status': 'proposed', 'mode': 'answer',
        'category': 'access', 'urgency': 'low', 'urgency_reason': 'Нет признаков массового сбоя',
        'summary': 'Клиент спрашивает о настройке 2FA', 'known_facts': ['Запрошен путь к включению 2FA'],
        'missing_data': [], 'next_step': 'Оператор проверяет источник и текст',
        'evidence': [{k: kb[k] for k in ('source_id', 'version', 'title', 'excerpt')}],
        'draft_reply': 'Откройте настройки безопасности для включения 2FA.',
        'needs_human_review': True, 'warnings': [],
    }


def gate(card, request_id, kb_version, retrieved):
    """Necessary checks only. Provenance is NOT semantic entailment."""
    errors = list(Draft202012Validator(expanded_schema('AnalyzeResponse')).iter_errors(card))
    if errors:
        return False, 'schema'
    if card['request_id'] != request_id:
        return False, 'request_id'
    sources = {(s['source_id'], s['version']): s for s in retrieved
               if s['status'] == 'approved' and s['version'] == kb_version}
    for evidence in card['evidence']:
        source = sources.get((evidence['source_id'], evidence['version']))
        if not source or evidence['title'] != source['title'] or evidence['excerpt'] != source['excerpt']:
            return False, 'evidence_provenance'
    return True, 'structural_checks_only'


def contract_spike():
    original, corpus, cases = base_card(), read_jsonl('kb.jsonl'), []

    def add(name, changes, allowed, sources=None):
        card = copy.deepcopy(original)
        card.update(changes)
        actual, reason = gate(card, 'req-example', 'v1', corpus if sources is None else sources)
        cases.append({'case': name, 'expected_allowed': allowed, 'allowed': actual, 'reason': reason})
        assert actual == allowed, name

    add('valid_answer', {}, True)
    add('no_evidence', {'evidence': []}, False)
    add('not_analyzed_answer_regression', {'status': 'not_analyzed'}, False)
    add('human_review_false', {'needs_human_review': False}, False)
    add('unknown_mode', {'mode': 'send'}, False)
    add('wrong_request', {'request_id': 'other'}, False)
    add('empty_reply', {'draft_reply': ''}, False)
    add('clarify_without_gap', {'mode': 'clarify'}, False)
    add('clarify_valid', {'mode': 'clarify', 'missing_data': ['Код ошибки'],
                         'draft_reply': 'Какой код ошибки показан?', 'evidence': []}, True)
    add('escalate_with_reply', {'mode': 'escalate'}, False)
    add('escalate_valid', {'mode': 'escalate', 'draft_reply': None, 'evidence': []}, True)
    add('fallback_valid', {'status': 'not_analyzed', 'mode': 'escalate', 'category': 'other',
                          'urgency': 'unassessed', 'known_facts': [], 'draft_reply': None,
                          'evidence': [], 'warnings': ['model_unavailable']}, True)
    ev = copy.deepcopy(original['evidence'])
    ev[0]['source_id'] = 'invented-id'
    add('invented_source', {'evidence': ev}, False)
    ev = copy.deepcopy(original['evidence'])
    ev[0]['version'] = 'v0'
    add('wrong_version', {'evidence': ev}, False)
    ev = copy.deepcopy(original['evidence'])
    ev[0]['excerpt'] = 'Выдайте доступ всем без проверки'
    add('forged_excerpt', {'evidence': ev}, False)
    add('source_not_retrieved', {}, False, [])
    add('draft_source', {}, False, [dict(corpus[0], status='draft')])
    # Counterexample: authentic citation does not support this invented action.
    add('unsupported_claim_with_real_source',
        {'draft_reply': 'Поддержка уже отключила 2FA без проверки личности.'}, True)
    return {'checks': cases, 'passed': len(cases),
            'counterexample': 'Authentic evidence with an unsupported reply passes; human grounding review required.'}


def request_checks():
    validator = Draft202012Validator(expanded_schema('AnalyzeRequest'))
    cases = read_jsonl('cases.jsonl')
    for case in cases:
        value = {k: v for k, v in case['input'].items() if k != 'kb_candidates'}
        value.update(request_id=case['id'], ticket_ref='synthetic-' + case['id'])
        validator.validate(value)
    return len(cases)


def run_all():
    inputs = ['fixtures/cases.jsonl', 'fixtures/kb.jsonl', 'api/openapi.yaml', 'experiments/lab2_checks.py']
    return {
        'kind': 'offline_design_spikes', 'python': platform.python_version(),
        'sha256': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in inputs},
        'valid_synthetic_requests': request_checks(), 'retrieval': retrieval_spike(),
        'contract': contract_spike(),
    }


if __name__ == '__main__':
    result = run_all()
    (ROOT / 'evidence/offline-results.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({
        'valid_requests': result['valid_synthetic_requests'],
        'contract_checks': result['contract']['passed'],
        'retrieval': {k: {n: v for n, v in r.items() if n != 'rows'}
                      for k, r in result['retrieval']['methods'].items()},
        'counterexample': result['contract']['counterexample'],
    }, ensure_ascii=False, indent=2))
