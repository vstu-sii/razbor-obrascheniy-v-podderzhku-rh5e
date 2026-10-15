"""HTTP API разбора обращений."""
import asyncio
import os
import secrets
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from ml.models.baseline import Baseline
from ml.experiments.evaluate import SCHEMA, provenance_gate, read_jsonl

ROOT = Path(__file__).resolve().parents[2]


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    speaker: Literal['customer', 'operator']
    text: str = Field(min_length=1, max_length=10000)


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=100)
    ticket_ref: str = Field(min_length=1, max_length=100)
    ticket_text: str = Field(min_length=1, max_length=10000)
    history: list[HistoryMessage] = Field(max_length=50)
    kb_version: str = Field(min_length=1, max_length=50)


def create_router(request_model, operator_dependency, engine):
    router = APIRouter()

    @router.post('/v1/analyze', dependencies=[Depends(operator_dependency)])
    async def analyze(body: request_model):
        result = await asyncio.to_thread(engine.analyze, body.model_dump())
        return result['final_card']

    return router


def create_app():
    token = os.environ.get('DEMO_OPERATOR_TOKEN', '')
    if not token:
        raise RuntimeError('Set DEMO_OPERATOR_TOKEN')
    engine = Baseline(
        read_jsonl(ROOT / 'ml/data/kb.jsonl'), SCHEMA, provenance_gate,
        mode=os.environ.get('BASELINE_MODE', 'extractive'),
        trace_path=os.environ.get('TRACE_PATH', str(ROOT / 'runtime/traces.jsonl')),
    )
    bearer = HTTPBearer(auto_error=False)

    def operator(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not credentials or not secrets.compare_digest(credentials.credentials, token):
            raise HTTPException(401, 'Operator authentication required')
        return 'operator'

    app = FastAPI(title='Support Triage AI', version='0.3.0')
    app.include_router(create_router(AnalyzeRequest, operator, engine))

    @app.get('/health')
    def health():
        return {'status': 'ok', 'baseline_mode': engine.mode}

    return app
