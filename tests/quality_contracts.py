"""Current event-runtime safety and monitoring contracts, not model benchmarks."""

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "evals"))
from runtime_checks import HEADERS, ROOT, PROJECT, api, fixture, step
from security_checks import proposal
from evaluate_model import target_violations


class CurrentContracts(unittest.TestCase):
    def test_taxonomy_rule_keeps_raw_model_output(self):
        self.assertEqual(
            api.module.operational_category(
                {"ticket_text": "Импорт CSV показывает E42"}
            ),
            "product_error",
        )
        self.assertIsNone(
            api.module.operational_category(
                {"ticket_text": "Где настроить интеграцию API?"}
            )
        )
        rows = api.read_jsonl(
            ROOT / "tests/evidence/model-v2.1/independent-responses.jsonl"
        )
        row = next(
            r
            for r in rows
            if r["raw_card"]
            and r["raw_card"]["category"] == "integrations"
            and api.module.operational_category(r["request"]) == "product_error"
        )
        original = json.dumps(row["raw_card"], sort_keys=True, ensure_ascii=False)
        engine = api.Baseline(
            api.read_jsonl(PROJECT / "data/kb.jsonl"),
            api.SCHEMA,
            api.provenance_gate,
            mode="llm",
            transport=lambda *args: (row["raw_card"], row["usage"]),
        )
        result = engine.analyze(row["request"], max_attempts=1)
        self.assertEqual(result["final_card"]["category"], "product_error")
        self.assertEqual(result["raw_card"]["category"], "integrations")
        self.assertEqual(
            json.dumps(row["raw_card"], sort_keys=True, ensure_ascii=False), original
        )

    def test_quality_gate_rejects_numeric_degradation(self):
        report = json.loads(
            (
                ROOT / "tests/evidence/model-v2.1/current-runtime-replay-taxonomy.json"
            ).read_text(encoding="utf-8")
        )
        metrics = report["system_output_metrics"]
        self.assertEqual(target_violations(metrics), [])
        for field in (
            "schema_valid",
            "gate_passed",
            "mode_correct",
            "category_correct",
        ):
            bad = dict(metrics, **{field: {"value": 0}})
            self.assertIn(field, target_violations(bad))
        bad = dict(metrics, false_answer_on_nonanswer_cases={"value": 1})
        self.assertIn("false_answer", target_violations(bad))

    def test_exact_quote_passes_and_added_claim_fails(self):
        request = {
            "request_id": "contract",
            "ticket_text": "Где скачать счёт?",
            "history": [],
            "kb_version": "v1",
        }
        sources = api.module.retrieve(
            request["ticket_text"], [], "v1", api.read_jsonl(PROJECT / "data/kb.jsonl")
        )
        card = proposal(request, sources)
        self.assertIsNone(
            api.module._tool.grounded_reply_reason(card, request, sources)
        )
        card["draft_reply"] = "Поддержка уже вернула все деньги."
        self.assertTrue(
            api.provenance_gate(card, request["request_id"], "v1", sources)[0]
        )
        self.assertEqual(
            api.module._tool.grounded_reply_reason(card, request, sources),
            "unsupported_reply_text",
        )

    def test_metrics_are_authenticated_and_aggregate_only(self):
        with (
            patch.dict("os.environ", {"METRICS_TOKEN": "synthetic-metrics-test"}),
            fixture() as f,
        ):
            f.enqueue()
            step(f.q, f.engine)
            self.assertEqual(f.client.get("/metrics").status_code, 401)
            self.assertEqual(f.client.get("/metrics", headers=HEADERS).status_code, 401)
            response = f.client.get(
                "/metrics", headers={"Authorization": "Bearer synthetic-metrics-test"}
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn("triage_model_attempts 1", response.text)
            self.assertNotIn("ticket_ref", response.text)
            self.assertNotIn("synthetic-metrics-test", response.text)

    def test_invalid_limit_configuration_fails_closed(self):
        from limits.settings import configured

        for value in ("0", "-1", "999999999999999", "invalid"):
            with (
                self.subTest(value=value),
                patch.dict("os.environ", {"MAX_RUNNING": value}),
            ):
                with self.assertRaises(ValueError):
                    configured("MAX_RUNNING", 2, 32)

    def test_model_tool_has_no_external_actions(self):
        contract = api.module._tool.tool_contract(api.SCHEMA)
        self.assertFalse(contract["side_effects"])
        self.assertEqual(contract["external_actions"], [])
        self.assertEqual(contract["name"], "propose_card")
