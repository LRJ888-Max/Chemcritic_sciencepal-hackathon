"""补证杠杆：把"不能比"换算成"补哪一条最值"。

为什么需要它：
- 只输出 `BLOCKED / NEEDS_EVIDENCE` 的系统，用户看到的是"都不行"，下一步仍是空白；
- 但同一批评审对象里，缺的证据不是等价的：补一条可能解锁 4 对，补另一条一对也解锁不了。
  于是"下一个动作"可以被**排序**，而不是靠猜。

算法（确定性，无 LLM 参与）：
1. 对每一对 (用户, 参照) 取硬门槛的两种阻断：
   - `permanent`：任一硬门槛 `FAIL` —— 确定不可比，补证据也改不了结论；
   - `missing`：硬门槛 `UNKNOWN` —— 缺证据，补上即可重新判定。
2. 把缺失维度折算成**一次真实动作**（字段按"谁去做、做什么"合并，不按门槛编号拆）：
   维度 3 与 4 在文献侧都由"回原文重读一遍"解决，在用户侧都由"说明你怎么测的"解决，
   所以各合并为一个字段——否则会把一次动作虚报成两次工作量。
3. **单字段边际值**：`solo` = 只补这一条就能解锁的对数；`involved` = 牵涉到的对数。
4. **贪心补证计划**：每步取"新解锁对数最多"的字段；若无字段能立即解锁，
   则取"推进最多未完成对"的字段（保证有限步内收敛）。得到 k=1,2,3… 的解锁曲线。
   这是最小集合覆盖的贪心近似——不保证最优，但足以回答"先补哪一个"。
5. **语料级杠杆**（`corpus_leverage`）：把参照库自身两两比较，"补齐哪几条记录的口径
   能让可比对最多"——这是关于文献的可核对结论，不依赖演示用户。

铁律：
- 本模块不产生判定，只对 `gates.judge()` 的结果做统计；
- `FAIL` 的对绝不计入可解锁——否则等于承诺"补证据能改变确定不同的口径"。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:  # 作为子包导入
    from .gates import assay_blame, judge
    from .schemas import (
        ALLOWED_OUTCOME_TYPES,
        COMPARABLE,
        FAIL,
        HARD_GATES,
        UNKNOWN,
        Record,
    )
except ImportError:  # 独立脚本运行
    from gates import assay_blame, judge  # type: ignore
    from schemas import (  # type: ignore
        ALLOWED_OUTCOME_TYPES,
        COMPARABLE,
        FAIL,
        HARD_GATES,
        UNKNOWN,
        Record,
    )

FIELD_ORDER = {
    "substrate:subject": 0,
    "substrate:reference": 1,
    "product:subject": 2,
    "product:reference": 3,
    "caliber:subject": 4,
    "caliber:reference": 5,
}

FIELD_LABELS = {
    "substrate:subject": "补你的底物结构（芳基卤 + 硼酸的 SMILES 或名称）",
    "substrate:reference": "回原文补齐该条记录的底物结构",
    "product:subject": "补你的目标产物结构",
    "product:reference": "回原文补齐该条记录的产物结构",
    "caliber:subject": "说明你这个数值怎么测的（分离称重 / GC / NMR，含内标与基准）",
    "caliber:reference": "回原文确认该条记录的口径与校准（含内标）",
}

FIELD_SHORT = {
    "substrate:subject": "你的底物结构",
    "substrate:reference": "某条记录的底物结构",
    "product:subject": "你的产物结构",
    "product:reference": "某条记录的产物结构",
    "caliber:subject": "你的测法说明",
    "caliber:reference": "某条记录的口径与校准",
}

_BLANK = {"", "none", "null", "unknown", "n/a", "待核验", "未知"}


def _blank(value: Any) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in _BLANK


def _field_of(gate: int, side: str) -> str:
    """门槛维度 → 一次可执行动作。"""
    if gate in (3, 4):
        return f"caliber:{side}"
    if gate == 1:
        return f"substrate:{side}"
    return f"product:{side}"


def _side_of(subject: Record, ref: Record, gate: int) -> str:
    """这条缺失该由谁补：subject（用户自己）还是 reference（回原文）。"""
    if gate == 4:
        return assay_blame(subject, ref) or "reference"
    if gate == 3:
        return "subject" if subject.outcome_type not in ALLOWED_OUTCOME_TYPES else "reference"
    if gate == 1:
        for key in ("aryl_halide", "boronic_acid"):
            if _blank(subject.substrate.get(key)):
                return "subject"
        return "reference"
    if gate == 2:
        return "subject" if _blank(subject.substrate.get("product")) else "reference"
    return "reference"


def _subject_status(subject: Record) -> Optional[str]:
    """用户侧还没准备好时，任何"补文献"的建议都是噪声。"""
    if subject.outcome_value is None:
        return "NO_SUBJECT_VALUE"
    if subject.outcome_type in ALLOWED_OUTCOME_TYPES and not (subject.label_raw or "").strip():
        return "SUBJECT_CALIBER_UNSTATED"
    return None


def _greedy_plan(units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """最小"补证集"的贪心近似：先解锁最多的，其次推进最多的。"""
    chosen: List[str] = []
    covered: set = set()
    plan: List[Dict[str, Any]] = []
    while True:
        best_key, best_score, best_new = None, (0, 0), set()
        candidates = sorted({k for u in units for k in u["missing"] if k not in chosen}, key=FIELD_ORDER.get)
        for candidate in candidates:
            trial = set(chosen) | {candidate}
            newly = {i for i, u in enumerate(units) if set(u["missing"]) <= trial}
            touched = sum(
                1 for i, u in enumerate(units)
                if candidate in u["missing"] and i not in covered
            )
            score = (len(newly - covered), touched)
            if score > best_score:
                best_key, best_score, best_new = candidate, score, newly
        if best_key is None:
            break
        chosen.append(best_key)
        covered |= best_new
        plan.append({
            "field": best_key,
            "short": FIELD_SHORT[best_key],
            "action": FIELD_LABELS[best_key],
            "unlocks_new": len(best_new - covered),
            "cumulative_pairs": len(covered),
        })
    # unlocks_new 以"相对上一步的新增"为准（covered 中途更新过，这里统一重算）
    for prev, step in zip([None] + plan[:-1], plan):
        before = prev["cumulative_pairs"] if prev else 0
        step["unlocks_new"] = step["cumulative_pairs"] - before
    return plan


def pair_profile(subject: Record, records: List[Record]) -> List[Dict[str, Any]]:
    """逐对拆出阻断画像（不改变任何判定，只解读 judge 的结果）。"""
    status = _subject_status(subject)
    rows: List[Dict[str, Any]] = []
    for ref in records:
        verdict = judge(subject, ref)
        if verdict.verdict == COMPARABLE:
            rows.append({"ref": ref.record_id, "verdict": verdict.verdict,
                         "permanent": [], "missing": []})
            continue
        permanent = [g for g in HARD_GATES if verdict.gates[g].status == FAIL]
        if status == "SUBJECT_CALIBER_UNSTATED":
            # judge 在口径未声明时会整体返回 UNKNOWN；真实缺口只有一个：说明你怎么测的。
            missing = ["caliber:subject"]
        elif permanent:
            missing = []
        else:
            missing = sorted(
                {
                    _field_of(g, _side_of(subject, ref, g))
                    for g in HARD_GATES
                    if verdict.gates[g].status == UNKNOWN
                },
                key=FIELD_ORDER.get,
            )
        rows.append({
            "ref": ref.record_id,
            "verdict": verdict.verdict,
            "blocking_dimension": verdict.blocking_dimension,
            "permanent": permanent,
            "missing": missing,
        })
    return rows


def leverage_report(subject: Record, records: List[Record]) -> Dict[str, Any]:
    """补证杠杆报告：给 UI 与 Agent 用的完整结构。"""
    status = _subject_status(subject)
    if status == "NO_SUBJECT_VALUE":
        return {
            "status": status,
            "headline": "没有实测数值，无法判断任何一对能否并列——先给一个数",
            "action": "先取得实测收率（分离称重或仪器定量），再做补证排序",
            "blocked": 0, "comparable": 0, "fixable": 0, "permanent": 0,
            "per_field": [], "plan": [], "curve": [], "next": [],
        }

    profile = pair_profile(subject, records)
    comparable = [r for r in profile if r["verdict"] == COMPARABLE]
    blocked = [r for r in profile if r["verdict"] != COMPARABLE]
    fixable = [r for r in blocked if r["missing"]]
    permanent = [r for r in blocked if r["permanent"]]

    per_field: List[Dict[str, Any]] = []
    for key in sorted({k for r in fixable for k in r["missing"]}, key=FIELD_ORDER.get):
        per_field.append({
            "field": key,
            "side": key.split(":")[1],
            "short": FIELD_SHORT[key],
            "action": FIELD_LABELS[key],
            "solo_unlocks": len([r for r in fixable if r["missing"] == [key]]),
            "solo_refs": [r["ref"] for r in fixable if r["missing"] == [key]],
            "involved_pairs": len([r for r in fixable if key in r["missing"]]),
        })
    per_field.sort(key=lambda row: (-row["solo_unlocks"], FIELD_ORDER[row["field"]]))

    plan = _greedy_plan(fixable)
    curve = [{"fields": i, "pairs": step["cumulative_pairs"]} for i, step in enumerate(plan, start=1)]

    if not blocked:
        headline = f"{len(comparable)} 对全部可并列，没有需要补的证据"
    elif not fixable:
        headline = f"{len(blocked)} 对禁止并列，且全是确定不同——补证据改不了结论"
    else:
        headline = (
            f"{len(blocked)} 对不能并列：{len(fixable)} 对可以靠补证据解决，"
            f"{len(permanent)} 对是确定不同（补也没用）"
        )

    return {
        "status": "OK",
        "headline": headline,
        "blocked": len(blocked),
        "comparable": len(comparable),
        "fixable": len(fixable),
        "permanent": len(permanent),
        "permanent_refs": [r["ref"] for r in permanent],
        "per_field": per_field,
        "plan": plan,
        "curve": curve,
        "next": [step["action"] for step in plan[:2]],
    }


def corpus_leverage(records: List[Record], limit: int = 5) -> Dict[str, Any]:
    """语料级杠杆：补齐哪几条记录的口径，能让可比对最多。

    这是关于文献本身的可核对结论，不依赖演示用户——用于回答
    "这批记录里，有多少"不能比"其实只差一条口径说明"。
    按两两无序对统计（每条比较只计一次），口径确认视为可通过。
    """
    pairs: List[Dict[str, Any]] = []
    total = comparable = 0
    for i, a in enumerate(records):
        for b in records[i + 1:]:
            total += 1
            verdict = judge(a, b)
            if verdict.verdict == COMPARABLE:
                comparable += 1
                continue
            permanent = [g for g in HARD_GATES if verdict.gates[g].status == FAIL]
            if permanent:
                continue
            missing = sorted(
                {
                    _field_of(g, _side_of(a, b, g))
                    for g in HARD_GATES
                    if verdict.gates[g].status == UNKNOWN
                },
                key=FIELD_ORDER.get,
            )
            if missing:
                pairs.append({
                    "a": a.record_id, "b": b.record_id,
                    "missing": missing,
                    "units": [a.record_id if k.endswith(":subject") else b.record_id for k in missing],
                })

    plan: List[Dict[str, Any]] = []
    covered: set = set()
    chosen: List[str] = []
    while True:
        best_unit, best_new = None, set()
        for unit in sorted({u for p in pairs for u in p["units"]} - set(chosen)):
            newly = {i for i, p in enumerate(pairs) if set(p["units"]) <= set(chosen) | {unit}}
            if len(newly - covered) > len(best_new):
                best_unit, best_new = unit, newly
        if best_unit is None:
            break
        chosen.append(best_unit)
        plan.append({
            "record_id": best_unit,
            "unlocks_new": len(best_new - covered),
            "cumulative_unlocked": len(best_new),
            "cumulative_total": comparable + len(best_new),
        })
        covered = best_new

    return {
        "total_pairs": total,
        "comparable_pairs": comparable,
        "caliber_blocked_pairs": len(pairs),
        "caliber_blocked_with_other_gaps": len([p for p in pairs if len(p["missing"]) > 1]),
        "plan": plan[:limit],
        "plan_full": plan,
        "curve": [{"records": i, "pairs": step["cumulative_total"]} for i, step in enumerate(plan, start=1)],
    }


def render(report: Dict[str, Any]) -> List[str]:
    """终端展示用：三行之内说清"补哪一条最值"。"""
    lines = [f"  {report['headline']}"]
    if report["status"] != "OK":
        lines.append(f"  下一步：{report['action']}")
        return lines
    if not report["plan"]:
        return lines
    curve = "｜".join(f"补 {c['fields']} 条 → 解锁 {c['pairs']} 对" for c in report["curve"][:3])
    lines.append(f"  {curve}")
    for index, step in enumerate(report["plan"][:3], start=1):
        lines.append(
            f"  {index}. {step['action']}　（新解锁 {step['unlocks_new']} 对，累计 {step['cumulative_pairs']}）"
        )
    return lines


def render_corpus(report: Dict[str, Any]) -> List[str]:
    """语料级杠杆的终端展示。"""
    lines = [
        f"  {report['total_pairs']} 个两两比较：{report['comparable_pairs']} 对可直接并列；"
        f"{report['caliber_blocked_pairs']} 对只缺口径确认"
    ]
    if report["plan"]:
        first = report["plan"][0]
        lines.append(
            f"  只补第一条记录的口径（{first['record_id']}）→ 新增 {first['unlocks_new']} 对可比"
        )
        last = report["plan"][-1]
        lines.append(
            f"  补这 {len(report['plan'])} 条 → 可比对 {report['comparable_pairs']} → {last['cumulative_total']}"
        )
    return lines
