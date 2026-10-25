"""8 维门槛判定（骨架，逻辑与 `docs/rules/8维门槛规则表-待签字.md` 一一对应）。

铁律：
- 本模块的判定是确定性的，LLM 无权覆盖。
- 任一硬门槛 FAIL → BLOCKED；任一硬门槛 UNKNOWN → NEEDS_EVIDENCE。
- 软门槛只产出 L1/L2 标注，不改变结论。

TODO（待化学签字后确认）：
- 底物/产物比对目前是名称规范化；接入 rdkit 后改为 canonical SMILES + 子结构比对。
- 维度 4 对 isolated 口径的判据见规则表 §2.4 D2。
"""

from __future__ import annotations

from typing import Optional

try:  # 作为子包（app/chemcritic/engine/）导入
    from .schemas import (
        ALLOWED_OUTCOME_TYPES,
        BLOCKED,
        COMPARABLE,
        FAIL,
        GATE_NAMES,
        HARD_GATES,
        NEEDS_EVIDENCE,
        PASS,
        SOFT_GATES,
        UNKNOWN,
        GateResult,
        Record,
        Verdict,
    )
except ImportError:  # 作为独立脚本运行（python engine/run_demo.py）
    from schemas import (  # type: ignore
        ALLOWED_OUTCOME_TYPES,
        BLOCKED,
        COMPARABLE,
        FAIL,
        GATE_NAMES,
        HARD_GATES,
        NEEDS_EVIDENCE,
        PASS,
        SOFT_GATES,
        UNKNOWN,
        GateResult,
        Record,
        Verdict,
    )

UNLOCK_MESSAGES = {
    1: "请确认底物结构一致（提供芳基卤与硼酸的 SMILES）",
    2: "请确认目标产物结构",
    3: "请确认该数值的口径（分离 / GC / NMR / 转化率），未确认前不得与任何数值并列",
    4: "请补内标种类与定量基准（GC/NMR），或说明分离与纯化方式",
    5: "请补录投料规模、底物当量比与浓度",
    6: "请补录催化剂前体、配体与 mol%",
    7: "请补录碱的种类与当量、溶剂、温度、时间与气氛",
    8: "请补录后处理与纯化方式",
}


# ---------- 工具 ----------

def _canon(value: Optional[str]) -> Optional[str]:
    """名称规范化占位实现。TODO: 换成 rdkit canonical SMILES。"""
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in ("", "none", "null", "unknown", "n/a", "待核验"):
        return None
    for junk in ("(weighed)", "reflux"):
        v = v.replace(junk, "")
    return " ".join(v.split())


def _num(conditions: dict, key: str) -> Optional[float]:
    v = conditions.get(key)
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _eq(conditions: dict, key: str) -> Optional[float]:
    eq = conditions.get("equivalents") or {}
    v = eq.get(key)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _is_aqueous(solvent: Optional[str]) -> Optional[bool]:
    if not solvent:
        return None
    s = str(solvent).lower()
    return ("h2o" in s) or ("水" in s) or ("etoh" in s) or ("meoh" in s) or ("乙醇" in s)


# ---------- 硬门槛 ----------

def gate_substrate(user: Record, ref: Record) -> GateResult:
    keys = ("aryl_halide", "boronic_acid")
    u = {_canon(user.substrate.get(k)) for k in keys}
    r = {_canon(ref.substrate.get(k)) for k in keys}
    if None in u or None in r:
        return GateResult(1, UNKNOWN, "底物结构未完整记录")
    if u == r:
        return GateResult(1, PASS, "底物对一致")
    return GateResult(1, FAIL, f"底物对不同：{sorted(x for x in u if x)} vs {sorted(x for x in r if x)}")


def gate_product(user: Record, ref: Record) -> GateResult:
    up = _canon(user.substrate.get("product"))
    rp = _canon(ref.substrate.get("product"))
    if up is None or rp is None:
        return GateResult(2, UNKNOWN, "产物结构未完整记录")
    if up == rp:
        return GateResult(2, PASS, "产物一致")
    return GateResult(2, FAIL, f"产物不同：{up} vs {rp}")


def gate_outcome_type(user: Record, ref: Record) -> GateResult:
    u, r = user.outcome_type, ref.outcome_type
    if u not in ALLOWED_OUTCOME_TYPES or r not in ALLOWED_OUTCOME_TYPES:
        return GateResult(3, UNKNOWN, f"口径未决：subject={u}, reference={r}")
    if u == r:
        return GateResult(3, PASS, f"口径一致：{u}")
    return GateResult(3, FAIL, f"口径不同：{u} vs {r}（禁止换算与区间拼接）")


def gate_assay(user: Record, ref: Record) -> GateResult:
    """维度 4 · Assay 与校准（三层，任一不过即 UNKNOWN）。

    ① **口径标记必须与原文措辞一致**——`outcome_type` 是抽取器给出的分类，
       不是事实本身；它必须被 `label_raw`（原文逐字措辞）支持。
    ② **证据等级必须达到可比较门槛**——只有 `A_confirmed`（已回原文核验）才允许
       进入数值比较；`imported_unchecked` / `candidate` 一律不支撑比较。
    ③ **定量方法与校准信息**足以支撑该口径（GC 要内标+基准，NMR 要内标）。

    设计立场：**对文献侧苛刻，对用户侧宽松**。用户是现场记录者，
    对其追问比阻断更有用；文献数值若未核验，一旦允许比较就是在制造假精确。
    """
    return (
        _label_consistency(ref, "reference")
        or _evidence_check(ref)
        or _calibration_check(user, "subject")
        or _calibration_check(ref, "reference")
        or GateResult(4, PASS, "口径标记、证据等级与校准信息均足以支撑比较")
    )


# 每个口径在原文措辞里必须能找到的支撑词
_LABEL_HINTS = {
    "isolated": ("isolated", "分离", "称重", "weighed"),
    "gc": ("gc", "glc", "气相"),
    "nmr": ("nmr", "核磁", "1h", "¹h"),
    "conversion": ("conversion", "转化"),
}

_REQUIRED_EVIDENCE = ("a_confirmed",)  # 小写比对，见 _evidence_check


def _label_consistency(record: Record, side: str) -> Optional[GateResult]:
    """口径标记必须被原文措辞支撑。标记与措辞矛盾时不得比较。"""
    t = record.outcome_type
    if t not in ALLOWED_OUTCOME_TYPES:
        return GateResult(4, UNKNOWN, f"{side} 口径未决，无法判定 assay")
    raw = (record.label_raw or "").strip()
    if not raw:
        return GateResult(4, UNKNOWN, f"{side} 未记录原文措辞（label_raw），无法确认口径")
    low = raw.lower()
    if not any(h in low for h in _LABEL_HINTS[t]):
        return GateResult(
            4, UNKNOWN,
            f"{side} 口径标记为 {t}，但原文措辞不支持：「{raw}」——需回原文确认",
        )
    return None


def _evidence_check(record: Record) -> Optional[GateResult]:
    """文献侧证据等级门槛：未回原文核验的记录不支撑数值比较。"""
    grade = (record.evidence_grade or "imported_unchecked").strip()
    if grade.lower() in _REQUIRED_EVIDENCE:
        return None
    if grade.lower() == "candidate":
        return GateResult(4, UNKNOWN, "文献侧记录为候选状态（未完成原文核验），不支撑数值比较")
    return GateResult(
        4, UNKNOWN,
        "文献侧记录尚未回原文核验（imported_unchecked），不支撑数值比较——需 A/B 抽检后升级",
    )


def _calibration_check(record: Record, side: str) -> Optional[GateResult]:
    """定量方法是否足以支撑该口径。返回 None 表示通过。"""
    t = record.outcome_type
    if t not in ALLOWED_OUTCOME_TYPES:
        return GateResult(4, UNKNOWN, f"{side} 口径未决")
    if t == "isolated":
        # 纯化方式是否记录/一致 → 维度 8（软）。此处不再重复要求。
        return None
    if t in ("gc", "nmr"):
        std = record.assay.get("internal_standard")
        basis = record.assay.get("quantification_basis")
        if t == "nmr" and std:
            return None
        if t == "gc" and std and basis:
            return None
        missing = []
        if not std:
            missing.append("内标")
        if t == "gc" and not basis:
            missing.append("定量基准")
        return GateResult(4, UNKNOWN, f"{side} 缺{('、'.join(missing)) or '校准信息'}")
    return None


# ---------- 软门槛 ----------

def gate_scale(user: Record, ref: Record) -> GateResult:
    levels, notes = [], []
    us, rs = _num(user.conditions, "scale_mmol"), _num(ref.conditions, "scale_mmol")
    if us is None or rs is None:
        levels.append("L2"); notes.append("规模/当量缺失")
    elif rs and (max(us, rs) / min(us, rs)) > 5:
        levels.append("L1"); notes.append(f"规模相差 {max(us, rs) / min(us, rs):.1f} 倍")

    for key, label in (("aryl_halide", "芳基卤"), ("boronic_acid", "硼酸")):
        ue, re_ = _eq(user.conditions, key), _eq(ref.conditions, key)
        if ue is not None and re_ is not None and abs(ue - re_) > 0.2:
            levels.append("L1"); notes.append(f"{label}当量比相差 {abs(ue - re_):.1f}")

    uc, rc = _num(user.conditions, "concentration_m"), _num(ref.conditions, "concentration_m")
    if uc and rc and max(uc, rc) / min(uc, rc) > 2:
        levels.append("L2"); notes.append("浓度相差 > 2 倍")

    return _soft(5, levels, notes)


def gate_catalyst(user: Record, ref: Record) -> GateResult:
    levels, notes = [], []
    uc = _canon(user.conditions.get("catalyst"))
    rc = _canon(ref.conditions.get("catalyst"))
    if uc is None or rc is None:
        levels.append("L2"); notes.append("催化剂信息缺失")
    elif uc != rc:
        levels.append("L2"); notes.append(f"催化剂不同：{uc} vs {rc}")

    ul = _canon(user.conditions.get("ligand"))
    rl = _canon(ref.conditions.get("ligand"))
    if ul != rl:
        levels.append("L2"); notes.append(f"配体不同：{ul} vs {rl}")

    um, rm = _num(user.conditions, "catalyst_mol_pct"), _num(ref.conditions, "catalyst_mol_pct")
    if um is None or rm is None:
        levels.append("L2"); notes.append("mol% 缺失")
    elif um and rm and max(um, rm) / min(um, rm) > 3:
        levels.append("L2"); notes.append(f"载量相差 {max(um, rm) / min(um, rm):.1f} 倍")
    elif um != rm:
        levels.append("L1"); notes.append(f"载量不同：{um} vs {rm}")

    return _soft(6, levels, notes)


def gate_conditions(user: Record, ref: Record) -> GateResult:
    levels, notes = [], []

    ut, rt = _num(user.conditions, "temp_c"), _num(ref.conditions, "temp_c")
    if ut is not None and rt is not None:
        d = abs(ut - rt)
        if d > 30:
            levels.append("L2"); notes.append(f"温度相差 {d:.0f} °C")
        elif d > 0:
            levels.append("L1"); notes.append(f"温度相差 {d:.0f} °C")

    ub, rb = _canon(user.conditions.get("base")), _canon(ref.conditions.get("base"))
    if ub is None:
        levels.append("L2"); notes.append("碱未记录")
    elif ub != rb:
        levels.append("L2"); notes.append(f"碱不同：{ub} vs {rb}")

    uaq, raq = _is_aqueous(user.conditions.get("solvent")), _is_aqueous(ref.conditions.get("solvent"))
    if uaq is not None and raq is not None and uaq != raq:
        levels.append("L2"); notes.append("溶剂族不同（含水 vs 非含水）")

    uh, rh = _num(user.conditions, "time_h"), _num(ref.conditions, "time_h")
    if uh and rh and max(uh, rh) / min(uh, rh) > 2:
        levels.append("L1"); notes.append(f"时间相差 {max(uh, rh) / min(uh, rh):.1f} 倍")

    ua, ra = _canon(user.conditions.get("atmosphere")), _canon(ref.conditions.get("atmosphere"))
    if ua != ra:
        levels.append("L1"); notes.append(f"气氛不同：{ua} vs {ra}")

    return _soft(7, levels, notes)


def gate_workup(user: Record, ref: Record) -> GateResult:
    levels, notes = [], []
    up, rp = _canon(user.purification), _canon(ref.purification)
    if up is None and rp is None:
        levels.append("L1"); notes.append("双方均未记录纯化方式")
    elif up != rp:
        levels.append("L1"); notes.append(f"纯化方式不同：{up} vs {rp}")
    return _soft(8, levels, notes)


def _soft(gate: int, levels: list, notes: list) -> GateResult:
    if not levels:
        return GateResult(gate, PASS, "无明显差异")
    level = "L2" if "L2" in levels else "L1"
    return GateResult(gate, PASS, "；".join(notes), level=level)


# ---------- 结论 ----------

def judge(user: Record, ref: Record) -> Verdict:
    gates = {
        1: gate_substrate(user, ref),
        2: gate_product(user, ref),
        3: gate_outcome_type(user, ref),
        4: gate_assay(user, ref),
        5: gate_scale(user, ref),
        6: gate_catalyst(user, ref),
        7: gate_conditions(user, ref),
        8: gate_workup(user, ref),
    }

    blocking = None
    for g in HARD_GATES:
        if gates[g].status == FAIL:
            blocking = gates[g]
            break

    if blocking is not None:
        return Verdict(
            user.record_id, ref.record_id, BLOCKED, gates,
            blocking_dimension=GATE_NAMES[blocking.gate],
            unlock_condition=UNLOCK_MESSAGES[blocking.gate],
            soft_flags=_soft_flags(gates),
        )

    for g in HARD_GATES:
        if gates[g].status == UNKNOWN:
            return Verdict(
                user.record_id, ref.record_id, NEEDS_EVIDENCE, gates,
                blocking_dimension=GATE_NAMES[g],
                unlock_condition=UNLOCK_MESSAGES[g],
                soft_flags=_soft_flags(gates),
            )

    return Verdict(user.record_id, ref.record_id, COMPARABLE, gates, soft_flags=_soft_flags(gates))


def _soft_flags(gates: dict) -> list:
    flags = []
    for g in SOFT_GATES:
        r = gates[g]
        if r.status == PASS and r.level:
            flags.append({"dimension": GATE_NAMES[g], "level": r.level, "detail": r.detail})
    return flags
