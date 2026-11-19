"""Paired follow-up metrics; reads stored outputs, never calls a model."""
import copy
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT/'ml/experiments/lab5'), str(ROOT)]
from analyze import metrics


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def analyze():
    before = read_rows(ROOT/'ml/experiments/lab5/evidence/final/results.jsonl')
    all_cases = read_rows(ROOT/'ml/experiments/lab5/fixtures/cases.jsonl')
    replay = read_rows(HERE/'evidence/replay/results.jsonl')
    fresh = read_rows(HERE/'evidence/live/results.jsonl')
    planned = read_rows(HERE/'fixtures/cases.jsonl')
    observed = {r['case_id'] for r in fresh}
    cases = [c for c in planned if c['case_id'] in observed]
    assert len(fresh) == len(observed)
    report = dict(new_model_calls=0, planned=len(planned), observed=len(fresh),
                  replay_suites={}, live_paired_suites={})
    for suite in sorted({c['suite'] for c in all_cases}):
        selected = [c for c in all_cases if c['suite'] == suite]
        report['replay_suites'][suite] = {label:metrics(rows, selected) for label,rows in [('before', before), ('after', replay)]}
    for suite in sorted({c['suite'] for c in cases}):
        selected = [c for c in cases if c['suite'] == suite]
        report['live_paired_suites'][suite] = {label:metrics(rows, selected) for label,rows in [('before', before), ('after', fresh)]}
    old = {r['case_id']:r for r in before}
    gold = {c['case_id']:c['expected'] for c in cases}
    report['cases'] = [dict(case_id=r['case_id'], question=r['input']['ticket_text'],
                           expected=gold[r['case_id']],
                           before={k:old[r['case_id']]['final_card'][k] for k in ('category','mode')},
                           after={k:r['final_card'][k] for k in ('category','mode')},
                           classification=(r.get('handoff') or {}).get('classification'),
                           route=[dict(provider=s['provider'],reason=s['gate_reason']) for s in r['stages']],
                           reply=r['final_card'].get('draft_reply'), operator_summary=r['final_card']['summary'])
                       for r in fresh]
    if (HERE/'evidence/followup/results.jsonl').exists():
        followup=read_rows(HERE/'evidence/followup/results.jsonl')
        ids={r['case_id'] for r in followup}
        selected=[c for c in planned if c['case_id'] in ids]
        first={r['case_id']:r for r in fresh}
        report['followup']=dict(scope='Two-case prompt revision; first series remains unchanged.',
            before=metrics(fresh,selected),after=metrics(followup,selected),
            cases=[dict(case_id=r['case_id'],question=r['input']['ticket_text'],
                        before={k:first[r['case_id']]['final_card'][k] for k in ('category','mode')},
                        after={k:r['final_card'][k] for k in ('category','mode')},
                        route=[dict(provider=s['provider'],reason=s['gate_reason']) for s in r['stages']],
                        reply=r['final_card'].get('draft_reply'),operator_summary=r['final_card']['summary']) for r in followup])
    destination = HERE/'evidence/analysis';destination.mkdir(parents=True,exist_ok=True)
    (destination/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


if __name__ == '__main__':
    r = analyze()
    print(json.dumps({key:r[key] for key in ('planned','observed','new_model_calls')}))
