"""Retry only confirmed pre-HTTP connection failures; keep an append-only audit."""
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
from evaluate import DATASET, EVIDENCE, SCHEMA, read_jsonl, provenance_gate
from free_api import load_key, generate, mistral_payload, parse_response
from run import sources_for

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--mistral-free-account-confirmed', action='store_true')
    parser.add_argument('--retry-connect-only', action='store_true')
    args = parser.parse_args()
    if not args.mistral_free_account_confirmed:
        parser.error('Free account confirmation required')
    config = json.loads((EVIDENCE / 'live-config.json').read_text(encoding='utf-8'))
    if not config.get('full_run_completed') or config['provider'] != 'mistral':
        raise SystemExit('Complete the first Mistral pass before connection recovery')
    key = load_key(args.env_file, 'mistral')
    rows = read_jsonl(EVIDENCE / 'live-responses.jsonl')
    cases = {case['id']: case for case in read_jsonl(DATASET / 'cases.jsonl')}
    kb = read_jsonl(DATASET / 'kb.jsonl')
    audit_path = EVIDENCE / 'connection-recovery.jsonl'
    previous = read_jsonl(audit_path) if audit_path.exists() else []
    latest = {row['case_id']: row for row in previous}
    done = {case_id for case_id, row in latest.items() if not args.retry_connect_only or row.get('error') not in ('ConnectError', 'ConnectTimeout')}
    pending = [row for row in rows if row.get('http_status') is None and row.get('error') in ('ConnectError', 'ConnectTimeout') and row['case_id'] not in done]
    with audit_path.open('a', encoding='utf-8') as output:
        for original in pending:
            case = cases[original['case_id']]
            request = dict(case['input'], request_id=original['request_id'])
            sources = sources_for(case, kb)
            payload = mistral_payload(config['model'], SCHEMA, request, sources, True)
            started, status = time.perf_counter(), None
            result = {'raw_card': None, 'usage': None, 'error': None}
            try:
                status, body = asyncio.run(generate(key, payload, 'mistral'))
                result = parse_response(status, body)
            except (ValueError, KeyError, IndexError, TypeError, OSError, TimeoutError, httpx.HTTPError) as error:
                result['error'] = type(error).__name__
            gate, reason = provenance_gate(result['raw_card'], request['request_id'], request['kb_version'], sources)
            result.update(case_id=case['id'], request_id=request['request_id'], provider='free_api',
                          model=config['model'], run_kind='connection_recovery_before_http_only',
                          started_utc=datetime.now(timezone.utc).isoformat(), http_status=status,
                          latency_ms=round((time.perf_counter() - started) * 1000, 3),
                          retrieved_ids=[item['source_id'] for item in sources], gate_passed=gate,
                          gate_reason=reason, billed_usd=result.get('observed_cost_usd'), transport_kind='external_http',
                          original_error=original['error'], payload_sha256=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
            output.write(json.dumps(result, ensure_ascii=False) + '\n')
            output.flush()
            if status in (401, 402, 403, 429) or result.get('cost_violation'):
                break
            time.sleep(6)
    audit = read_jsonl(audit_path)
    recovery = {row['case_id']: row for row in audit}
    # Original first-pass file stays immutable. The second view is clearly
    # labelled and never used to claim first-pass service availability.
    combined = [recovery.get(row['case_id'], row) for row in rows]
    path = EVIDENCE / 'generation-responses.jsonl'
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in combined), encoding='utf-8')
    status = {'first_pass_attempts': len(rows), 'connection_failures_in_first_pass': sum(row.get('error') in ('ConnectError','ConnectTimeout') for row in rows),
              'recovery_distinct_cases': len(recovery), 'recovery_attempts_total': len(audit), 'final_http_200': sum(row.get('http_status') == 200 for row in combined),
              'all_inputs_received_model_response': len(combined) == 42 and all(row.get('http_status') == 200 for row in combined),
              'tls_verification_disabled': False, 'model_http_retries': 0,
              'connection_retry_policy': 'first recovery used 2, subsequent recovery uses 5 pre-HTTP connection retries'}
    (EVIDENCE / 'connection-recovery-summary.json').write_text(json.dumps(status, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(status))
    subprocess.check_call([sys.executable, str(Path(__file__).with_name('evaluate.py')), '--responses', str(path),
                           '--output', str(EVIDENCE / 'generation-report.json'), '--context-mode', 'fixture'])

if __name__ == '__main__':
    main()
