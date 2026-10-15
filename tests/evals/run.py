"""Run free-only model evals directly; independently of the application backend."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import httpx
from evaluate import DATASET, EVIDENCE, SCHEMA, provenance_gate, read_jsonl
from free_api import BASE, PROMPT, generate, load_key, parse_response, payload_for, verify_free_model, mistral_payload

HERE = Path(__file__).resolve().parent

def sources_for(case, kb):
    # This independently tests generation given specified candidate context.
    # It does NOT claim to evaluate the AI Engineer's retriever.
    return [item for item in kb if item['source_id'] in case['input']['kb_candidates']
            and item['status'] == 'approved' and item['version'] == case['input']['kb_version']]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['replay', 'free-api'], default='replay')
    parser.add_argument('--model')
    parser.add_argument('--provider', choices=['openrouter', 'mistral'], default='openrouter')
    parser.add_argument('--mistral-free-account-confirmed', action='store_true')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--max-calls', type=int, default=42)
    parser.add_argument('--interval', type=float, default=6)
    args = parser.parse_args()
    if args.mode == 'replay':
        raise SystemExit(subprocess.call([sys.executable, str(HERE / 'evaluate.py'), '--check-floors']))
    if not args.model or not 1 <= args.max_calls <= 42 or args.interval < 3.1:
        parser.error('Free API requires --model, 1..42 calls and interval >=3.1 seconds')
    if args.provider == 'mistral' and not args.mistral_free_account_confirmed:
        parser.error('Mistral free account confirmation is required; Scale usage is forbidden')
    key = load_key(args.env_file, args.provider)
    endpoint = 'https://api.mistral.ai/v1' if args.provider == 'mistral' else BASE
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        catalog = client.get(endpoint + '/models', headers={'Authorization': 'Bearer ' + key} if args.provider == 'mistral' else {})
        catalog.raise_for_status()
        if args.provider == 'openrouter':
            free = verify_free_model(args.model, catalog.json())
        else:
            model = next((item for item in catalog.json()['data'] if item['id'] == args.model), None)
            if model is None or not model.get('capabilities', {}).get('function_calling'):
                raise SystemExit('Requested Mistral model lacks advertised function-calling access')
            free = {'model': args.model, 'basis': 'participant confirmed Free/Experiment account on 2026-10-15', 'account_plan_verified_by_api': False}
    cases, kb = read_jsonl(DATASET / 'cases.jsonl'), read_jsonl(DATASET / 'kb.jsonl')
    # Related variants stay in the dataset, but consume no duplicate model call.
    independent = [case for case in cases if case['id'] == case['group_id']]
    config = {'date_utc': datetime.now(timezone.utc).isoformat(), 'run_kind': 'new_live_free_api',
              'model': args.model, 'provider': args.provider, 'free_model_check': free, 'endpoint': endpoint,
              'prompt_sha256': hashlib.sha256(PROMPT.encode()).hexdigest(),
              'dataset_sha256': hashlib.sha256((DATASET / 'cases.jsonl').read_text(encoding='utf-8').encode()).hexdigest(),
              'candidate_context': 'explicit_fixture_context_no_retrieval_claim',
              'selected_case_ids': [case['id'] for case in independent], 'max_calls': args.max_calls,
              'interval_seconds': args.interval, 'retries': 0,
              'price_cap_usd_per_million': 0 if args.provider == 'openrouter' else None,
              'cost_control_basis': 'zero-price routing caps' if args.provider == 'openrouter' else 'participant-confirmed Mistral Free account',
              'human_gold_approved': False}
    EVIDENCE.mkdir(exist_ok=True, parents=True)
    path = EVIDENCE / 'live-responses.jsonl'
    config_path = EVIDENCE / 'live-config.json'
    previous = []
    if path.exists():
        if not args.resume:
            raise SystemExit('Existing live run preserved. Use --resume for unchanged config.')
        prior = json.loads(config_path.read_text(encoding='utf-8'))
        for name in ('model', 'provider', 'prompt_sha256', 'dataset_sha256'):
            if prior.get(name) != config[name]:
                raise SystemExit('Resume config mismatch: ' + name)
        previous = read_jsonl(path)
    done = {row['case_id'] for row in previous}
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    completed, halted = len(previous), None
    calls = 0
    with path.open('a' if previous else 'w', encoding='utf-8') as output:
        for case in [case for case in independent if case['id'] not in done][:args.max_calls]:
            request = dict(case['input'], request_id='Q004-' + case['id'])
            sources = sources_for(case, kb)
            payload = mistral_payload(args.model, SCHEMA, request, sources, args.mistral_free_account_confirmed) if args.provider == 'mistral' else payload_for(args.model, SCHEMA, request, sources)
            started, status = time.perf_counter(), None
            result = {'raw_card': None, 'usage': None, 'error': None}
            try:
                status, body = asyncio.run(generate(key, payload, args.provider))
                result = parse_response(status, body)
            except (ValueError, KeyError, IndexError, TypeError, OSError, TimeoutError, httpx.HTTPError) as error:
                # Never log exception text, headers or arbitrary error bodies.
                result['error'] = type(error).__name__
            gate, reason = provenance_gate(result['raw_card'], request['request_id'], request['kb_version'], sources)
            result.update(case_id=case['id'], request_id=request['request_id'], provider='free_api',
                          model=args.model, run_kind='new_live_free_api',
                          started_utc=datetime.now(timezone.utc).isoformat(), http_status=status,
                          latency_ms=round((time.perf_counter() - started) * 1000, 3),
                          retrieved_ids=[item['source_id'] for item in sources], gate_passed=gate, gate_reason=reason,
                          billed_usd=result.get('observed_cost_usd'), transport_kind='external_http')
            output.write(json.dumps(result, ensure_ascii=False) + '\n')
            output.flush()
            completed += 1
            calls += 1
            if status in (401, 402, 403, 429) or result.get('cost_violation'):
                halted = 'provider_auth_quota_or_cost_guard'
                break
            if calls < args.max_calls and completed < len(independent):
                time.sleep(args.interval)
    config.update(attempted=completed, calls_this_invocation=calls, total_required=len(independent), halted=halted,
                  full_run_completed=completed == len(independent) and halted is None)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    code = subprocess.call([sys.executable, str(HERE / 'evaluate.py'), '--responses', str(path), '--output', str(EVIDENCE / 'live-report.json'), '--context-mode', 'fixture'])
    if code or not config['full_run_completed']:
        raise SystemExit(code or 2)

if __name__ == '__main__':
    main()
