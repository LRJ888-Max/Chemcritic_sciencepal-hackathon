import copy
import json
from pathlib import Path
import unittest
import subprocess
import sys

from app.chemcritic import ValidationError, review


ROOT = Path(__file__).resolve().parents[1]


class RulesTests(unittest.TestCase):
    def setUp(self):
        self.case = json.loads((ROOT / "cases/C2_simulated_42_goal60.json").read_text(encoding="utf-8"))
        self.sources = json.loads((ROOT / "cases/SM004_precedent.json").read_text(encoding="utf-8"))

    def test_reference_case(self):
        result = review(self.case, self.sources)
        self.assertEqual(result["goal_comparison"], {"state": "below_target", "reason": "goal_below", "gap_pp": 18.0})
        self.assertTrue(result["simulated"])
        self.assertEqual(result["base"]["state"], "unknown")
        self.assertEqual(result["product_evidence"]["state"], "user_report_only")
        self.assertFalse(result["product_evidence"]["independently_verified_by_system"])
        self.assertEqual(result["precedent_comparisons"][0]["reason"], "assay_mismatch")
        self.assertIsNone(result["status"])

    def test_target_boundary_and_zero(self):
        for value, state, gap in [(60, "met", 0), (61, "met", -1), (0, "below_target", 60), (59.9, "below_target", 0.1)]:
            with self.subTest(value=value):
                self.case["results"]["yield_pct"] = value
                result = review(self.case)["goal_comparison"]
                self.assertEqual((result["state"], result["gap_pp"]), (state, gap))

    def test_absent_goal_never_inferred_from_literature(self):
        self.case["user_goal"] = None
        result = review(self.case, self.sources)
        self.assertEqual(result["goal_comparison"]["reason"], "goal_not_set")
        self.assertIn("target_comparison", result["blocked_dimensions"])

    def test_unknown_and_mismatched_goal_assay(self):
        for assay, reason in [("unknown", "assay_unknown"), ("gc", "assay_mismatch")]:
            self.case["results"]["yield_type"] = assay
            self.assertEqual(review(self.case)["goal_comparison"]["reason"], reason)

    def test_missing_yield(self):
        self.case["results"]["yield_pct"] = None
        self.assertEqual(review(self.case)["goal_comparison"]["reason"], "outcome_missing")

    def test_no_sources_does_not_change_goal_or_product(self):
        with_sources, empty = review(self.case, self.sources), review(self.case)
        for key in ("goal_comparison", "product_evidence"):
            self.assertEqual(with_sources[key], empty[key])
        self.assertFalse(empty["retrieval"]["live_retrieval_performed"])

    def test_same_assay_does_not_authorize_comparison(self):
        self.sources[0]["outcome"]["yield_type"] = "isolated"
        comparison = review(self.case, self.sources)["precedent_comparisons"][0]
        self.assertFalse(comparison["numeric_comparison_allowed"])
        self.assertEqual(comparison["reason"], "mvp_numeric_disabled")

    def test_missing_and_contradictory_evidence(self):
        self.case["results"]["characterization"] = []
        self.assertEqual(review(self.case)["product_evidence"]["state"], "missing")
        self.case["results"]["characterization"] = [{"method": "NMR", "provenance": "user_report", "supports_target": False}]
        result = review(self.case)
        self.assertEqual(result["product_evidence"]["state"], "contradictory")
        self.assertFalse(result["product_evidence"]["independently_verified_by_system"])

    def test_explicit_absence_and_conflict(self):
        self.case["conditions"]["base_status"] = "absent"
        self.assertEqual(review(self.case)["base"]["state"], "absent")
        self.case["conditions"]["base_name"] = "NaOtBu"
        with self.assertRaises(ValidationError):
            review(self.case)

    def test_invalid_numeric_inputs(self):
        for value in [-1, 101, True, "42", float("nan"), float("inf")]:
            with self.subTest(value=value):
                self.case["results"]["yield_pct"] = value
                with self.assertRaises(ValidationError):
                    review(self.case)

    def test_simulated_is_required(self):
        del self.case["simulated"]
        with self.assertRaises(ValidationError):
            review(self.case)

    def test_source_locator_and_duplicates(self):
        self.sources[0]["locator"] = ""
        with self.assertRaises(ValidationError):
            review(self.case, self.sources)
        self.sources[0]["locator"] = "Table 1 entry 7"
        with self.assertRaises(ValidationError):
            review(self.case, self.sources * 2)

    def test_repeat_count_alone_is_not_repeatability(self):
        self.case["results"]["repeat_attempts"] = 3
        self.assertIn("repeatability", review(self.case)["blocked_dimensions"])

    def test_no_reasking_known_loading_and_assay(self):
        fields = {q["field"] for q in review(self.case)["followups"]}
        self.assertNotIn("conditions.catalyst_loading_mol_pct", fields)
        self.assertNotIn("results.yield_type", fields)
        self.assertNotIn("user_goal.minimum_yield_pct", fields)
        self.assertIn("conditions.catalyst_loading_basis", fields)

    def test_malformed_enum_returns_validation_error(self):
        self.case["results"]["yield_type"] = []
        with self.assertRaises(ValidationError):
            review(self.case)

    def test_cli_utf8_output_and_missing_file(self):
        command = [sys.executable, "-m", "app.chemcritic"]
        result = subprocess.run(command + ["cases/C2_simulated_42_goal60.json", "--precedents", "cases/SM004_precedent.json"], cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout.decode("utf-8"))
        self.assertIn("模拟案例", output["warnings"][0])
        self.assertEqual(output["goal_comparison"]["gap_pp"], 18)
        invalid = subprocess.run(command + ["cases/nonexistent.json"], cwd=ROOT, capture_output=True)
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(json.loads(invalid.stderr.decode("utf-8"))["error"], "invalid_input")

    def test_deterministic_and_non_mutating(self):
        before = copy.deepcopy((self.case, self.sources))
        result = review(self.case, self.sources)
        for _ in range(3):
            self.assertEqual(result, review(self.case, self.sources))
        self.assertEqual(before, (self.case, self.sources))


if __name__ == "__main__":
    unittest.main()
