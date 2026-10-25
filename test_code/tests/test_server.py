import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.chemcritic.server import create_server
from app.chemcritic.storage import ReviewStore, review_title


ROOT = Path(__file__).resolve().parents[1]


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "reviews.sqlite3"
        self.server = create_server(0, self.db)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.payload = {
            "experiment": json.loads((ROOT / "cases/C2_simulated_42_goal60.json").read_text(encoding="utf-8")),
            "sciencepal_text": (ROOT / "cases/sciencepal/SM004_raw.json").read_text(encoding="utf-8"),
        }

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, payload=None, headers=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(self.base + path, data=data, headers=headers or ({"Content-Type": "application/json"} if payload is not None else {}))
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            body = response.read()
            content = json.loads(body) if "application/json" in response.headers.get("Content-Type", "") else body.decode("utf-8")
            return response.status, content

    def test_page_assets_and_bootstrap(self):
        for path in ("/", "/app.js", "/style.css"):
            status, content = self.request(path)
            self.assertEqual(status, 200)
            self.assertTrue(content)
        status, data = self.request("/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(data["experiment"]["results"]["yield_pct"], 42)
        self.assertEqual(json.loads(data["sciencepal_text"])["task_id"], "SM-004")
        self.assertEqual(self.request("/../README.md")[0], 404)

    def test_review_without_saving(self):
        status, data = self.request("/api/review", self.payload)
        self.assertEqual(status, 200)
        self.assertEqual(data["result"]["goal_comparison"]["gap_pp"], 18)
        self.assertTrue(data["result"]["simulated"])
        self.assertEqual(data["result"]["retrieval"]["mode"], "sciencepal_file_import")
        self.assertEqual(self.request("/api/history")[1], [])

    def test_library_endpoint_and_saved_replay(self):
        status, library = self.request("/api/library")
        self.assertEqual(status, 200)
        self.assertEqual(len(library["records"]), 7)
        self.payload["sciencepal_text"] = json.dumps(library, ensure_ascii=False)
        status, saved = self.request("/api/reviews", self.payload)
        self.assertEqual(status, 201)
        self.assertEqual(saved["result"]["retrieval"]["mode"], "local_library_exact_pair")
        self.assertEqual(saved["result"]["retrieval"]["independent_publication_count"], 5)
        reopened = self.request("/api/history/" + saved["id"])[1]
        self.assertEqual(reopened["sciencepal_text"], self.payload["sciencepal_text"])
        self.assertEqual(len(reopened["result"]["precedent_comparisons"]), 7)

    def test_save_reopen_restart_and_preserve_source(self):
        status, saved = self.request("/api/reviews", self.payload)
        self.assertEqual(status, 201)
        self.assertEqual(saved["sciencepal_text"], self.payload["sciencepal_text"])
        self.assertEqual(saved["experiment"], self.payload["experiment"])
        status, reopened = self.request("/api/history/" + saved["id"])
        self.assertEqual(reopened, saved)
        self.assertEqual(self.request("/api/history")[1][0]["id"], saved["id"])
        # A new store instance reads persisted bytes, rather than process memory.
        self.assertEqual(ReviewStore(self.db).get(saved["id"]), saved)
        self.assertEqual(self.request("/api/history/no-such-record")[0], 404)

    def test_modified_attempt_is_new_snapshot(self):
        original = self.request("/api/reviews", self.payload)[1]
        updated = copy.deepcopy(self.payload)
        updated["experiment"]["results"]["yield_pct"] = 65
        updated["experiment"]["simulated"] = False
        saved = self.request("/api/reviews", updated)[1]
        self.assertNotEqual(original["id"], saved["id"])
        self.assertNotEqual(original["input_sha256"], saved["input_sha256"])
        self.assertFalse(saved["result"]["simulated"])
        self.assertEqual(saved["result"]["goal_comparison"]["state"], "met")
        self.assertEqual(ReviewStore(self.db).get(original["id"])["result"]["goal_comparison"]["state"], "below_target")
        history = self.request("/api/history")[1]
        indexed = {item["id"]: item for item in history}
        self.assertEqual(indexed[original["id"]]["display_title"], "42% 分离收率 · 目标≥60% 分离收率")
        self.assertEqual(indexed[saved["id"]]["display_title"], "65% 分离收率 · 目标≥60% 分离收率")
        self.assertEqual(indexed[saved["id"]]["experiment_id"], original["experiment"]["experiment_id"])
        self.assertNotIn("payload", indexed[saved["id"]])

    def test_history_titles_preserve_zero_unknowns_and_different_assays(self):
        experiment = self.payload["experiment"]
        experiment["results"]["yield_pct"] = 0
        experiment["user_goal"] = None
        self.assertEqual(review_title(experiment), "0% 分离收率 · 未设目标")
        experiment["results"]["yield_pct"] = None
        experiment["results"]["yield_type"] = "unknown"
        self.assertEqual(review_title(experiment), "实测未填写 · 未设目标")
        experiment["results"].update(yield_pct=65, yield_type="gc")
        experiment["user_goal"] = {"minimum_yield_pct": 60, "yield_type": "isolated"}
        self.assertEqual(review_title(experiment), "65% GC收率 · 目标≥60% 分离收率")

    def test_forged_client_result_is_not_saved(self):
        self.payload["result"] = {"goal_comparison": {"state": "met"}}
        status, saved = self.request("/api/reviews", self.payload)
        self.assertEqual(status, 201)
        self.assertEqual(saved["result"]["goal_comparison"]["state"], "below_target")

    def test_bad_experiment_and_bad_source_do_not_create_history(self):
        original = copy.deepcopy(self.payload)
        self.payload["experiment"]["results"]["yield_pct"] = 101
        status, error = self.request("/api/reviews", self.payload)
        self.assertEqual(status, 400)
        self.assertEqual(error["field"], "results.yield_pct")
        original["sciencepal_text"] = "not JSON"
        self.assertEqual(self.request("/api/reviews", original)[0], 400)
        self.assertEqual(self.request("/api/history")[1], [])

    def test_no_source_and_no_goal(self):
        self.payload["sciencepal_text"] = ""
        self.payload["experiment"]["user_goal"] = None
        status, data = self.request("/api/review", self.payload)
        self.assertEqual(status, 200)
        self.assertEqual(data["result"]["goal_comparison"]["reason"], "goal_not_set")
        self.assertEqual(data["result"]["precedent_comparisons"], [])

    def test_reject_cross_origin_writes(self):
        headers = {"Content-Type": "application/json", "Origin": "https://example.org"}
        self.assertEqual(self.request("/api/reviews", self.payload, headers)[0], 403)
        self.assertEqual(self.request("/api/history")[1], [])


if __name__ == "__main__":
    unittest.main()
