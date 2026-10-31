"""等价类划分与档内定位（骨架）。

职责边界：
- 只做"分组"与"定位"，不做"好坏判断"。
- "异常"只在档内定义；跨档只输出迁移目标，不输出正常区间。
- 档位由 `tier.tier_of()` 从受控词表字段确定性计算，**不读人工标注**（标注仅用于交叉校验）。

TODO：
- 覆盖率统计需扩展到 40–50 条 entry 后才有说服力。
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Any, Dict, List, Optional

try:  # 作为子包导入
    from . import tier as tier_mod
    from .gates import label_supported
    from .schemas import Record
except ImportError:  # 独立脚本运行
    import tier as tier_mod  # type: ignore
    from gates import label_supported  # type: ignore
    from schemas import Record  # type: ignore

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
        rec = Record.from_dict(raw)
        annotated = rec.tier
        if rec.catalytic_profile:
            meta = tier_mod.tier_of(rec.catalytic_profile)
            meta["annotated"] = annotated
            rec.tier_meta = meta
            if meta["tier"]:
                rec.tier = meta["tier"]
        records.append(rec)
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


def ensure_keys(d: dict) -> dict:
    """给每个 placement 状态补齐字段键（值可为 None / []，但键必须在）。

    依据 v8 行 103-111：提示词把 reference_values / reference_count / delta_vs_min
    当成恒存在字段来读（「Z 必须取自 placement.delta_vs_min」）。若某些 status 下整键
    缺失，agent 读到的是 undefined 而不是 null，它会以为"没有这个字段"，于是要么崩，
    要么自己补一个——补就是编造。
    """
    for k, default in (("reference_values", []), ("reference_count", 0),
                       ("delta_vs_min", None), ("delta_vs_max", None),
                       ("comparables", []), ("migration_targets", []),
                       ("observed", None), ("uncalibrated_refs", [])):
        d.setdefault(k, default)
    return d


def placement(user: Record, records: List[Record]) -> Dict[str, Any]:
    """用户落在哪个等价类、哪一档、与档内水平的差距。

    **C 方案（A 2026-10-26）：** 只有 `calibrated`（有绝对标尺）的参照参与
    差值计算与结论；`uncalibrated`（仅说明定量方式、无内标）单独列出，供展示。

    2026-10-30：所有状态分支统一过 `ensure_keys`——v8 把 reference_values /
    reference_count / delta_vs_min 当恒存在字段读，任何分支缺键都会让 agent
    读到 undefined 后自行补值（补即编造）。键必须在，值可以为 null / []。
    """
    return ensure_keys(_placement_impl(user, records))


def _placement_impl(user: Record, records: List[Record]) -> Dict[str, Any]:
    """placement 的内部实现；键补齐由包装层 placement() 负责。"""
    if user.outcome_value is None:
        # 没有实测数值就没有"定位"这回事（审计发现：此前仍返回 PLACED，observed=None）
        return {
            "status": "NO_OBSERVED_VALUE",
            "reason": "用户未提供实测收率数值",
            "action": "先取得实测值（分离称重或仪器定量），再做档内定位",
        }

    if user.outcome_type not in OUTCOME_ORDER:
        return {
            "status": "UNDEFINED",
            "reason": "收率口径未决，无法定位",
            "action": "先确认口径（分离 / GC / NMR / 转化率）",
        }

    if not label_supported(user):
        return {
            "status": "UNDEFINED",
            "reason": "用户侧口径分类缺少原话支撑，无法定位",
            "action": "先让用户明确该数值是分离收率、GC 收率、核磁定量还是转化率",
        }

    # 2026-10-29 修：参照池必须同时满足「同口径」与「同底物对」。
    # 原实现只按 outcome_type 过滤，导致 4-溴苯甲醚的池子里混进 4-溴甲苯（P28/P30），
    # 3-溴噻吩这种库外底物也拿到同一个池子并算出 delta=0。
    same_class = [r for r in records
                  if r.outcome_type == user.outcome_type and _same_pair(user, r)]
    if not same_class:
        return {
            "status": "NO_COMPARABLE",
            "reason": f"同类口径（{user.outcome_type}）下没有任何已核验记录",
            "action": "需补充该口径的文献记录，或改用其它口径定量",
        }

    tier = user.tier
    if not tier:
        # P5（B 2026-10-27）：档位算不出来时报错，不得静默回退成"跨档全池"
        return {
            "status": "TIER_UNKNOWN",
            "reason": "催化体系档位无法判定（受控词表字段缺失或非法）",
            "action": "先确认催化剂物理形态与配体族，再做档内定位",
        }

    same_tier = [r for r in same_class if r.tier == tier]
    pool_ref = same_tier or same_class
    calibrated = [r for r in pool_ref if _level(r) == "calibrated"]
    uncalibrated = [r for r in pool_ref if _level(r) != "calibrated"]
    uncal_ids = [r.record_id for r in uncalibrated]

    # I2（2026-10-28）：跨档迁移目标——**同底物 + 同产物** + 同口径 + calibrated，但档位不同。
    # 它们不得参与差值，只用于回答"想突破该往哪个体系走"。
    migration = [
        r for r in same_class
        if _level(r) == "calibrated" and r.tier and r.tier != tier
        and r.outcome_value is not None and _same_pair(user, r)
    ]
    migration_targets = [
        {"record_id": r.record_id, "value": r.outcome_value, "tier": r.tier}
        for r in sorted(migration, key=lambda r: r.outcome_value or 0, reverse=True)
    ]

    if not calibrated:
        return {
            "status": "NO_CALIBRATED_REFERENCE",
            "tier": tier,
            "reason": "同档/同口径下只有未校准参照（无内标，仅说明定量方式）",
            "uncalibrated_refs": uncal_ids,
            "action": "可展示这些参照，但不给出'低于档内水平'一类结论；如需结论，需补一条有内标或分离称重的参照",
        }

    values = [r.outcome_value for r in calibrated if r.outcome_value is not None]
    if not values:
        # 有 calibrated 参照但都没有数值 → 不得 min([]) 崩溃
        return {
            "status": "NO_REFERENCE_VALUE",
            "tier": tier,
            "comparables": [r.record_id for r in calibrated],
            "reason": "同档同口径的参照存在，但都没有可用数值",
            "action": "回原文补录参照的收率数值",
        }

    can_delta = same_tier and user.outcome_value is not None
    return {
            "status": "PLACED",
            "outcome_type": user.outcome_type,
            "tier": tier,
            "tier_basis": "同档" if same_tier else "同口径（跨档）",
            "comparables": [r.record_id for r in calibrated],
            "uncalibrated_refs": uncal_ids,
            "observed": user.outcome_value,
            "reference_min": min(values),
            "reference_max": max(values),
            "reference_values": sorted(values),
            "reference_count": len(values),
            # 差值只给"同档 + 用户有值"的情形；跨档一律 None（只并排，不定位）
            "delta_vs_min": round(user.outcome_value - min(values), 1) if can_delta else None,
            "delta_vs_max": round(user.outcome_value - max(values), 1) if can_delta else None,
            # 跨档迁移目标：不参与差值，只回答"换体系能到哪"
            "migration_targets": migration_targets,
        }


def _canon_pair(value) -> str:
    """底物/产物名称比较用的规范化（与 gates._canon 不同：这里永不返回 None）。

    ⚠️ 不能叫 `_canon`：单文件 bundle 把各模块拼进同一命名空间，
    同名函数会互相覆盖（2026-10-28 实测：placement 的版本覆盖了 gates 的版本，
    使"未记录"被当成空字符串参与比较，软门槛措辞与本地不一致）。
    """
    return str(value or "").strip().lower()


def _same_substrate_set(us: dict, rs: dict) -> bool:
    """底物组合是否同一（不看方向、不看键名）。

    2026-10-31：原实现对 ("aryl_halide","boronic_acid") 逐键比对，等于假定全库都是
    Suzuki 型。Heck（芳卤+烯烃）、Sonogashira（芳卤+炔）、Kumada（芳卤+格氏）的记录
    会被判成"不同底物"，连同口径也进不了参照池。改为「值集合两两配对」：
    键名不同、物质相同即算同一组合；两侧都必须至少两个底物。
    """
    uv = [v for k, v in (us or {}).items() if k != "product" and _canon_pair(v)]
    rv = [v for k, v in (rs or {}).items() if k != "product" and _canon_pair(v)]
    if len(uv) < 2 or len(rv) < 2 or len(uv) != len(rv):
        return False
    unmatched = list(rv)
    for a in uv:
        hit = next((b for b in unmatched
                    if _canon_pair(a) == _canon_pair(b) or _chem_same(a, b)), None)
        if hit is None:
            return False
        unmatched.remove(hit)
    return True


def _same_pair(user: Record, ref: Record) -> bool:
    """底物对 + 产物一致（迁移目标必须同底物，不能建议用户换底物）。

    三道判据，按序短路：
    1. 归一化名相等（别名表 + 大小写/空白归一）；
    2. 化学同一性（chemsense 规范 SMILES 判同，别名表漏项的兜底）；
    3. 都不行 → 不同底物。
    """
    us, rs = user.substrate or {}, ref.substrate or {}
    if not _same_substrate_set(us, rs):
        return False
    up = _canon_pair(getattr(user, "product", None) or us.get("product"))
    rp = _canon_pair(getattr(ref, "product", None) or rs.get("product"))
    # 2026-10-31：产物**只有两侧都记录了**才作为排除条件。
    # 语料层的表常只给产物编号（"3a"、"8b"），补不出名字；若把"一侧未记录"
    # 当成"不同产物"，这些记录会永远进不了参照池——入册等于白入。
    # 未记录不等于不同：这里不阻断，产物是否一致仍由 gate 2 单独给出判定。
    if up and rp:
        if up != rp and not _chem_same(
            getattr(user, "product", None) or us.get("product"),
            getattr(ref, "product", None) or rs.get("product"),
        ):
            return False
    return True


def _chem_same(a: Any, b: Any) -> bool:
    """化学同一性判同（只在两侧名字都进得了映射表时给出答案）。

    无 rdkit / 名字不识别 / chemsense 缺席 → False（回落到原字符串判同，行为不变）。
    单文件 bundle 里 chemsense 段排在 placement 前，same_compound 已在同命名空间；
    包内运行则走相对导入。
    """
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    fn = globals().get("same_compound")
    if fn is None:
        try:
            from .chemsense import same_compound as fn  # type: ignore
        except Exception:
            return False
    try:
        return bool(fn(a, b))
    except Exception:
        return False


def _level(record: Record) -> str:
    if record.outcome_type == "isolated":
        return "calibrated"
    if (record.assay or {}).get("internal_standard"):
        return "calibrated"
    return "uncalibrated"


def sensitivity(user: Record, records: List[Record], tiers: List[str]) -> Dict[str, Any]:
    """档归属敏感时的双档（多档）敏感性分析。

    档不确定只影响"档内定位"，不影响硬门槛，因此不该让流程卡住：
    - 各档结论一致 → 结论稳健，直接输出
    - 各档结论冲突 → 输出"档归属敏感"，并给出需要补录的可观察事实
    """
    results = {t: placement(replace(user, tier=t), records) for t in tiers}
    signatures = {
        t: (
            r.get("status"),
            tuple(r.get("reference_values") or []),
        )
        for t, r in results.items()
    }
    consistent = len(set(signatures.values())) == 1
    return {
        "tiers": tiers,
        "consistent": consistent,
        "per_tier": results,
        "action": None if consistent else "档归属敏感：需补录可观察的化学事实（催化剂物理形态），或分别按各档解读",
    }
