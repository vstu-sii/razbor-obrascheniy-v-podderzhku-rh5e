"""Score saved or new model results without confusing fallbacks with quality."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
from statistics import median

from jsonschema import Draft202012Validator

TASK = Path(__file__).resolve().parents[2]
DATASET = TASK / 'docs/quality/golden-dataset'
EVIDENCE = Path(__file__).parent / 'evidence'
SCHEMA = json.loads((Path(__file__).with_name('response.schema.json')).read_text(encoding='utf-8'))
VALIDATOR = Draft202012Validator(SCHEMA)

def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]

def provenance_gate(card, request_id, version, retrieved):
    if not isinstance(card, dict) or not VALIDATOR.is_valid(card):
        return False, 'schema'
    if card['request_id'] != request_id:
        return False, 'request_id'
    allowed = {(item['source_id'], item['version']): item for item in retrieved
               if item['status'] == 'approved' and item['version'] == version}
    for evidence in card['evidence']:
        source = allowed.get((evidence['source_id'], evidence['version']))
        if source is None or any(evidence[key] != source[key] for key in ('title', 'excerpt')):
            return False, 'evidence_provenance'
    return True, 'structure_and_provenance_only'

def fraction(numerator, denominator):
    return {'numerator': numerator, 'denominator': denominator,
            'value': numerator / denominator if denominator else None}

def macro_f1(expected, actual):
    classes = sorted(set(expected))
    scores = []
    for label in classes:
        tp = sum(e == label and a == label for e, a in zip(expected, actual))
        fp = sum(e != label and a == label for e, a in zip(expected, actual))
        fn = sum(e == label and a != label for e, a in zip(expected, actual))
        scores.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0)
    return {'classes_in_sample': classes, 'value': sum(scores) / len(scores) if scores else None}

def score(rows, cases, kb):
    by_id = {case['id']: case for case in cases}
    seen = set()
    details = []
    for row in rows:
        case_id = row['case_id']
        assert case_id in by_id, f'Unknown case: {case_id}'
        identity = (row['provider'], case_id)
        assert identity not in seen, f'Duplicate response: {identity}'
        seen.add(identity)
        case = by_id[case_id]
        card = row.get('raw_card')
        sources = [item for item in kb if item['source_id'] in row.get('retrieved_ids', [])]
        gate, reason = provenance_gate(card, row['request_id'], case['input']['kb_version'], sources)
        expected = case['expected']
        gold = set(expected['evidence_ids'])
        retrieved = set(row.get('retrieved_ids', [])[:3])
        details.append({
            'case_id': case_id, 'uc': case['uc'], 'provider': row['provider'],
            'http_ok': row.get('http_status') == 200,
            'transport_kind': row.get('transport_kind', 'external_http'),
            'completion_available': isinstance(card, dict),
            'schema_valid': isinstance(card, dict) and VALIDATOR.is_valid(card),
            'gate_passed': gate, 'gate_reason': reason,
            'mode_correct': isinstance(card, dict) and card.get('mode') == expected['mode'],
            'category_correct': isinstance(card, dict) and card.get('category') == expected['category'],
            'expected_mode': expected['mode'], 'actual_mode': card.get('mode') if isinstance(card, dict) else None,
            'expected_category': expected['category'], 'actual_category': card.get('category') if isinstance(card, dict) else None,
            'false_answer': isinstance(card, dict) and card.get('mode') == 'answer' and expected['mode'] != 'answer',
            'human_review_violation': isinstance(card, dict) and card.get('needs_human_review') is not True,
            'recall_at_3': len(gold & retrieved) / len(gold) if gold else None,
            'no_gold_evidence_with_hits': not gold and bool(retrieved),
            'evidence_citations': len(card.get('evidence', [])) if isinstance(card, dict) and isinstance(card.get('evidence'), list) else 0,
            'latency_ms': row.get('latency_ms'), 'usage': row.get('usage'),
            'billed_usd': row.get('billed_usd'),
            'operator_review': 'pending', 'semantic_grounding': None,
        })
    models = {}
    for provider in sorted({row['provider'] for row in rows}):
        selected = [item for item in details if item['provider'] == provider]
        n = len(selected)
        latencies = sorted(item['latency_ms'] for item in selected if isinstance(item['latency_ms'], (int, float)))
        recall = [item['recall_at_3'] for item in selected if item['recall_at_3'] is not None]
        nonanswer = [item for item in selected if item['expected_mode'] != 'answer']
        models[provider] = {
            'n': n, 'by_uc': dict(Counter(item['uc'] for item in selected)),
            **{name: fraction(sum(bool(item[name]) for item in selected), n) for name in
               ('completion_available', 'schema_valid', 'gate_passed', 'mode_correct', 'category_correct')},
            'http_ok': fraction(sum(item['http_ok'] for item in selected if item['transport_kind'] == 'external_http'),
                                sum(item['transport_kind'] == 'external_http' for item in selected)),
            'category_macro_f1': macro_f1([item['expected_category'] for item in selected], [item['actual_category'] for item in selected]),
            'false_answer_on_nonanswer_cases': fraction(sum(item['false_answer'] for item in nonanswer), len(nonanswer)),
            'human_review_violations': sum(item['human_review_violation'] for item in selected),
            'retrieval_recall_at_3': {'n_positive': len(recall), 'value': sum(recall) / len(recall) if recall else None},
            'no_gold_evidence_with_hits': sum(item['no_gold_evidence_with_hits'] for item in selected),
            'latency': {'n_observed': len(latencies), 'p50_ms': median(latencies) if latencies else None,
                        'empirical_p95_ms': latencies[math.ceil(.95 * len(latencies)) - 1] if latencies else None,
                        'minimum_30_observations': len(latencies) >= 30,
                        'sufficient_for_product_p95': False},
            'billing': {'n_observed': sum(item['billed_usd'] is not None for item in selected),
                        'sum_observed_usd': sum(item['billed_usd'] or 0 for item in selected)},
            'tokens': {'n_observed': sum(isinstance(item['usage'], dict) for item in selected),
                       'sum_observed_total': sum((item['usage'] or {}).get('total_tokens', 0) for item in selected)},
            'not_measured': ['operator_card_usability', 'semantic_fact_recall', 'unsupported_atomic_claim_rate',
                             'urgency_f1_without_gold_labels', 'tone', 'end_to_end_product_latency', 'production_cost_rub'],
            'by_case_failures': [item['case_id'] for item in selected if not (item['gate_passed'] and item['mode_correct'] and item['category_correct'])],
        }
    return {'scope': 'diagnostic_on_reviewed_synthetic_not_product_acceptance', 'models': models, 'details': details}
