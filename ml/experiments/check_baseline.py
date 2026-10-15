"""Validate full current golden coverage against actual LAB03 model evidence."""
import hashlib
import json
from pathlib import Path
from evaluate import DATASET,EVIDENCE,read_jsonl,score,check_floors

def digest(path):
    return hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()

def main():
    cases,kb=read_jsonl(DATASET/'cases.jsonl'),read_jsonl(DATASET/'kb.jsonl')
    rows=read_jsonl(EVIDENCE/'baseline-responses.jsonl')
    independent=read_jsonl(EVIDENCE/'baseline-independent-responses.jsonl')
    coverage=json.loads((EVIDENCE/'baseline-coverage.json').read_text(encoding='utf-8'))
    manifest=json.loads((EVIDENCE/'baseline-source-manifest.json').read_text(encoding='utf-8'))
    report=json.loads((EVIDENCE/'baseline-report.json').read_text(encoding='utf-8'))
    assert len(rows)==len(cases)==43
    assert {row['case_id'] for row in rows}=={case['id'] for case in cases}
    assert len(independent)==len({row['case_id'] for row in independent})==38
    assert coverage['covered']==coverage['total']==43 and coverage['missing']==[]
    assert report['coverage']['new_baseline_run'] and report['coverage']['all_records_covered']
    assert manifest['dataset_sha256']==digest(DATASET/'cases.jsonl')
    assert manifest['kb_sha256']==digest(DATASET/'kb.jsonl')
    assert manifest['baseline_responses_sha256']==digest(EVIDENCE/'baseline-responses.jsonl')
    assert manifest['independent_responses_sha256']==digest(EVIDENCE/'baseline-independent-responses.jsonl')
    assert manifest['original_run_sha256']==digest(EVIDENCE/'iteration-v2/responses.jsonl')
    assert manifest['completion_attempts_sha256']==digest(EVIDENCE/'baseline-completion-attempts.jsonl')
    by_id={case['id']:case for case in cases}
    actual={row['case_id']:row for row in independent}
    first={row['case_id']:row for row in read_jsonl(EVIDENCE/'iteration-v2/responses.jsonl')}
    attempts=read_jsonl(EVIDENCE/'baseline-completion-attempts.jsonl')
    assert len(attempts)==4
    for item in attempts:
        assert first[item['case_id']]['raw_card'] is None
        assert item['previous_http_delivery_unknown'] and item['previous_attempt_error']=='TimeoutError'
    recovered={item['case_id']:item for item in attempts}
    for item in independent:
        original=first[item['case_id']]
        assert item==(original if original.get('raw_card') is not None else recovered[item['case_id']])
    for row in rows:
        case=by_id[row['case_id']];base=by_id[case['group_id']]
        assert case['input']==base['input'] and case['expected']==base['expected']
        assert case['candidate_proposal']==base['candidate_proposal']
        assert row['raw_card']==actual[case['group_id']]['raw_card']
        assert row['request_id']==actual[case['group_id']]['request_id']
        assert row['http_status']==200
    measured=score(rows,cases,kb);grouped=score(independent,cases,kb)
    for provider,model in measured['models'].items():
        for field in ('n','schema_valid','gate_passed','mode_correct','category_correct'):
            assert model[field]==report['models'][provider][field]
            assert grouped['models'][provider][field]==report['independent_group_metrics'][provider][field]
    floors=json.loads(Path(__file__).with_name('baseline-floors.json').read_text(encoding='utf-8'))
    assert not check_floors(grouped,floors)
    assert check_floors(score([dict(row,raw_card=None) for row in independent],cases,kb),floors)
    print(json.dumps({'new_LAB03_baseline':True,'golden_records_covered':43,'golden_records_total':43,
        'distinct_inputs':38,'reused_identical_inputs':5,'api_calls_in_this_check':0}))

if __name__=='__main__':
    main()
