"""AI endpoints only propose cards; they cannot write to storage or helpdesk."""
import asyncio
from fastapi import APIRouter, Depends

def create_router(request_model, operator_dependency, engine):
    router = APIRouter()

    @router.post('/v1/analyze', dependencies=[Depends(operator_dependency)])
    async def analyze(body: request_model):
        result = await asyncio.to_thread(engine.analyze, body.model_dump())
        return result['final_card']

    return router
