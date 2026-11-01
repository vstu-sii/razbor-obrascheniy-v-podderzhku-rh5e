"""Service-authenticated inference, one provider call per durable reservation."""
import asyncio
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from backend.main import AnalyzeRequest, BodyLimitMiddleware, Baseline, read_jsonl, ROOT, SCHEMA, provenance_gate
import os
import secrets
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials


def create_app():
    app = FastAPI(title='Support Triage AI')
    app.add_middleware(BodyLimitMiddleware)
    engine = Baseline(read_jsonl(ROOT/'data/kb.jsonl'), SCHEMA, provenance_gate,
                      mode=os.environ.get('BASELINE_MODE', 'extractive'), trace_path=os.environ.get('TRACE_PATH'))
    bearer = HTTPBearer(auto_error=False)

    def service(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        token = os.environ.get('SERVICE_TOKEN', '')
        if not token or not credentials or not secrets.compare_digest(token.encode(), credentials.credentials.encode()):
            raise HTTPException(401, 'Service authentication required')

    class Inference(BaseModel):
        model_config = ConfigDict(extra='forbid')
        input: AnalyzeRequest
        timeout: float = Field(gt=0, le=25)

    @app.get('/health')
    def health():
        return {'status': 'ok', 'baseline_mode': engine.mode}

    @app.post('/internal/inference', dependencies=[Depends(service)])
    async def inference(body: Inference):
        return await asyncio.to_thread(engine.analyze, body.input.model_dump(), max_attempts=1, deadline_seconds=body.timeout)

    @app.post('/v1/analyze', dependencies=[Depends(service)])
    async def analyze(body: AnalyzeRequest):
        result = await asyncio.to_thread(engine.analyze, body.model_dump(), max_attempts=1, deadline_seconds=25)
        return result['final_card']

    return app
