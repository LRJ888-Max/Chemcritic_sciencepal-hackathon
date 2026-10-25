import copy
import json
from pathlib import Path
import unittest

from app.chemcritic.comparison import compare_fields, publication_key
from app.chemcritic.rules import ValidationError, review
from app.chemcritic.server import evaluate_payload
from app.chemcritic.sources import load_sources

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "cases/library/suzuki_handoff_v1.json"


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.case = json.loads((ROOT / "cases/C2_simulated_42_goal60.json").read_text(encoding="utf-8"))
        self.text = LIBRARY.read_text(encoding="utf-8")
        self.library = json.loads(self.text)

    def test_seven_results_five_papers(self):
        records, info = load_sources(self.text, self.case)
        self.assertEqual(info["record_count"], 7)
        result = review(self.case, records)
        self.assertEqual(result["retrieval"]["independent_publication_count"], 5)
        ids = {r["record_id"] for r in records}
        self.assertEqual(len(ids), 7)
        self.assertIn("SM-001-GC", ids)
        self.assertIn("SM-001-isolated", ids)
        sm4 = [r for r in records if r["source_id"] == "SM-004"]
        self.assertEqual(len({publication_key(r) for r in sm4}), 1)

    def test_gc_yield_conversion_disagreement_is_not_resolved_by_code(self):
        records, _ = load_sources(self.text, self.case)
        disputed = [r for r in records if r["source_id"] == "SM-004"]
        self.assertEqual([r["outcome"]["yield_type"] for r in disputed], ["unknown", "unknown"])
        self.assertTrue(all(r["outcome"]["interpretation_pending"] for r in disputed))
        self.assertTrue(all(r["outcome"]["original_metric"] for r in disputed))

    def test_unknown_is_not_different(self):
        records, _ = load_sources(self.text, self.case)
        fields = {r["label"]: r for r in compare_fields(self.case, records[0])}
        self.assertEqual(fields["碱名称"]["state"], "unknown")
        self.assertEqual(fields["是否加碱"]["state"], "unknown")
        self.assertEqual(fields["催化剂名称"]["state"], "same")
        self.assertEqual(fields["溶剂"]["state"], "different")
        self.assertEqual(fields["温度（°C）"]["state"], "unknown")

    def test_exact_pair_chinese_aliases_and_out_of_library(self):
        self.case["reaction"]["substrates"] = ["4-溴苯甲醚", "苯硼酸"]
        self.assertEqual(len(load_sources(self.text, self.case)[0]), 7)
        self.case["reaction"]["substrates"] = ["4-chloroanisole", "phenylboronic acid"]
        records, info = load_sources(self.text, self.case)
        self.assertEqual(records, [])
        self.assertTrue(info["no_match"])
        self.assertFalse(info["live_retrieval_performed"])

    def test_same_assay_never_opens_numeric_comparison(self):
        records, _ = load_sources(self.text, self.case)
        for kind in ["isolated", "gc", "unknown"]:
            self.case["results"]["yield_type"] = kind
            result = review(self.case, records)
            self.assertTrue(all(not item["numeric_comparison_allowed"] for item in result["precedent_comparisons"]))
            self.assertFalse(result["numeric_comparison_policy"]["enabled_in_mvp"])

    def test_source_ids_cannot_fake_independent_papers(self):
        records, _ = load_sources(self.text, self.case)
        sources = [copy.deepcopy(r) for r in records if r["source_id"] == "SM-004"]
        sources[1]["source_id"] = "FAKE-SECOND-PAPER"
        self.assertEqual(review(self.case, sources)["retrieval"]["independent_publication_count"], 1)

    def test_no_goal_never_raised_by_high_literature_values(self):
        self.case["user_goal"] = None
        _, _, result = evaluate_payload({"experiment": self.case, "sciencepal_text": self.text})
        self.assertIsNone(result["status"])
        self.assertEqual(result["goal_comparison"]["reason"], "goal_not_set")
        self.assertIn("target_comparison", result["blocked_dimensions"])
        self.assertIn("precedent_numeric", result["blocked_dimensions"])

    def test_missing_evidence_has_s3_and_no_numeric_conclusion(self):
        self.case["results"]["characterization"] = []
        result = review(self.case)
        self.assertEqual(result["status"], "S3")
        self.assertEqual(result["reason_code"], "evidence_gap")
        self.assertIsNone(result["goal_comparison"]["gap_pp"])
        self.assertEqual(result["goal_comparison"]["state"], "unavailable")

    def test_malformed_library_rejected(self):
        self.library["records"][0]["conditions"] = None
        with self.assertRaises(ValidationError):
            load_sources(json.dumps(self.library), self.case)

    def test_comparisons_do_not_mutate_records(self):
        before = copy.deepcopy(self.library)
        records, _ = load_sources(self.text, self.case)
        first = review(self.case, records)
        for _ in range(3):
            self.assertEqual(review(self.case, records), first)
        self.assertEqual(json.loads(self.text), before)


if __name__ == "__main__":
    unittest.main()
