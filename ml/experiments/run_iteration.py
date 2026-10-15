"""A fresh free-account Mistral run; preserves the frozen earlier experiment."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
EVALS = HERE if (HERE / 'free_api.py').exists() else HERE.parent / 'lab03-q-005-gold-review/submission/tests/evals'
sys.path.insert(0, str(EVALS))
from evaluate import DATASET, SCHEMA, read_jsonl, score, provenance_gate
from free_api import generate, load_key, parse_response, mistral_payload
from run import sources_for
import httpx

def digest(path):
    return hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--model', default='ministral-14b-2512')
    parser.add_argument('--interval', type=float, default=6)
    parser.add_argument('--max-calls', type=int, default=38)
    parser.add_argument('--output', type=Path, default=HERE / 'evidence/iteration-v2')
    args = parser.parse_args()
    if not 1 <= args.max_calls <= 38 or args.interval < 3.1:
        parser.error('At most 38 calls and interval >=3.1 seconds')
    cases = [case for case in read_jsonl(DATASET / 'cases.jsonl') if case['id'] == case['group_id']]
    kb = read_jsonl(DATASET / 'kb.jsonl')
    prompt = (HERE / 'prompt-v2.md').read_text(encoding='utf-8')
    config = {'run_kind': 'new_live_prompt_and_candidate_input_iteration', 'model': args.model,
        'provider': 'mistral', 'free_account_basis': 'participant confirmed Mistral Free; no paid routes',
        'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
        'dataset_sha256': digest(DATASET / 'cases.jsonl'), 'kb_sha256': digest(DATASET / 'kb.jsonl'),
        'schema_sha256': digest(EVALS / 'response.schema.json'),
        'selected_case_ids': [case['id'] for case in cases], 'context': 'fixture_not_retriever',
        'candidate_proposal_included': True, 'new_api_calls': 0, 'complete': False,
        'sampling_scope': 'development_set_used_for_iteration_not_independent_test',
        'iteration_targets_not_product_acceptance': {'schema_valid': 1.0, 'mode_correct': .9, 'category_correct': .9}}
    assert len(cases) == 38
    args.output.mkdir(parents=True, exist_ok=True)
    path, config_path = args.output / 'responses.jsonl', args.output / 'config.json'
    previous = []
    if config_path.exists():
        if not args.resume:
            raise SystemExit('Existing experiment preserved; use --resume for same config.')
        prior = json.loads(config_path.read_text(encoding='utf-8'))
        for field in ('model', 'prompt_sha256', 'dataset_sha256', 'kb_sha256', 'schema_sha256'):
            if prior[field] != config[field]:
                raise SystemExit('Resume mismatch: ' + field)
        if path.exists():
            previous = read_jsonl(path)
    elif path.exists():
        raise SystemExit('Responses without config preserved; inspect first.')
    done = {row['case_id'] for row in previous}
    assert len(previous) == len(done) and done <= set(config['selected_case_ids'])
    write(config_path, config)
    if not args.run:
        print('Prepared 38 fixed development inputs; API calls: 0. Use --run --resume to execute.')
        return
    # The participant has already explicitly confirmed Free mode. Only this
    # official endpoint client is used; no alternate provider or key is read.
    key = load_key(args.env_file, 'mistral')
    calls, halted = 0, None
    with path.open('a' if previous else 'w', encoding='utf-8', newline='\n') as output, \
         (args.output / 'connection-audit.jsonl').open('a', encoding='utf-8', newline='\n') as audit:
        for case in [item for item in cases if item['id'] not in done][:args.max_calls]:
            request = dict(case['input'], request_id='Q006-' + case['id'])
            fragments = sources_for(case, kb)
            payload = mistral_payload(args.model, SCHEMA, request, fragments, True)
            payload['messages'][0]['content'] = prompt
            value = json.loads(payload['messages'][1]['content'])
            value['candidate_proposal'] = case.get('candidate_proposal')
            payload['messages'][1]['content'] = json.dumps(value, ensure_ascii=False)
            started = time.perf_counter()
            status, result = None, {'raw_card': None, 'error': None, 'usage': None}
            for attempt in range(1, 4):
                try:
                    status, body = asyncio.run(generate(key, payload, 'mistral'))
                    result = parse_response(status, body)
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout) as error:
                    audit.write(json.dumps({'case_id': case['id'], 'request_id': request['request_id'],
                        'attempt': attempt, 'error': type(error).__name__, 'phase': 'connection_before_http'}) + '\n')
                    audit.flush()
                    result['error'] = type(error).__name__
                    if attempt < 3:
                        time.sleep(3)
                except (ValueError, KeyError, IndexError, TypeError, OSError, TimeoutError, httpx.HTTPError) as error:
                    result['error'] = type(error).__name__
                    break  # No retries after possible HTTP delivery.
            gate, reason = provenance_gate(result.get('raw_card'), request['request_id'], request['kb_version'], fragments)
            result.update(case_id=case['id'], request_id=request['request_id'], provider='free_api', model=args.model,
                run_kind=config['run_kind'], started_utc=datetime.now(timezone.utc).isoformat(),
                http_status=status, latency_ms=round((time.perf_counter() - started) * 1000, 3),
                retrieved_ids=[item['source_id'] for item in fragments], gate_passed=gate, gate_reason=reason,
                billed_usd=result.get('observed_cost_usd'), transport_kind='external_http',
                prompt_sha256=config['prompt_sha256'], candidate_proposal_included=case.get('candidate_proposal') is not None)
            output.write(json.dumps(result, ensure_ascii=False) + '\n')
            output.flush()
            calls += 1
            print(json.dumps({'case_id': case['id'], 'http_status': status, 'gate_passed': gate}), flush=True)
            if status in (401, 402, 403, 429) or result.get('cost_violation'):
                halted = 'auth_quota_or_cost_guard'
                break
            if calls < args.max_calls and len(previous) + calls < len(cases):
                time.sleep(args.interval)
    rows = read_jsonl(path)
    report = score(rows, cases, kb)
    for model in report['models'].values():
        model['provided_context_recall_at_3'] = model.pop('retrieval_recall_at_3')
    report.update(scope=config['sampling_scope'], context_mode='fixture', retrieval_quality_measured=False,
                  run_kind=config['run_kind'], config_sha256=digest(config_path))
    write(args.output / 'report.json', report)
    config.update(new_api_calls=calls, total_attempted=len(rows), halted=halted,
                  complete=len(rows) == 38 and all(row['http_status'] == 200 for row in rows),
                  responses_sha256=digest(path))
    write(config_path, config)
    # report config hash must identify the completed metadata, not its initial draft.
    report['config_sha256'] = digest(config_path)
    write(args.output / 'report.json', report)
    print(json.dumps({'complete': config['complete'], 'new_api_calls': calls, 'models': report['models']}), flush=True)
    if halted or not config['complete']:
        raise SystemExit(2)

if __name__ == '__main__':
    main()
