"""Recheck published LAB05 Quality evidence offline. No keys or new API calls."""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parents[1]
RUNTIME = HERE / "runtime"
sys.dont_write_bytecode = True


def read(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def save(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def verify(tag):
    manifest = json.loads((HERE / "source-manifest.json").read_text(encoding="utf-8"))
    for name, entry in manifest["files"].items():
        path = (PACKAGE / name).resolve()
        if not path.is_relative_to(PACKAGE) or not path.is_file():
            raise ValueError("Missing published input: " + name)
        data = (
            path.read_bytes()
            if entry["binary"]
            else path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").encode()
        )
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("Published input changed: " + name)
    head = subprocess.check_output(
        ["git", "-C", str(PACKAGE), "rev-parse", "HEAD"], text=True
    ).strip()
    if tag:
        target = subprocess.check_output(
            ["git", "-C", str(PACKAGE), "rev-parse", tag + "^{commit}"], text=True
        ).strip()
        dirty = subprocess.check_output(
            [
                "git",
                "-C",
                str(PACKAGE),
                "status",
                "--porcelain",
                "--untracked-files=no",
            ],
            text=True,
        )
        if target != head or dirty:
            raise ValueError("Exact clean publication tag required")
    return dict(
        publication_commit=head,
        publication_tag=tag,
        tag_verified=bool(tag),
        source_commit=manifest["source_commit"],
        runtime_commit=manifest["runtime_commit"],
        runtime_release_tag=manifest["runtime_release_tag"],
        files_verified=len(manifest["files"]),
    )


def legacy_golden(output):
    from jsonschema import Draft202012Validator
    from ml.models import baseline

    contract = load("lab5_legacy_contract", RUNTIME / "backend/contracts/evaluate.py")
    scorer = load("lab5_score_saved", HERE / "score_saved.py")
    scorer.VALIDATOR = Draft202012Validator(contract.SCHEMA)

    def gate(card, request_id, version, sources):
        passed, reason = contract.provenance_gate(card, request_id, version, sources)
        if passed:
            extra = baseline._tool.grounded_reply_reason(
                card, dict(kb_version=version), sources
            )
            if extra:
                return False, extra
        return passed, reason

    scorer.provenance_gate = gate
    cases = read(HERE / "fixtures/golden-dataset/cases.jsonl") + read(
        HERE / "fixtures/golden-dataset/live-regressions.jsonl"
    )
    groups = read(HERE / "evidence/model-v2.1/independent-responses.jsonl")
    records = read(HERE / "evidence/model-v2.1/all-record-responses.jsonl")
    assert len(cases) == 49 and len(groups) == 38 and len(records) == 49
    by_id = {c["id"]: c for c in cases}
    by_group = {r["case_id"]: r for r in groups}
    assert {r["case_id"] for r in records} == set(by_id)
    for row in records:
        case = by_id[row["case_id"]]
        base = by_id[case["group_id"]]
        assert all(
            case[k] == base[k] for k in ("input", "expected", "candidate_proposal")
        )
        assert row["raw_card"] == by_group[case["group_id"]]["raw_card"]
    kb = read(RUNTIME / "data/kb.jsonl")
    raw = scorer.score(groups, cases, kb)
    replay = []
    for row in groups:

        def transport(request, sources, timeout, original=row):
            if original["raw_card"] is None:
                raise TimeoutError("Recorded unavailable generation")
            return original["raw_card"], original["usage"]

        engine = baseline.Baseline(
            kb,
            contract.SCHEMA,
            contract.provenance_gate,
            mode="llm",
            transport=transport,
        )
        replay.append(
            dict(
                row,
                raw_card=engine.analyze(row["request"], max_attempts=1)["final_card"],
            )
        )
    system = scorer.score(replay, cases, kb)
    metrics = system["models"]["mistral_v2_system"]
    failures = [
        name
        for name, limit in [
            ("schema_valid", 1),
            ("gate_passed", 1),
            ("mode_correct", 0.9),
            ("category_correct", 0.9),
        ]
        if metrics[name]["value"] < limit
    ]
    if metrics["false_answer_on_nonanswer_cases"]["value"] > 0.02:
        failures.append("false_answer")
    save(
        output / "legacy-golden.json",
        dict(
            scope="legacy single-model replay, not a new cascade baseline",
            records=49,
            distinct_inputs=38,
            raw_metrics=raw["models"]["mistral_v2_system"],
            system_metrics=metrics,
            target_violations=failures,
        ),
    )
    assert not failures, failures
    return dict(records=49, distinct_inputs=38, targets_passed=True)


def grounding(output):
    from ml.cascade.engine import CascadeEngine
    from ml.cascade.security import unsupported_reissue_policy
    from ml.contracts.validation import SCHEMA, provenance_gate

    data = json.loads(
        (HERE / "fixtures/cascade/unsupported-reissue.json").read_text(encoding="utf-8")
    )
    original = data["diagnostics"]

    def replay(provider, request, sources, schema, timeout, trace):
        return dict(
            card=copy.deepcopy(original["raw_card"]),
            assessment=copy.deepcopy(original["assessment"]),
            usage=original["usage"],
            http_status=200,
        )

    engine = CascadeEngine(
        read(RUNTIME / "ml/data/cascade/kb.jsonl"), SCHEMA, provenance_gate, replay
    )
    first = engine.analyze_attempt(data["request"], "mistral", 25, "saved-job", "first")
    second = engine.analyze_attempt(
        data["request"],
        "sol",
        25,
        "saved-job",
        "second",
        stage_context=first["stage_context"],
    )
    results = [
        dict(
            name="unchanged real response rejected by both stage guards",
            passed=first["gate_reason"] == "unsupported_reissue_policy"
            and first["cascade_next"]
            and second["gate_reason"] == "unsupported_reissue_policy"
            and not second["automatic_resolution"]
            and second["final_card"]["draft_reply"] is None,
        )
    ]
    card = dict(
        draft_reply="Система не предоставляет код повторно.",
        evidence=[],
        known_facts=[],
    )
    blocked = unsupported_reissue_policy(card) == "unsupported_reissue_policy"
    card["evidence"] = [dict(excerpt="Система не предоставляет код повторно.")]
    results.append(
        dict(
            name="explicit cited policy required",
            passed=blocked and unsupported_reissue_policy(card) is None,
        )
    )
    values = []
    for field, text in [
        ("summary", "Код не выдаётся системой повторно."),
        ("next_step", "Повторная выдача кода не предоставляется."),
    ]:
        values.append(
            unsupported_reissue_policy(
                dict(draft_reply=None, evidence=[], known_facts=[], **{field: text})
            )
            == "unsupported_reissue_policy"
        )
    results.append(dict(name="summary and next step covered", passed=all(values)))
    results.append(
        dict(
            name="storage fact alone allowed",
            passed=unsupported_reissue_policy(
                dict(
                    draft_reply="Код восстановления хранит сам пользователь.",
                    evidence=[],
                    known_facts=[],
                )
            )
            is None,
        )
    )
    save(
        output / "grounding.json",
        dict(
            issue="Q5-SEC-001",
            scope="real Mistral response replay and controlled Sol, no new API calls",
            checks=results,
        ),
    )
    assert all(r["passed"] for r in results)
    return dict(passed=len(results), total=len(results))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-tag")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Use a fresh output directory")
    os.environ.update(
        CASCADE_LIVE_ENABLED="false",
        SOL_LIVE_ENABLED="false",
        TRACE_SYNTHETIC_CONTENT="false",
    )
    provenance = verify(args.require_tag)
    sys.path.insert(0, str(RUNTIME))
    args.output.mkdir(parents=True)
    golden = legacy_golden(args.output)
    cascade = load("lab5_saved_cascade", RUNTIME / "ml/experiments/lab4/run.py")
    target = args.output / "cascade"
    target.mkdir()
    cascade.offline(target)
    report = json.loads((target / "offline.json").read_text(encoding="utf-8"))
    assert report["cases"] == 13 and report["mode_matches"] == 13
    assert all(
        r["all_required_found"] for r in report["retrieval"] if r["required_total"]
    )
    assert len(report["cache_replay"]) == 7
    regressions = grounding(args.output)
    archive = json.loads(
        (HERE / "evidence/lab5-integrated-20261112/release-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        archive["all_passed"]
        and archive["postgres_checked"]
        and archive["provenance"]["tag_verified"]
    )
    assert archive["provenance"]["revision"] == provenance["runtime_commit"]
    assert len(archive["checks"]) == 7 and all(
        r["exit_code"] == 0 for r in archive["checks"]
    )
    summary = dict(
        utc=datetime.now(timezone.utc).isoformat(),
        provenance=provenance,
        legacy_golden=golden,
        cascade_replay=dict(cases=13, mode_matches=13, cache_pairs=7),
        grounding=regressions,
        archived_release_proof_valid=True,
        external_model_calls=0,
        all_passed=True,
        not_measured=[
            "new full live cascade baseline",
            "human North Star",
            "new API/SQL execution in this minimal package",
        ],
    )
    save(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
