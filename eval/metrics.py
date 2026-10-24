"""评测指标：FPR / FNR（骨架）。

定义（与 docs/plan/2026-10-25-ChemCritic-多智能体系统技术方案.md §7 一致）：

    FPR（假精确率）= 引擎判 COMPARABLE、但 gold 判非 COMPARABLE 的对数 / 引擎判 COMPARABLE 的对数
    FNR（漏拒率）  = 引擎判非 COMPARABLE、但 gold 判 COMPARABLE 的对数 / gold 判 COMPARABLE 的对数

注意：
- seed gold set 与引擎同源，指标会偏乐观，**不能当作独立评测结果**。
- 只有 eval/goldset.json 里 status=independent 的对数才计入正式指标。

用法：
    python eval/metrics.py                # 用全部对
    python eval/metrics.py --independent  # 只用独立标注的对
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace

ENGINE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine")
sys.path.insert(0, ENGINE_DIR)

import gates  # noqa: E402
import placement as placement_mod  # noqa: E402
import run_demo  # noqa: E402
from schemas import COMPARABLE  # noqa: E402

GOLDSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "goldset.json")


def subject_for(name: str, records):
    by_id = {r.record_id: r for r in records}
    user = run_demo.USER
    if name == "USER-001":
        return user
    if name == "USER-011":
        return replace(user, record_id=name, outcome_type="unknown")
    if name == "USER-012":
        return replace(user, record_id=name, assay={"method": "reported as yield"})
    return by_id[name]


def evaluate(independent_only: bool = False) -> dict:
    records = placement_mod.load_records()
    by_id = {r.record_id: r for r in records}
    with open(GOLDSET, "r", encoding="utf-8") as fh:
        goldset = json.load(fh)

    rows = []
    for pair in goldset["pairs"]:
        if independent_only and pair.get("status") != "independent":
            continue
        subject = subject_for(pair["subject"], records)
        verdict = gates.judge(subject, by_id[pair["reference"]])
        rows.append(
            {
                "pair_id": pair["pair_id"],
                "subject": pair["subject"],
                "reference": pair["reference"],
                "gold": pair["gold_verdict"],
                "gold_dim": pair["gold_dim"],
                "engine": verdict.verdict,
                "engine_dim": verdict.blocking_dimension,
                "verdict_ok": verdict.verdict == pair["gold_verdict"],
                "dim_ok": (pair["gold_dim"] is None) or (verdict.blocking_dimension == pair["gold_dim"]),
                "status": pair.get("status"),
            }
        )

    engine_pos = [r for r in rows if r["engine"] == COMPARABLE]
    gold_pos = [r for r in rows if r["gold"] == COMPARABLE]
    fp = [r for r in engine_pos if r["gold"] != COMPARABLE]
    fn = [r for r in gold_pos if r["engine"] != COMPARABLE]

    return {
        "n_pairs": len(rows),
        "suite": "independent" if independent_only else "all",
        "accuracy": round(sum(r["verdict_ok"] for r in rows) / len(rows), 3) if rows else 0.0,
        "dimension_accuracy": round(sum(r["dim_ok"] for r in rows) / len(rows), 3) if rows else 0.0,
        "fpr": round(len(fp) / len(engine_pos), 3) if engine_pos else 0.0,
        "fnr": round(len(fn) / len(gold_pos), 3) if gold_pos else 0.0,
        "false_positive": [r["pair_id"] for r in fp],
        "false_negative": [r["pair_id"] for r in fn],
        "errors": [r["pair_id"] for r in rows if not (r["verdict_ok"] and r["dim_ok"])],
        "rows": rows,
    }


def main() -> int:
    independent_only = "--independent" in sys.argv
    result = evaluate(independent_only)
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2))
    if result["errors"]:
        print("\n不一致的用例：")
        for r in result["rows"]:
            if r["pair_id"] in result["errors"]:
                print(f"  {r['pair_id']}  gold={r['gold']}/{r['gold_dim']}  engine={r['engine']}/{r['engine_dim']}")
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
