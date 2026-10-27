"""评测指标：FPR / FNR / BDA / CAL。

定义（与技术方案 §7 一致）：

    FPR（假精确率）= 引擎判 COMPARABLE、但 gold 判非 COMPARABLE 的对数 / 引擎判 COMPARABLE 的对数
    FNR（漏拒率）  = 引擎判非 COMPARABLE、但 gold 判 COMPARABLE 的对数 / gold 判 COMPARABLE 的对数
    BDA（维度归因准确率）= 阻断维度判对的对数 / **gold 给出了阻断维度的对数**
    CAL（校准分级准确率）= calibration 判对的对数 / **gold 标注了 calibration 的对数**

两点纪律（来自 B 的评审，2026-10-26）：
1. **分母为 0 时必须输出 `null`**，不得输出 `0.0`——0.0 会被读成"完美"，实际是"无样本"。
2. **BDA 的分母只算 gold_dim 非空的对**。若用全部对做分母，`gold_dim=None` 的对会被算成
   `dim_ok=True`，从而虚高。

语料与标注：
- 记录：`data/records.json`（8 条已核）+ `data/pool30.json`（32 条，含 P35）
- 标注：`eval/goldset.json`；`status=independent` 的 9 对（G13–G21）才是正式指标
- `USER-B` / `USER-C` 是**合成用户档**（非真实实验），只为让 FPR/FNR 有分母

用法：
    python eval/metrics.py                # 全部对（含 seed）
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
import tier as tier_mod  # noqa: E402
from schemas import COMPARABLE, Record  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDSET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "goldset.json")
DATA_FILES = [
    os.path.join(BASE, "data", "records.json"),
    os.path.join(BASE, "data", "pool30.json"),
]


def load_all_records():
    """合并 records.json 与 pool30.json（按 record_id 去重），档位由受控词表重算。"""
    raw_by_id = {}
    order = []
    for path in DATA_FILES:
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        subject = payload.get("subject") or {}
        for raw in payload.get("records", []):
            rid = raw.get("record_id")
            if not rid or rid in raw_by_id:
                continue
            if not raw.get("substrate"):
                raw["substrate"] = subject
            raw_by_id[rid] = raw
            order.append(rid)

    records = []
    for rid in order:
        rec = Record.from_dict(raw_by_id[rid])
        if rec.catalytic_profile:
            meta = tier_mod.tier_of(rec.catalytic_profile)
            meta["annotated"] = rec.tier
            rec.tier_meta = meta
            if meta.get("tier"):
                rec.tier = meta["tier"]
        records.append(rec)
    return records


def _synth_user(name: str, spec: dict) -> Record:
    raw = {
        "record_id": name,
        "substrate": spec.get("substrate") or {},
        "conditions": spec.get("conditions") or {},
        "assay": spec.get("assay") or {},
        "outcome": {
            "value": spec.get("outcome_value"),
            "outcome_type": spec.get("outcome_type"),
            "label_raw": "用户现场报告（合成档，非真实实验）",
        },
        "tier": spec.get("tier"),
        "evidence_grade": "user_report",
    }
    return Record.from_dict(raw)


def subject_for(name: str, by_id, subjects: dict) -> Record:
    spec = subjects.get(name) or {}
    alias = spec.get("alias")
    if alias:
        name = alias

    # 与 run_demo 内置的演示用户保持一致，避免复制导致漂移
    if name == "USER-001":
        import run_demo  # 延迟导入：只在用到时加载

        return run_demo.USER
    if name == "USER-011":
        import run_demo

        return replace(run_demo.USER, record_id=name, outcome_type="unknown")
    if name == "USER-012":
        import run_demo

        return replace(run_demo.USER, record_id=name, assay={"method": "reported as yield"})

    if spec.get("substrate"):
        return _synth_user(name, spec)
    return by_id[name]


def evaluate(independent_only: bool = False) -> dict:
    records = load_all_records()
    by_id = {r.record_id: r for r in records}

    with open(GOLDSET, "r", encoding="utf-8") as fh:
        goldset = json.load(fh)
    subjects = goldset.get("subjects") or {}

    rows = []
    for pair in goldset["pairs"]:
        if independent_only and pair.get("status") != "independent":
            continue
        ref = by_id.get(pair["reference"])
        if ref is None:
            rows.append(
                {
                    "pair_id": pair["pair_id"],
                    "error": f"reference {pair['reference']} 不在语料中",
                }
            )
            continue
        subject = subject_for(pair["subject"], by_id, subjects)
        verdict = gates.judge(subject, ref)

        gold_cal = pair.get("calibration")
        rows.append(
            {
                "pair_id": pair["pair_id"],
                "subject": subject.record_id,
                "reference": pair["reference"],
                "gold": pair["gold_verdict"],
                "gold_dim": pair.get("gold_dim"),
                "gold_cal": gold_cal,
                "engine": verdict.verdict,
                "engine_dim": verdict.blocking_dimension,
                "engine_cal": verdict.calibration,
                "verdict_ok": verdict.verdict == pair["gold_verdict"],
                "dim_ok": (pair.get("gold_dim") is None)
                or (verdict.blocking_dimension == pair.get("gold_dim")),
                "cal_ok": (gold_cal is None) or (verdict.calibration == gold_cal),
                "status": pair.get("status"),
            }
        )

    rows = [r for r in rows if "error" not in r]

    engine_pos = [r for r in rows if r["engine"] == COMPARABLE]
    gold_pos = [r for r in rows if r["gold"] == COMPARABLE]
    fp = [r for r in engine_pos if r["gold"] != COMPARABLE]
    fn = [r for r in gold_pos if r["engine"] != COMPARABLE]

    # BDA 分母：只算 gold 给出了阻断维度的对（否则 None 会被算成"对"，虚高）
    dim_rows = [r for r in rows if r["gold_dim"] is not None]
    # CAL 分母：只算 gold 标注了 calibration 的对
    cal_rows = [r for r in rows if r["gold_cal"] is not None]

    return {
        "n_pairs": len(rows),
        "suite": "independent" if independent_only else "all",
        "n_records": len(records),
        "accuracy": round(sum(r["verdict_ok"] for r in rows) / len(rows), 3) if rows else None,
        # 分母为 0 → null，不得写 0.0（B 于 2026-10-25 指出）
        "fpr": round(len(fp) / len(engine_pos), 3) if engine_pos else None,
        "fnr": round(len(fn) / len(gold_pos), 3) if gold_pos else None,
        "denominator_fpr": len(engine_pos),
        "denominator_fnr": len(gold_pos),
        "defined": bool(engine_pos and gold_pos),
        "bda": round(sum(r["dim_ok"] for r in dim_rows) / len(dim_rows), 3) if dim_rows else None,
        "denominator_bda": len(dim_rows),
        "cal": round(sum(r["cal_ok"] for r in cal_rows) / len(cal_rows), 3) if cal_rows else None,
        "denominator_cal": len(cal_rows),
        "false_positive": [r["pair_id"] for r in fp],
        "false_negative": [r["pair_id"] for r in fn],
        "errors": [r["pair_id"] for r in rows if not (r["verdict_ok"] and r["dim_ok"])],
        "rows": rows,
    }


def _fmt(value, denominator) -> str:
    if value is None:
        return "undefined（分母=0，不得写 0.0）"
    return f"{value}（n={denominator}）"


def main() -> int:
    independent_only = "--independent" in sys.argv
    result = evaluate(independent_only)
    printable = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    print()
    print(f"  语料记录数 = {result['n_records']}，标注对数 = {result['n_pairs']}")
    print(f"  FPR  = {_fmt(result['fpr'], result['denominator_fpr'])}")
    print(f"  FNR  = {_fmt(result['fnr'], result['denominator_fnr'])}")
    print(f"  BDA  = {_fmt(result['bda'], result['denominator_bda'])}  ← 维度归因准确率")
    print(f"  CAL  = {_fmt(result['cal'], result['denominator_cal'])}  ← 校准分级准确率")
    if not result["defined"]:
        print("\n  ⚠ 指标未定义：缺少 COMPARABLE 样本（任一侧）。这不是成绩，是样本不足。")
    if result["errors"]:
        print("\n不一致的用例：")
        for r in result["rows"]:
            if r["pair_id"] in result["errors"]:
                print(
                    f"  {r['pair_id']}  gold={r['gold']}/{r['gold_dim']}  "
                    f"engine={r['engine']}/{r['engine_dim']}"
                )
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
