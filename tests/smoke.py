"""Check the running LAB02 Compose skeleton using only Python stdlib and Docker CLI."""
import json
import os
import subprocess
from pathlib import Path
from urllib import request, error

ROOT = Path(__file__).resolve().parents[1]
(ROOT / 'test-results').mkdir(exist_ok=True)
PROJECT = os.environ.get('COMPOSE_PROJECT_NAME', 'lab02-delivery')
COMPOSE = ['docker', 'compose', '-p', PROJECT, '-f', str(ROOT / 'compose.dev.yml')]
BACKEND = 'http://127.0.0.1:' + os.environ.get('BACKEND_PORT', '8000')
UI = 'http://127.0.0.1:' + os.environ.get('UI_PORT', '8080')


def call(url, body=None):
    req = request.Request(url, data=body, headers={'Content-Type': 'application/json'})
    try:
        with request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def docker(*args, input_text=None):
    result = subprocess.run(COMPOSE + list(args), input=input_text, text=True,
                            capture_output=True, encoding='utf-8', check=True)
    return result.stdout


rows = [json.loads(line) for line in docker('ps', '--format', 'json').splitlines() if line.strip()]
assert len(rows) == 9, f'Expected nine services, got {len(rows)}'
assert all(row['State'] == 'running' and row['Health'] == 'healthy' for row in rows)
for base in (BACKEND,):
    status, payload = call(base + '/health')
    assert status == 200 and payload['mode'] == 'stub'
with request.urlopen(UI, timeout=5) as response:
    assert response.status == 200 and 'Support Triage' in response.read().decode('utf-8')
valid = {'request_id': 'DEMO-REQUEST-1', 'ticket_ref': 'DEMO-014',
         'ticket_text': 'Synthetic request', 'history': [], 'kb_version': 'demo-v1'}
for base in (BACKEND,):
    status, result = call(base + '/v1/analyze', json.dumps(valid).encode())
    assert status == 200 and result['status'] == 'not_analyzed'
    assert result['mode'] == 'escalate' and result['needs_human_review'] is True
    assert result['draft_reply'] is None and result['evidence'] == []
    for invalid in ({}, {**valid, 'history': [None]}, {**valid, 'extra': True},
                    {**valid, 'ticket_text': 'x' * 10001}):
        assert call(base + '/v1/analyze', json.dumps(invalid).encode())[0] == 400
    assert call(base + '/v1/analyze', b'{broken')[0] == 400
    assert call(base + '/v1/analyze', b'x' * 65537)[0] == 413
    for forbidden in ('send', 'assign', 'approve'):
        assert call(base + '/v1/' + forbidden, b'{}')[0] == 404
# AI is internal: check its health from inside the container, not a public port.
assert json.loads(docker('exec', '-T', 'ai', 'python', '-c',
    "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"))['mode'] == 'stub'
config = json.loads(docker('config', '--format', 'json'))
assert set(config['services']['backend']['networks']) == {'frontend', 'application'}
assert set(config['services']['ai']['networks']) == {'application'}
assert 'ports' not in config['services']['ai']
# Initial migrations and integrity constraints execute in real PostgreSQL.
for service, db in (('triage-db', 'triage'), ('knowledge-db', 'knowledge')):
    assert docker('exec', '-T', service, 'psql', '-U', 'triage', '-d', db, '-Atc',
                  'SELECT version FROM schema_migration;').strip() == '1'
sql = (ROOT / 'tests' / 'database.sql').read_text(encoding='utf-8-sig')
docker('exec', '-T', 'triage-db', 'psql', '-v', 'ON_ERROR_STOP=1', '-U', 'triage', '-d', 'triage', input_text=sql)
# Loss of AI must fall back to manual review and recover afterwards.
try:
    docker('stop', 'ai')
    status, payload = call(BACKEND + '/v1/analyze', json.dumps(valid).encode())
    assert status == 503 and payload['next_step'] == 'manual_review'
finally:
    docker('start', '--wait', 'ai')
assert call(BACKEND + '/v1/analyze', json.dumps(valid).encode())[0] == 200
report = {'date': '2026-10-08', 'project': PROJECT, 'services': [{'service':row['Service'],'state':row['State'],'health':row['Health']} for row in rows],
          'checks': ['9 healthy services','UI and API health','safe fallback card','input validation','no send/assign/approve endpoints','2 initial migrations','SQL integrity constraints','AI failure 503 and recovery'], 'result':'PASS'}
(ROOT / 'test-results' / 'compose-check.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print('COMPOSE CHECK PASSED: 9 services, APIs, SQL constraints and AI failure/recovery.')
