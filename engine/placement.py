"""等价类划分与档内定位（骨架）。

职责边界：
- 只做"分组"与"定位"，不做"好坏判断"。
- "异常"只在档内定义；跨档只输出迁移目标，不输出正常区间。

TODO（待化学签字后确认）：
- 档位判定目前读取 records.json 的 tier 字段；上线后应由 `tier_of()` 按规则动态判定。
- 覆盖率统计需扩展到 40–50 条 entry 后才有说服力。
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from schemas import Record

OUTCOME_ORDER = ("isolated", "gc", "nmr", "conversion")


def load_records(path: Optional[str] = None) -> List[Record]:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = path or os.path.join(base, "data", "records.json")
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    subject = payload.get("subject") or {}
    records = []
    for raw in payload["records"]:
        if not raw.get("substrate"):
            raw["substrate"] = subject
        records.append(Record.from_dict(raw))
    return records


def equivalence_classes(records: List[Record]) -> Dict[str, List[Record]]:
    """按结果口径分组；`unknown` 单独成一类，不参与任何比较。"""
    classes: Dict[str, List[Record]] = {}
    for r in records:
        classes.setdefault(r.outcome_type, []).append(r)
    return classes


def class_summary(records: List[Record]) -> List[Dict[str, Any]]:
    out = []
    for key, members in sorted(equivalence_classes(records).items(), key=lambda kv: kv[0]):
        values = [m.outcome_value for m in members if m.outcome_value is not None]
        out.append(
            {
                "outcome_type": key,
                "count": len(members),
                "values": values,
                "records": [m.record_id for m in members],
                "comparable": key in OUTCOME_ORDER,
            }
        )
    return out


def coverage(records: List[Record]) -> Dict[str, Any]:
    """可比类覆盖率 = 最大等价类大小 / 报告值总数。

    分母包含口径未决（`unknown`）的数值——它们同样是被报告出来的值，
    只是无法进入任何等价类。这样的算法更能反映"文献里到底有多少数字真的能比"。
    """
    all_values = [r for r in records if r.outcome_value is not None]
    classes = {k: v for k, v in equivalence_classes(records).items() if k in OUTCOME_ORDER}
    largest = max((len(v) for v in classes.values()), default=0)
    total = len(all_values)
    return {
        "total_values": total,
        "largest_class": largest,
        "coverage": round(largest / total, 3) if total else 0.0,
        "classes": {k: len(v) for k, v in sorted(equivalence_classes(records).items())},
        "unresolved": len([r for r in records if r.outcome_type not in OUTCOME_ORDER]),
    }


def placement(user: Record, records: List[Record]) -> Dict[str, Any]:
    """用户落在哪个等价类、哪一档、与档内水平的差距。"""
    if user.outcome_type not in OUTCOME_ORDER:
        return {
            "status": "UNDEFINED",
            "reason": "收率口径未决，无法定位",
            "action": "先确认口径（分离 / GC / NMR / 转化率）",
        }

    same_class = [r for r in records if r.outcome_type == user.outcome_type]
    if not same_class:
        return {
            "status": "NO_COMPARABLE",
            "reason": f"同类口径（{user.outcome_type}）下没有任何已核验记录",
            "action": "需补充该口径的文献记录，或改用其它口径定量",
        }

    tier = user.tier
    same_tier = [r for r in same_class if r.tier == tier] if tier else []
    ref = same_tier or same_class
    values = [r.outcome_value for r in ref if r.outcome_value is not None]

    return {
        "status": "PLACED",
        "outcome_type": user.outcome_type,
        "tier": tier,
        "tier_basis": "同档" if same_tier else "同口径（跨档）",
        "comparables": [r.record_id for r in ref],
        "observed": user.outcome_value,
        "reference_min": min(values),
        "reference_max": max(values),
        "reference_values": sorted(values),
        "delta_vs_min": round(user.outcome_value - min(values), 1),
        "delta_vs_max": round(user.outcome_value - max(values), 1),
    }
