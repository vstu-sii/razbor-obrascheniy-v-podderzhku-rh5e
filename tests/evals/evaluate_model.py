"""Recompute all recorded v2 outputs with the current strict proposal contract."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from runtime_checks import ROOT, TASK, PROJECT, api


def score_all(directory):
    # Frozen input/output provenance is required before re-scoring current policy.
    manifest = json.loads(
        (ROOT / "tests/fixture-manifest.json").read_text(encoding="utf-8")
    )
    for relative, expected in manifest["normalized_text_sha256"].items():
        content = (
            (ROOT / relative)
            .read_text(encoding="utf-8")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
        if hashlib.sha256(content.encode()).hexdigest() != expected:
            raise ValueError("Frozen evaluation evidence changed: " + relative)
    golden = TASK / "fixtures/golden-dataset"
    cases = api.read_jsonl(golden / "cases.jsonl") + api.read_jsonl(
        golden / "live-regressions.jsonl"
    )
    rows = api.read_jsonl(directory / "all-record-responses.jsonl")
    groups = api.read_jsonl(directory / "independent-responses.jsonl")
    if (
        len(rows) != 49
        or len(groups) != 38
        or {r["case_id"] for r in rows} != {c["id"] for c in cases}
    ):
        raise ValueError("Full golden coverage missing")
    by_case = {c["id"]: c for c in cases}
    by_group = {r["case_id"]: r for r in groups}
    for row in rows:
        case = by_case[row["case_id"]]
        base = by_case[case["group_id"]]
        if any(case[k] != base[k] for k in ("input", "expected", "candidate_proposal")):
            raise ValueError("Invalid reuse group")
        if row["raw_card"] != by_group[case["group_id"]]["raw_card"]:
            raise ValueError("Reused output modified")
    source = TASK / "score_saved.py"
    spec = importlib.util.spec_from_file_location("q010_measured_scorer", source)
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    scorer.VALIDATOR = Draft202012Validator(api.SCHEMA)

    def gate(card, request_id, version, sources):
        passed, why = api.provenance_gate(card, request_id, version, sources)
        if passed:
            extra = api.module._tool.grounded_reply_reason(
                card, {"kb_version": version}, sources
            )
            if extra:
                return False, extra
        return passed, why

    scorer.provenance_gate = gate
    kb = api.read_jsonl(PROJECT / "data/kb.jsonl")
    raw = scorer.score(groups, cases, kb)
    # Replay immutable model outputs through current runtime policy. Do not rewrite
    # the original responses or claim that local fallback changes improve the LLM.
    replayed = []
    for row in groups:
        request = row["request"]

        def transport(_request, _sources, _timeout, original=row):
            if original["raw_card"] is None:
                raise TimeoutError("recorded unavailable generation")
            return original["raw_card"], original["usage"]

        engine = api.Baseline(
            kb, api.SCHEMA, api.provenance_gate, mode="llm", transport=transport
        )
        current = engine.analyze(request, max_attempts=1)
        replayed.append(dict(row, raw_card=current["final_card"]))
    system = scorer.score(replayed, cases, kb)
    report = {
        "scope": "full_reviewed_development_set_not_independent_test",
        "records": 49,
        "distinct_inputs": 38,
        "raw_generation_metrics": raw["models"]["mistral_v2_system"],
        "system_output_metrics": system["models"]["mistral_v2_system"],
        "raw_details": raw["details"],
        "system_details": system["details"],
        "system_metric_scope": "current_runtime_replay_of_immutable_real_outputs; no new API calls; raw LLM metrics separate",
        "evidence_inventory": json.loads(
            (directory / "report.json").read_text(encoding="utf-8")
        ),
    }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--directory", type=Path, default=ROOT / "tests/evidence/model-v2.1"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-targets", action="store_true")
    args = parser.parse_args()
    report = score_all(args.directory)
    metrics = report["system_output_metrics"]
    failures = target_violations(metrics)
    if args.output:
        args.output.write_text(
            json.dumps(
                report | {"predeclared_target_violations": failures},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    print(
        json.dumps(
            {
                "records": 49,
                "distinct_inputs": 38,
                "mode": metrics["mode_correct"],
                "category": metrics["category_correct"],
                "false_answer": metrics["false_answer_on_nonanswer_cases"],
                "target_violations": failures,
            }
        )
    )
    if args.require_targets and failures:
        raise SystemExit(1)


def target_violations(metrics):
    failures = []
    for name, minimum in [
        ("schema_valid", 1),
        ("gate_passed", 1),
        ("mode_correct", 0.9),
        ("category_correct", 0.9),
    ]:
        if metrics[name]["value"] < minimum:
            failures.append(name)
    if metrics["false_answer_on_nonanswer_cases"]["value"] > 0.02:
        failures.append("false_answer")
    return failures


if __name__ == "__main__":
    main()
