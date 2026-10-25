import copy
import json
from pathlib import Path
import unittest

from app.chemcritic.server import evaluate_payload

ROOT = Path(__file__).resolve().parents[1]


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.case = json.loads((ROOT / "cases/C2_simulated_42_goal60.json").read_text(encoding="utf-8"))
        self.library = (ROOT / "cases/library/suzuki_handoff_v1.json").read_text(encoding="utf-8")

    def evaluate(self, source=None):
        return evaluate_payload({"experiment": self.case, "sciencepal_text": self.library if source is None else source})[2]

    def test_three_action_limit_no_request_for_known_values(self):
        result = self.evaluate()
        self.assertEqual(len(result["next_checks"]), 3)
        self.assertEqual(result["next_checks"][0]["rule_id"], "A05_REPORTED_EVIDENCE")
        fields = {field for action in result["next_checks"] for field in action["triggered_fields"]}
        self.assertNotIn("results.yield_type", fields)
        self.assertNotIn("conditions.catalyst_loading_mol_pct", fields)
        self.assertIn("conditions.catalyst_loading_basis", fields)

    def test_missing_evidence_and_assay_take_priority(self):
        self.case["results"].update(characterization=[], yield_type="unknown")
        actions = self.evaluate()["next_checks"]
        self.assertEqual([action["rule_id"] for action in actions][:2], ["A01_PRODUCT_EVIDENCE", "A03_OUTCOME_TYPE"])
        self.assertTrue(all(action["limits"] for action in actions))

    def test_citations_are_subset_of_actual_sources(self):
        result = self.evaluate()
        source_ids = {source["record_id"] for source in result["precedent_comparisons"]}
        for action in result["next_checks"]:
            for source in action["related_sources"]:
                self.assertIn(source["record_id"], source_ids)
                self.assertTrue(source["locator"])
        base = next(action for action in result["next_checks"] if action["rule_id"] == "A07_BASE_RECORD")
        self.assertTrue(base["related_sources"])

    def test_no_source_does_not_fabricate_citations(self):
        result = self.evaluate(source="")
        self.assertTrue(any(action["rule_id"] == "A06_FIND_PRECEDENT" for action in result["next_checks"]))
        self.assertTrue(all(not action["related_sources"] for action in result["next_checks"]))

    def test_actions_do_not_modify_rules_or_input(self):
        original = copy.deepcopy(self.case)
        first = self.evaluate()
        self.assertEqual(first["goal_comparison"]["gap_pp"], 18)
        self.assertFalse(first["numeric_comparison_policy"]["enabled_in_mvp"])
        self.assertEqual(self.evaluate(), first)
        self.assertEqual(self.case, original)

    def test_base_action_tracks_remaining_fields_then_disappears(self):
        self.case["conditions"].update(base_status="present", base_name="Na2CO3")
        partial = self.evaluate()
        action = next(item for item in partial["next_checks"] if item["rule_id"] == "A07_BASE_RECORD")
        self.assertEqual(action["triggered_fields"], ["conditions.base_equivalents"])
        self.case["conditions"]["base_equivalents"] = 2
        completed = self.evaluate()
        self.assertNotIn("A07_BASE_RECORD", [item["rule_id"] for item in completed["next_checks"]])
        self.assertFalse(any(field.startswith("conditions.base_") for field in completed["unknowns"]))
        self.assertIn("A09_CONTEXT_RECORD", [item["rule_id"] for item in completed["next_checks"]])
        self.assertEqual(completed["goal_comparison"], partial["goal_comparison"])

    def test_resolved_evidence_and_assay_remove_obsolete_actions(self):
        self.case["results"].update(characterization=[], yield_type="unknown")
        initial = self.evaluate()
        self.assertEqual(initial["goal_comparison"]["state"], "unavailable")
        self.case["results"].update(yield_type="isolated", characterization=[{
            "method": "1H NMR", "supports_target": True,
            "provenance": "human_review", "review_reference": "SIMULATED-review-001",
            "raw_spectrum_provided": True,
        }])
        completed = self.evaluate()
        action_ids = {item["rule_id"] for item in completed["next_checks"]}
        self.assertTrue(action_ids.isdisjoint({"A01_PRODUCT_EVIDENCE", "A03_OUTCOME_TYPE", "A05_REPORTED_EVIDENCE"}))
        self.assertEqual(completed["goal_comparison"]["state"], "below_target")
        self.assertFalse(completed["product_evidence"]["independently_verified_by_system"])

    def test_meeting_goal_does_not_erase_evidence_gaps(self):
        initial = self.evaluate()
        self.case["results"]["yield_pct"] = 65
        completed = self.evaluate()
        self.assertEqual(completed["goal_comparison"]["state"], "met")
        self.assertEqual(completed["next_checks"], initial["next_checks"])
        self.assertIn("causal_attribution", completed["blocked_dimensions"])
        self.assertFalse(completed["numeric_comparison_policy"]["enabled_in_mvp"])


if __name__ == "__main__":
    unittest.main()
