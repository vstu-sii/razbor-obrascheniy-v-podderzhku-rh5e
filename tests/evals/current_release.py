"""Recompute recorded LAB05 results and replay current code. Never calls model APIs."""

# Offline flags and the pinned runtime path must be configured before application imports.
# ruff: noqa: E402
import argparse
import copy
import json
import logging
import os
import statistics
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = (
    HERE / "runtime" if (HERE / "runtime/backend/main.py").exists() else HERE.parents[1]
)
EVIDENCE = HERE / "evidence/lab5-current-20261126"
if not EVIDENCE.exists():
    EVIDENCE = PROJECT / "tests/evidence/lab5-current-20261126"
sys.path.insert(0, str(PROJECT))
os.environ.update(
    CASCADE_LIVE_ENABLED="false",
    SOL_LIVE_ENABLED="false",
    AI_BASE_URL="",
    MISTRAL_REQUESTS_PER_MINUTE="0",
    MISTRAL_MAX_RUNNING="2",
    TRACE_SYNTHETIC_CONTENT="false",
    CASCADE_BUDGET_RUB="10",
    TOKEN_BUDGET="20000000",
    USER_TOKENS_PER_DAY="20000000",
)
for name in [
    "MISTRAL_API_KEY",
    "APIMIRA_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
]:
    os.environ.pop(name, None)
logging.disable(logging.CRITICAL)
from backend.main import create_app
from backend.worker import step
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from ml.cascade.engine import CascadeEngine
from ml.cascade.providers import ProviderFailure
from ml.contracts.validation import SCHEMA, provenance_gate


def read(name):
    return [
        json.loads(line)
        for line in (EVIDENCE / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def save(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def fraction(n, d):
    return {"numerator": n, "denominator": d, "value": n / d if d else None}


def score(rows, cases):
    gold = {c["case_id"]: c["expected"] for c in cases}
    selected = [r for r in rows if r["case_id"] in gold]
    assert len(selected) == len(gold) and len({r["case_id"] for r in selected}) == len(
        gold
    )
    assert all(r.get("final_card") for r in selected)
    validator = Draft202012Validator(SCHEMA)
    labels = sorted(
        {g["category"] for g in gold.values()}
        | {r["final_card"]["category"] for r in selected}
    )
    parts = []
    for label in labels:
        tp = sum(
            gold[r["case_id"]]["category"] == r["final_card"]["category"] == label
            for r in selected
        )
        fp = sum(
            gold[r["case_id"]]["category"] != label
            and r["final_card"]["category"] == label
            for r in selected
        )
        fn = sum(
            gold[r["case_id"]]["category"] == label
            and r["final_card"]["category"] != label
            for r in selected
        )
        parts.append(
            {
                "label": label,
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0,
            }
        )
    nonanswer = [r for r in selected if gold[r["case_id"]]["mode"] != "answer"]
    kb = read_kb()
    citations = 0
    for row in selected:
        known = {
            s["source_id"]: s
            for s in kb
            if s["version"] == row["input"]["kb_version"] and s["status"] == "approved"
        }
        citations += all(
            s.get("source_id") in known
            and all(
                s.get(k) == known[s["source_id"]][k]
                for k in ["version", "title", "excerpt"]
            )
            for s in row["final_card"]["evidence"]
        )
    return {
        "n": len(selected),
        "mode_accuracy": fraction(
            sum(
                r["final_card"]["mode"] == gold[r["case_id"]]["mode"] for r in selected
            ),
            len(selected),
        ),
        "category_accuracy": fraction(
            sum(
                r["final_card"]["category"] == gold[r["case_id"]]["category"]
                for r in selected
            ),
            len(selected),
        ),
        "category_macro_f1": {
            "value": statistics.mean(p["f1"] for p in parts),
            "per_class": parts,
        },
        "schema_valid": fraction(
            sum(validator.is_valid(r["final_card"]) for r in selected), len(selected)
        ),
        "exact_citations": fraction(citations, len(selected)),
        "false_answer_on_nonanswer": fraction(
            sum(r["final_card"]["mode"] == "answer" for r in nonanswer), len(nonanswer)
        ),
        "automatic": fraction(
            sum(not r["final_card"]["needs_human_review"] for r in selected),
            len(selected),
        ),
    }


def read_kb():
    return [
        json.loads(line)
        for line in (PROJECT / "ml/data/cascade/kb.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]


class RecordedProvider:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def __call__(self, name, *args):
        self.calls.append(name)
        stage = next((s for s in self.row["stages"] if s["provider"] == name), None)
        if stage is None:
            raise AssertionError(
                "Current code requested an unrecorded model stage: "
                + self.row["case_id"]
            )
        if stage.get("raw_card") is None:
            raise ProviderFailure(
                stage["gate_reason"],
                not_sent=True,
                http_status=stage.get("http_status"),
            )
        return {
            "card": copy.deepcopy(stage["raw_card"]),
            "assessment": copy.deepcopy(stage.get("assessment")),
            "usage": None,
            "not_sent": True,
            "http_status": stage.get("http_status"),
        }


def replay(rows):
    results = []
    for row in rows:
        provider = RecordedProvider(row)
        engine = CascadeEngine(read_kb(), SCHEMA, provenance_gate, provider)
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(
                Path(folder) / "replay.sqlite",
                engine,
                "offline-quality",
                start_worker=False,
            )
            with TestClient(app) as api:
                headers = {"Authorization": "Bearer offline-quality"}
                response = api.post(
                    "/api/demo/tickets", json=row["input"], headers=headers
                )
                assert response.status_code == 202
                job = response.json()["job_id"]
                for _ in range(4):
                    if app.state.queue.status(job)["state"] not in [
                        "queued",
                        "running",
                    ]:
                        break
                    assert step(app.state.queue, engine)
                status = app.state.queue.status(job)
                assert status["state"] in ["ready", "manual_review"]
                proposal = api.get(
                    "/api/proposals/" + status["proposal_id"], headers=headers
                ).json()
                assert proposal["reviews"] == [], "No human review may be invented"
                results.append(
                    {
                        "case_id": row["case_id"],
                        "input": row["input"],
                        "final_card": proposal["card"],
                        "state": status["state"],
                        "reason": status["reason"],
                        "saved_provider_calls": provider.calls,
                    }
                )
    return results


def violations(metrics):
    failed = []
    for name, limit in [
        ("mode_accuracy", 0.9),
        ("category_accuracy", 0.9),
        ("schema_valid", 1),
        ("exact_citations", 1),
    ]:
        if metrics[name]["value"] < limit:
            failed.append(
                {"metric": name, "actual": metrics[name]["value"], "minimum": limit}
            )
    if metrics["category_macro_f1"]["value"] < 0.85:
        failed.append(
            {
                "metric": "category_macro_f1",
                "actual": metrics["category_macro_f1"]["value"],
                "minimum": 0.85,
            }
        )
    if metrics["false_answer_on_nonanswer"]["value"] > 0.02:
        failed.append(
            {
                "metric": "false_answer_on_nonanswer",
                "actual": metrics["false_answer_on_nonanswer"]["value"],
                "maximum": 0.02,
            }
        )
    return failed


def run(output):
    if output.exists():
        raise ValueError("Use a fresh output directory")
    output.mkdir(parents=True)
    cases = [c for c in read("cases.jsonl") if c["suite"] == "legacy-q015"]
    before = [
        r for r in read("full-live-20261119.jsonl") if r["suite"] == "legacy-q015"
    ]
    selected = [
        r for r in read("selected-live-20261120.jsonl") if r["suite"] == "legacy-q015"
    ]
    assert len(cases) == len(before) == 38 and len(selected) == 19
    indexed = {c["case_id"]: c for c in cases}
    for row in before + selected:
        case = indexed[row["case_id"]]
        assert all(
            row["input"][k] == case["request"][k]
            for k in ["ticket_text", "history", "kb_version"]
        )
    current = replay(before)
    (output / "replayed-results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in current),
        encoding="utf-8",
    )
    selected_cases = [indexed[r["case_id"]] for r in selected]
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "external_model_calls": 0,
        "new_baseline_run": False,
        "scope": "Current API/queue/SQLite replay of immutable real 19 Nov generations; selected live 20 Nov scored separately",
        "source": json.loads((EVIDENCE / "source.json").read_text(encoding="utf-8")),
        "full_historical_live": score(before, cases),
        "current_full_replay": score(current, cases),
        "selected_historical_live": score(selected, selected_cases),
        "not_measured": [
            "new full live generation after latest fix",
            "human North Star",
            "independent holdout",
            "semantic fact accuracy",
            "urgency and destination accuracy",
            "production uptime",
            "new model latency or billing",
        ],
    }
    result["target_violations"] = violations(result["current_full_replay"])
    result["measured_targets_passed"] = not result["target_violations"]
    result["technical_replay_completed"] = True
    save(output / "report.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-targets", action="store_true")
    args = parser.parse_args()
    result = run(args.output)
    print(
        json.dumps(
            {
                "completed": 38,
                "selected_live": 19,
                "mode": result["current_full_replay"]["mode_accuracy"],
                "category": result["current_full_replay"]["category_accuracy"],
                "target_violations": result["target_violations"],
                "external_model_calls": 0,
            }
        )
    )
    if args.require_targets and not result["measured_targets_passed"]:
        raise SystemExit(1)
