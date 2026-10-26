"""Incoming ACL forwards only verified, bounded event metadata to its owner."""
import hashlib
import hmac
import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import httpx
from backend.main import BodyLimitMiddleware


def create_app():
    app = FastAPI(title='Support Triage event ingress')
    app.add_middleware(BodyLimitMiddleware)

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    @app.post('/events')
    async def event(request: Request):
        raw = await request.body()
        signature = request.headers.get('X-Event-Signature', '')
        secret = os.environ.get('EVENT_SECRET', '')
        if not secret or not hmac.compare_digest(signature.encode(), hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest().encode()):
            raise HTTPException(401, 'Invalid event signature')
        try:
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.post(os.environ['BACKEND_URL']+'/ingress/events', content=raw,
                    headers={'Content-Type':'application/json', 'X-Event-Signature':signature})
            return JSONResponse(response.json(), status_code=response.status_code,
                                headers={'Retry-After':'5'} if response.status_code == 503 else None)
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, 'Backend unavailable; retain and retry event', headers={'Retry-After':'5'}) from None

    return app
