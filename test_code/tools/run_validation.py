"""Reproducible local acceptance cases; no scientific validity claims."""

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from app.chemcritic.server import evaluate_payload


ROOT = Path(__file__).resolve().parents[1]


def scenarios():
    base = json.loads((ROOT / "cases/C2_simulated_42_goal60.json").read_text(encoding="utf-8"))
    library = (ROOT / "cases/library/suzuki_handoff_v1.json").read_text(encoding="utf-8")
    items = []

    def add(name, edits, expected, source_text=library):
        experiment = copy.deepcopy(base)
        experiment["experiment_id"] = name
        for section, changes in edits.items():
            if changes is None:
                experiment[section] = None
            else:
                experiment[section].update(changes)
        items.append({"case_id": name, "input": {"experiment": experiment, "sciencepal_text": source_text}, "expected": expected})

    add("C2_below_target_42", {}, {"status": None, "goal_state": "below_target", "gap_pp": 18, "record_count": 7})
    add("C2_meets_target_65", {"results": {"yield_pct": 65}}, {"status": None, "goal_state": "met", "gap_pp": -5, "record_count": 7})
    add("C3_missing_evidence_unknown_assay", {"results": {"characterization": [], "yield_type": "unknown"}},
        {"status": "S3", "reason_code": "evidence_gap", "goal_state": "unavailable", "gap_pp": None, "record_count": 7})
    add("C4_no_precedent_provided", {}, {"status": None, "goal_state": "below_target", "record_count": 0}, source_text="")
    add("C4b_local_library_no_match", {"reaction": {"substrates": ["4-chloroanisole", "phenylboronic acid"]}},
        {"status": None, "goal_state": "below_target", "record_count": 0, "no_match": True})
    add("C_no_user_goal", {"user_goal": None}, {"status": None, "goal_state": "unavailable", "goal_reason": "goal_not_set", "record_count": 7})
    add("C_contradictory_characterization", {"results": {"characterization": [{"method": "1H NMR", "provenance": "user_report", "supports_target": False}]}},
        {"status": "S3", "reason_code": "evidence_gap", "goal_state": "unavailable", "gap_pp": None, "record_count": 7})
    add("C_unknown_assay_with_evidence", {"results": {"yield_type": "unknown"}},
        {"status": None, "goal_state": "unavailable", "goal_reason": "assay_unknown", "record_count": 7})
    return items


def run():
    output = ROOT / "outputs" / "validation"
    output.mkdir(parents=True, exist_ok=True)
    reports = []
    for item in scenarios():
        start = time.perf_counter()
        results = [evaluate_payload(item["input"])[2] for _ in range(3)]
        result = results[0]
        actual = {"status": result["status"], "reason_code": result["reason_code"],
                  "goal_state": result["goal_comparison"]["state"], "goal_reason": result["goal_comparison"]["reason"],
                  "gap_pp": result["goal_comparison"]["gap_pp"], "record_count": result["retrieval"]["record_count"],
                  "no_match": result["retrieval"].get("no_match", False)}
        checks = {key: actual[key] == value for key, value in item["expected"].items()}
        checks["stable_3x"] = all(r == result for r in results)
        checks["numeric_comparison_disabled"] = all(not r["numeric_comparison_allowed"] for r in result["precedent_comparisons"])
        checks["simulated_retained"] = result["simulated"] is True
        artifact = {**item, "actual": actual, "checks": checks, "result": result}
        (output / (item["case_id"] + ".json")).write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        digest = hashlib.sha256(json.dumps(item["input"], sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        reports.append({"case_id": item["case_id"], "input_sha256": digest, "verdict": "PASS" if all(checks.values()) else "FAIL",
                        "checks": checks, "actual": actual, "elapsed_seconds_for_3_runs": time.perf_counter() - start})
    summary = {"created_at": datetime.now(timezone.utc).isoformat(), "scope": "local deterministic software acceptance only",
               "case_count": len(reports), "runs_per_case": 3, "passed": sum(r["verdict"] == "PASS" for r in reports),
               "not_evaluated": ["M1完整Agent主链路", "M2原文链接可访问性", "M3原文与陈述独立核验", "M8人工复盘耗时", "M9用户理解度", "LLM输出越界", "SciencePal实时检索", "未决S1/S2分支"],
               "cases": reports}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 本地规则验收记录", "", "范围：确定性软件功能测试，不是化学有效性或Agent端到端评测。", "",
             "| 案例 | 结果 | 目标判断 | 状态 | 三次一致 |", "|---|---|---|---|---|"]
    lines += [f"| {r['case_id']} | {r['verdict']} | {r['actual']['goal_state']} | {r['actual']['status'] or '待确认'} | {r['checks']['stable_3x']} |" for r in reports]
    lines += ["", "尚未评测：" + "；".join(summary["not_evaluated"]) + "。", "", "完整输入、结果和断言见同目录各JSON；历史数据库未被本次验收写入。"]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(reports), "passed": summary["passed"], "runs": len(reports)*3, "report": str(output / "report.md")}, ensure_ascii=True))
    return 0 if summary["passed"] == len(reports) else 1


if __name__ == "__main__":
    raise SystemExit(run())
