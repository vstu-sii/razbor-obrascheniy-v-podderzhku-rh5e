"""Offline Demo Day adapter: real API/queue, saved responses, never a model API."""

import copy
import importlib.util
import json
import os
import sys
from pathlib import Path


def project_root():
    for parent in Path(__file__).resolve().parents:
        if (parent / "backend/main.py").is_file():
            return parent
        if (parent / "project/backend/main.py").is_file():
            return parent / "project"
    raise RuntimeError("Project root missing")


PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))
os.environ.update(
    CASCADE_LIVE_ENABLED="false",
    SOL_LIVE_ENABLED="false",
    AI_BASE_URL="",
    TRACE_SYNTHETIC_CONTENT="false",
    MISTRAL_REQUESTS_PER_MINUTE="0",
    MISTRAL_MAX_RUNNING="2",
)
for name in [
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
]:
    os.environ.pop(name, None)
from backend.contracts.cascade import SCHEMA, provenance_gate
from backend.worker import step
from ml.cascade.engine import CascadeEngine
from ml.cascade.providers import MODELS, ProviderFailure

TOKEN = "lab6-local-operator"
HEADERS = {"Authorization": "Bearer " + TOKEN}


def cases():
    return json.loads((PROJECT / "docs/demo/cases.json").read_text(encoding="utf-8"))[
        "cases"
    ]


def request_for(case, suffix=""):
    from uuid import uuid4

    request = {
        key: copy.deepcopy(case["request"][key])
        for key in ["ticket_text", "history", "kb_version"]
    }
    request.update(
        request_id=str(uuid4()), ticket_ref="lab6-demo-" + case["id"] + suffix
    )
    return request


class SavedProvider:
    def __init__(self, unavailable=False):
        self.unavailable = unavailable
        self.calls = []

    def __call__(self, name, request, sources, schema, timeout, trace):
        self.calls.append(name)
        if self.unavailable:
            raise ProviderFailure("demo_provider_unavailable", not_sent=True)
        match = next(
            (
                case
                for case in cases()
                if all(
                    request[key] == case["request"][key]
                    for key in ["ticket_text", "history", "kb_version"]
                )
            ),
            None,
        )
        if match is None:
            raise ProviderFailure("no_saved_demo_case", not_sent=True)
        stage = next((s for s in match["stages"] if s["provider"] == name), None)
        if not stage or not stage.get("raw_card"):
            raise ProviderFailure("saved_demo_stage_unavailable", not_sent=True)
        card = copy.deepcopy(stage["raw_card"])
        # Rebind only correctly associated service ids; do not repair a recorded foreign id.
        if card["request_id"] == match["request"]["request_id"]:
            card["request_id"] = request["request_id"]
        card["warnings"] = card["warnings"][:18] + ["lab06_saved_response_replay"]
        return {
            "card": card,
            "assessment": copy.deepcopy(stage.get("assessment")),
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "returned_model": "saved/" + MODELS[name],
            "http_status": None,
            "not_sent": True,
            "completion_id": "saved-" + match["id"],
        }


def application(db, unavailable=False, start_worker=False):
    kb = [
        json.loads(s)
        for s in (PROJECT / "ml/data/cascade/kb.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if s
    ]
    provider = SavedProvider(unavailable)
    engine = CascadeEngine(kb, SCHEMA, provenance_gate, provider=provider)
    spec = importlib.util.spec_from_file_location(
        "lab6_app", PROJECT / "backend/main.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = module.create_app(db, engine, TOKEN, start_worker=start_worker)
    app.state.demo_provider = provider
    return app


def complete(client, request):
    response = client.post("/api/demo/tickets", json=request, headers=HEADERS)
    if response.status_code != 202:
        raise AssertionError("Admission " + str(response.status_code))
    job = response.json()["job_id"]
    for _ in range(4):
        step(client.app.state.queue, client.app.state.engine)
    status = client.app.state.queue.status(job)
    result = client.get("/api/proposals/" + status["proposal_id"], headers=HEADERS)
    if result.status_code != 200:
        raise AssertionError("Proposal unavailable")
    return status, result.json()
