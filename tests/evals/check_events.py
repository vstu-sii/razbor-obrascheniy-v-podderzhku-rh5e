"""Validate event test evidence. Plan validation never means runtime acceptance."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate(plan, results, evidence_root):
    scenarios = plan['scenarios']
    ids = [item['id'] for item in scenarios]
    if len(ids) != 14 or set(ids) != {f'EVT-{i:02}' for i in range(1, 15)}:
        raise ValueError('Expected exactly EVT-01 through EVT-14')
    if plan['deadline_seconds'] != 55 or plan['max_model_calls'] != 2:
        raise ValueError('Unexpected task budget')
    for item in scenarios:
        if not item.get('stimulus') or not item.get('owner') or not item.get('checks'):
            raise ValueError('Incomplete scenario')
        for check in item['checks']:
            if not check.get('field') or check['op'] not in ('eq', 'lte') or 'value' not in check:
                raise ValueError('Invalid assertion')
    rows = results['results']
    if len(rows) != 14 or {row['id'] for row in rows} != set(ids):
        raise ValueError('Missing or duplicate runtime result')
    by_id = {row['id']: row for row in rows}
    passed, pending, failed = [], [], []
    root = Path(evidence_root).resolve()
    for scenario in scenarios:
        row = by_id[scenario['id']]
        status = row['status']
        if status == 'pending':
            if not row.get('reason') or row.get('observed') is not None or row.get('provenance') is not None:
                raise ValueError('Pending case must have reason and no invented observation')
            pending.append(row['id'])
            continue
        if status not in ('passed', 'failed'):
            raise ValueError('Unknown status')
        provenance = row['provenance']
        for key in ('application_revision', 'environment', 'executed_at', 'configuration', 'trace_path', 'trace_sha256'):
            if not provenance.get(key):
                raise ValueError('Missing runtime provenance: ' + key)
        stamp = datetime.fromisoformat(provenance['executed_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('Execution timestamp requires timezone')
        if not isinstance(provenance['configuration'], dict):
            raise ValueError('Configuration must record actual limits')
        if Path(provenance['trace_path']).is_absolute():
            raise ValueError('Trace path must be relative')
        trace = (root / provenance['trace_path']).resolve()
        if not trace.is_relative_to(root) or not trace.is_file():
            raise ValueError('Missing or out-of-bounds trace')
        raw = trace.read_bytes()
        if not raw.strip() or hashlib.sha256(raw).hexdigest() != provenance['trace_sha256']:
            raise ValueError('Empty or modified runtime trace')
        observed = row['observed']
        ok = True
        for check in scenario['checks']:
            if check['field'] not in observed:
                raise ValueError('Missing measured outcome: ' + check['field'])
            actual, expected = observed[check['field']], check['value']
            if check['op'] == 'eq':
                matches = type(actual) is type(expected) and actual == expected
            else:
                matches = type(actual) in (int, float) and 0 <= actual <= expected
            ok = ok and matches
        if (status == 'passed') != ok:
            raise ValueError('Reported status disagrees with measured outcomes')
        (passed if ok else failed).append(row['id'])
    return {'scope': 'event_transport_separate_from_model_evals', 'planned': len(ids),
            'executed': len(passed) + len(failed), 'passed': passed, 'failed': failed,
            'pending': pending, 'runtime_acceptance': len(passed) == len(ids),
            'api_calls_in_this_check': 0,
            'limitation': 'Evidence consistency only; independent review of traces is still required'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--results', type=Path, default=HERE / 'evidence/event-results.json')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--require-runtime', action='store_true')
    args = parser.parse_args()
    report = validate(load(HERE / 'event-scenarios.json'), load(args.results), args.results.parent)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    if args.require_runtime and not report['runtime_acceptance']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
