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
DATASET = TASK / 'ml/data'
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

def check_floors(report, floors):
    violations = []
    for provider, expected in floors.items():
        actual = report['models'].get(provider)
        if actual is None:
            violations.append(f'{provider}: no observations')
            continue
        for metric, minimum in expected.items():
            value = actual[metric]['value'] if isinstance(actual[metric], dict) and 'value' in actual[metric] else actual[metric]
            if value is None or value < minimum:
                violations.append(f'{provider}/{metric}: {value} < {minimum}')
    return violations

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--context-mode', choices=['retrieved', 'fixture'], default='fixture')
    parser.add_argument('--responses', type=Path, default=EVIDENCE / 'baseline-independent-responses.jsonl')
    parser.add_argument('--output', type=Path, default=EVIDENCE / 'recalculated-report.json')
    parser.add_argument('--check-floors', action='store_true')
    parser.add_argument('--floors', type=Path, default=Path(__file__).with_name('baseline-floors.json'))
    args = parser.parse_args()
    cases = read_jsonl(DATASET / 'cases.jsonl')
    report = score(read_jsonl(args.responses), cases, read_jsonl(DATASET / 'kb.jsonl'))
    report['context_mode'] = args.context_mode
    if args.context_mode == 'fixture':
        for model in report['models'].values():
            model['provided_context_recall_at_3'] = model.pop('retrieval_recall_at_3')
        report['retrieval_quality_measured'] = False
    report['hashing_policy'] = 'SHA-256 of UTF-8 text with LF line endings'
    report['response_sha256'] = hashlib.sha256(args.responses.read_text(encoding='utf-8').encode('utf-8')).hexdigest()
    report['dataset_sha256'] = hashlib.sha256((DATASET / 'cases.jsonl').read_text(encoding='utf-8').encode('utf-8')).hexdigest()
    report['run_kind'] = 'historical_replay' if args.responses.resolve() == (EVIDENCE / 'historical-responses.jsonl').resolve() else 'provided_responses'
    report['coverage'] = {'unique_evaluated_cases': len({row['case_id'] for row in report['details']}), 'dataset_cases': len(cases),
                          'new_baseline_run': False if report['run_kind'] == 'historical_replay' else 'see response provenance'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.check_floors:
        floors = json.loads(args.floors.read_text(encoding='utf-8'))
        violations = check_floors(report, floors)
        if violations:
            raise SystemExit('\n'.join(violations))
    print(json.dumps({'run_kind': report['run_kind'], 'models': report['models']}, ensure_ascii=True, indent=2))

if __name__ == '__main__':
    main()
