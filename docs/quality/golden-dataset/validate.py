"""Validate v2 schema, provenance, related-input grouping and edge coverage."""
import json
from pathlib import Path
from collections import Counter

HERE = Path(__file__).resolve().parent

def read(name):
    return [json.loads(line) for line in (HERE / name).read_text(encoding='utf-8').splitlines() if line.strip()]

def validate():
    cases, kb = read('cases.jsonl'), read('kb.jsonl')
    regressions = read('live-regressions.jsonl')
    decision = json.loads((HERE / 'team-review.json').read_text(encoding='utf-8'))
    manifest = json.loads((HERE / 'version-manifest.json').read_text(encoding='utf-8'))
    assert len(cases) == len({case['id'] for case in cases}) == 43
    assert len(regressions) == 6
    all_cases = cases + regressions
    assert len({case['id'] for case in all_cases}) == 49
    assert len({case['group_id'] for case in all_cases}) == 38
    assert {case['uc'] for case in all_cases} == {f'UC-0{i}' for i in range(1, 5)}
    assert not set(decision['excluded_case_ids']) & {case['id'] for case in all_cases}
    assert manifest['retained_records'] == 49 and manifest['expected_changes_applied'] == 19
    reviewers = [person['id'] for person in decision['reviewers']]
    assert len(set(reviewers)) == 3
    assert len({item['source_id'] for item in kb}) == len(kb) == 6
    assert sum('live_output_regression' in case['tags'] for case in cases) == 5
    base = {case['id']: case for case in cases if case['id'] == case['group_id']}
    for case in all_cases:
        assert case['dataset_version'] == 'v2.1-reviewed' and case['status'] == 'reviewed_synthetic'
        assert case['provenance']['review_status'] == 'approved' and case['provenance']['reviewers'] == reviewers
        assert case['provenance']['review_date'] == decision['review_date']
        assert case['provenance']['review_basis'] == decision['review_method']
        assert case['group_id'] and case['split'] == 'development'
        assert case['expected']['needs_human_review'] is True
        assert case['expected']['mode'] in {'answer', 'clarify', 'escalate'}
        assert case['input']['ticket_text'] and len(case['input']['history']) <= 50
        expected = set(case['expected']['evidence_ids'])
        sources = {item['source_id'] for item in kb if item['version'] == case['input']['kb_version'] and item['status'] == 'approved'}
        assert expected <= sources
        if case['expected']['mode'] == 'answer':
            assert expected
        if case['expected']['mode'] == 'clarify':
            assert case['expected']['missing_data']
        if case['id'] != case['group_id']:
            assert case['input'] == base[case['group_id']]['input']
            assert case['expected'] == base[case['group_id']]['expected']
        if 'live_output_regression' in case['tags']:
            failure = case['provenance']['source_failure']
            assert failure['case_id'] == case['group_id'] and failure['request_id'] and failure['started_utc']
    long = next(case for case in cases if case['id'] == 'EDGE-LONG-01')
    assert len(long['input']['history']) >= 12
    stale = next(case for case in cases if case['id'] == 'EDGE-STALE-01')
    assert stale['input']['kb_version'] == 'v2' and not stale['expected']['evidence_ids']
    assert all(item['version'] != 'v2' for item in kb if item['source_id'] in stale['input']['kb_candidates'])
    return {'cases': len(cases), 'live_failure_regressions': len(regressions),
            'reviewed_synthetic_records': 49, 'review_basis': decision['review_method'],
            'independent_blind_annotation_documented': decision['independent_blind_annotation_documented']}

if __name__ == '__main__':
    print(json.dumps(validate(), ensure_ascii=True))
