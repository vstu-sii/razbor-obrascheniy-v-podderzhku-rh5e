"""Single-process LAB03 prototype. No external send or helpdesk write endpoint."""
import asyncio
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

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, ConfigDict

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE
Q = ROOT
AI = ROOT
sys.path.insert(0, str(ROOT / 'backend/contracts'))
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
    ticket_ref: str = Field(min_length=1, max_length=100)
    ticket_text: str = Field(min_length=1, max_length=10000)
    history: list[HistoryItem] = Field(default_factory=list, max_length=50)
    kb_version: str = Field(min_length=1, max_length=50)

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

def create_app(db_path=None, engine=None, token=None):
    db_path = Path(db_path or os.environ.get('DATABASE_PATH', HERE / 'validation-output/triage.db'))
    token = token or os.environ.get('DEMO_OPERATOR_TOKEN', '')
    if not token:
        raise RuntimeError('Set DEMO_OPERATOR_TOKEN before serving the demo')
    engine = engine or Baseline(read_jsonl(ROOT / 'data/kb.jsonl'), SCHEMA, provenance_gate,
        mode=os.environ.get('BASELINE_MODE', 'extractive'), trace_path=os.environ.get('TRACE_PATH', HERE / 'validation-output/traces.jsonl'))

    def connect():
        return store_module.open_store(db_path)

    @asynccontextmanager
    async def lifespan(app):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with connect() as connection:
            store_module.migrate(connection, HERE / 'migrations')
        yield

    app = FastAPI(title='Support Triage LAB03 prototype', lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)
    bearer = HTTPBearer(auto_error=False)

    def operator(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not credentials or not secrets.compare_digest(credentials.credentials, token):
            raise HTTPException(401, 'Operator authentication required')
        return 'demo-operator'

    app.include_router(router_module.create_router(AnalyzeRequest, operator, engine))

    @app.get('/health')
    def health():
        with connect() as connection:
            connection.execute('SELECT 1')
        return {'status': 'ok', 'baseline_mode': engine.mode}

    @app.get('/')
    def index():
        return FileResponse(HERE / 'frontend/index.html')

    @app.post('/api/analyses', dependencies=[Depends(operator)])
    async def create_analysis(body: AnalyzeRequest):
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
        return {'proposal_id': proposal_id, 'card': json.loads(row['card_json']), 'input': json.loads(row['input_json']), 'reviews': [dict(item) for item in reviews]}

    @app.post('/api/proposals/{proposal_id}/reviews')
    def review(proposal_id: str, body: ReviewRequest, actor=Depends(operator)):
        with connect() as connection:
            row = connection.execute('SELECT card_json FROM analysis_proposal WHERE proposal_id=?', (proposal_id,)).fetchone()
            if not row:
                raise HTTPException(404, 'Proposal not found')
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
            connection.execute('INSERT INTO operator_review(review_id,proposal_id,actor_ref,decision,final_text,reason,destination,confirmed_at) VALUES (?,?,?,?,?,?,?,CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END)',
                (review_id, proposal_id, actor, body.decision, final, body.reason, body.destination, body.confirmed))
        return {'review_id': review_id, 'decision': body.decision, 'external_action_performed': False}

    return app
