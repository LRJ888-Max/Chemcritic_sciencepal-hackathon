"""数据结构定义（骨架）。

约束：
- 本模块只定义结构，不含业务判断；判断逻辑全部在 gates.py。
- 原文未报告的字段一律为 None，禁止用默认值补成"事实"。
- 如需换成 Pydantic，保持字段名不变即可。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# 判定状态
PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

# 结论
COMPARABLE = "COMPARABLE"
BLOCKED = "BLOCKED"
NEEDS_EVIDENCE = "NEEDS_EVIDENCE"

ALLOWED_OUTCOME_TYPES = {"isolated", "gc", "nmr", "conversion"}

# 外部壳（前端/集成层）传来的收率基准字段 → 内部口径。
# 只做同义映射，不做推断；映射不到的一律 unknown。
YIELD_BASIS_MAP = {
    "isolated": "isolated", "分离": "isolated", "分离收率": "isolated", "称重": "isolated",
    "gc": "gc", "glc": "gc", "气相": "gc", "gc收率": "gc", "gc 收率": "gc",
    "nmr": "nmr", "核磁": "nmr", "核磁定量": "nmr", "1h nmr": "nmr", "1h-nmr": "nmr",
    "conversion": "conversion", "转化": "conversion", "转化率": "conversion",
}

# 名称归一（别名 → 规范名），键与值都按去空白小写比对
_ALIAS_FIELDS = ("aryl_halide", "boronic_acid", "product")

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


def _norm_key(value: Any) -> str:
    return " ".join(str(value).split()).lower()


def resolve_outcome_type(outcome: Dict[str, Any]) -> str:
    """口径：outcome_type 优先；缺失时用外部壳的 yield_basis 做同义映射；都没有 → unknown。

    映射表只含同义写法（分离/isolated、气相/GC、核磁/NMR、转化/conversion），
    不做任何"看起来像"的推断——映射不到就是 unknown。
    """
    raw = _norm_key(outcome.get("outcome_type") or "")
    if raw:
        return raw
    basis = _norm_key(outcome.get("yield_basis") or "")
    return YIELD_BASIS_MAP.get(basis, "unknown")


def resolve_label_raw(outcome: Dict[str, Any]) -> Optional[str]:
    """原文措辞：label_raw 优先，其次外部壳的 user_verbatim（逐字照抄，不改写、不概括）。"""
    for key in ("label_raw", "user_verbatim"):
        value = outcome.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def apply_aliases(record: "Record", aliases: Any) -> None:
    """按 name_aliases（别名 → 规范名）归一底物与产物名称。

    只改名称，不改数值、不改口径、不改任何判定依据——同义归一不是证据升级。
    """
    if not isinstance(aliases, dict) or not aliases:
        return
    table = {_norm_key(k): v for k, v in aliases.items() if isinstance(v, str)}
    for name in _ALIAS_FIELDS:
        value = record.substrate.get(name)
        if not isinstance(value, str):
            continue
        canonical = table.get(_norm_key(value))
        if canonical:
            record.substrate[name] = canonical


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
    locator_zh: Optional[str] = None  # 中文定位，供用户文案使用；英文 locator 仍保留供审计
    purification: Optional[str] = None
    verification: str = "unverified"
    catalytic_profile: Dict[str, Any] = field(default_factory=dict)
    tier_meta: Dict[str, Any] = field(default_factory=dict)
    label_raw: Optional[str] = None       # 原文措辞，逐字照抄（口径判定的唯一依据）
    label_raw_provenance: str = "unknown"  # verbatim | transcribed | inferred | unknown
    evidence_grade: str = "imported_unchecked"  # A_confirmed | imported_unchecked | candidate
    citation: Optional[str] = None        # 对外引用：作者 + 年份 + 期刊卷页（不做成 model 记忆）
    doi: Optional[str] = None
    inputs_source: Dict[str, str] = field(default_factory=dict)  # 字段路径 → user_stated / unknown
    input_audit: Dict[str, Any] = field(
        default_factory=lambda: {"status": "not_declared", "errors": []}
    )

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Record":
        outcome = d.get("outcome") or {}
        record = cls(
            record_id=d["record_id"],
            outcome_type=resolve_outcome_type(outcome),
            outcome_value=outcome.get("value"),
            assay=d.get("assay") or {},
            conditions=d.get("conditions") or {},
            substrate=dict(d.get("substrate") or {}),
            tier=d.get("tier"),
            source_id=d.get("source_id"),
            locator=d.get("locator"),
            locator_zh=d.get("locator_zh"),
            purification=d.get("purification"),
            verification=d.get("verification", "unverified"),
            catalytic_profile=d.get("catalytic_profile") or {},
            label_raw=resolve_label_raw(outcome),
            label_raw_provenance=(d.get("_extraction") or {}).get("label_raw_provenance", "unknown"),
            evidence_grade=d.get("evidence_grade") or "imported_unchecked",
            citation=d.get("citation"),
            doi=d.get("doi"),
            inputs_source=d.get("inputs_source") or {},
        )
        apply_aliases(record, d.get("name_aliases"))
        return record

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
