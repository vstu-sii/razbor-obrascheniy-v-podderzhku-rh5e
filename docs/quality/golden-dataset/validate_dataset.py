"""Validate the JSONL seed and its LAB02 UC coverage; not a model quality test."""
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read_jsonl(name):
    return [json.loads(line) for line in (HERE / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    kb = read_jsonl("kb.jsonl")
    cases = read_jsonl("cases.jsonl")
    ids = {item["source_id"] for item in kb if item["status"] == "approved"}
    assert len(cases) == 40
    assert len({item["id"] for item in cases}) == len(cases)
    assert Counter(item["uc"] for item in cases) == {"UC-01": 10, "UC-02": 10, "UC-03": 10, "UC-04": 10}
    assert all(item["status"] == "draft_synthetic" and item["provenance"]["source_type"] == "synthetic" for item in cases)
    assert all(item["input"]["ticket_text"] and item["expected"]["next_step"] for item in cases)
    assert all(item["expected"]["needs_human_review"] is True for item in cases)
    assert all(set(item["expected"]["evidence_ids"]) <= ids for item in cases)
    assert all(item["expected"]["mode"] in {"answer", "clarify", "escalate"} for item in cases)
    assert all(item["expected"]["review_action"] in {"edit", "reject"} for item in cases if item["uc"] == "UC-04")
    assert sum("safety" in item["tags"] for item in cases) >= 10
    print("PASS: 40 unique synthetic drafts; 10 per UC; evidence references and review fields valid")


if __name__ == "__main__":
    main()
