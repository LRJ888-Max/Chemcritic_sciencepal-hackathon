"""Local append-only review snapshots stored in SQLite."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid


def _serialize(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def review_title(experiment):
    """Describe a snapshot using its saved measurements, not its stable ID."""
    labels = {"isolated": "分离收率", "gc": "GC收率", "nmr": "NMR收率",
              "lc": "LC收率", "conversion": "转化率", "unknown": "口径未知"}
    results = experiment.get("results") or {}
    goal = experiment.get("user_goal") or {}
    value = results.get("yield_pct")
    outcome = "实测未填写" if value is None else f"{format(value, 'g')}% {labels.get(results.get('yield_type', 'unknown'), '口径未知')}"
    target = goal.get("minimum_yield_pct")
    target_text = "未设目标" if target is None else f"目标≥{format(target, 'g')}% {labels.get(goal.get('yield_type', 'unknown'), '口径未知')}"
    return f"{outcome} · {target_text}"


class ReviewStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS reviews (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                experiment_id TEXT NOT NULL, simulated INTEGER NOT NULL,
                goal_state TEXT NOT NULL, input_sha256 TEXT NOT NULL,
                payload TEXT NOT NULL
            )""")
            connection.commit()

    def save(self, experiment, source_text, result):
        identifier = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        payload = {"experiment": experiment, "sciencepal_text": source_text, "result": result}
        digest = hashlib.sha256(_serialize({"experiment": experiment, "sciencepal_text": source_text}).encode("utf-8")).hexdigest()
        snapshot = {"id": identifier, "created_at": created_at, "input_sha256": digest, **payload}
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)", (
                identifier, created_at, experiment["experiment_id"], int(experiment["simulated"]),
                result["goal_comparison"]["state"], digest, _serialize(snapshot),
            ))
            connection.commit()
        return snapshot

    def list(self):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT id, created_at, experiment_id, simulated, goal_state, input_sha256, payload FROM reviews ORDER BY created_at DESC, id DESC LIMIT 100").fetchall()
            summaries = []
            for row in rows:
                summary = dict(row)
                saved = json.loads(summary.pop("payload"))
                summary["simulated"] = bool(row["simulated"])
                summary["display_title"] = review_title(saved["experiment"])
                summaries.append(summary)
            return summaries

    def get(self, identifier):
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute("SELECT payload FROM reviews WHERE id = ?", (identifier,)).fetchone()
            return json.loads(row[0]) if row else None
