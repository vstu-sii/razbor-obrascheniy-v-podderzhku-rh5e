"""Recalculate the fresh iteration offline; no model API calls."""
import hashlib
import argparse
from collections import Counter
import json
from pathlib import Path
from evaluate import DATASET, EVIDENCE, read_jsonl, score, check_floors

def digest(path):
    return hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()

def check():
    parser = argparse.ArgumentParser()
    parser.add_argument('--require-targets', action='store_true')
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    evidence = EVIDENCE / 'iteration-v2'
    config = json.loads((evidence / 'config.json').read_text(encoding='utf-8'))
    rows = read_jsonl(evidence / 'responses.jsonl')
    cases, kb = read_jsonl(DATASET / 'cases.jsonl'), read_jsonl(DATASET / 'kb.jsonl')
    assert config['total_attempted'] == len(rows) == len({row['case_id'] for row in rows}) == 38
    assert {row['case_id'] for row in rows} == set(config['selected_case_ids'])
    assert config['dataset_sha256'] == digest(DATASET / 'cases.jsonl')
    assert config['kb_sha256'] == digest(DATASET / 'kb.jsonl')
    assert config['schema_sha256'] == digest(here / 'response.schema.json')
    assert config['prompt_sha256'] == digest(here / 'prompt-v2.md')
    assert config['responses_sha256'] == digest(evidence / 'responses.jsonl')
    assert all(row['model'] == config['model'] for row in rows)
    assert all(row.get('usage') for row in rows if row['http_status'] == 200)
    assert all(row['request_id'] == 'Q006-' + row['case_id'] and not row.get('cost_violation') for row in rows)
    assert all(row['prompt_sha256'] == config['prompt_sha256'] for row in rows)
    by_id = {case['id']: case for case in cases}
    assert all(row['candidate_proposal_included'] == (by_id[row['case_id']]['candidate_proposal'] is not None) for row in rows)
    report = score(rows, cases, kb)
    comparison = json.loads((EVIDENCE / 'iteration-comparison.json').read_text(encoding='utf-8'))
    assert report['models'] == comparison['new']
    assert score(read_jsonl(EVIDENCE / 'reviewed-responses.jsonl'), cases, kb)['models'] == comparison['old']
    floors = json.loads((here / 'iteration-targets.json').read_text(encoding='utf-8'))
    violations = check_floors(report, floors)
    if args.require_targets and violations:
        raise SystemExit('\n'.join(violations))
    snapshot = json.loads((here / 'iteration-snapshot-floors.json').read_text(encoding='utf-8'))
    assert not check_floors(report, snapshot)
    assert check_floors(score([dict(row, raw_card=None) for row in rows], cases, kb), floors)
    assessments = read_jsonl(evidence / 'output-review.jsonl')
    summary = json.loads((evidence / 'output-review-summary.json').read_text(encoding='utf-8'))
    assert len(assessments) == len({row['case_id'] for row in assessments}) == 38
    assert summary['source_response_sha256'] == digest(evidence / 'responses.jsonl')
    assert summary['source_gold_sha256'] == digest(DATASET / 'cases.jsonl')
    assert summary['source_kb_sha256'] == digest(DATASET / 'kb.jsonl')
    assert summary['assessment_sha256'] == digest(evidence / 'output-review.jsonl')
    assert summary['decisions'] == dict(Counter(row['decision'] for row in assessments))
    actual = {row['case_id']:row for row in rows}
    fields = ('factual_correctness','required_facts_complete','tone','operator_usability')
    for item in assessments:
        assert item['reviewer']=='Codex' and item['reviewer_type']=='agent'
        assert not item['independent_human_review'] and item['rationale']
        assert item['request_id']==actual[item['case_id']]['request_id']
        if actual[item['case_id']].get('raw_card') is None:
            assert item['decision']=='not_received' and all(item[field] is None for field in fields)
        else:
            assert all(type(item[field]) is int and 0<=item[field]<=2 for field in fields)
    received = [item for item in assessments if item['decision']!='not_received']
    assert len(received)==summary['received_and_reviewed']==34
    for field in fields:
        assert summary['statistics'][field]['distribution']=={str(value):sum(item[field]==value for item in received) for value in range(3)}
        assert summary['statistics'][field]['mean']==sum(item[field] for item in received)/34
    print(json.dumps({'attempted_inputs':38, 'received_responses':sum(row['http_status']==200 for row in rows),
                      'targets_passed':not violations, 'target_violations':violations, 'snapshot_check':'passed',
                      'api_calls_in_this_check':0}))

if __name__ == '__main__':
    check()
