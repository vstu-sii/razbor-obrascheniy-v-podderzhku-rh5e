"""Serve the explicitly labelled LOCAL saved-response backup, never public production."""

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent / "runtime"))
import uvicorn
from demo_support import application
from starlette.responses import HTMLResponse
from reset import require_demo_database


def make_app(db, unavailable=False):
    db = db.resolve()
    require_demo_database(db)
    with patch.dict("os.environ", {"OPERATOR_TOKENS": ""}):
        app = application(db, unavailable=unavailable, start_worker=True)

    @app.middleware("http")
    async def label_backup(request, call_next):
        response = await call_next(request)
        if request.url.path == "/":
            body = b"".join([chunk async for chunk in response.body_iterator]).decode(
                "utf-8"
            )
            banner = '<aside style="padding:14px;background:#fff0c2;color:#532b00;font-weight:bold">ЛОКАЛЬНЫЙ ЗАПАСНОЙ РЕЖИМ: сохранённые реальные ответы. Новых вызовов моделей нет. Это не production.</aside>'
            if unavailable:
                banner += '<p role="status">Имитируется отказ моделей. Новые обращения переходят на ручную проверку.</p>'
            content = (
                body.replace("<h1", banner + "<h1", 1)
                if "<h1" in body
                else banner + body
            )
            return HTMLResponse(content, status_code=response.status_code)
        return response

    return app


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db", type=Path, default=PROJECT / "validation-output/lab6-demo.sqlite"
    )
    p.add_argument("--port", type=int, default=18096)
    p.add_argument("--simulate-outage", action="store_true", help="Force a local provider outage for new jobs")
    a = p.parse_args()
    uvicorn.run(make_app(a.db, a.simulate_outage), host="127.0.0.1", port=a.port, log_level="warning")
