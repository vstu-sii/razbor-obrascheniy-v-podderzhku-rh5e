"""One worker iteration claims once and makes at most ONE inference attempt."""
import json
import logging
import os
import time
import tempfile

import httpx

log = logging.getLogger('triage.worker')


def step(queue, engine):
    job = queue.claim()
    if not job:
        return False
    try:
        request = json.loads(job['input_json'])
        remaining = min(25, job['deadline_at'] - time.time())
        if remaining <= 0:
            queue.finish(job)
            return True
        base = os.environ.get('AI_BASE_URL', '').rstrip('/')
        if base:
            with httpx.Client(timeout=remaining, trust_env=False) as client:
                response = client.post(base + '/internal/inference', json={'input': request, 'timeout': remaining},
                                       headers={'Authorization': 'Bearer '+os.environ['SERVICE_TOKEN']})
                response.raise_for_status()
                result = response.json()
        else:
            result = engine.analyze(request, max_attempts=1, deadline_seconds=remaining)
        # Transport failure is retryable. A validated manual-review card is final.
        if result.get('provider') == 'llm' and result.get('raw_card') is None:
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
    os.environ['DEMO_OPERATOR_TOKEN'] = operator_token('/data')
    os.environ['SERVICE_TOKEN'] = operator_token('/data', 'service_token')
    from backend.main import create_app
    app = create_app(start_worker=False)
    with app.state.queue.connect() as connection:
        app.state.store.initialize(connection, app.state.migrations)
    run(app.state.queue, app.state.engine, threading.Event())
