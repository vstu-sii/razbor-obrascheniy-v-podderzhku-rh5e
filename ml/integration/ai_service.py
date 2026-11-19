from typing import Literal
from backend.observability import TelemetryMiddleware, configure
"""Service-authenticated inference, one provider call per durable reservation."""
import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import time
from backend.main import build_engine, store_module
from backend.cascade_queue import Queue
from backend.limits.degradation import analyze as controlled_analyze
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, AliasChoices, model_validator
from backend.main import AnalyzeRequest, BodyLimitMiddleware, Baseline, read_jsonl, ROOT, SCHEMA, provenance_gate
import os
import secrets
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from ml.cascade.providers import CASCADE_STAGE_TIMEOUTS


def create_app():
    @asynccontextmanager
    async def lifespan(app):
        async def reconcile_loop():
            while True:
                try:
                    await asyncio.to_thread(queue.reconcile_billing)
                except Exception:
                    logging.getLogger('triage.billing').warning('Billing reconciliation deferred')
                await asyncio.sleep(5)

        task = asyncio.create_task(reconcile_loop()) if engine.mode == 'cascade' else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title='Support Triage AI', lifespan=lifespan)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(TelemetryMiddleware)
    configure()
    engine = build_engine()
    if engine.mode == 'cascade':
        from ml.cascade.cache import AnswerCache
        engine.answer_cache = AnswerCache()
    target = os.environ.get('DATABASE_URL') or os.environ.get('DATABASE_PATH', '/data/triage.db')
    queue = Queue(lambda: store_module.open_store(target), mode=engine.mode, cascade_enabled=engine.mode=='cascade', cascade_kb=getattr(engine,'kb',None))
    bearer = HTTPBearer(auto_error=False)

    def service(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        token = os.environ.get('SERVICE_TOKEN', '')
        if not token or not credentials or not secrets.compare_digest(token.encode(), credentials.credentials.encode()):
            raise HTTPException(401, 'Service authentication required')

    class Inference(BaseModel):
        model_config = ConfigDict(extra='forbid')
        input: AnalyzeRequest
        timeout: float = Field(gt=0, le=max(CASCADE_STAGE_TIMEOUTS.values()))
        mode: Literal['extractive'] | None = None
        provider: Literal['mistral', 'sol'] | None = None
        job_id: str | None = None
        claim_id: str | None = Field(default=None, max_length=100, validation_alias=AliasChoices('claim_id', 'attempt_id'))

        @model_validator(mode='after')
        def validate_stage_timeout(self):
            cap = CASCADE_STAGE_TIMEOUTS[self.provider] if self.provider else 25
            if self.timeout > cap:
                raise ValueError(f'Timeout exceeds the stage limit of {cap} seconds')
            return self

    @app.get('/health')
    def health():
        return {'status': 'ok', 'baseline_mode': engine.mode}

    @app.post('/internal/inference', dependencies=[Depends(service)])
    async def inference(body: Inference):
        if engine.mode == 'cascade' and body.provider is None and body.mode != 'extractive':
            raise HTTPException(422, 'Cascade reservation metadata required')
        if body.provider:
            if engine.mode != 'cascade' or body.mode is not None:
                raise HTTPException(409, 'Cascade policy differs from reservation')
            started = time.monotonic()
            stored, remaining = await asyncio.to_thread(queue.claim_inference, body.job_id, body.claim_id, body.provider, body.input.model_dump())
            context = await asyncio.to_thread(queue.inference_context, body.job_id, body.claim_id, stored)
            from ml.cascade.service_cache import authenticated_cache_scope
            scope = await asyncio.to_thread(authenticated_cache_scope, queue, body.job_id)
            timeout = min(body.timeout, remaining - (time.monotonic() - started))
            if timeout <= 0:
                raise HTTPException(409, 'Insufficient time for reserved inference')
            return await asyncio.to_thread(engine.analyze_attempt, stored, body.provider, timeout, body.job_id, body.claim_id, stage_context=context, cache_scope=scope)
        if engine.mode in ('llm', 'cascade') or body.claim_id:
            from backend.guards.inference import consume
            from backend.main import store_module
            consume(store_module,body)
        return await asyncio.to_thread(controlled_analyze, engine,body.input.model_dump(),body.mode,body.timeout)

    @app.post('/v1/analyze', dependencies=[Depends(service)])
    async def analyze(body: AnalyzeRequest):
        if engine.mode == 'cascade': raise HTTPException(409, 'Use the durable event queue')
        if engine.mode in ('llm', 'cascade'): raise HTTPException(403,'Use reserved inference')
        result = await asyncio.to_thread(engine.analyze, body.model_dump(), max_attempts=1, deadline_seconds=25)
        return result['final_card']

    return app
