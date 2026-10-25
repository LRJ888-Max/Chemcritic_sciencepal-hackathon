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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "reference_id": self.reference_id,
            "verdict": self.verdict,
            "blocking_dimension": self.blocking_dimension,
            "unlock_condition": self.unlock_condition,
            "gates": {str(k): v.to_dict() for k, v in sorted(self.gates.items())},
            "soft_flags": self.soft_flags,
        }
