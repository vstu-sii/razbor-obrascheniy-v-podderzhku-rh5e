"""Check immutable live evidence, human decision audit and versioned rescore."""
import hashlib
import json
from pathlib import Path
from evaluate import DATASET, EVIDENCE, check_floors, read_jsonl, score

def digest(path):
    return hashlib.sha256(path.read_text(encoding='utf-8').encode('utf-8')).hexdigest()

def check():
    config = json.loads((EVIDENCE / 'live-config.json').read_text(encoding='utf-8'))
    manifest = json.loads((DATASET / 'version-manifest.json').read_text(encoding='utf-8'))
    decision = json.loads((DATASET / 'team-review.json').read_text(encoding='utf-8'))
    cases = read_jsonl(DATASET / 'cases.jsonl')
    regressions = read_jsonl(DATASET / 'live-regressions.jsonl')
    kb = read_jsonl(DATASET / 'kb.jsonl')
    frozen = read_jsonl(EVIDENCE / 'frozen-original-cases.jsonl')
    old_by_id = {case['id']: case for case in frozen}
    raw = read_jsonl(EVIDENCE / 'generation-responses.jsonl')
    first = {row['case_id']: row for row in read_jsonl(EVIDENCE / 'live-responses.jsonl')}
    assert config['provider'] == 'mistral' and config['full_run_completed']
    assert len(raw) == len({row['case_id'] for row in raw}) == len(first) == 42
    assert set(first) == {row['case_id'] for row in raw} == set(config['selected_case_ids'])
    assert all(row['http_status'] == 200 and row['usage'] and row['model'] == config['model'] for row in raw)
    assert config['dataset_sha256'] == digest(EVIDENCE / 'frozen-original-cases.jsonl')
    for row in read_jsonl(EVIDENCE / 'connection-recovery.jsonl'):
        original = first[row['case_id']]
        assert original.get('http_status') is None and original['error'] in ('ConnectError', 'ConnectTimeout')
        assert original['request_id'] == row['request_id']
    for field, path in (
        ('dataset_sha256', DATASET / 'cases.jsonl'),
        ('live_regressions_sha256', DATASET / 'live-regressions.jsonl'),
        ('frozen_model_outputs_sha256', EVIDENCE / 'generation-responses.jsonl'),
        ('first_pass_sha256', EVIDENCE / 'live-responses.jsonl'),
        ('recovery_sha256', EVIDENCE / 'connection-recovery.jsonl')):
        assert manifest[field] == digest(path)
    audit = read_jsonl(DATASET / 'change-audit.jsonl')
    audit_by_id = {item['case_id']: item for item in audit}
    assert len(audit_by_id) == 58
    assert {item['case_id'] for item in audit if item['decision'] == 'excluded'} == set(decision['excluded_case_ids'])
    assert sum(item['decision'] == 'approved_with_changes' for item in audit) == 19
    assert sum(item['decision'] == 'approved_unchanged' for item in audit) == 30
    active = cases + regressions
    assert {row['id'] for row in active} == {item['case_id'] for item in audit if item['decision'] != 'excluded'}
    for case in active:
        item = audit_by_id[case['id']]
        original = old_by_id[case['group_id']]
        assert case['input'] == original['input'] and case['candidate_proposal'] == original['candidate_proposal']
        assert item['expected_before'] == original['expected']
        assert case['expected'] == dict(item['expected_before'], **item['expected_changes'])
        assert case['provenance']['review_status'] == 'approved'
    selected = {case['id'] for case in cases if case['id'] == case['group_id']}
    rows = read_jsonl(EVIDENCE / 'reviewed-responses.jsonl')
    assert len(rows) == len(selected) == 38
    assert rows == [row for row in raw if row['case_id'] in selected]
    mini = read_jsonl(EVIDENCE / 'live-mini-responses.jsonl')
    old_mini = read_jsonl(EVIDENCE / 'frozen-original-live-mini-responses.jsonl')
    assert len(mini) == 10 and mini == [row for row in old_mini if row['case_id'] in selected]
    floors = json.loads(Path(__file__).with_name('live-floors.json').read_text(encoding='utf-8'))
    assert not check_floors(score(mini, cases, kb), floors)
    actual = {row['case_id']: row for row in raw}
    for case in regressions:
        failure = case['provenance']['source_failure']
        source = actual[failure['case_id']]
        assert source['request_id'] == failure['request_id'] and source['model'] == failure['model']
    report = json.loads((EVIDENCE / 'reviewed-report.json').read_text(encoding='utf-8'))
    recalculated = score(rows, cases, kb)
    for name in ('n', 'schema_valid', 'gate_passed', 'mode_correct', 'category_correct', 'false_answer_on_nonanswer_cases'):
        assert recalculated['models']['free_api'][name] == report['models']['free_api'][name]
    assert all(row['decision'] == 'pending' for row in read_jsonl(EVIDENCE / 'human-review.jsonl'))
    agent = read_jsonl(EVIDENCE / 'agent-output-review.jsonl')
    agent_summary = json.loads((EVIDENCE / 'agent-output-review-summary.json').read_text(encoding='utf-8'))
    assert len(agent) == len({row['case_id'] for row in agent}) == 42
    assert {row['case_id'] for row in agent} == set(actual)
    assert agent_summary['source_response_sha256'] == digest(EVIDENCE / 'generation-responses.jsonl')
    assert agent_summary['source_gold_sha256'] == digest(DATASET / 'cases.jsonl')
    assert agent_summary['source_kb_sha256'] == digest(DATASET / 'kb.jsonl')
    assert agent_summary['source_schema_sha256'] == digest(Path(__file__).with_name('response.schema.json'))
    assert agent_summary['assessment_sha256'] == digest(EVIDENCE / 'agent-output-review.jsonl')
    assert agent_summary['reviewer_type'] == 'agent' and not agent_summary['human_model_output_review_completed']
    for row in agent:
        assert row['reviewer_type'] == 'agent' and row['reviewer'] == 'Codex'
        assert row['active_dataset'] == (row['case_id'] in selected)
        assert row['request_id'] == actual[row['case_id']]['request_id']
        assert row['rationale'] and not row['independent_human_judge']
        for field in ('factual_correctness', 'required_facts_complete', 'tone', 'operator_usability'):
            assert type(row[field]) is int and 0 <= row[field] <= 2
    from collections import Counter
    for label, subset in (('active_38', [row for row in agent if row['active_dataset']]),
                          ('excluded_4_diagnostic', [row for row in agent if not row['active_dataset']])):
        saved = agent_summary['groups'][label]
        assert saved['n'] == len(subset)
        assert saved['decisions'] == dict(Counter(row['decision'] for row in subset))
        for field, statistics in saved['rubric'].items():
            assert statistics['distribution_0_1_2'] == {str(value): sum(row[field] == value for row in subset) for value in range(3)}
            assert statistics['mean_score_out_of_2'] == sum(row[field] for row in subset) / len(subset)
    return {'original_live_responses': 42, 'offline_rescored_responses': 38,
            'active_synthetic_records': 49, 'live_mini_cases': 10,
            'review_basis': decision['review_method'], 'agent_output_assessments': 42,
            'api_calls_in_this_check': 0}

if __name__ == '__main__':
    print(json.dumps(check()))
