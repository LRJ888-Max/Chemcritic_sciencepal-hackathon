#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ChemCritic 判定引擎 · 单文件自包含版（零第三方依赖）。

⚠️ 本文件由 engine/build_bundle.py 自动生成，请勿直接编辑。
   改判定逻辑请改 engine/*.py，然后重新运行 `python engine/build_bundle.py`。

内嵌来源：engine/schemas.py · engine/profiles.py · engine/tier.py ·
          engine/gates.py · engine/placement.py · engine/run_demo.py ·
          data/records.json

契约（平台侧唯一允许的判定入口）：
    输入  Record JSON（见 docs/integration/2026-10-27-给C-的合并指令-引擎判定接入.md §2.1）
    输出  {"subject", "classes", "coverage", "verdicts", "placement"}

铁律：verdict 三值 COMPARABLE / BLOCKED / NEEDS_EVIDENCE 只来自本文件的 judge()；
      LLM 只负责把证书翻译成用户文案，不得改写、不得补"我认为"的结论。

用法：
    python chemcritic_engine.py                          # 三屏演示 + 19 项自检
    python chemcritic_engine.py --selftest               # 只跑 T1–T18（19 passed, 0 failed）
    python chemcritic_engine.py --json                   # 内置 35% 案例的结构化结论
    python chemcritic_engine.py --json --input user.json # 判定你自己的实验记录
    python chemcritic_engine.py --json --input user.json --records refs.json
    python chemcritic_engine.py --brief --input user.json # 证书摘要（几百字节，降级贴回会话）
    python chemcritic_engine.py --version

退出码：0 判定成功 / 自检全过；1 自检失败；2 输入不合法。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, replace as replacement
from types import SimpleNamespace
from typing import Any, Dict, List, Optional



# ============================================================================
# 1/8 · schemas.py —— 数据结构（无业务判断；原文未报告的字段一律 None）
# ============================================================================
# 数据结构定义（骨架）。
#
# 约束：
# - 本模块只定义结构，不含业务判断；判断逻辑全部在 gates.py。
# - 原文未报告的字段一律为 None，禁止用默认值补成"事实"。
# - 如需换成 Pydantic，保持字段名不变即可。

# 判定状态
PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

# 结论
COMPARABLE = "COMPARABLE"
BLOCKED = "BLOCKED"
NEEDS_EVIDENCE = "NEEDS_EVIDENCE"

ALLOWED_OUTCOME_TYPES = {"isolated", "gc", "nmr", "conversion"}

GATE_NAMES = {
    1: "substrate",
    2: "product",
    3: "outcome_type",
    4: "assay",
    5: "scale_equivalents",
    6: "catalyst_system",
    7: "base_solvent_temp_time_atmosphere",
    8: "workup_purification",
}

HARD_GATES = (1, 2, 3, 4)
SOFT_GATES = (5, 6, 7, 8)


@dataclass
class Record:
    """一条 entry 级记录；用户输入与文献记录共用此结构。"""

    record_id: str
    outcome_type: str  # isolated | gc | nmr | conversion | unknown
    outcome_value: Optional[float]
    assay: Dict[str, Any] = field(default_factory=dict)
    conditions: Dict[str, Any] = field(default_factory=dict)
    substrate: Dict[str, Any] = field(default_factory=dict)
    tier: Optional[str] = None
    source_id: Optional[str] = None
    locator: Optional[str] = None
    purification: Optional[str] = None
    verification: str = "unverified"
    catalytic_profile: Dict[str, Any] = field(default_factory=dict)
    tier_meta: Dict[str, Any] = field(default_factory=dict)
    label_raw: Optional[str] = None       # 原文措辞，逐字照抄（口径判定的唯一依据）
    label_raw_provenance: str = "unknown"  # verbatim | transcribed | inferred | unknown
    evidence_grade: str = "imported_unchecked"  # A_confirmed | imported_unchecked | candidate
    citation: Optional[str] = None        # 对外引用：作者 + 年份 + 期刊卷页（不做成 model 记忆）
    doi: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Record":
        outcome = d.get("outcome") or {}
        return cls(
            record_id=d["record_id"],
            outcome_type=(outcome.get("outcome_type") or "unknown").lower(),
            outcome_value=outcome.get("value"),
            assay=d.get("assay") or {},
            conditions=d.get("conditions") or {},
            substrate=d.get("substrate") or {},
            tier=d.get("tier"),
            source_id=d.get("source_id"),
            locator=d.get("locator"),
            purification=d.get("purification"),
            verification=d.get("verification", "unverified"),
            catalytic_profile=d.get("catalytic_profile") or {},
            label_raw=outcome.get("label_raw"),
            label_raw_provenance=(d.get("_extraction") or {}).get("label_raw_provenance", "unknown"),
            evidence_grade=d.get("evidence_grade") or "imported_unchecked",
            citation=d.get("citation"),
            doi=d.get("doi"),
        )

    @property
    def is_verified(self) -> bool:
        return self.verification in ("verified", "verified_with_caveat")


@dataclass
class GateResult:
    gate: int
    status: str  # PASS | FAIL | UNKNOWN
    detail: str
    level: Optional[str] = None  # L1 | L2（仅软门槛）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate": self.gate,
            "dimension": GATE_NAMES[self.gate],
            "status": self.status,
            "detail": self.detail,
            "level": self.level,
        }


@dataclass
class Verdict:
    """一条对照结论，对应产品中的一张"可比性证书"。"""

    subject_id: str
    reference_id: str
    verdict: str
    gates: Dict[int, GateResult]
    blocking_dimension: Optional[str] = None
    unlock_condition: Optional[str] = None
    soft_flags: list = field(default_factory=list)
    calibration: str = "calibrated"          # 汇总：任一侧 uncalibrated 即 uncalibrated
    ref_calibration: str = "calibrated"      # 文献侧校准（B 于 10/26 要求拆分，便于归因）
    subject_calibration: str = "calibrated"  # 用户侧校准
    non_independent: bool = False            # 同篇不同条目，不得当两条独立来源
    reference_meta: Dict[str, Any] = field(default_factory=dict)  # citation/doi/locator/tier

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "reference_id": self.reference_id,
            "verdict": self.verdict,
            "blocking_dimension": self.blocking_dimension,
            "unlock_condition": self.unlock_condition,
            "calibration": self.calibration,
            "ref_calibration": self.ref_calibration,
            "subject_calibration": self.subject_calibration,
            "non_independent": self.non_independent,
            "reference": self.reference_meta,
            "gates": {str(k): v.to_dict() for k, v in sorted(self.gates.items())},
            "soft_flags": self.soft_flags,
        }


# ============================================================================
# 2/8 · profiles.py —— 档位字段补丁表（记录缺 catalytic_profile 时兜底）
# ============================================================================
# 档位字段补丁表。
#
# 来源：`test_code/cases/library/suzuki_handoff_v1.json` 的 7 条记录 + SM-006（本项目新增）。
# 用途：C 的案例库尚未包含这 5 个字段，本表按 record_id 兜底，避免他必须先改 JSON 才能跑。
# 落地方式：长期应把 `catalytic_profile` 直接写进案例库每条记录（见 docs/integration/ 的合并指令）。
#
# 判据口径见 `docs/rules/分档定义-待签字.md` §2 / §4。

PROFILES: Dict[str, Dict[str, str]] = {
    # 分子膦配体均相（Pd(PPh3)4 / Na2CO3 / 苯）
    "SM-001-GC": dict(catalyst_form="molecular_complex", ligand_family="phosphine",
                      support="none", medium="organic", activation_mode="thermal"),
    "SM-001-isolated": dict(catalyst_form="molecular_complex", ligand_family="phosphine",
                            support="none", medium="organic", activation_mode="thermal"),
    # 无配体水相（PdCl2 / K2CO3 / EtOH-H2O）
    "SM-002-isolated": dict(catalyst_form="in_situ_salt", ligand_family="none",
                            support="none", medium="aqueous", activation_mode="thermal"),
    # 膦类配体（Pd(OAc)2 + SPO 亚膦酸 / NaOtBu / THF）
    "SM-003-NMR": dict(catalyst_form="molecular_complex", ligand_family="phosphine",
                       support="none", medium="organic", activation_mode="thermal"),
    # MOF 固载
    "SM-004-T1-E1": dict(catalyst_form="supported", ligand_family="none",
                         support="solid_support", medium="organic", activation_mode="thermal"),
    "SM-004-T1-E7": dict(catalyst_form="supported", ligand_family="none",
                         support="solid_support", medium="organic", activation_mode="thermal"),
    # 预先制备的生物法 Pd 纳米颗粒（边界 B/C，主判 C）
    "SM-005-isolated": dict(catalyst_form="nanoparticle", ligand_family="none",
                            support="none", medium="aqueous", activation_mode="thermal"),
    # SNS-Pd pincer（非膦配体 / 140 °C 水相）
    "SM-006": dict(catalyst_form="molecular_complex", ligand_family="non_phosphine",
                   support="none", medium="aqueous", activation_mode="thermal"),
}


def profile_for(record_id: Optional[str]) -> Dict[str, str]:
    return PROFILES.get(record_id or "", {})


# ============================================================================
# 3/8 · tier.py —— 档位判定（受控词表 → 规则 → 主判 / 备选）
# ============================================================================
# 档位判定：受控词表 → 规则 → 主判 / 备选（确定性、可审计）。
#
# 设计要点（对应 `docs/rules/分档定义-待签字.md` §4）：
# 1. 判定只吃受控词表字段，不吃自由文本，因此可复现。
# 2. 多条规则命中且结论不同 → 强制 `borderline`，禁止"取第一条"静默通过。
# 3. 主判优先级 = 物理形态 > 配体族 > 介质。理由：活性物种是不是分离相，
#    比"有没有配体"更能解释收率差异与非均相行为（可回收性、烧结、浸出）。
# 4. 档的不确定性不阻断流程，由 placement.sensitivity() 做双档敏感性分析。
#
# ⚠️ 本文件的规则与阈值**由技术侧代签**（2026-10-25），A 可随时推翻重定。

RULE_VERSION = "tier-0.1"

TIER_NAMES = {
    "A": "分子膦配体均相 Pd",
    "B": "无配体 / 水相 Pd",
    "C": "特化体系（负载 / 纳米颗粒 / 非膦配体 / 无溶剂）",
}

# 受控词表（抽取层只允许输出这些值）
CONTROLLED_VOCAB: Dict[str, List[str]] = {
    "catalyst_form": ["molecular_complex", "in_situ_salt", "nanoparticle", "supported", "bulk_metal"],
    "ligand_family": ["phosphine", "non_phosphine", "none"],
    "support": ["none", "solid_support"],
    "medium": ["aqueous", "organic", "solventless"],
    "activation_mode": ["thermal", "mechanochemical", "microwave", "photochemical"],
}

# (rule_id, 说明, 判定函数, 归入档)
RULES = [
    ("R1", "support=solid_support", lambda p: p.get("support") == "solid_support", "C"),
    ("R2", "medium=solventless", lambda p: p.get("medium") == "solventless", "C"),
    ("R3", "catalyst_form=nanoparticle", lambda p: p.get("catalyst_form") == "nanoparticle", "C"),
    ("R4", "catalyst_form in (supported, bulk_metal)",
     lambda p: p.get("catalyst_form") in ("supported", "bulk_metal"), "C"),
    ("R5", "ligand_family=non_phosphine", lambda p: p.get("ligand_family") == "non_phosphine", "C"),
    ("R6", "ligand_family=none 且 medium=aqueous",
     lambda p: p.get("ligand_family") == "none" and p.get("medium") == "aqueous", "B"),
]

FALLBACK = ("R0", "兜底：分子配合物 + 膦类配体", "A")

# 主判优先级：物理形态 > 配体族 > 介质（数值越小越优先）
PRIMARY_PRIORITY = {"R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5, "R6": 6, "R0": 9}


def _validate(profile: Dict[str, Any]) -> List[str]:
    problems = []
    for field, allowed in CONTROLLED_VOCAB.items():
        value = profile.get(field)
        if value is None:
            problems.append(f"{field} 缺失")
        elif value not in allowed:
            problems.append(f"{field}={value!r} 不在词表 {allowed}")
    return problems


def tier_of(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """返回 {tier, borderline, rules, confidence, problems}。"""
    if not profile:
        return {"tier": None, "borderline": [], "rules": [], "confidence": "none",
                "problems": ["未提供 catalytic_profile"], "rule_version": RULE_VERSION}

    problems = _validate(profile)
    if problems:
        return {"tier": None, "borderline": [], "rules": [], "confidence": "none",
                "problems": problems, "rule_version": RULE_VERSION}

    hits = [(rid, desc, tier) for rid, desc, fn, tier in RULES if fn(profile)]
    if not hits:
        rid, desc, tier = FALLBACK
        hits = [(rid, desc, tier)]

    tiers = sorted({t for _, _, t in hits})
    primary_rule = min(hits, key=lambda h: PRIMARY_PRIORITY.get(h[0], 99))
    primary = primary_rule[2]

    if len(tiers) > 1:
        confidence = "low"
    elif len(hits) > 1:
        confidence = "high"
    else:
        confidence = "medium"

    return {
        "tier": primary,
        "borderline": tiers if len(tiers) > 1 else [],
        "rules": [{"rule_id": r, "condition": d, "tier": t} for r, d, t in hits],
        "confidence": confidence,
        "problems": [],
        "rule_version": RULE_VERSION,
    }


# ============================================================================
# 4/8 · gates.py —— 8 维门槛判定（确定性，LLM 无权覆盖）
# ============================================================================
# 8 维门槛判定（骨架，逻辑与 `docs/rules/8维门槛规则表-待签字.md` 一一对应）。
#
# 铁律：
# - 本模块的判定是确定性的，LLM 无权覆盖。
# - 任一硬门槛 FAIL → BLOCKED；任一硬门槛 UNKNOWN → NEEDS_EVIDENCE。
# - 软门槛只产出 L1/L2 标注，不改变结论。
#
# TODO（待化学签字后确认）：
# - 底物/产物比对目前是名称规范化；接入 rdkit 后改为 canonical SMILES + 子结构比对。
# - 维度 4 对 isolated 口径的判据见规则表 §2.4 D2。

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

_REQUIRED_EVIDENCE = ("a_confirmed", "candidate")  # 小写比对；candidate 可通过，但由 calibration/软标注体现


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
    """文献侧证据等级门槛。

    - `A_confirmed`：已回原文核对 → 通过
    - `candidate`：有未决歧义（如校准未注明）→ **通过**，但由此产生的"未校准"
      由 `calibration_level()` 标注，且不参与档内定位结论（C 方案，2026-10-26）
    - `imported_unchecked`：从交接表导入、未回原文核对 → 不支撑数值比较
    """
    grade = (record.evidence_grade or "imported_unchecked").strip().lower()
    if grade in _REQUIRED_EVIDENCE:
        return None
    return GateResult(
        4, UNKNOWN,
        "文献侧记录尚未回原文核验（imported_unchecked），不支撑数值比较——需 A/B 抽检后升级",
    )


def _calibration_check(record: Record, side: str) -> Optional[GateResult]:
    """维度 4 · 第三层：定量方法是否足以支撑该口径。

    **C 方案（A 于 2026-10-26 定）：**
    - 有内标（+ 基准）→ 通过，`calibration = calibrated`
    - 无内标但**说明了定量方式**（如 "1H-NMR analysis" / "GC area normalization"）
      → **也通过**，但降级为 `uncalibrated`，由 `calibration_level()` 标注
    - 连定量方式都没写 → `UNKNOWN`（这才是真的不知道）

    理由：文献普遍不写内标（行业惯例），一律阻断会让可比样本归零；
    但"未校准"必须显式标注，且**不参与档内定位的结论计算**。
    """
    t = record.outcome_type
    if t not in ALLOWED_OUTCOME_TYPES:
        return GateResult(4, UNKNOWN, f"{side} 口径未决")
    if t == "isolated":
        # 分离称重本身就是绝对标尺
        return None

    std = record.assay.get("internal_standard")
    basis = record.assay.get("quantification_basis")
    method = str(record.assay.get("method") or "").strip()

    if t == "nmr" and std:
        return None
    if t in ("gc", "conversion") and std and basis:
        return None
    if method:
        # 说明了定量方式但无内标 → 通过但标未校准
        return None
    return GateResult(4, UNKNOWN, f"{side} 未说明定量方法（连测法都没有）")


def calibration_level(record: Record) -> str:
    """`calibrated`（有绝对标尺）/ `uncalibrated`（仅说明定量方式）。"""
    if record.outcome_type == "isolated":
        return "calibrated"
    if record.assay.get("internal_standard"):
        return "calibrated"
    return "uncalibrated"


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
    elif um and rm and max(um, rm) / min(um, rm) > 2:
        levels.append("L2"); notes.append(f"载量相差 {max(um, rm) / min(um, rm):.1f} 倍")
    elif um != rm:
        levels.append("L1"); notes.append(f"载量不同：{um} vs {rm}")

    return _soft(6, levels, notes)


def gate_conditions(user: Record, ref: Record) -> GateResult:
    levels, notes = [], []

    ut, rt = _num(user.conditions, "temp_c"), _num(ref.conditions, "temp_c")
    if ut is not None and rt is not None:
        d = abs(ut - rt)
        # 阈值 20 °C（A 于 2026-10-25 定：Suzuki 里 20 °C 已足以显著改变转化率；20 °C 即 L2）
        if d >= 20:
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
    """维度 8 · 后处理与纯化（软）。

    分级（A 于 2026-10-25 定）：
    - 双方都记录了纯化方式且**明确不同** → L2（柱层析 vs 蒸馏的回收率可差 10 个百分点以上）
    - 只有一方记录 → L1（无法判定"是否不同"，只提示信息缺失）
    - 双方都未记录 → L1
    """
    levels, notes = [], []
    up, rp = _canon(user.purification), _canon(ref.purification)
    if up is None and rp is None:
        levels.append("L1"); notes.append("双方均未记录纯化方式")
    elif up is None or rp is None:
        levels.append("L1"); notes.append(f"仅一方记录纯化方式（{up or rp}）")
    elif up != rp:
        levels.append("L2"); notes.append(f"纯化方式明确不同：{up} vs {rp}")
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

    ni = _same_source(user, ref)
    rc, sc = calibration_level(ref), calibration_level(user)
    # 证书自带引用信息：Agent 不必回头翻 records.json 找 citation（10/27 第 2 次运行诊断 · 问题 3）
    ref_meta = {
        "citation": ref.citation,
        "doi": ref.doi,
        "locator": ref.locator,
        "tier": ref.tier,
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
            reference_meta=ref_meta,
            calibration=_combined_calibration(user, ref),
            ref_calibration=rc, subject_calibration=sc, non_independent=ni,
        )

    for g in HARD_GATES:
        if gates[g].status == UNKNOWN:
            return Verdict(
                user.record_id, ref.record_id, NEEDS_EVIDENCE, gates,
                blocking_dimension=GATE_NAMES[g],
                unlock_condition=UNLOCK_MESSAGES[g],
                soft_flags=_soft_flags(gates),
                reference_meta=ref_meta,
                calibration=_combined_calibration(user, ref),
                ref_calibration=rc, subject_calibration=sc, non_independent=ni,
            )

    return Verdict(
        user.record_id, ref.record_id, COMPARABLE, gates,
        soft_flags=_soft_flags(gates),
        reference_meta=ref_meta,
        calibration=_combined_calibration(user, ref),
        ref_calibration=rc,
        subject_calibration=sc,
        non_independent=ni,
    )


def _same_source(user: Record, ref: Record) -> bool:
    """同篇不同条目：不得当作两条独立来源（B 于 2026-10-26 指出该字段一直缺）。"""
    us, rs = (user.source_id or "").strip(), (ref.source_id or "").strip()
    return bool(us) and us == rs and user.record_id != ref.record_id


def _combined_calibration(user: Record, ref: Record) -> str:
    """任一侧未校准 → 整对的结论标为 uncalibrated。"""
    return "uncalibrated" if "uncalibrated" in (calibration_level(user), calibration_level(ref)) else "calibrated"


def _soft_flags(gates: dict) -> list:
    flags = []
    for g in SOFT_GATES:
        r = gates[g]
        if r.status == PASS and r.level:
            flags.append({"dimension": GATE_NAMES[g], "level": r.level, "detail": r.detail})
    return flags


# ============================================================================
# 5/8 · placement.py —— 等价类 · 档内定位 · 双档敏感性
# ============================================================================
# 等价类划分与档内定位（骨架）。
#
# 职责边界：
# - 只做"分组"与"定位"，不做"好坏判断"。
# - "异常"只在档内定义；跨档只输出迁移目标，不输出正常区间。
# - 档位由 `tier.tier_of()` 从受控词表字段确定性计算，**不读人工标注**（标注仅用于交叉校验）。
#
# TODO：
# - 覆盖率统计需扩展到 40–50 条 entry 后才有说服力。

OUTCOME_ORDER = ("isolated", "gc", "nmr", "conversion")


def _load_records_from_file(path: Optional[str] = None) -> List[Record]:
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
            meta = tier_of(rec.catalytic_profile)
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


def placement(user: Record, records: List[Record]) -> Dict[str, Any]:
    """用户落在哪个等价类、哪一档、与档内水平的差距。

    **C 方案（A 2026-10-26）：** 只有 `calibrated`（有绝对标尺）的参照参与
    差值计算与结论；`uncalibrated`（仅说明定量方式、无内标）单独列出，供展示。
    """
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
    pool_ref = same_tier or same_class

    calibrated = [r for r in pool_ref if _level(r) == "calibrated"]
    uncalibrated = [r for r in pool_ref if _level(r) != "calibrated"]
    uncal_ids = [r.record_id for r in uncalibrated]

    if not calibrated:
        return {
            "status": "NO_CALIBRATED_REFERENCE",
            "tier": tier,
            "reason": "同档/同口径下只有未校准参照（无内标，仅说明定量方式）",
            "uncalibrated_refs": uncal_ids,
            "action": "可展示这些参照，但不给出'低于档内水平'一类结论；如需结论，需补一条有内标或分离称重的参照",
        }

    values = [r.outcome_value for r in calibrated if r.outcome_value is not None]
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
            "delta_vs_min": round(user.outcome_value - min(values), 1) if same_tier else None,
            "delta_vs_max": round(user.outcome_value - max(values), 1) if same_tier else None,
        }


def _level(record: Record) -> str:
    """避免循环导入：仅在此处按同一规则判断校准级别。"""
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


# ============================================================================
# 6/8 · run_demo.py —— 三屏演示与规则表验收矩阵（T1–T18）
# ============================================================================
# 演示入口（骨架）：跑通"35% 分离收率 → 分堆 → 逐条判定 → 档内定位"三屏。
#
# 用法：
#     python engine/run_demo.py          # 打印三屏 + 跑规则表测试矩阵
#     python engine/run_demo.py --json   # 只输出结构化结论
#
# 设计原则：
# - 结论全部由 gates.py 生成；本文件只做展示与断言，不含任何判断逻辑。
# - 测试矩阵是"规则表可交付给 C"的验收标准（对应 docs/rules/8维门槛规则表-待签字.md §4）。

USER = Record(
    record_id="USER-001",
    outcome_type="isolated",
    outcome_value=35,
    tier="A",
    substrate={
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl",
    },
    conditions={
        "catalyst": "Pd(PPh3)4",
        "catalyst_mol_pct": None,
        "ligand": None,
        "ligand_mol_pct": None,
        "base": None,
        "base_eq": None,
        "solvent": "THF",
        "temp_c": 60,
        "time_h": 12,
        "atmosphere": None,
        "scale_mmol": None,
        "equivalents": {"aryl_halide": 1.0, "boronic_acid": None},
        "concentration_m": None,
    },
    assay={"method": "isolated (weighed)", "internal_standard": None, "quantification_basis": "isolated product"},
    purification=None,
    verification="user_report",
    catalytic_profile={
        "catalyst_form": "molecular_complex",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal",
    },
)

# 规则表 §4 的验收矩阵：T编号 -> (对照记录, 期望结论, 期望阻断维度)
EXPECTED = {
    "T1": ("SM-001b", COMPARABLE, None),
    "T2": ("SM-001a", BLOCKED, "outcome_type"),
    "T3": ("SM-002", COMPARABLE, None),
    "T4": ("SM-003", BLOCKED, "outcome_type"),
    "T5": ("SM-004.1", NEEDS_EVIDENCE, "outcome_type"),
    "T6": ("SM-004.7", NEEDS_EVIDENCE, "outcome_type"),
    "T7": ("SM-005", COMPARABLE, None),
    "T8": ("SM-006-e1", BLOCKED, "outcome_type"),
    "T9": ("SM-004.7", NEEDS_EVIDENCE, "outcome_type"),  # 参照物改为 SM-006 时见 T9b
    "T10": ("SM-004.7", NEEDS_EVIDENCE, "outcome_type"),
    "T11": ("SM-002", NEEDS_EVIDENCE, "outcome_type"),
    "T12": ("SM-002", COMPARABLE, None),
    "T13": ("SM-006-e1", COMPARABLE, None),
    "T14": ("SM-001b", NEEDS_EVIDENCE, "assay"),
    "T15": ("SM-001b", NEEDS_EVIDENCE, "assay"),
    "T16": ("SM-001b", COMPARABLE, None),
    "T17": ("SM-001b", COMPARABLE, None),
}


def _hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def screen_one(records):
    _hr("屏 1 · 分堆：8 个报告值属于哪些等价类")
    for row in placement_mod.class_summary(records):
        mark = "[OK] 可比  " if row["comparable"] else "[??] 口径未决"
        print(f"  {row['outcome_type']:<10} {mark} n={row['count']}  values={row['values']}  {row['records']}")
    cov = placement_mod.coverage(records)
    print(f"\n  可比类覆盖率：{cov['largest_class']}/{cov['total_values']} = {cov['coverage']:.1%}")
    print(f"  （分母含口径未决的 {cov['unresolved']} 条——它们一样是被报告的值，但进不了任何等价类）")


def screen_two(records, verbose=True):
    _hr("屏 2 · 逐条判定：哪些数字有资格与你同框")
    verdicts = [gates.judge(USER, r) for r in records]
    order = {COMPARABLE: 0, BLOCKED: 1, NEEDS_EVIDENCE: 2}
    for v in sorted(verdicts, key=lambda x: (order[x.verdict], x.reference_id)):
        if not verbose and v.verdict != COMPARABLE:
            continue
        ref = next(r for r in records if r.record_id == v.reference_id)
        tag = {COMPARABLE: "[OK] 可比", BLOCKED: "[XX] 禁止同框", NEEDS_EVIDENCE: "[!!] 需补证"}[v.verdict]
        print(f"  {tag:<10} {v.reference_id:<10} {str(ref.outcome_value) + '%':<7} {ref.outcome_type:<9}", end="")
        if v.verdict != COMPARABLE:
            print(f"  卡在：{v.blocking_dimension}")
            print(f"             解锁：{v.unlock_condition}")
        else:
            flags = ", ".join(f"{f['dimension']}:{f['level']}" for f in v.soft_flags) or "无"
            print(f"  差异标注：{flags}")
    return verdicts


def screen_three(records):
    _hr("屏 3 · 档内定位")
    result = placement_mod.placement(USER, records)
    if result["status"] != "PLACED":
        print(f"  {result['status']}：{result.get('reason')}")
        print(f"  下一步：{result.get('action')}")
        return result
    print(f"  你的值：{result['observed']}%  （口径 {result['outcome_type']}，档 {result['tier']}）")
    print(f"  同档可比记录：{result['comparables']}")
    print(f"  档内参照值：{result['reference_values']}")
    print(f"  与档内最低值差 {result['delta_vs_min']:+.1f} 个百分点；与最高值差 {result['delta_vs_max']:+.1f}")
    print()
    print("  结论（示例文案，需 A 签字后使用）：")
    print("    「你的 35% 不属于异常。档 A（经典膦配体体系）在此底物上的文献分离收率是 40%；")
    print("      100% 来自档 B（无配体水相），95% 来自档 C（生物法 Pd 纳米颗粒）——换条件调不到，需要换体系。」")

    meta = USER.tier_meta
    if meta.get("borderline"):
        sens = placement_mod.sensitivity(USER, records, meta["borderline"])
        print("\n  档归属敏感（%s）：" % "/".join(meta["borderline"]))
        for t, res in sens["per_tier"].items():
            print(f"    按档 {t} → 参照 {res.get('reference_values')}  status={res.get('status')}")
        print(f"    结论稳健性：{'一致' if sens['consistent'] else '不一致，需补录可观察事实'}")
    return result


def screen_four(records):
    _hr("屏 4 · 档位判定审计（字段 → 规则 → 结果，全程可复现）")
    print("  档位由 engine/tier.py 从受控词表字段计算，不读人工标注；标注仅用于交叉校验。\n")
    ok = 0
    for r in records:
        meta = r.tier_meta or {}
        rules = ", ".join(f"{x['rule_id']}" for x in meta.get("rules", [])) or "-"
        borderline = "/".join(meta.get("borderline", [])) or "-"
        annotated = meta.get("annotated") or "-"
        computed = meta.get("tier") or "-"
        same = "OK" if annotated == computed else "MISMATCH"
        ok += annotated == computed
        print(f"  {r.record_id:<10} tier={computed:<3} (标注 {annotated:<3}) {same:<8} "
              f"rules={rules:<8} borderline={borderline:<6} conf={meta.get('confidence', '-'):<7} "
              f"form={r.catalytic_profile.get('catalyst_form', '-'):<17} ligand={r.catalytic_profile.get('ligand_family', '-')}")
    print(f"\n  计算值与标注一致：{ok}/{len(records)}")
    return ok == len(records)


def test_matrix(records):
    _hr("规则表测试矩阵（验收标准）")
    by_id = {r.record_id: r for r in records}
    passed = failed = 0
    for case, (ref_id, exp_verdict, exp_dim) in EXPECTED.items():
        subject = USER
        if case == "T9":
            subject = by_id["SM-006-e1"]
            ref_id = "SM-004.7"
        elif case == "T10":
            subject = by_id["SM-004.1"]
            ref_id = "SM-004.7"
        elif case == "T11":
            subject = Record(record_id="USER-011", outcome_type="unknown", outcome_value=35,
                             substrate=USER.substrate, conditions=USER.conditions, assay=USER.assay, tier="A")
        elif case == "T12":
            subject = Record(record_id="USER-012", outcome_type="isolated", outcome_value=35,
                             substrate=USER.substrate, conditions=USER.conditions,
                             assay={"method": "reported as yield"}, tier="A")
        elif case == "T13":
            subject = Record(record_id="USER-013", outcome_type="gc", outcome_value=66,
                             substrate=USER.substrate, conditions=USER.conditions,
                             assay={"method": "GC", "internal_standard": None}, tier="A")
        elif case == "T14":
            # 文献侧：口径标记写 isolated，但原文措辞只写 "Yield (%)" → 标记与原文矛盾
            subject = USER
        elif case == "T15":
            # 文献侧：同口径同措辞，但证据等级未达标（导入未核验）→ 不支撑数值比较
            subject = USER
        elif case == "T16":
            # conversion 口径缺内标与基准 → 转化率无法复算，必须补证
            subject = Record(record_id="USER-016", outcome_type="conversion", outcome_value=95,
                             substrate=USER.substrate, conditions=USER.conditions,
                             assay={"method": "conversion"}, tier="A")

        ref_obj = by_id[ref_id]
        if case == "T14":
            ref_obj = replacement(by_id["SM-001b"], record_id="REF-014", label_raw="Yield (%)")
        elif case == "T15":
            ref_obj = replacement(by_id["SM-001b"], record_id="REF-015",
                                  evidence_grade="imported_unchecked")
        elif case == "T16":
            ref_obj = replacement(by_id["SM-001b"], record_id="REF-016", outcome_type="conversion",
                                  label_raw="conversion (%)", assay={"method": "GC"})

        v = gates.judge(subject, ref_obj)
        ok = v.verdict == exp_verdict and (exp_dim is None or v.blocking_dimension == exp_dim)
        passed += ok
        failed += not ok
        flag = "PASS" if ok else "FAIL"
        detail = f"{v.verdict:<14} {(v.blocking_dimension or '-'):<20}"
        print(f"  [{flag}] {case:<4} {subject.record_id:<10} vs {ref_id:<10} → {detail} expect={exp_verdict}/{exp_dim or '-'}")
    print(f"\n  结果：{passed} passed, {failed} failed")

    # T17 · C 方案：无内标不阻断比较，但必须标 uncalibrated，且不进档内定位结论
    ref_uncal = replacement(by_id["SM-001b"], record_id="REF-017", outcome_type="gc",
                            label_raw="GC yield",
                            assay={"method": "1H-NMR", "internal_standard": None})
    subj_uncal = Record(record_id="USER-017", outcome_type="gc", outcome_value=95,
                        substrate=USER.substrate, conditions=USER.conditions,
                        assay={"method": "1H-NMR", "internal_standard": None}, tier="A")
    v17 = gates.judge(subj_uncal, ref_uncal)
    pl17 = placement_mod.placement(subj_uncal, [ref_uncal])
    ok17 = (v17.verdict == COMPARABLE and v17.calibration == "uncalibrated"
            and pl17["status"] == "NO_CALIBRATED_REFERENCE")
    passed += ok17
    failed += not ok17
    print(f"  [{'PASS' if ok17 else 'FAIL'}] T17  user(gc,无内标) vs ref(无内标) → "
          f"{v17.verdict}/{v17.calibration}, placement={pl17['status']}  "
          f"expect=COMPARABLE/uncalibrated, NO_CALIBRATED_REFERENCE")
    print(f"\n  C 方案验收：{passed} passed, {failed} failed（含 T17）")

    # T18 · 同篇不同条目必须标 non_independent（B 指出该字段一直缺）
    v18 = gates.judge(by_id["SM-001a"], by_id["SM-001b"])
    ok18 = v18.non_independent is True
    passed += ok18
    failed += not ok18
    print(f"  [{'PASS' if ok18 else 'FAIL'}] T18  SM-001a vs SM-001b → "
          f"non_independent={v18.non_independent}  expect=True")

    print(f"\n  最终：{passed} passed, {failed} failed（含 T17/T18）")
    return failed == 0


# ============================================================================
# 内嵌数据 · data/records.json（8 条已核验 L2 记录，随文件走，不依赖工作目录）
# ============================================================================

RECORDS_JSON = r"""{
  "schema_version": "0.1",
  "notice": "本文件为 entry 级证据库。所有数值来自公开文献，非本团队湿实验。原文未报告字段一律为 null，禁止补猜。",
  "subject": {
    "aryl_halide": "4-bromoanisole",
    "boronic_acid": "phenylboronic acid",
    "product": "4-methoxybiphenyl"
  },
  "records": [
    {
      "record_id": "SM-001a",
      "source_id": "SM-001",
      "tier": "A",
      "citation": "Miyaura, Yanagi & Suzuki, Synth. Commun. 1981, 11, 513",
      "doi": "10.1080/00397918108063618",
      "locator": "Table II, p-MeOPhBr row, footnote",
      "outcome": {
        "value": 66,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "GLC yield",
        "note": "以卤代芳烃为基准"
      },
      "assay": {
        "method": "GLC",
        "internal_standard": null,
        "quantification_basis": "aryl halide"
      },
      "conditions": {
        "catalyst": "Pd(PPh3)4",
        "catalyst_mol_pct": 3.0,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "Na2CO3",
        "base_eq": 2.0,
        "solvent": "benzene",
        "temp_c": 80,
        "time_h": 6,
        "atmosphere": "N2",
        "scale_mmol": 1.0,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.1
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "证据等级继承自角色 A 的核验记录（docs/science/2026-10-1*/），非导入方独立复核",
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      }
    },
    {
      "record_id": "SM-001b",
      "source_id": "SM-001",
      "tier": "A",
      "citation": "Miyaura, Yanagi & Suzuki, Synth. Commun. 1981, 11, 513",
      "doi": "10.1080/00397918108063618",
      "locator": "Table II, p-MeOPhBr row, value in parentheses",
      "outcome": {
        "value": 40,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "(40) isolated yield",
        "note": "括号内数值；纯化方式未在表注说明"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "Pd(PPh3)4",
        "catalyst_mol_pct": 3.0,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "Na2CO3",
        "base_eq": 2.0,
        "solvent": "benzene",
        "temp_c": 80,
        "time_h": 6,
        "atmosphere": "N2",
        "scale_mmol": 1.0,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.1
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "证据等级继承自角色 A 的核验记录（docs/science/2026-10-1*/），非导入方独立复核",
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      }
    },
    {
      "record_id": "SM-002",
      "source_id": "SM-002",
      "tier": "B",
      "citation": "Liu et al., Org. Biomol. Chem. 2011, 9, 1054",
      "doi": "10.1039/C0OB00524J",
      "locator": "SI p.1 general procedure; p.2 Table 1 entry 1 + footnote",
      "outcome": {
        "value": 100,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "isolated yield",
        "note": "分离收率，不等于纯度"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "PdCl2",
        "catalyst_mol_pct": 0.5,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2.0,
        "solvent": "50% EtOH/H2O",
        "temp_c": 25,
        "time_h": 0.5,
        "atmosphere": "air",
        "scale_mmol": 0.5,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.5
        },
        "concentration_m": null
      },
      "purification": "short column chromatography",
      "evidence_grade": "A_confirmed",
      "verification": "verified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "证据等级继承自角色 A 的核验记录（docs/science/2026-10-1*/），非导入方独立复核",
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      }
    },
    {
      "record_id": "SM-003",
      "source_id": "SM-003",
      "tier": "A",
      "citation": "Chang et al., Dalton Trans. 2015, 44, 17129",
      "doi": "10.1039/C5DT02611C",
      "locator": "SI p.3 Table S1 entry 2 + footnote",
      "outcome": {
        "value": 95,
        "unit": "%",
        "outcome_type": "nmr",
        "label_raw": "1H NMR yield",
        "note": "两次实验平均"
      },
      "assay": {
        "method": "1H NMR",
        "internal_standard": null,
        "quantification_basis": "average of two runs"
      },
      "conditions": {
        "catalyst": "Pd(OAc)2",
        "catalyst_mol_pct": 1.0,
        "ligand": "SPO (phosphinous acid) 5h",
        "ligand_mol_pct": 2.0,
        "base": "NaOtBu",
        "base_eq": 3.0,
        "solvent": "THF",
        "temp_c": 60,
        "time_h": 1,
        "atmosphere": "N2",
        "scale_mmol": 1.0,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.5
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "证据等级继承自角色 A 的核验记录（docs/science/2026-10-1*/），非导入方独立复核",
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      }
    },
    {
      "record_id": "SM-004.1",
      "source_id": "SM-004",
      "tier": "C",
      "citation": "Molecules 2018, 23, 2435",
      "doi": "10.3390/molecules23102435",
      "locator": "Table 1 entry 1 + footnotes a/c, PDF p.4",
      "outcome": {
        "value": 35.0,
        "unit": "%",
        "outcome_type": "unknown",
        "label_raw": "Yield (%) — 脚注: total conversion determined from the internal standard yield ... based on GC",
        "note": "表列名与脚注冲突，口径未决；不得当作确定 GC 收率"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "hexadecane",
        "quantification_basis": "aryl halide"
      },
      "conditions": {
        "catalyst": "Pd-bpydc-La (1)",
        "catalyst_mol_pct": 0.5,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "NaOtBu",
        "base_eq": 1.0,
        "solvent": "MeOH",
        "temp_c": 30,
        "time_h": 8,
        "atmosphere": "air",
        "scale_mmol": 0.5,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.2
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified_with_caveat",
      "catalytic_profile": {
        "catalyst_form": "supported",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "证据等级继承自角色 A 的核验记录（docs/science/2026-10-1*/），非导入方独立复核",
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      }
    },
    {
      "record_id": "SM-004.7",
      "source_id": "SM-004",
      "tier": "C",
      "citation": "Molecules 2018, 23, 2435",
      "doi": "10.3390/molecules23102435",
      "locator": "Table 1 entry 7 + footnotes a/c, PDF p.4",
      "outcome": {
        "value": 91.9,
        "unit": "%",
        "outcome_type": "unknown",
        "label_raw": "Yield (%) — 脚注: total conversion determined from the internal standard yield ... based on GC",
        "note": "同篇不同条目；催化剂/温度/时间同时不同，不得与 entry 1 视为独立来源"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "hexadecane",
        "quantification_basis": "aryl halide"
      },
      "conditions": {
        "catalyst": "Pd-bpydc-Ce (2)",
        "catalyst_mol_pct": 0.5,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "NaOtBu",
        "base_eq": 1.0,
        "solvent": "MeOH",
        "temp_c": 60,
        "time_h": 4,
        "atmosphere": "air",
        "scale_mmol": 0.5,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.2
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified_with_caveat",
      "catalytic_profile": {
        "catalyst_form": "supported",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "证据等级继承自角色 A 的核验记录（docs/science/2026-10-1*/），非导入方独立复核",
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      }
    },
    {
      "record_id": "SM-005",
      "source_id": "SM-005",
      "tier": "C",
      "citation": "Dewan et al., ACS Omega 2018, 3, 5327",
      "doi": "10.1021/acsomega.8b00039",
      "locator": "Table 1 entry 1 + table footnote; Experimental typical procedure",
      "outcome": {
        "value": 95,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "isolated yield",
        "note": "产物以 1H/13C NMR 鉴定"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "Pd NPs (papaya peel, biosynthesized)",
        "catalyst_mol_pct": 0.2,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 3.0,
        "solvent": "H2O",
        "temp_c": 25,
        "time_h": 2,
        "atmosphere": "air",
        "scale_mmol": 0.5,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.2
        },
        "concentration_m": null
      },
      "purification": "column chromatography",
      "evidence_grade": "A_confirmed",
      "verification": "verified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "tier_note": "borderline {B, C}：物理形态命中 R3（纳米颗粒）→ C；操作谱系命中 R6（无配体水相）→ B。主判 C。",
      "evidence_note": "证据等级继承自角色 A 的核验记录（docs/science/2026-10-1*/），非导入方独立复核",
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      }
    },
    {
      "record_id": "SM-006-e1",
      "source_id": "SM-006",
      "tier": "C",
      "citation": "Fiebor et al., Beilstein J. Org. Chem. 2018, 14, 1859",
      "doi": "10.3762/bjoc.14.160",
      "locator": "Table 2 entry 1 + footnote a（PMC6071702 全文已核）",
      "outcome": {
        "value": 92,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "Yield (%) 列 = 92；表脚注 a: \"aGC yield.\"",
        "note": "Table 2 entry 1：K3PO4 2 equiv、H2O、17a 1 mol%、140 °C、4 h。同表 entry 7 为 K2CO3 → 95%，是另一个条目（见 pool15 的 SM-006-e7），不得与本条合并。"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": null
      },
      "conditions": {
        "catalyst": "SNS-Pd pincer 17a",
        "catalyst_mol_pct": 1.0,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K3PO4",
        "base_eq": 2.0,
        "solvent": "H2O",
        "temp_c": 140,
        "time_h": 4,
        "atmosphere": "air",
        "scale_mmol": 0.5,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.0
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "candidate",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "non_phosphine",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "候选状态：GC 校准未注明，未完成原文核验",
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "表脚注仅写 GC yield；全文未见内标或定量基准说明",
        "resolved_conflict": "2026-10-26 已用 PMC6071702 原文核对：entry 1=92%/K3PO4，entry 7=95%/K2CO3，两者都对，此前缺 entry 编号导致冲突"
      },
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      }
    }
  ]
}"""

ENGINE_VERSION = "chemcritic-engine/1.0.0"
RULES_VERSION = "gates v0.4 · tier-0.1"
CONTRACT_VERSION = "sciencepal-verdict/1"


# ============================================================================
# 统一入口 · 平台契约（C-2 / C-3）
# ============================================================================

def record_from_payload(raw: Dict[str, Any]) -> Record:
    """用户输入 / 单条参照 → Record；缺 catalytic_profile 时查补丁表，再算档位。"""
    record = Record.from_dict(raw)
    if not record.catalytic_profile:
        profile = PROFILES.get(record.record_id or "")
        if profile:
            record.catalytic_profile = dict(profile)
    if record.catalytic_profile:
        meta = tier_of(record.catalytic_profile)
        meta["annotated"] = record.tier
        record.tier_meta = meta
        if meta["tier"]:
            record.tier = meta["tier"]
    return record


def load_records(path: Optional[str] = None) -> List[Record]:
    """参照记录集：默认读内嵌数据；给了 --records 则读外部文件（保留可替换性）。"""
    if path:
        with open(path, "r", encoding="utf-8-sig") as fh:
            payload = json.load(fh)
    else:
        payload = json.loads(RECORDS_JSON)
    subject = payload.get("subject") or {}
    rows = payload["records"] if isinstance(payload, dict) else payload
    records: List[Record] = []
    for raw in rows:
        if not raw.get("substrate"):
            raw["substrate"] = subject
        records.append(record_from_payload(raw))
    return records


def load_input(path: str) -> tuple:
    """读一条用户记录；也接受 {"subject": {...}, "references": [...]} 的整包形式。"""
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and isinstance(data.get("subject"), dict):
        subject = record_from_payload(data["subject"])
        refs = data.get("references") or None
        records = [record_from_payload(r) for r in refs] if refs else None
        return subject, records
    return record_from_payload(data), None


def build_payload(subject: Record, records: List[Record]) -> Dict[str, Any]:
    """与 engine/run_demo.py --json 完全同构的输出（这是验收 "diff 应为空" 的依据）。"""
    return {
        "subject": subject.record_id,
        "classes": class_summary(records),
        "coverage": coverage(records),
        "verdicts": [judge(subject, r).to_dict() for r in records],
        "placement": placement(subject, records),
    }


def brief_payload(subject: Record, records: List[Record]) -> Dict[str, Any]:
    """降级路径用：几百字节的证书摘要，可直接贴回会话（本地跑 + 会话编排）。"""
    pl = placement(subject, records)
    return {
        "engine": ENGINE_VERSION,
        "rules": RULES_VERSION,
        "subject": subject.record_id,
        "placement": {
            "tier": pl.get("tier"),
            "status": pl.get("status"),
            "reference_values": pl.get("reference_values"),
            "delta_vs_min": pl.get("delta_vs_min"),
            "action": pl.get("action"),
        },
        "verdicts": [
            {
                "ref": v.reference_id,
                "v": v.verdict,
                "block": v.blocking_dimension,
                "calib": v.calibration,
                "cit": v.reference_meta.get("citation"),
                "loc": v.reference_meta.get("locator"),
            }
            for v in (judge(subject, r) for r in records)
        ],
    }



# run_demo 的三屏/验收函数按模块方式调用，这里补两个命名空间壳。
gates = SimpleNamespace(judge=judge)
placement_mod = SimpleNamespace(
    class_summary=class_summary,
    coverage=coverage,
    placement=placement,
    sensitivity=sensitivity,
    load_records=load_records,
)


def run_selftest(records: List[Record]) -> bool:
    """T1–T18 验收矩阵；成功时最后一行是 `最终：19 passed, 0 failed`。"""
    return test_matrix(records)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chemcritic_engine.py",
        description="ChemCritic 8 维门槛判定引擎（单文件版）",
    )
    parser.add_argument("--json", action="store_true", help="输出结构化结论 JSON")
    parser.add_argument("--brief", action="store_true", help="输出证书摘要（几百字节，降级路径贴回会话用）")
    parser.add_argument("--input", metavar="PATH", help="用户记录 JSON；给出时默认按 JSON 输出")
    parser.add_argument("--records", metavar="PATH", help="替换参照记录集（默认内嵌 8 条）")
    parser.add_argument("--selftest", action="store_true", help="只跑 T1–T18 验收矩阵")
    parser.add_argument("--version", action="store_true", help="打印引擎与规则版本")
    args = parser.parse_args(argv)

    if args.version:
        print(f"{ENGINE_VERSION} · rules={RULES_VERSION} · contract={CONTRACT_VERSION}")
        return 0

    records = load_records(args.records)

    subject = USER
    if args.input:
        try:
            subject, refs = load_input(args.input)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"输入记录不合法：{exc}", file=sys.stderr)
            return 2
        if refs:
            records = refs

    if args.selftest:
        return 0 if run_selftest(records) else 1

    if args.brief:
        print(json.dumps(brief_payload(subject, records), ensure_ascii=False, indent=2))
        return 0

    if args.json or args.input:
        print(json.dumps(build_payload(subject, records), ensure_ascii=False, indent=2))
        return 0

    screen_one(records)
    screen_two(records)
    screen_three(records)
    screen_four(records)
    ok = run_selftest(records)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

