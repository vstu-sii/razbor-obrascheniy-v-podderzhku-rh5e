"""Validate draft LAB02 JSONL structure and coverage; not model quality."""
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read_jsonl(name):
    return [json.loads(line) for line in (HERE / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_case(case, kb_by_id):
    assert case["status"] == "draft_synthetic"
    assert case["provenance"]["source_type"] == "synthetic"
    assert case["provenance"]["review_status"] == "pending"
    request = case["input"]
    expected = case["expected"]
    assert request["ticket_text"] and expected["next_step"]
    assert request["kb_version"] in {"v1", "v2"}
    assert isinstance(request["history"], list) and len(request["history"]) <= 50
    assert all(message["speaker"] in {"customer", "operator"} and message["text"] and len(message["text"]) <= 10000 for message in request["history"])
    assert set(request["kb_candidates"]) <= kb_by_id.keys()
    assert expected["needs_human_review"] is True
    assert expected["mode"] in {"answer", "clarify", "escalate"}
    assert expected["forbidden_claims"]
    assert set(expected["evidence_ids"]) <= set(request["kb_candidates"])
    assert all(kb_by_id[source_id]["status"] == "approved" and kb_by_id[source_id]["version"] == request["kb_version"] for source_id in expected["evidence_ids"])
    if expected["mode"] == "answer":
        assert expected["evidence_ids"], f"Unsubstantiated answer: {case['id']}"


def main():
    kb = read_jsonl("kb.jsonl")
    cases = read_jsonl("cases.jsonl")
    false_answer = read_jsonl("false-answer-cases.jsonl")
    kb_by_id = {item["source_id"]: item for item in kb}
    assert len(kb_by_id) == len(kb) == 7
    assert all(item["provenance"] == "synthetic_demo" for item in kb)
    assert len(cases) == 40
    assert Counter(item["uc"] for item in cases) == {"UC-01": 10, "UC-02": 10, "UC-03": 10, "UC-04": 10}
    assert len(false_answer) == 150
    assert Counter(item["expected"]["category"] for item in false_answer) == {category: 30 for category in ("access", "payment", "integrations", "settings", "product_error")}
    assert Counter(item["expected"]["mode"] for item in false_answer) == {"clarify": 75, "escalate": 75}
    all_cases = cases + false_answer
    assert len({item["id"] for item in all_cases}) == len(all_cases)
    metric_texts = {item["input"]["ticket_text"] for item in false_answer}
    assert len(metric_texts) == len(false_answer)
    assert metric_texts.isdisjoint({item["input"]["ticket_text"] for item in cases})
    for case in all_cases:
        validate_case(case, kb_by_id)
    assert sum("safety" in item["tags"] for item in cases) >= 10
    assert all(item["expected"]["review_action"] in {"edit", "reject"} for item in cases if item["uc"] == "UC-04")
    assert all("false_answer_metric" in item["tags"] and item["expected"]["mode"] != "answer" and not item["expected"]["evidence_ids"] for item in false_answer)

    by_id = {item["id"]: item for item in cases}
    long_history = by_id["UC02-10"]
    assert "long_history" in long_history["tags"] and len(long_history["input"]["history"]) >= 10
    conflicting_history = by_id["UC02-03"]
    history_text = " ".join(message["text"] for message in conflicting_history["input"]["history"])
    assert "conflicting_history" in conflicting_history["tags"] and "август" in history_text and "сентябр" in history_text
    stale = by_id["UC03-10"]
    assert "stale_kb" in stale["tags"] and stale["input"]["kb_candidates"]
    assert all(kb_by_id[source_id]["version"] != stale["input"]["kb_version"] for source_id in stale["input"]["kb_candidates"])
    assert stale["expected"]["mode"] != "answer" and not stale["expected"]["evidence_ids"]
    assert any("prompt_injection" in item["tags"] for item in cases)

    # If all 150 independently reviewed challenges pass, zero false answers
    # gives a one-sided exact 95% upper bound below the proposed 2% target.
    assert 1 - 0.05 ** (1 / len(false_answer)) < 0.02
    print("PASS: 40 core cases (10 per UC), real long/conflicting history and stale KB")
    print("PASS: 150 distinct false-answer challenges; 30 per category, 75 clarify/75 escalate")
    print("PASS: IDs, schema, version-matched evidence and review fields valid")


if __name__ == "__main__":
    main()
