"""Create/reset only a marked synthetic LAB06 database. No network requests."""

import argparse
import json
import logging
import sqlite3
import sys
import tempfile
import shutil
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent / "runtime"))
from demo_support import HEADERS, application, cases, complete, request_for
from fastapi.testclient import TestClient


def require_demo_database(target):
    try:
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as db:
            marker = db.execute("SELECT purpose FROM demo_environment").fetchone()
    except sqlite3.DatabaseError:
        raise ValueError("Refusing an unmarked database") from None
    if marker != ("lab06-synthetic-only",):
        raise ValueError("Refusing an unmarked database")


def reset(target, allow_reset=False):
    target = target.resolve()
    if target.suffix not in (".sqlite", ".db"):
        raise ValueError("SQLite file required")
    existed = target.exists()
    if existed:
        require_demo_database(target)
        if not allow_reset:
            raise ValueError("Marked demo DB and --reset required")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as folder:
        candidate = Path(folder) / "demo.sqlite"
        result = seed(candidate)
        if target.exists() != existed:
            raise ValueError("Demo database changed during reset")
        if existed:
            require_demo_database(target)
            backup = target.with_name(
                target.name + "." + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f") + ".bak"
            )
            shutil.copyfile(target, backup)
        candidate.replace(target)
    return result


def seed(target):
    results = []
    with patch.dict("os.environ", {"OPERATOR_TOKENS": ""}):
        app = application(target)
    with TestClient(app) as client:
        with client.app.state.queue.connect() as db:
            db.execute("CREATE TABLE demo_environment(purpose TEXT NOT NULL)")
            db.execute(
                "INSERT INTO demo_environment VALUES(?)", ("lab06-synthetic-only",)
            )
            db.commit()
        for case in cases():
            status, proposal = complete(client, request_for(case))
            assert proposal["card"]["mode"] == case["expected_mode"], case["id"]
            assert proposal["card"]["category"] == case["expected_category"], case["id"]
            results.append(
                {
                    "case": case["id"],
                    "state": status["state"],
                    "mode": proposal["card"]["mode"],
                    "category": proposal["card"]["category"],
                }
            )
        # Exercise the actual review endpoint with a scripted synthetic decision.
        case = cases()[4]
        _state, proposal = complete(client, request_for(case, "-review"))
        response = client.post(
            "/api/proposals/" + proposal["proposal_id"] + "/reviews",
            headers=HEADERS,
            json={
                "decision": "accepted",
                "reason": "Учебное решение: передать возврат специалисту",
                "confirmed": True,
                "destination": "billing",
            },
        )
        assert response.status_code == 200, response.status_code
    return {
        "scope": "local saved-response replay, not production or a new model evaluation",
        "cases": results,
        "operator_review_checked": True,
        "operator_review_kind": "scripted endpoint exercise, not human approval",
        "external_model_calls": 0,
    }


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db", type=Path, default=PROJECT / "validation-output/lab6-demo.sqlite"
    )
    p.add_argument("--reset", action="store_true")
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    result = reset(a.db, a.reset)
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False))
