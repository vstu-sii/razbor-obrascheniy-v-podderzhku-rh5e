"""Single-process LAB03 prototype. No external send or helpdesk write endpoint."""
import asyncio
import threading
import time
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
from typing import Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ConfigDict, AwareDatetime

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE
Q = ROOT
AI = ROOT
sys.path.insert(0, str(ROOT / 'backend/contracts'))
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT))
from events import Queue
from worker import run
from guards.auth import require_token
from backend.guards.identity import identities, authenticate
from backend.observability import TelemetryMiddleware, prometheus, configure
from evaluate import SCHEMA, provenance_gate, read_jsonl

spec = importlib.util.spec_from_file_location('lab03_baseline', AI / 'ml/models/baseline.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Baseline = module.Baseline
store_spec = importlib.util.spec_from_file_location('lab03_store', HERE / 'backend/models/store.py')
store_module = importlib.util.module_from_spec(store_spec)
store_spec.loader.exec_module(store_module)
router_spec = importlib.util.spec_from_file_location('lab03_ai_router', AI / 'backend/routers/ai.py')
router_module = importlib.util.module_from_spec(router_spec)
router_spec.loader.exec_module(router_module)

class HistoryItem(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    speaker: Literal['customer', 'operator']
    text: str = Field(min_length=1, max_length=10000)

class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    request_id: str = Field(min_length=1, max_length=100)
    ticket_ref: str = Field(min_length=1, max_length=128)
    ticket_text: str = Field(min_length=1, max_length=10000)
    history: list[HistoryItem] = Field(default_factory=list, max_length=50)
    kb_version: str = Field(min_length=1, max_length=64)

class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    decision: Literal['accepted', 'edited', 'rejected']
    final_text: str | None = Field(default=None, max_length=4000)
    reason: str = Field(min_length=1, max_length=1000)
    confirmed: bool = Field(default=False, strict=True)
    destination: Literal['billing', 'technical', 'access', 'general'] | None = None

class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PUT', 'PATCH'):
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            size += len(chunk)
            if size > 65536:
                from fastapi.responses import JSONResponse
                return await JSONResponse({'detail': 'Request too large'}, status_code=413)(scope, receive, send)
            chunks.append(chunk)
            if not message.get('more_body', False):
                break
        delivered = False
        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': b''.join(chunks), 'more_body': False}
            return await receive()
        return await self.app(scope, bounded_receive, send)

def create_app(db_path=None, engine=None, token=None, start_worker=None):
    explicit_target = db_path is not None
    target = db_path or os.environ.get('DATABASE_URL') or os.environ.get('DATABASE_PATH', HERE / 'validation-output/triage.db')
    postgres = str(target).startswith(('postgresql://', 'postgres://'))
    if not db_path and os.environ.get('DATABASE_URL') and not postgres:
        raise RuntimeError('DATABASE_URL must be a PostgreSQL connection string')
    db_path = str(target) if postgres else Path(target)
    token = token or os.environ.get('DEMO_OPERATOR_TOKEN', '')
    if not token:
        raise RuntimeError('Set DEMO_OPERATOR_TOKEN before serving the demo')
    engine = engine or Baseline(read_jsonl(ROOT / 'data/kb.jsonl'), SCHEMA, provenance_gate,
        mode=os.environ.get('BASELINE_MODE', 'extractive'), trace_path=os.environ.get('TRACE_PATH', HERE / 'validation-output/traces.jsonl'))

    def connect():
        return store_module.open_store(db_path)

    queue = Queue(connect, module.fallback, mode=engine.mode)
    tokens = identities(token)
    configure()

    @asynccontextmanager
    async def lifespan(app):
        if not postgres:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        with connect() as connection:
            store_module.initialize(connection, HERE / 'migrations')
        stop = threading.Event()
        enabled = start_worker if start_worker is not None else (not explicit_target and os.environ.get('START_WORKER', 'true') == 'true')
        threads = []
        if enabled:
            for _ in range(queue.running):
                thread = threading.Thread(target=run, args=(queue, engine, stop), daemon=True)
                thread.start()
                threads.append(thread)
        try:
            yield
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=26)

    app = FastAPI(title='Support Triage LAB03 prototype', lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(TelemetryMiddleware)
    app.state.queue, app.state.engine = queue, engine
    app.state.store, app.state.migrations = store_module, HERE / "migrations"
    bearer = HTTPBearer(auto_error=False)

    def operator(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        try:
            return authenticate(credentials,tokens)
        except HTTPException:
            queue.deny('operator_auth')
            raise

    # Inference is exposed only by the service-authenticated AI process.

    @app.get('/health')
    def health():
        with connect() as connection:
            connection.execute('SELECT 1')
        return {'status': 'ok', 'baseline_mode': engine.mode, 'architecture': 'event-queue-v1', 'infrastructure':'lab04', 'revision':os.environ.get('RENDER_GIT_COMMIT') or os.environ.get('APP_REVISION','unknown')}

    @app.get('/metrics')
    def metrics(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        from fastapi.responses import PlainTextResponse
        import math
        metrics_token = os.environ.get('METRICS_TOKEN', '')
        token_file = os.environ.get('METRICS_TOKEN_FILE')
        if token_file:
            try:
                metrics_token = Path(token_file).read_text(encoding='utf-8').strip()
            except OSError:
                raise HTTPException(503, 'Monitoring credentials unavailable') from None
        try: require_token(credentials, metrics_token)
        except HTTPException:
            queue.deny('metrics_auth')
            raise
        with connect() as connection:
            states = connection.execute('SELECT state,COUNT(*) AS n FROM analysis_job GROUP BY state').fetchall()
            attempts = connection.execute('SELECT COUNT(*) AS n,COALESCE(SUM(reserved_tokens),0) AS tokens FROM model_attempt').fetchone()
            observations = connection.execute('SELECT metadata_json FROM event_trace LIMIT 1000').fetchall()
        waits = sorted(json.loads(row['metadata_json']).get('queue_wait_ms') for row in observations
                       if isinstance(json.loads(row['metadata_json']).get('queue_wait_ms'), (int,float)))
        p95 = waits[math.ceil(.95*len(waits))-1] if waits else 0
        lines = ['# TYPE triage_queue_jobs gauge']
        lines += ['triage_queue_jobs{state="'+row['state']+'"} '+str(row['n']) for row in states]
        lines += ['# TYPE triage_model_attempts gauge', 'triage_model_attempts '+str(attempts['n']),
                  '# TYPE triage_reserved_tokens gauge', 'triage_reserved_tokens '+str(attempts['tokens']),
                  '# TYPE triage_queue_wait_p95_ms gauge', 'triage_queue_wait_p95_ms '+str(p95)]
        with connect() as connection:
            policies = connection.execute('SELECT event_type,reason,COUNT(*) AS n FROM security_event GROUP BY event_type,reason').fetchall()
            recent = connection.execute('SELECT COALESCE(SUM(reserved_tokens),0) AS n FROM model_attempt WHERE reserved_at>?',(time.time()-60,)).fetchone()['n']
            daily = connection.execute('SELECT COALESCE(SUM(reserved_tokens),0) AS n FROM daily_usage WHERE usage_day=?',(queue.daily.day(),)).fetchone()['n']
        lines += ['# TYPE triage_policy_events_total counter']
        for row in policies:
            lines.append('triage_policy_events_total{event="'+row['event_type']+'",reason="'+row['reason']+'"} '+str(row['n']))
        lines += ['triage_reserved_tokens_last_minute '+str(recent), 'triage_daily_reserved_tokens '+str(daily),
                  'triage_anomaly_threshold '+str(queue.daily.anomaly)]
        lines += prometheus()
        return PlainTextResponse('\n'.join(lines)+'\n', media_type='text/plain; version=0.0.4')

    @app.get('/')
    def index():
        return FileResponse(HERE / 'frontend/index.html')

    @app.post('/api/analyses', dependencies=[Depends(operator)])
    async def create_analysis(body: AnalyzeRequest):
        if engine.mode == 'llm' or os.environ.get('ENABLE_LEGACY_ANALYZE') != 'true':
            raise HTTPException(410, 'Use ticket events; manual inference is disabled')
        request = body.model_dump()
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with connect() as connection:
            previous = connection.execute('SELECT * FROM analysis_proposal WHERE request_id=?', (body.request_id,)).fetchone()
        if previous:
            if previous['input_hash'] != digest:
                raise HTTPException(409, 'request_id already belongs to different input')
            return {'proposal_id': previous['proposal_id'], 'card': json.loads(previous['card_json'])}
        result = await asyncio.to_thread(engine.analyze, request)
        proposal_id = str(uuid4())
        try:
            with connect() as connection:
                connection.execute('INSERT INTO analysis_proposal(proposal_id,request_id,input_hash,ticket_ref,card_json,diagnostics_json,input_json) VALUES (?,?,?,?,?,?,?)',
                    (proposal_id, body.request_id, digest, body.ticket_ref,
                     json.dumps(result['final_card'], ensure_ascii=False), json.dumps({key: result[key] for key in ('provider','latency_ms','usage','gate_passed','attempts')}, ensure_ascii=False), json.dumps(request, ensure_ascii=False)))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'Concurrent request_id; retrieve the original proposal')
        return {'proposal_id': proposal_id, 'card': result['final_card']}

    @app.get('/api/proposals', dependencies=[Depends(operator)])
    def list_proposals():
        with connect() as connection:
            rows = connection.execute('SELECT proposal_id,ticket_ref,card_json,created_at FROM analysis_proposal ORDER BY created_at DESC LIMIT 50').fetchall()
        return [dict(row) | {'card': json.loads(row['card_json'])} for row in rows]

    @app.get('/api/proposals/{proposal_id}', dependencies=[Depends(operator)])
    def get_proposal(proposal_id: str):
        with connect() as connection:
            row = connection.execute('SELECT * FROM analysis_proposal WHERE proposal_id=?', (proposal_id,)).fetchone()
            reviews = connection.execute('SELECT * FROM operator_review WHERE proposal_id=? ORDER BY reviewed_at', (proposal_id,)).fetchall()
        if not row:
            raise HTTPException(404, 'Proposal not found')
        with connect() as connection:
            current = connection.execute('SELECT input_version FROM ticket_snapshot WHERE ticket_ref=?', (row['ticket_ref'],)).fetchone()
        return {'is_current': not current or current['input_version'] == row['input_version'], 'proposal_id': proposal_id, 'card': json.loads(row['card_json']), 'input': json.loads(row['input_json']), 'input_version': row['input_version'], 'reviews': [dict(item) for item in reviews]}

    @app.post('/api/proposals/{proposal_id}/reviews')
    def review(proposal_id: str, body: ReviewRequest, actor=Depends(operator)):
        with queue.transaction() as connection:
            row = connection.execute('SELECT * FROM analysis_proposal WHERE proposal_id=?', (proposal_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Proposal not found')
            ticket = queue.ticket(connection, row['ticket_ref'])
            if ticket and row['input_version'] != ticket['input_version']:
                raise HTTPException(409, 'Proposal is stale; wait for the current version')
            card = json.loads(row['card_json'])
            if body.decision != 'rejected' and not body.confirmed:
                raise HTTPException(422, 'Explicit confirmation required')
            if body.decision != 'rejected' and card['mode'] == 'escalate' and not body.destination:
                raise HTTPException(422, 'Select a destination queue')
            if (body.decision == 'rejected' or card['mode'] != 'escalate') and body.destination:
                raise HTTPException(422, 'Destination is only valid for escalation')
            if body.decision == 'rejected' and body.final_text is not None:
                raise HTTPException(422, 'Rejected proposal cannot have final text')
            if body.decision == 'edited' and not (body.final_text or '').strip():
                raise HTTPException(422, 'Edited decision requires final text')
            if body.decision == 'accepted' and body.final_text is not None:
                raise HTTPException(422, 'Use edited when changing the text')
            final = card['draft_reply'] if body.decision == 'accepted' else body.final_text
            review_id = str(uuid4())
            connection.execute('INSERT INTO operator_review(review_id,proposal_id,actor_ref,decision,final_text,reason,destination,confirmed_at) VALUES (?,?,?,?,?,?,?,CASE WHEN ? = TRUE THEN CURRENT_TIMESTAMP ELSE NULL END)',
                (review_id, proposal_id, actor, body.decision, final, body.reason, body.destination, body.confirmed))
        return {'review_id': review_id, 'decision': body.decision, 'external_action_performed': False}

    class Event(BaseModel):
        model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
        event_id: str = Field(min_length=1, max_length=128)
        event_type: Literal['ticket.created', 'customer.message_received']
        ticket_ref: str = Field(min_length=1, max_length=128)
        input_version: int = Field(ge=1, strict=True)
        occurred_at: AwareDatetime
        kb_version: str = Field(min_length=1, max_length=64)

    class Snapshot(BaseModel):
        model_config = ConfigDict(extra='forbid')
        input_version: int = Field(ge=1, strict=True)
        input: AnalyzeRequest

    def service(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        expected = os.environ.get('SERVICE_TOKEN', '')
        if not expected or not credentials or not secrets.compare_digest(credentials.credentials.encode(), expected.encode()):
            raise HTTPException(401, 'Service authentication required')

    @app.post('/internal/snapshots', dependencies=[Depends(service)])
    def stage_snapshot(body: Snapshot):
        # Trusted helpdesk adapter supplies the snapshot separately from the webhook.
        payload = body.input.model_dump()
        with queue.transaction() as connection:
            old = connection.execute('SELECT input_json FROM helpdesk_snapshot WHERE ticket_ref=? AND input_version=?', (body.input.ticket_ref, body.input_version)).fetchone()
            encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            if old and old['input_json'] != encoded:
                raise HTTPException(409, 'Snapshot version is immutable')
            connection.execute('INSERT INTO helpdesk_snapshot VALUES (?,?,?) ON CONFLICT(ticket_ref,input_version) DO NOTHING', (body.input.ticket_ref, body.input_version, encoded))
        return {'stored': True}

    @app.post('/ingress/events', status_code=202)
    async def ingress(request: Request):
        import hmac
        secret = os.environ.get('EVENT_SECRET', '')
        raw = await request.body()
        signature = request.headers.get('X-Event-Signature', '')
        if not secret or not hmac.compare_digest(signature.encode(), hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest().encode()):
            raise HTTPException(401, 'Invalid event signature')
        try:
            event = Event.model_validate_json(raw).model_dump(mode='json')
        except ValueError:
            raise HTTPException(422, 'Invalid event metadata') from None
        with queue.transaction() as connection:
            source = connection.execute('SELECT input_json FROM helpdesk_snapshot WHERE ticket_ref=? AND input_version=?', (event['ticket_ref'], event['input_version'])).fetchone()
            if not source:
                raise HTTPException(404, 'Authorized snapshot unavailable')
            payload = json.loads(source['input_json'])
            if payload['kb_version'] != event['kb_version']:
                raise HTTPException(409, 'Knowledge version differs from snapshot')
            result = queue.accept(connection, event, payload)
        return result

    def quota_denials(function):
        from functools import wraps
        @wraps(function)
        def checked(*args,**kwargs):
            try: return function(*args,**kwargs)
            except HTTPException as error:
                if error.status_code in (429,503): queue.deny('daily_request_limit' if error.status_code==429 else 'queue_full')
                raise
        return checked

    @app.post('/api/demo/tickets', status_code=202)
    @quota_denials
    def demo_ticket(body: AnalyzeRequest, actor=Depends(operator)):
        # This is an explicit helpdesk simulator, not an operator "analyze" action.
        payload = body.model_dump()
        with queue.transaction() as connection:
            existing = connection.execute('SELECT j.* FROM analysis_job j JOIN analysis_event e ON e.event_id=j.event_id WHERE e.event_id=?', (body.request_id,)).fetchone()
            if existing:
                if json.loads(existing['input_json']) != payload:
                    raise HTTPException(409, 'Event belongs to different input')
                return {'job_id': existing['job_id'], 'duplicate': True}
            ticket = queue.ticket(connection, body.ticket_ref)
            version = ticket['input_version']+1 if ticket else 1
            event = {'event_id': body.request_id, 'event_type': 'customer.message_received' if ticket else 'ticket.created',
                     'ticket_ref': body.ticket_ref, 'input_version': version,
                     'occurred_at': datetime.now(timezone.utc).isoformat(), 'kb_version': body.kb_version}
            result = queue.accept(connection, event, payload, actor=actor)
        return result

    @app.get('/api/jobs/{job_id}', dependencies=[Depends(operator)])
    def job_status(job_id: str):
        return queue.status(job_id)

    @app.get('/api/tickets', dependencies=[Depends(operator)])
    def tickets():
        with queue.transaction() as connection:
            queue.recover(connection, time.time())
            rows = connection.execute('SELECT t.ticket_ref,t.input_version,j.job_id,j.state,j.reason,j.proposal_id FROM ticket_snapshot t JOIN analysis_job j ON j.job_id=t.job_id ORDER BY j.created_at DESC LIMIT 50').fetchall()
        return [dict(row) for row in rows]

    @app.get('/api/traces', dependencies=[Depends(operator)])
    def traces():
        with connect() as connection:
            rows = connection.execute('SELECT metadata_json FROM event_trace ORDER BY trace_id LIMIT 100').fetchall()
        return [json.loads(row['metadata_json']) for row in rows]

    return app
