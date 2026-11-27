"""One worker iteration claims once and makes at most ONE inference attempt."""
import json
import logging
import os
import time
import tempfile

import httpx
from backend.limits.degradation import analyze as controlled_analyze
from backend.observability import span, emit, configure
from ml.cascade.providers import CASCADE_GRACE_SECONDS, CASCADE_STAGE_TIMEOUTS

log = logging.getLogger('triage.worker')


def step(queue, engine):
    if hasattr(queue, 'reconcile_billing'):
        queue.reconcile_billing()
    job = queue.claim()
    if not job:
        return False
    try:
        request = json.loads(job['input_json'])
        provider = job.get('provider')
        deadline = min(job['deadline_at'], job['lease_until']) if provider else job['deadline_at']
        remaining = min(CASCADE_STAGE_TIMEOUTS[provider] if provider else 25,
                        deadline - time.time() - 1)
        if remaining <= 0:
            queue.finish(job)
            return True
        with span('model.attempt', {'provider': provider or job.get('inference_mode', 'unknown'), 'attempt': job['attempts']}) as traceparent:
            base = os.environ.get('AI_BASE_URL', '').rstrip('/')
            if base:
                body = {'input': request, 'timeout': remaining, 'mode': 'extractive' if job.get('inference_mode')=='extractive' else None,
                        'job_id':job['job_id'], 'claim_id':job['claim_id'], 'provider':provider}
                wait = remaining + .75
                if provider:
                    # The service needs time for the reservation and the worker for saving the result.
                    wait = min(remaining + CASCADE_GRACE_SECONDS, deadline - time.time() - .25)
                    if wait <= 0:
                        queue.finish(job)
                        return True
                with httpx.Client(timeout=wait, trust_env=False) as client:
                    response = client.post(base+'/internal/inference', json=body,
                        headers={'Authorization':'Bearer '+os.environ['SERVICE_TOKEN'], 'traceparent':traceparent})
                    response.raise_for_status()
                    result = response.json()
            elif provider:
                started = time.monotonic()
                stored, reserved_remaining = queue.claim_inference(job['job_id'], job['claim_id'], provider, request)
                context = queue.inference_context(job['job_id'], job['claim_id'], stored)
                timeout = min(remaining, reserved_remaining - (time.monotonic() - started))
                if timeout <= 0:
                    queue.finish(job)
                    return True
                result = engine.analyze_attempt(stored, provider, timeout,
                                               job['job_id'], job['claim_id'], stage_context=context)
            else:
                result = controlled_analyze(engine, request, job.get('inference_mode'), remaining)
            emit('model_attempt', outcome='completed', provider=provider or job.get('inference_mode', 'unknown'), attempt=job['attempts'])
        # Transport failure is retryable. A validated manual-review card is final.
        if result.get('provider') == 'llm' and result.get('raw_card') is None and not result.get('inference_skipped'):
            queue.finish(job)
        else:
            queue.finish(job, result)
    except Exception:
        # No exception body: provider URLs and credentials must never enter logs.
        log.warning('Inference attempt failed job=%s attempt=%s', job['job_id'], job['attempts'])
        queue.finish(job)
    return True


def run(queue, engine, stop):
    while not stop.is_set():
        try:
            from pathlib import Path
            (Path(tempfile.gettempdir())/'worker-heartbeat').touch()
            step(queue, engine)
        except Exception:
            log.warning('Queue unavailable; retrying without losing persisted tasks')
        stop.wait(.2)


if __name__ == '__main__':
    import threading
    from backend.bootstrap import operator_token, configure_database
    configure_database()
    configure()
    os.environ['DEMO_OPERATOR_TOKEN'] = operator_token('/data')
    os.environ['SERVICE_TOKEN'] = operator_token('/data', 'service_token')
    from backend.main import create_app
    app = create_app(start_worker=False)
    with app.state.queue.connect() as connection:
        app.state.store.initialize(connection, app.state.migrations)
    run(app.state.queue, app.state.engine, threading.Event())
