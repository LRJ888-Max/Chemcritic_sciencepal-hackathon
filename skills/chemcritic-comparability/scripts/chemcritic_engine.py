#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ChemCritic 判定引擎 · 单文件自包含版（零第三方依赖）。

⚠️ 本文件由 engine/build_bundle.py 自动生成，请勿直接编辑。
   改判定逻辑请改 engine/*.py，然后重新运行 `python engine/build_bundle.py`。

内嵌来源：engine/schemas.py · engine/input_audit.py · engine/profiles.py · engine/tier.py ·
          engine/gates.py · engine/placement.py · engine/leverage.py · engine/run_demo.py ·
          data/records.json

契约（平台侧唯一允许的判定入口）：
    输入  Record JSON（见 docs/integration/2026-10-27-给C-的合并指令-引擎判定接入.md §2.1）
    输出  {"subject", "inputs_source", "input_audit", "classes", "coverage", "verdicts",
           "placement", "leverage"}

铁律：verdict 三值 COMPARABLE / BLOCKED / NEEDS_EVIDENCE 只来自本文件的 judge()；
      LLM 只负责把证书翻译成用户文案，不得改写、不得补"我认为"的结论。
      `leverage` 只统计 judge() 的结果（补哪一条解锁最多），同样不由 LLM 生成。

用法：
    python chemcritic_engine.py                          # 三屏 + 补证杠杆 + 23 项自检
    python chemcritic_engine.py --selftest               # 只跑 T1–T23c（26 passed, 0 failed）
    python chemcritic_engine.py --json                   # 内置 35% 案例的结构化结论
    python chemcritic_engine.py --json --input user.json # 判定你自己的实验记录
    python chemcritic_engine.py --json --input user.json --records refs.json
    python chemcritic_engine.py --leverage               # 补证杠杆：补哪一条解锁最多（含语料级）
    python chemcritic_engine.py --judge-pair SM-001a P20 # 文献—文献两两判定（双向证书）
    python chemcritic_engine.py --audit-table table.json # 整表审计：哪些行能同框、卡在哪一维
    python chemcritic_engine.py --replicates reps.json   # 重复实验统计（均值/SD/95% CI，描述性）
    python chemcritic_engine.py --chemsense "p-bromoanisole" "4-bromoanisole"  # 化学同一性/官能团
    python chemcritic_engine.py --list-records           # 全部参照的引用/定位/口径/档位
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
# 1/13 · version.py —— 引擎/规则/契约版本号（唯一事实来源，包内模式与单文件版共用）
# ============================================================================
# 引擎版本号与契约版本——**唯一事实来源**。
#
# 为什么单独成文件（2026-10-31）：
# 版本号原先写在 `build_bundle.py` 的 RUNNER 模板字符串里，于是只有**单文件版**知道自己是
# 哪个版本，包内模式（`python engine/run_demo.py`）根本读不到 `ENGINE_VERSION`。
# 后果是同一个功能在两条运行路径上版本不一致、也没法互相校验。
#
# 现在它是 SECTIONS 的第一节，单文件版与包内模式共用同一个常量；bundle 里不再重复写死。
# 改版本号只改这里。

ENGINE_VERSION = "chemcritic-engine/1.7.2"
RULES_VERSION = "gates v0.5 · tier-0.1"
CONTRACT_VERSION = "sciencepal-verdict/2"


# ============================================================================
# 2/13 · schemas.py —— 数据结构（无业务判断；原文未报告的字段一律 None）
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


# ============================================================================
# 3/13 · input_audit.py —— 用户输入来源审计（禁止历史记忆拼装判定输入）
# ============================================================================
# 用户判定输入的来源审计（不含化学判断）。
#
# 目标：让 user.json 的每个外部事实都能追溯来源，禁止把历史对话或账号记忆
# 拼装成"用户申报过"的实验条件。
#
# `inputs_source` 是字段路径到来源的映射，例如：
#     {"outcome.outcome_type": "user_stated", "conditions.base": "unknown"}
#
# 来源只允许：
# - user_stated：用户在当前对话中明确提供；
# - unknown：用户未提供或明确表示无法提供，对应字段必须为 null / 缺省；
# - memory：历史对话或账号记忆。该来源不允许进入判定，审计直接拒绝。

ALLOWED_INPUT_SOURCES = {"user_stated", "memory", "unknown"}
_MISSING = object()


def _value_at_path(payload: Dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def audit_input_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """返回可序列化审计结果；rejected 的输入不得进入判定。"""
    declared = payload.get("inputs_source")
    if declared is None:
        return {"status": "not_declared", "errors": []}
    if not isinstance(declared, dict):
        return {"status": "rejected", "errors": ["inputs_source 必须是字段路径到来源的对象"]}

    errors = []
    for path, source in declared.items():
        if not isinstance(path, str) or not path.strip():
            errors.append("inputs_source 含空字段路径")
            continue
        if source not in ALLOWED_INPUT_SOURCES:
            errors.append(f"{path}: 未知来源 {source!r}")
            continue
        value = _value_at_path(payload, path)
        if value is _MISSING:
            errors.append(f"{path}: 字段路径不存在")
            continue
        if source == "memory":
            errors.append(f"{path}: 历史记忆不得作为判定输入；请改为 null 并标 unknown")
        elif source == "unknown" and value is not None:
            errors.append(f"{path}: 标为 unknown 时字段值必须为 null")

    return {"status": "rejected" if errors else "pass", "errors": errors}


# ============================================================================
# 4/13 · profiles.py —— 档位字段补丁表（记录缺 catalytic_profile 时兜底）
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
# 5/13 · tier.py —— 档位判定（受控词表 → 规则 → 主判 / 备选）
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
    # R7（2026-10-28）：补上词表漏洞——"无配体 + 非水介质"（如乙二醇、DMF 中的 Pd(OAc)2）
    # 既不是 A（A 要求膦配体），也不满足 R6（要求水相），此前会掉进兜底被误判成 A。
    # 判例：P35（Chin. J. Catal. 2011，Pd(OAc)2 / 乙二醇 / 室温）。
    ("R7", "ligand_family=none 且 medium=organic",
     lambda p: p.get("ligand_family") == "none" and p.get("medium") == "organic", "B"),
]

FALLBACK = ("R0", "兜底：分子配合物 + 膦类配体", "A")

# 主判优先级：物理形态 > 配体族 > 介质（数值越小越优先）
PRIMARY_PRIORITY = {"R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5, "R6": 6, "R7": 7, "R0": 9}


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
# 6/13 · chemsense.py —— 化学感知层（RDKit 同一性 / 官能团描述；无 rdkit 自动降级）
# ============================================================================
# 化学感知层（chemsense）：RDKit 驱动的底物同一性与分类。
#
# 定位（对应 2026-10-30 开发单 Track B1）：
# - **同一性**：别名表（name_aliases）兜不住的写法变体（如 "p-bromoanisole" vs
#   "1-bromo-4-methoxybenzene"），通过规范 SMILES 判同。别名表是白名单，
#   漏一个别名就误判成"不同底物"；SMILES 判同是化学事实，不靠枚举。
# - **分类**：电子效应基团（EDG/EWG）、杂环、卤素种类——供后续把"同底物"从
#   字符串相等升级为"同类底物"做准备（当前只用同一性，分类先作为能力暴露）。
#
# 设计约束：
# 1. **可降级**：沙箱可能没装 rdkit。所有入口在 import 失败时返回 None / False，
#    引擎行为回落到别名表判同——化学感知是增强，不是依赖。
# 2. **不推断**：本模块只回答"这两个名字是不是同一个化合物"和"它有哪些官能团"，
#    不输出任何可比性结论。可比性判定仍然只属于 gates。
# 3. 词表映射手工维护（没有 OPSIN 的沙箱里解析任意 IUPAC 名不现实），
#    映射表本身进入代码评审；不在表里的名字返回 None，绝不猜。
#
# 用法：
#     python engine/chemsense.py "4-bromoanisole" "p-bromoanisole"
#     python engine/chemsense.py --classify "3-bromothiophene"

import re

# 名称 → SMILES。键一律小写；查询前做 _norm。
# 覆盖：库内全部底物对 + 常见英文变体。加底物时同步加这里。
# 2026-10-31：随语料层晋升（Heck / Sonogashira / Kumada 类记录入册）补齐 131 条记录用到的底物。
SUBSTRATE_SMILES: Dict[str, str] = {
    # aryl halides
    "bromobenzene": "Brc1ccccc1",
    "iodobenzene": "Ic1ccccc1",
    "chlorobenzene": "Clc1ccccc1",
    "4-bromoanisole": "COc1ccc(Br)cc1",
    "p-bromoanisole": "COc1ccc(Br)cc1",
    "1-bromo-4-methoxybenzene": "COc1ccc(Br)cc1",
    "4-iodoanisole": "COc1ccc(I)cc1",
    "p-iodoanisole": "COc1ccc(I)cc1",
    "4-bromotoluene": "Cc1ccc(Br)cc1",
    "p-bromotoluene": "Cc1ccc(Br)cc1",
    "1-bromo-4-methylbenzene": "Cc1ccc(Br)cc1",
    "4-iodotoluene": "Cc1ccc(I)cc1",
    "p-iodotoluene": "Cc1ccc(I)cc1",
    "1-chloro-4-iodobenzene": "Clc1ccc(I)cc1",
    "3-bromothiophene": "Brc1ccsc1",
    "2-bromothiophene": "Brc1sccc1",
    "4-bromoacetophenone": "CC(=O)c1ccc(Br)cc1",
    "1-(4-bromophenyl)ethanone": "CC(=O)c1ccc(Br)cc1",
    "4-bromobenzonitrile": "N#Cc1ccc(Br)cc1",
    "4-bromonitrobenzene": "O=[N+]([O-])c1ccc(Br)cc1",
    "4-bromobenzaldehyde": "O=Cc1ccc(Br)cc1",
    "4-bromoaniline": "Nc1ccc(Br)cc1",
    # boronic acids / esters
    "phenylboronic acid": "OB(O)c1ccccc1",
    "benzeneboronic acid": "OB(O)c1ccccc1",
    "4-methoxyphenylboronic acid": "COc1ccc(B(O)O)cc1",
    "4-methylphenylboronic acid": "Cc1ccc(B(O)O)cc1",
    "p-tolylboronic acid": "Cc1ccc(B(O)O)cc1",
    "4-fluorophenylboronic acid": "Fc1ccc(B(O)O)cc1",
    "4-cyanophenylboronic acid": "N#Cc1ccc(B(O)O)cc1",
    "3-thienylboronic acid": "OB(O)c1ccsc1",
    "thiophene-3-boronic acid": "OB(O)c1ccsc1",
    # 有机金属亲核试剂（Kumada / Negishi）
    "phmgbr": "Br[Mg]c1ccccc1",
    "phenylmagnesium bromide": "Br[Mg]c1ccccc1",
    "phzncl": "Cl[Zn]c1ccccc1",
    # 烯烃 / 炔（Heck / Sonogashira 的另一侧底物）
    "styrene": "C=Cc1ccccc1",
    "methyl acrylate": "COC(=O)C=C",
    "ethyl acrylate": "CCOC(=O)C=C",
    "n-butyl acrylate": "CCCCOC(=O)C=C",
    "butyl acrylate": "CCCCOC(=O)C=C",
    "acrylic acid": "OC(=O)C=C",
    "phenylacetylene": "C#Cc1ccccc1",
    "ethynylbenzene": "C#Cc1ccccc1",
    # products
    "biphenyl": "c1ccc(-c2ccccc2)cc1",
    "4-methoxybiphenyl": "COc1ccc(-c2ccccc2)cc1",
    "4-methylbiphenyl": "Cc1ccc(-c2ccccc2)cc1",
    "stilbene": "C(=Cc1ccccc1)c1ccccc1",
    "trans-stilbene": "C(=Cc1ccccc1)c1ccccc1",
}

# 只有长得像 SMILES 的输入才交给 rdkit 解析。否则 rdkit 会把
# 4-iodoanisole 这种名字当 SMILES 试一次，失败后往 stderr 打一串 Parse Error
# —— 沙箱里 agent 读的是 stdout，这些噪音会淹掉真正的结论。
_SMILES_HINT = re.compile(r"[cnoCNOSPF]|\[|=|#|\(|\)|@")


def _looks_like_smiles(text: str) -> bool:
    """只有带 SMILES 语法特征才交给 rdkit。

    2026-10-31：原判据是 `[cnoCNOSPF]`，几乎任何英文名都含 c/n/o/s，
    于是 "2-bromoanisole" 被当 SMILES 解析，rdkit 往 stderr 刷 Parse Error
    （沙箱里会淹掉结论）。判据必须看**语法**，不看字符表。

    特征：括号/等号/井号/@ 等 SMILES 专有符号；或"环闭合数字 + 短字母段"
    （c1ccccc1 的字母段都是 1 位）。英文名的字母段通常 ≥ 5 位。
    """
    t = str(text or "").strip()
    if not t or " " in t:
        return False
    if any(ch in t for ch in "[]=#()@"):
        return True
    longest = max((len(r) for r in re.findall(r"[A-Za-z]+", t)), default=0)
    return longest <= 3 and bool(re.search(r"\d", t))

_FGS = [
    ("edg_alkoxy", "[OX2]([CH3])[c]"),
    ("edg_alkyl", "[c][CH3]"),
    ("edg_amine", "[NX3;!$(N[O])][c]"),
    ("ewg_nitro", "[N+](=O)[O-]"),
    ("ewg_nitrile", "C#N"),
    ("ewg_carbonyl", "[CX3](=O)[#6]"),
    ("ewg_halogen_F", "[c][F]"),
    ("halide_Br", "[c][Br]"),
    ("halide_I", "[c][I]"),
    ("halide_Cl", "[c][Cl]"),
    ("heteroaryl_S", "c1ccsc1"),
    ("heteroaryl_N", "c1ccnc1"),
    ("heteroaryl_O", "c1ccoc1"),
]


def _norm(name: str) -> str:
    return " ".join(str(name).strip().lower().split())


def _mol(name_or_smiles: str):
    """名字或 SMILES → RDKit Mol；失败返回 None（不猜）。"""
    try:
        from rdkit import Chem
    except Exception:
        return None
    smi = SUBSTRATE_SMILES.get(_norm(name_or_smiles))
    if smi is None:
        if not _looks_like_smiles(name_or_smiles):
            return None  # 名字不在表里，且不像 SMILES：直接放弃，不让 rdkit 去试错
        smi = name_or_smiles
    return Chem.MolFromSmiles(smi)


def canonical(name: str) -> Optional[str]:
    """名字 → 规范 SMILES；不在映射表或无 rdkit → None。"""
    mol = _mol(name)
    if mol is None:
        return None
    from rdkit import Chem
    return Chem.MolToSmiles(mol)


def same_compound(name_a: str, name_b: str) -> Optional[bool]:
    """两个名字是否为同一化合物。

    返回 None = 无法判定（名字不在映射表 / 无 rdkit），调用方回落到别名表。
    """
    ca, cb = canonical(name_a), canonical(name_b)
    if ca is None or cb is None:
        return None
    return ca == cb


def classify(name: str) -> Optional[Dict[str, Any]]:
    """名字 → 化学描述（分子量、式、官能团类别）。无 rdkit / 不识别 → None。"""
    mol = _mol(name)
    if mol is None:
        return None
    from rdkit import Chem
    from rdkit.Chem import Descriptors, rdMolDescriptors
    out: Dict[str, Any] = {
        "input": name,
        "canonical_smiles": Chem.MolToSmiles(mol),
        "mol_wt": round(Descriptors.MolWt(mol), 2),
        "formula": rdMolDescriptors.CalcMolFormula(mol),
        "aromatic_rings": rdMolDescriptors.CalcNumAromaticRings(mol),
        "functional_groups": [],
    }
    for label, sma in _FGS:
        patt = Chem.MolFromSmarts(sma)
        if patt is not None and mol.HasSubstructMatch(patt):
            out["functional_groups"].append(label)
    return out


def chemsense_payload(names: list, records_lookup_note: bool = False) -> Dict[str, Any]:
    """CLI 输出：逐名分类 + 两两同一性。"""
    try:
        import rdkit  # noqa: F401
        rdkit_ok = True
    except Exception:
        rdkit_ok = False
    if not rdkit_ok:
        return {
            "chemsense": "unavailable（未安装 rdkit；引擎回落到别名表判同，判定行为不变）",
            "names": names,
        }
    entries = []
    for n in names:
        c = classify(n)
        entries.append(c if c is not None else {"input": n, "unrecognized": True,
                        "note": "不在 SUBSTRATE_SMILES 映射表；本层不猜，绝不推断"})
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            same = same_compound(names[i], names[j])
            pairs.append({
                "a": names[i], "b": names[j],
                "same_compound": same,
                "note": None if same is not None else "至少一侧未识别，无法判定",
            })
    return {
        "chemsense": "rdkit",
        "mapping_table_size": len(SUBSTRATE_SMILES),
        "entries": entries,
        "pairs": pairs,
        "boundary": "本输出只是化学同一性与官能团描述；『能不能比』的判定只在 gates。",
    }


# ============================================================================
# 7/13 · gates.py —— 8 维门槛判定（确定性，LLM 无权覆盖）
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

def _substrate_pair(sub: dict) -> Dict[str, str]:
    """底物组合（不含产物）：任何有值的键都算，键名保留供审计。

    2026-10-31 之前这里写死 ("aryl_halide", "boronic_acid")——那等于假定全库都是
    Suzuki 型。Heck（芳卤+烯烃）、Sonogashira（芳卤+炔）、Buchwald（芳卤+胺）的记录
    入册后会被一律判成"底物未完整记录"，等于白入库。改成取所有有值的键，
    比较时用值的集合（底物对没有方向），键名不同但物质相同视为同一组合。
    """
    return {k: v for k, v in ((k, _canon(x)) for k, x in (sub or {}).items())
            if k != "product" and v}


def gate_substrate(user: Record, ref: Record) -> GateResult:
    u = _substrate_pair(user.substrate)
    r = _substrate_pair(ref.substrate)
    if len(u) < 2 or len(r) < 2:
        return GateResult(1, UNKNOWN, "底物结构未完整记录")
    if set(u.values()) == set(r.values()):
        return GateResult(1, PASS, "底物组合一致：" + " + ".join(sorted(set(u.values()))))
    return GateResult(
        1, FAIL,
        f"底物组合不同：{sorted(set(u.values()))} vs {sorted(set(r.values()))}")


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
        _label_consistency(user, "subject")
        or _label_consistency(ref, "reference")
        or _evidence_check(ref)
        or _calibration_check(user, "subject")
        or _calibration_check(ref, "reference")
        or GateResult(4, PASS, "口径标记、证据等级与校准信息均足以支撑比较")
    )


def assay_blame(user: Record, ref: Record) -> Optional[str]:
    """维度 4 的缺证归因：`subject`（用户侧）/ `reference`（文献侧）/ None（无阻断）。

    严格复刻 `gate_assay()` 的合成顺序，因此归因与判定不会互相矛盾。
    用途：把"缺 assay 证据"翻译成"该补谁的那一条"（见 engine/leverage.py）。
    """
    for side, rec in (("subject", user), ("reference", ref)):
        if _label_consistency(rec, side):
            return side
    if _evidence_check(ref):
        return "reference"
    for side, rec in (("subject", user), ("reference", ref)):
        if _calibration_check(rec, side):
            return side
    return None


# 每个口径在原文措辞里必须能找到的支撑词
# 2026-10-31 扩词：原文写全称（"determined by gas chromatography"、"1H nuclear magnetic
# resonance"）与写缩写是同一件事，词表漏收会让正确的记录被判"口径无支撑"。
# 这是词表补全，不是判定放宽——仍然要求原文有明确的定量方式措辞。
_LABEL_HINTS = {
    "isolated": ("isolated", "分离", "称重", "weighed"),
    "gc": ("gc", "glc", "gas chromatog", "气相"),
    "nmr": ("nmr", "nuclear magnetic", "核磁", "1h", "¹h"),
    "conversion": ("conversion", "转化"),
}

_REQUIRED_EVIDENCE = ("a_confirmed", "candidate")  # 小写比对；candidate 可通过，但由 calibration/软标注体现


def label_supported(record: Record) -> bool:
    """记录的口径分类是否被其原话明确支撑（用户侧与文献侧同一标准）。"""
    t = record.outcome_type
    raw = (record.label_raw or "").strip().lower()
    return t in ALLOWED_OUTCOME_TYPES and bool(raw) and any(h in raw for h in _LABEL_HINTS[t])


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
    elif us and rs and (max(us, rs) / min(us, rs)) > 5:
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
    # 2026-10-29 修：缺失不是差异。None 渲染成「未记录」；一方缺失时不写「不同」。
    # 依据 kb/L3-gates.md「未记录属待补，不属原因」+ 提示词 v8 行 240。
    # 注意：不要在这里 import re——单文件 bundle 由多模块拼接而成，
    # gates 段历史上没有 import re，加依赖会在 --json 路径上直接 NameError。
    fixed = []
    for n in notes:
        n = str(n).replace("：None vs ", "：未记录 vs ").replace(" vs None", " vs 未记录")
        if "不同：未记录 vs " in n:
            head, tail = n.split("不同：未记录 vs ", 1)
            n = f"{head}未记录（参照为 {tail}）"
        elif "不同：" in n and n.endswith(" vs 未记录"):
            n = f"{n.split('不同：', 1)[0]}未记录（您未提供）"
        fixed.append(n)
    return GateResult(gate, PASS, "；".join(fixed), level=level)


# ---------- 结论 ----------

def judge(user: Record, ref: Record) -> Verdict:
    # 所有返回路径都带完整参照元数据：无实测值时也能直接盘点知识库，
    # Agent 不应再翻引擎源码找引用或原文措辞。
    ref_meta = {
        "citation": ref.citation,
        "doi": ref.doi,
        "locator": ref.locator,
        "locator_zh": ref.locator_zh,
        "tier": ref.tier,
        "value": ref.outcome_value,
        "outcome_type": ref.outcome_type,
        "label_raw": ref.label_raw,
        "source_id": ref.source_id,
    }

    # P5b/A-审计：用户没报实测数值时，不存在"可比/不可比"这回事。
    # 此前会直接走到 COMPARABLE，等于对一个没有数值的实验给出"可并排比较"的结论。
    if user.outcome_value is None:
        gates = {g: GateResult(g, UNKNOWN, "用户未提供实测收率数值") for g in range(1, 9)}
        return Verdict(
            user.record_id, ref.record_id, NEEDS_EVIDENCE, gates,
            blocking_dimension=GATE_NAMES[3],
            unlock_condition="请提供实测收率数值（以及它是分离收率还是仪器定量）",
            soft_flags=[],
            reference_meta=ref_meta,
            calibration=_combined_calibration(user, ref),
            ref_calibration=calibration_level(ref),
            subject_calibration=calibration_level(user),
            non_independent=_same_source(user, ref),
        )

    # P5b（B 2026-10-27）：用户侧口径必须有原话支撑。
    # 否则模型可以凭空补一个"分离收率"，引擎照单全收——这是最后一道防线。
    if user.outcome_type in ("isolated", "gc", "nmr", "conversion") and not (user.label_raw or "").strip():
        gates = {g: GateResult(g, UNKNOWN, "用户侧收率口径缺少原话支撑") for g in range(1, 9)}
        return Verdict(
            user.record_id, ref.record_id, NEEDS_EVIDENCE, gates,
            blocking_dimension=GATE_NAMES[3],
            unlock_condition="请说明这个数值是分离收率还是仪器定量（需要用户原话，不接受推定）",
            soft_flags=[],
            reference_meta=ref_meta,
            calibration=_combined_calibration(user, ref),
            ref_calibration=calibration_level(ref),
            subject_calibration=calibration_level(user),
            non_independent=_same_source(user, ref),
        )

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
# 8/13 · placement.py —— 等价类 · 档内定位 · 双档敏感性
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


def ensure_keys(d: dict) -> dict:
    """给每个 placement 状态补齐字段键（值可为 None / []，但键必须在）。

    依据 v8 行 103-111：提示词把 reference_values / reference_count / delta_vs_min
    当成恒存在字段来读（「Z 必须取自 placement.delta_vs_min」）。若某些 status 下整键
    缺失，agent 读到的是 undefined 而不是 null，它会以为"没有这个字段"，于是要么崩，
    要么自己补一个——补就是编造。
    """
    for k, default in (("reference_values", []), ("reference_count", 0),
                       ("delta_vs_min", None), ("delta_vs_max", None),
                       ("delta_reference_min", None), ("delta_reference_max", None),
                       ("product_confirmed", {"confirmed": 0, "unconfirmed": 0}),
                       ("source_count", 0), ("non_independent", False),
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

    # 2026-10-31：产物「是否确认」与底物对「是否一致」是两件事。
    # _same_pair 为了不把"产物只写编号"的语料记录全排除，对"一侧未记录"放行；
    # 但放行 ≠ 已确认。若把放行直接说成 PLACED，下游会读成"可以比较"——
    # 用户实测已复现该矛盾：status=PLACED，报告正文却写"产物结构未确认"。
    states = {r.record_id: _product_state(user, r) for r in calibrated}
    confirmed = [r for r in calibrated
                 if r.outcome_value is not None and states[r.record_id] == "same"]
    inferred = [r for r in calibrated
                if r.outcome_value is not None and states[r.record_id] == "inferred"]
    unconfirmed_n = sum(1 for s in states.values() if s == "unconfirmed")
    # 可算差值的参照：产物逐字确认 + 由反应类型推定（后者须在文案里声明推定）
    usable = confirmed + inferred
    use_values = [r.outcome_value for r in usable]

    # 差值必须有"产物同一"这个前提，否则两个不同反应的收率相减就是假精确。
    can_delta = bool(same_tier) and user.outcome_value is not None and bool(use_values)
    dmin = min(use_values) if use_values else None
    dmax = max(use_values) if use_values else None

    if confirmed:
        status = "PLACED"
    elif inferred:
        status = "PLACED_PRODUCT_INFERRED"
    else:
        status = "PLACED_PRODUCT_UNCONFIRMED"

    payload = {
        "status": status,
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
        # 2026-10-31：条数 ≠ 独立证据数。条件优化表常在一张表里报十几个点，
        # 它们同属一次优化、同一批材料，按 13 条独立参照呈现就是虚增证据量。
        "source_count": len({(r.source_id or r.record_id) for r in calibrated}),
        "non_independent": len({(r.source_id or r.record_id) for r in calibrated}) == 1
                           and len(calibrated) > 1,
        # 可比面里每一条的产物依据，逐级可查
        "product_confirmed": {"confirmed": len(confirmed), "inferred": len(inferred),
                              "unconfirmed": unconfirmed_n},
        # 差值只给"同档 + 用户有值 + 产物可用"，三缺一即 None
        "delta_vs_min": round(user.outcome_value - dmin, 1) if can_delta else None,
        "delta_vs_max": round(user.outcome_value - dmax, 1) if can_delta else None,
        # 差值的基准值单独给出：可能与 reference_min/max 不同（那两条含产物未确认的）
        "delta_reference_min": dmin,
        "delta_reference_max": dmax,
        "delta_basis": ("product_recorded" if confirmed and not inferred
                        else "reaction_class_inferred" if inferred else None) if can_delta else None,
        # 跨档迁移目标：不参与差值，只回答"换体系能到哪"
        "migration_targets": migration_targets,
    }
    if status == "PLACED_PRODUCT_UNCONFIRMED":
        payload["reason"] = (
            f"同底物对、同口径的 {len(values)} 条参照都在，但产物结构两侧均未记录，"
            "且无法由底物推出反应类型，不能确认是同一产物")
        payload["action"] = "先确认产物结构（或补录参照的产物名）；确认前只并排看区间，不给差值"
    elif status == "PLACED_PRODUCT_INFERRED":
        payload["reason"] = (
            f"产物名两侧均未逐字记录；按底物键名推定为 {_reaction_kind(user.substrate)} 型，"
            f"两侧一致，故可按同一反应处理（{len(inferred)} 条）")
        payload["caveat"] = ("产物结构系由底物类别推定，不是原文逐字给出；"
                             "引用差值时必须同时说明这一点。若原文另有区域/立体选择性说明，以原文为准")
    elif unconfirmed_n:
        payload["delta_note"] = (
            f"差值基于产物可用的 {len(usable)} 条；另 {unconfirmed_n} 条产物未记录且推不出类别，未参与差值")
    return payload


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

    产物侧只排除「两侧都记录且确实不同」；「未记录」放行（详见 _product_state）。
    """
    if not _same_substrate_set(user.substrate or {}, ref.substrate or {}):
        return False
    return _product_state(user, ref) != "diff"


def _product_state(user: Record, ref: Record) -> str:
    """产物关系：same / inferred / unconfirmed / diff —— 四态，不允许把"不知道"归到任一侧。

    2026-10-31：语料层的表常只给产物编号（"3a"、"8b"），补不出名字。
    若把"一侧未记录"当成"不同产物"，131 条语料记录会永远进不了参照池（入册等于白入）；
    若把它当成"同一产物"，就会拿两个不同反应的收率相减（假精确）。
    所以既不放行成 same，也不阻断成 diff，而是分开三档：

    - same       两侧都记录了产物名且判同（唯一可以不做说明就引用的情形）
    - inferred   两侧都未记录，但底物键名给出的反应类型一致（芳卤+烯烃 → Heck 等）。
                 产物由反应类型决定，可算差值，但必须声明"产物名系推定，非原文逐字"。
    - unconfirmed 推不出（反应类型无法从键名确定）→ 只能并排，不给差值
    - diff       两侧都记录且确实不同 → 不是同一反应
    """
    us, rs = user.substrate or {}, ref.substrate or {}
    ua = getattr(user, "product", None) or us.get("product")
    ra = getattr(ref, "product", None) or rs.get("product")
    up, rp = _canon_pair(ua), _canon_pair(ra)
    if up and rp:
        return "same" if (up == rp or _chem_same(ua, ra)) else "diff"
    # 至少一侧未记录：只允许「反应类型可确定且相同」时降级为 inferred
    ku, kr = _reaction_kind(us), _reaction_kind(rs)
    if ku and ku == kr:
        return "inferred"
    return "unconfirmed"


# 底物键名 → 反应类型。**用键名而不是 reaction_class 字段**：
# 后者由标题关键词粗归类（PMC9301730 标题是 "Buchwald–Hartwig-Type Amination"，
# 底物却是芳卤 + 硼酸酯），键名来自底物名本身，错得更少。
_KIND_BY_KEY = (
    (("boronic_acid", "boronic", "boronate"), "suzuki"),
    (("alkene", "olefin", "acrylate"), "heck"),
    (("alkyne", "acetylene", "ethynyl"), "sonogashira"),
    (("amine", "aniline", "amide"), "buchwald_hartwig"),
    (("organometallic", "grignard", "zinc"), "kumada"),
)


def _reaction_kind(sub: dict) -> Optional[str]:
    """从底物键名推反应类型；推不出返回 None（不猜）。"""
    keys = {k for k, v in (sub or {}).items()
            if k != "product" and _canon_pair(v)}
    for names, kind in _KIND_BY_KEY:
        if keys & set(names):
            return kind
    return None


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


# ============================================================================
# 9/13 · leverage.py —— 补证杠杆（补哪一条解锁最多）· 语料级口径杠杆
# ============================================================================
# 补证杠杆：把"不能比"换算成"补哪一条最值"。
#
# 为什么需要它：
# - 只输出 `BLOCKED / NEEDS_EVIDENCE` 的系统，用户看到的是"都不行"，下一步仍是空白；
# - 但同一批评审对象里，缺的证据不是等价的：补一条可能解锁 4 对，补另一条一对也解锁不了。
#   于是"下一个动作"可以被**排序**，而不是靠猜。
#
# 算法（确定性，无 LLM 参与）：
# 1. 对每一对 (用户, 参照) 取硬门槛的两种阻断：
#    - `permanent`：任一硬门槛 `FAIL` —— 确定不可比，补证据也改不了结论；
#    - `missing`：硬门槛 `UNKNOWN` —— 缺证据，补上即可重新判定。
# 2. 把缺失维度折算成**一次真实动作**（字段按"谁去做、做什么"合并，不按门槛编号拆）：
#    维度 3 与 4 在文献侧都由"回原文重读一遍"解决，在用户侧都由"说明你怎么测的"解决，
#    所以各合并为一个字段——否则会把一次动作虚报成两次工作量。
# 3. **单字段边际值**：`solo` = 只补这一条就能解锁的对数；`involved` = 牵涉到的对数。
# 4. **贪心补证计划**：每步取"新解锁对数最多"的字段；若无字段能立即解锁，
#    则取"推进最多未完成对"的字段（保证有限步内收敛）。得到 k=1,2,3… 的解锁曲线。
#    这是最小集合覆盖的贪心近似——不保证最优，但足以回答"先补哪一个"。
# 5. **语料级杠杆**（`corpus_leverage`）：把参照库自身两两比较，"补齐哪几条记录的口径
#    能让可比对最多"——这是关于文献的可核对结论，不依赖演示用户。
#
# 铁律：
# - 本模块不产生判定，只对 `gates.judge()` 的结果做统计；
# - `FAIL` 的对绝不计入可解锁——否则等于承诺"补证据能改变确定不同的口径"。

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


# ============================================================================
# 10/13 · corpus_query.py —— 语料索引检索（表级；judgeable=false，不参与判定）
# ============================================================================
# 语料索引检索：让"库里有 194 张表"这件事对 agent 变成可调用的能力。
#
# 定位（2026-10-31）：
# - 这一层回答的是「文献语料里有没有相关数据、报了多少、区间多少」。
# - 它**不回答**「这两个数值能不能比」——旁边永远带 `judgeable: false`。
# - 判定只认同仓库 records.json 里已核验的 entry；索引里的数值是检索线索，不是参照值。
#
# 为什么要这层：语料 1981 行如果只在 data/corpus/ 里，SciencePal 沙箱里没有那个目录，
# agent 看不到，"知识库"就只是仓库里的一堆文件。索引内嵌进单文件引擎后才真正可查。

_NOTICE = (
    "语料索引（表级检索）：judgeable=false。这些数值只用于回答『库里有没有相关文献数据』，"
    "不得作为参照值参与可比性判定、差值计算或『优于/劣于文献』一类结论。"
    "需要判定请走 records.json 里已核验的 entry。"
)


def _haystack(t: Dict[str, Any]) -> str:
    return " ".join([
        str(t.get("t") or ""), str(t.get("cls") or ""), str(t.get("lab") or ""),
        str(t.get("cap") or ""), str(t.get("jr") or ""), str(t.get("doi") or ""),
        " ".join(t.get("sub") or []),
    ]).lower()


def _name_hit(text: str, kw: str) -> bool:
    """关键词命中，但**不把定位取代基的名字算进来**。

    2026-10-31 平台实测：查 `iodobenzene` 时命中了 `1-chloro-4-iodobenzene`，
    报出 41 条（真实 31 条）。纯子串匹配在这里不是"宽松"，是**换了化合物**。

    规则：命中处左侧不能直接接字母/数字（否则是更长名字的一部分）；
    左侧接 `-` 时，再往左不能是数字（`4-iodoanisole` 里的 iod... 不算，
    但 `Mizoroki-Heck` 这种连字符连接的反应名要保留）。
    """
    start = 0
    while True:
        i = text.find(kw, start)
        if i < 0:
            return False
        prev = text[i - 1] if i else " "
        nxt = text[i + len(kw)] if i + len(kw) < len(text) else " "
        ok = not prev.isalnum() and not nxt.isalnum()
        if ok and prev == "-":
            j = i - 2
            ok = j < 0 or not text[j].isdigit()
        if ok:
            return True
        start = i + 1


def corpus_query(index: Optional[Dict[str, Any]], keywords: List[str],
                 limit: int = 12) -> Dict[str, Any]:
    """按关键词检索语料索引。

    keywords 之间是"与"的关系（每个词都要命中），因为用户常同时给底物 + 反应类型。
    命中排序：有底物名的优先 → 有数值的优先 → 行数多的优先。
    """
    if not index or not index.get("tables"):
        return {"available": False, "reason": "语料索引未随引擎分发", "judgeable": False}

    kws = [str(k).strip().lower() for k in keywords if str(k).strip()]
    if not kws:
        return {"available": True, "hit_tables": 0,
                "notice": "请给出至少一个关键词（底物名 / 反应类型 / 期刊 / DOI 片段）",
                "judgeable": False}

    hits = [t for t in index["tables"] if all(_name_hit(_haystack(t), k) for k in kws)]

    # 只靠子串才命中的表：报出来，让 agent 知道"少掉的去哪了"，而不是自己再猜一遍
    strict_ids = {t.get("t") for t in hits}
    loose = []
    for t in index["tables"]:
        if t.get("t") in strict_ids:
            continue
        h = _haystack(t)
        if all(k in h for k in kws):
            subs = [s for s in (t.get("sub") or []) if any(k in str(s).lower() for k in kws)]
            loose.append({"table": t.get("t"), "substrates": subs[:4],
                          "year": t.get("yr"), "journal": t.get("jr")})

    hits.sort(key=lambda t: (bool(t.get("sub")), bool(t.get("nnum")),
                             t.get("n") or 0), reverse=True)

    rows = []
    for t in hits[:limit]:
        row = {
            "table": t.get("t"),
            "reaction_class": t.get("cls"),
            "label": t.get("lab"),
            "year": t.get("yr"),
            "journal": t.get("jr"),
            "doi": t.get("doi"),
            "caption": t.get("cap"),
            "rows": t.get("n"),
            "substrates": t.get("sub") or [],
            "substrate_scope_table": bool(t.get("sub_scope")),
            "scope_note": t.get("scope_note"),
            "range_withheld": t.get("range_withheld"),
            "blank_controls": t.get("blank_controls"),
            "yield_range": ([t["ymin"], t["ymax"]] if "ymin" in t else None),
            "numeric_cells": t.get("nnum"),
            "caliber": t.get("cal"),
            "triage": t.get("tier"),
            "judgeable": False,
        }
        rows.append(row)

    return {
        "available": True,
        "query": " AND ".join(kws),
        "match_rule": "关键词需独立成词：`iodobenzene` 不会命中 `1-chloro-4-iodobenzene`（换化合物不是放宽）",
        "hit_tables": len(hits),
        "returned": len(rows),
        "near_miss_tables": loose[:8],
        "near_miss_count": len(loose),
        "corpus_scale": {"tables": index.get("n_tables"), "articles": index.get("n_articles")},
        "judgeable": False,
        "notice": _NOTICE,
        "tables": rows,
    }


# 中文底物名 → 库内英文名。只覆盖库里真实存在的底物，不做通用翻译
# （通用翻译会引入"译名对得上但化合物不同"的假匹配，比不认更糟）。
_ZH_SUBSTRATE = {
    "碘苯": "iodobenzene", "溴苯": "bromobenzene", "氯苯": "chlorobenzene",
    "苯硼酸": "phenylboronic acid", "苯乙炔": "phenylacetylene",
    "苯乙烯": "styrene", "丙烯酸甲酯": "methyl acrylate",
    "丙烯酸乙酯": "ethyl acrylate", "丙烯酸丁酯": "n-butyl acrylate",
    "4-碘苯甲醚": "4-iodoanisole", "对碘苯甲醚": "4-iodoanisole",
    "4-溴苯甲醚": "4-bromoanisole", "对溴苯甲醚": "4-bromoanisole",
    "2-溴苯甲醚": "2-bromoanisole",
    "4-碘甲苯": "4-iodotoluene", "对碘甲苯": "4-iodotoluene",
    "4-溴甲苯": "4-bromotoluene", "对溴甲苯": "4-bromotoluene",
    "甲苯胺": "toluidine", "对甲苯胺": "toluidine", "苯胺": "aniline",
    "4-溴苯乙酮": "4-bromoacetophenone", "4-溴苯甲醛": "4-bromobenzaldehyde",
    "4-溴二苯甲酮": "4-bromobenzophenone",
    "1-氯-4-碘苯": "1-chloro-4-iodobenzene", "4-氯碘苯": "1-chloro-4-iodobenzene",
}


def _qnorm(name: Any) -> str:
    """底物名归一：大小写、空白、连接符、尾部句点。只做这些，不做模糊。"""
    s = str(name or "").strip().lower()
    for a, b in (("–", "-"), ("—", "-"), ("’", "'"), ("′", "'")):
        s = s.replace(a, b)
    s = " ".join(s.split())
    return s.rstrip(" .;")


def find_substrate_payload(records: List[Record], names: List[str]) -> Dict[str, Any]:
    """按底物名精确盘点参照记录。

    为什么要有这条命令（2026-10-31 平台实测）：
    agent 被要求"库里有 X 的数据吗"，它自己从 --list-records 里数，用的是子串匹配，
    于是 `1-chloro-4-iodobenzene` 被算进了 `iodobenzene`，报出 41 条（真实 31 条）。
    模型做字符串匹配就一定会犯这个错，所以把匹配交给引擎，并把**被排除的近名**
    显式列出来——不然 agent 看不出"少掉的 10 条去哪了"，还会再猜一次。

    匹配规则：
    1. 归一后相等（大小写/空白/连接符/尾句点）；
    2. 同一化合物（chemsense 规范 SMILES 判同，覆盖 p- 与 4- 这类写法差异）；
    3. 都不是 → 不计入，若名字包含查询词则进 `excluded_similar`。

    只匹配 substrate 里的非产物字段：产物名不是底物，混进来会把"能生成该产物的
    其他反应"也算成"该底物的数据"。
    """
    out: Dict[str, Any] = {
        "engine": ENGINE_VERSION,
        "match_rule": ("底物名归一后精确相等，或经 chemsense 判定为同一化合物；"
                       "不做子串匹配。名字包含查询词但化合物不同的，列在 excluded_similar 里且不计入。"),
        "queries": [],
        "total_records": len(records),
    }
    all_rows: List[Dict[str, Any]] = []
    for raw in names:
        q = _ZH_SUBSTRATE.get(str(raw).strip(), str(raw).strip())
        qn = _qnorm(q)
        matched, excluded = [], {}

        for r in records:
            sub = r.substrate or {}
            vals = {k: v for k, v in sub.items() if k != "product" and v}
            basis = None
            for k, v in vals.items():
                if _qnorm(v) == qn:
                    basis = ("exact" if str(v).strip() == str(raw).strip() else "normalized")
                    break
            if basis is None:
                # 化学同一性兜底：p-bromoanisole ≡ 4-bromoanisole
                for k, v in vals.items():
                    if _chem_same(v, q):
                        basis = "chem_identity"
                        break
            if basis:
                partner = next(((k, v) for k, v in vals.items()
                                if _qnorm(v) != qn), (None, None))
                matched.append({
                    "record_id": r.record_id,
                    "source_id": r.source_id or r.citation,
                    "citation": r.citation,
                    "locator_zh": r.locator_zh,
                    "value": r.outcome_value,
                    "outcome_type": r.outcome_type,
                    "tier": r.tier,
                    "tier_status": "determined" if r.tier else "undetermined",
                    "reaction_class": _reaction_kind(sub),
                    "substrate_key": k,
                    "name": v,
                    "partner_field": partner[0],
                    "partner": partner[1],
                    "match_basis": basis,
                })
            else:
                for v in vals.values():
                    vn = _qnorm(v)
                    if qn and qn in vn and vn != qn:
                        excluded[v] = excluded.get(v, 0) + 1

        # 按来源聚合：条数不等于独立证据数，同一篇文献报 12 个点仍是一个来源
        groups: Dict[str, Dict[str, Any]] = {}
        for m in matched:
            key = m["source_id"] or "(无来源)"
            g = groups.setdefault(key, {
                "source_id": key, "citation": m["citation"],
                "reaction_class": m["reaction_class"],
                "aryl_halide": None,
                "partner_field": m["partner_field"], "partner": m["partner"],
                "count": 0, "values": [], "outcome_types": set(), "tiers": set(),
            })
            g["count"] += 1
            if m["value"] is not None:
                g["values"].append(m["value"])
            g["outcome_types"].add(m["outcome_type"])
            g["tiers"].add(m["tier"] or "undetermined")

        source_rows = []
        for g in groups.values():
            vs = g.pop("values")
            tiers = g.pop("tiers")
            source_rows.append({
                **g,
                "outcome_types": sorted(g["outcome_types"]),
                "tier": ("determined" if tiers - {"undetermined"} else "undetermined"),
                "tier_detail": sorted(tiers),
                "value_range": ([min(vs), max(vs)] if vs else None),
            })
        source_rows.sort(key=lambda x: -x["count"])

        # 一个来源里芳基卤到底是哪个，从记录本身取，不靠猜
        for row in source_rows:
            for m in matched:
                if (m["source_id"] or "(无来源)") == row["source_id"] and m["substrate_key"] == "aryl_halide":
                    row["aryl_halide"] = m["name"]
                    break

        by_class: Dict[str, int] = {}
        for m in matched:
            by_class[m["reaction_class"] or "unknown"] = by_class.get(m["reaction_class"] or "unknown", 0) + 1
        det = sum(1 for m in matched if m["tier"])
        reasons: Dict[str, int] = {}
        for r in records:
            if not any(m["record_id"] == r.record_id for m in matched):
                continue
            if r.tier:
                continue
            for p in (r.tier_meta or {}).get("problems") or ["未提供 catalytic_profile"]:
                head = p.split("=")[0]
                reasons[head] = reasons.get(head, 0) + 1

        payload = {
            "query": q,
            "query_raw": raw,
            "count": len(matched),
            "sources": len(source_rows),
            "by_reaction_class": by_class,
            "tier": {"determined": det, "undetermined": len(matched) - det,
                     "undetermined_reasons": reasons},
            "source_detail": source_rows,
            "excluded_similar": [
                {"substrate": k, "count": v,
                 "why": "名字包含查询词，但不是同一化合物；不计入 count"}
                for k, v in sorted(excluded.items(), key=lambda kv: -kv[1])
            ],
            "records": matched,
        }
        if not matched:
            payload["note"] = (
                f"库里没有以「{q}」为底物的参照记录。"
                + ("excluded_similar 里是名字相近但不同的化合物，不能替代。"
                   if excluded else "也没有名字相近的化合物。"))
        elif excluded:
            payload["note"] = (
                "count 只算同一化合物。" + "、".join(
                    f"{k}（{v} 条）" for k, v in list(excluded.items())[:3])
                + " 名字相近但化合物不同，已排除——不要把它们并进 count，也不要合并成同一底物。")
        out["queries"].append(payload)
    out["count"] = sum(q["count"] for q in out["queries"])
    return out


# ============================================================================
# 11/13 · run_demo.py —— 三屏演示与规则表验收矩阵（T1–T23c）
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
    label_raw="分离收率（柱层析称重）",
    label_raw_provenance="verbatim",
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
    "T12": ("SM-002", NEEDS_EVIDENCE, "assay"),  # 用户只说 35%，未声明口径 → 维度 4 第一层：措辞不支撑 isolated
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
    if result["status"] not in ("PLACED", "PLACED_PRODUCT_UNCONFIRMED"):
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
    # 三态，只有第三种是失败：
    #   OK        有标注 && 计算一致
    #   未标注    没有人工标注（语料晋升的 candidate）——不是矛盾，是"还没人核"，不能说成已核验
    #   MISMATCH  有标注 && 计算不一致 —— 真错误（规则或字段有问题）
    # 2026-10-31：原先写 `ok == len(records)`，140 条里有 36 条 candidate 没有标注，于是
    # 永远 104/140 → run_demo 退出码恒为 1，而屏幕上是"29 passed, 0 failed"。
    # 症状很坏：脚本/CI 判失败，人看输出判成功。三态之后两者一致。
    annotated_n = agree_n = 0
    mismatches = []
    for r in records:
        meta = r.tier_meta or {}
        rules = ", ".join(f"{x['rule_id']}" for x in meta.get("rules", [])) or "-"
        borderline = "/".join(meta.get("borderline", [])) or "-"
        annotated = meta.get("annotated") or "-"
        computed = meta.get("tier") or "-"
        if annotated == "-":
            state = "未标注"
        elif annotated == computed:
            state = "OK"
            annotated_n += 1
            agree_n += 1
        else:
            state = "MISMATCH"
            annotated_n += 1
            mismatches.append((r.record_id, annotated, computed))
        print(f"  {r.record_id:<10} tier={computed:<3} (标注 {annotated:<3}) {state:<8} "
              f"rules={rules:<8} borderline={borderline:<6} conf={meta.get('confidence', '-'):<7} "
              f"form={r.catalytic_profile.get('catalyst_form', '-'):<17} ligand={r.catalytic_profile.get('ligand_family', '-')}")

    unannotated_n = sum(1 for r in records if not (r.tier_meta or {}).get("annotated"))
    undetermined_n = sum(1 for r in records if not (r.tier_meta or {}).get("tier"))
    if mismatches:
        shown = "、".join(f"{rid}(标注{a}/计算{b})" for rid, a, b in mismatches[:3])
        print(f"\n  交叉校验：{agree_n}/{annotated_n} 条有标注的记录一致；**{len(mismatches)} 条不一致**（{shown}）")
    else:
        print(f"\n  交叉校验：{agree_n}/{annotated_n} 条有标注的记录全部一致（0 条不一致）")
    print(f"  未标注：{unannotated_n} 条（语料晋升 candidate —— 有计算档位，但没有人工核验；"
          f"对外只能说『计算为 X 档』，不能说『已核验为 X 档』）")
    print(f"  无法定档：{undetermined_n} 条（缺受控字段，只并排、不定档）")
    return not mismatches


def screen_five(records):
    _hr("屏 5 · 补证杠杆：补哪一条，解锁最多")
    report = leverage_mod.leverage_report(USER, records)
    for line in leverage_mod.render(report):
        print(line)
    corpus = leverage_mod.corpus_leverage(records)
    print("\n  —— 语料级（补哪几条记录的口径，能新增最多可比对）")
    for line in leverage_mod.render_corpus(corpus):
        print(line)
    return report


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
            subject = Record(record_id="USER-012", outcome_type="isolated", outcome_value=35, label_raw="35%（用户只说 35%，未说明口径）",
                             substrate=USER.substrate, conditions=USER.conditions,
                             assay={"method": "reported as yield"}, tier="A")
        elif case == "T13":
            subject = Record(record_id="USER-013", outcome_type="gc", outcome_value=66, label_raw="GC 66%",
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
            subject = Record(record_id="USER-016", outcome_type="conversion", outcome_value=95, label_raw="转化率 95%",
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
    subj_uncal = Record(record_id="USER-017", outcome_type="gc", outcome_value=95, label_raw="GC 95%",
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

    # T19 · 迁移目标必须同底物（2026-10-28：此前会把溴苯的 P20 当迁移目标）
    pl = placement_mod.placement(USER, records)
    mt_ids = [t["record_id"] for t in pl.get("migration_targets", [])]
    by_mt = {r.record_id: r for r in records}
    ok19 = (
        bool(mt_ids)
        and all((by_mt[i].substrate or {}).get("aryl_halide") == "4-bromoanisole" for i in mt_ids)
        and "P20" not in mt_ids
        and pl.get("delta_vs_min") == -5
    )
    passed += ok19
    failed += not ok19
    print(f"  [{'PASS' if ok19 else 'FAIL'}] T19  迁移目标同底物 → {mt_ids}  expect 仅 4-溴苯甲醚，delta=-5")

    # T20 · 输入来源审计：记忆不得进入判定；unknown 必须对应 null；当前对话原话可通过
    valid_raw = {
        "outcome": {"value": 35, "outcome_type": "isolated", "label_raw": "分离收率"},
        "conditions": {"base": None},
        "inputs_source": {"outcome.outcome_type": "user_stated", "conditions.base": "unknown"},
    }
    memory_raw = {
        "conditions": {"base": "Na2CO3"},
        "inputs_source": {"conditions.base": "memory"},
    }
    unknown_bad_raw = {
        "conditions": {"base": "Na2CO3"},
        "inputs_source": {"conditions.base": "unknown"},
    }
    a_valid = audit_input_payload(valid_raw)
    a_memory = audit_input_payload(memory_raw)
    a_unknown = audit_input_payload(unknown_bad_raw)
    ok20 = (a_valid["status"] == "pass" and a_memory["status"] == "rejected"
            and a_unknown["status"] == "rejected")
    passed += ok20
    failed += not ok20
    print(f"  [{'PASS' if ok20 else 'FAIL'}] T20  输入来源审计 → valid={a_valid['status']}, "
          f"memory={a_memory['status']}, unknown非空={a_unknown['status']}")

    # T21 · 全部参照都有中英定位；无实测值的提前返回也带引用与原文措辞
    no_value = replacement(USER, outcome_value=None)
    v21 = gates.judge(no_value, by_id["SM-004.1"])
    meta21 = v21.reference_meta
    ok21 = (
        all(r.citation and r.locator and r.locator_zh for r in records)
        and meta21.get("locator_zh")
        and "Yield (%)" in (meta21.get("label_raw") or "")
        and "total conversion" in (meta21.get("label_raw") or "")
    )
    passed += ok21
    failed += not ok21
    print(f"  [{'PASS' if ok21 else 'FAIL'}] T21  参照元数据完整 → 中英定位齐全，"
          f"无实测值证书仍带 label_raw/locator_zh")

    # T22 · 即使用户 JSON 错标 isolated，"收了35%"也不得产生档内差值
    ambiguous = Record(
        record_id="USER-022", outcome_type="isolated", outcome_value=35,
        label_raw="收了35%", substrate=USER.substrate, conditions=USER.conditions,
        assay=USER.assay, tier="A",
    )
    v22 = gates.judge(ambiguous, by_id["SM-001b"])
    pl22 = placement_mod.placement(ambiguous, records)
    ok22 = (v22.verdict == NEEDS_EVIDENCE and v22.blocking_dimension == "assay"
            and pl22["status"] == "UNDEFINED" and pl22.get("delta_vs_min") is None)
    passed += ok22
    failed += not ok22
    print(f"  [{'PASS' if ok22 else 'FAIL'}] T22  模糊口径双重拦截 → "
          f"verdict={v22.verdict}/{v22.blocking_dimension}, placement={pl22['status']}")

    # T23 · 补证杠杆：FAIL 的对绝不能被算成"可解锁"；曲线单调；口径未声明时只指向一条
    rep = leverage_mod.leverage_report(USER, records)
    rep2 = leverage_mod.leverage_report(USER, records)
    permanent_fail = all(
        row["verdict"] == BLOCKED
        for row in leverage_mod.pair_profile(USER, records)
        if row["permanent"]
    )
    curve = [c["pairs"] for c in rep["curve"]]
    plan_fields = {step["field"] for step in rep["plan"]}
    ok23 = (
        rep["status"] == "OK"
        and rep["fixable"] + rep["permanent"] == rep["blocked"]
        and rep["blocked"] + rep["comparable"] == len(records)
        and curve == sorted(curve)
        and (not curve or curve[-1] == rep["fixable"])
        and permanent_fail
        and not (plan_fields & {"substrate:subject", "product:subject"})  # 用户底物/产物已记录，不该建议补
        and rep["plan"] == rep2["plan"]  # 确定性：同输入同计划
    )
    passed += ok23
    failed += not ok23
    print(f"  [{'PASS' if ok23 else 'FAIL'}] T23  补证杠杆 → blocked={rep['blocked']}, "
          f"fixable={rep['fixable']}, permanent={rep['permanent']}, "
          f"curve={curve}, plan={[s['field'] for s in rep['plan']]}")

    # T23b · 口径未声明时，计划只应指向"你的口径声明"，不得让用户先去补文献
    unclear = Record(record_id="USER-023", outcome_type="isolated", outcome_value=35,
                     label_raw="", substrate=USER.substrate, conditions=USER.conditions,
                     assay={"method": "reported as yield"}, tier="A")
    rep_unclear = leverage_mod.leverage_report(unclear, records)
    ok23b = (
        rep_unclear["status"] == "OK"
        and len(rep_unclear["plan"]) == 1
        and rep_unclear["plan"][0]["field"] == "caliber:subject"
    )
    passed += ok23b
    failed += not ok23b
    print(f"  [{'PASS' if ok23b else 'FAIL'}] T23b 口径未声明 → plan="
          f"{[s['field'] for s in rep_unclear['plan']]} expect 只有 caliber:subject")

    # T23c · 无实测值：不得给出补证排序，应直接要求先给数
    novalue = Record(record_id="USER-024", outcome_type="isolated", outcome_value=None,
                     label_raw="分离收率", substrate=USER.substrate, conditions=USER.conditions,
                     assay=USER.assay, tier="A")
    rep_novalue = leverage_mod.leverage_report(novalue, records)
    ok23c = rep_novalue["status"] == "NO_SUBJECT_VALUE" and not rep_novalue["plan"]
    passed += ok23c
    failed += not ok23c
    print(f"  [{'PASS' if ok23c else 'FAIL'}] T23c 无实测值 → status="
          f"{rep_novalue['status']}, plan={len(rep_novalue['plan'])} expect 空")

    # T24 · 外部壳字段兼容：yield_basis → 口径，user_verbatim → 原文措辞
    ext_raw = {
        "record_id": "USER-025",
        "outcome": {"value": 35, "yield_basis": "分离", "user_verbatim": "柱层析后称重得 35%"},
        "substrate": {"aryl_halide": "4-bromoanisole", "boronic_acid": "phenylboronic acid",
                      "product": "4-methoxybiphenyl"},
        "conditions": {"catalyst": "Pd(PPh3)4", "base": "Na2CO3", "solvent": "THF", "temp_c": 60},
        "assay": {"method": "isolated (weighed)", "quantification_basis": "isolated product"},
        "catalytic_profile": USER.catalytic_profile,
    }
    ext_rec = Record.from_dict(ext_raw)
    v24 = gates.judge(ext_rec, by_id["SM-001b"])
    ok24 = (
        ext_rec.outcome_type == "isolated"
        and ext_rec.label_raw == "柱层析后称重得 35%"
        and v24.verdict == COMPARABLE and v24.blocking_dimension is None
    )
    passed += ok24
    failed += not ok24
    print(f"  [{'PASS' if ok24 else 'FAIL'}] T24  外部壳字段 → 口径={ext_rec.outcome_type}, "
          f"措辞={'有' if ext_rec.label_raw else '无'}, verdict={v24.verdict}")

    # T25 · 名称别名归一：中文名按 name_aliases 映射后才参与底物/产物比对
    alias_raw = {
        "record_id": "USER-026",
        "name_aliases": {"4-溴苯甲醚": "4-bromoanisole", "苯硼酸": "phenylboronic acid",
                         "4-甲氧基联苯": "4-methoxybiphenyl"},
        "outcome": {"value": 35, "outcome_type": "isolated", "label_raw": "分离收率"},
        "substrate": {"aryl_halide": "4-溴苯甲醚", "boronic_acid": "苯硼酸", "product": "4-甲氧基联苯"},
        "conditions": {"catalyst": "Pd(PPh3)4", "base": "Na2CO3", "solvent": "THF", "temp_c": 60},
        "assay": {"method": "isolated (weighed)", "quantification_basis": "isolated product"},
        "catalytic_profile": USER.catalytic_profile,
    }
    alias_rec = Record.from_dict(alias_raw)
    v25 = gates.judge(alias_rec, by_id["SM-001b"])
    ok25 = (
        alias_rec.substrate.get("aryl_halide") == "4-bromoanisole"
        and v25.gates[1].status == PASS and v25.gates[2].status == PASS
    )
    passed += ok25
    failed += not ok25
    print(f"  [{'PASS' if ok25 else 'FAIL'}] T25  别名归一 → 底物={alias_rec.substrate.get('aryl_halide')}, "
          f"gate1={v25.gates[1].status}, gate2={v25.gates[2].status}")

    # T26 · 按底物盘点必须精确同一化合物
    # 子串匹配会把 1-chloro-4-iodobenzene 算进 iodobenzene，平台实测因此报出 41 条
    # （真实 31 条），并在同一份报告里既写"芳基卤=1-氯-4-碘苯"又说"共 41 条碘苯记录"。
    # 该函数在 corpus_query.py（唯一实现），单文件版由 SECTIONS 内联、包内模式由上面的 import 提供，
    # 两条路径都该有；取不到说明模块拆分被改坏了，不再静默跳过。
    finder = globals().get("find_substrate_payload")
    if finder is None:
        print("  [SKIP] T26  按底物精确匹配（未找到 corpus_query.find_substrate_payload）")
    else:
        q26 = finder(records, ["iodobenzene"])["queries"][0]
        exact26 = sum(1 for r in records
                      if any(str(v).strip().lower() == "iodobenzene"
                             for k, v in (r.substrate or {}).items() if k != "product" and v))
        names26 = {str(m["name"]).strip().lower() for m in q26["records"]}
        ok26 = (q26["count"] == exact26 and names26 <= {"iodobenzene"})
        passed += ok26
        failed += not ok26
        print(f"  [{'PASS' if ok26 else 'FAIL'}] T26  按底物精确匹配 → count={q26['count']} "
              f"(精确重算 {exact26}), 排除近名={len(q26['excluded_similar'])}")

    print(f"\n  最终：{passed} passed, {failed} failed（含 T17–T26）")
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
      },
      "locator_zh": "表 II，p-MeOPhBr 行，脚注"
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
      },
      "locator_zh": "表 II，p-MeOPhBr 行，括号内数值"
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
      },
      "locator_zh": "补充资料第 1 页通用步骤；第 2 页表 1 第 1 条及脚注"
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
      },
      "locator_zh": "补充资料第 3 页表 S1 第 2 条及脚注"
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
      },
      "locator_zh": "表 1 第 1 条及脚注 a/c，PDF 第 4 页"
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
      },
      "locator_zh": "表 1 第 7 条及脚注 a/c，PDF 第 4 页"
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
      },
      "locator_zh": "表 1 第 1 条及表脚注；实验部分通用步骤"
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
      },
      "locator_zh": "表 2 第 1 条及脚注 a"
    },
    {
      "record_id": "P11",
      "source_id": "P11",
      "tier": "C",
      "citation": "Gholinejad, Jafarpour & Sansano, RSC Adv. 2026, 16(38), 42714-42723",
      "doi": "10.1039/d6ra03494b",
      "locator": "Table 1 entry 3 + 脚注 a（PMC13397079 全文已核，A 2026-10-26）",
      "outcome": {
        "value": 96.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "Yield (%) 列 entry 3 = 96；脚注 a: Yields determined by GC",
        "note": "同底物 + 同 60 °C；表内载量梯度 0.035→15%、0.07→50%、0.1→96%、0.17→97%；温度梯度 25→20%、50→60%、60→96%；24 h→98%"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "GC 面积（未注明归一化方式）",
        "assay_basis_source": "Table 1 脚注 a: Yields determined by GC；全文未见内标"
      },
      "conditions": {
        "catalyst": "clinochlore-CQDs@Pd",
        "catalyst_loading_raw": "0.1 mol%",
        "catalyst_mol_pct": 0.1,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 1.5,
        "solvent": "H2O/EtOH = 1:1, 2 mL",
        "temp_c": 60,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": 0.2,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.5
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
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "missing_fields": [
          "assay.internal_standard"
        ],
        "needs_A_check": [
          "页/表/entry 编号",
          "K2CO3 当量（正文只说 as a base）",
          "GC 内标与定量基准",
          "底物用量"
        ],
        "assay_basis_source": "Table 1 脚注 a 仅写 'Yields determined by GC'；全文未见内标说明",
        "calibration": "uncalibrated",
        "verified_at": "2026-10-26",
        "verified_by_A": "2026-10-26 开放获取核验记录 §2.5"
      },
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      },
      "locator_zh": "表 1 第 3 条及脚注 a"
    },
    {
      "record_id": "P12",
      "source_id": "P12",
      "tier": "C",
      "citation": "Hajighasemi, Nahipour, Ghorbani-Choghamarani & Taherinia, Nanoscale Adv. 2023, 5(18), 4925-4933",
      "doi": "10.1039/d3na00403a",
      "locator": "Table 3 entry 7（Table 3 无表脚注、Yield 列无上标）",
      "outcome": {
        "value": 99.0,
        "unit": "%",
        "outcome_type": "unknown",
        "label_raw": "Yield (%) entry 7 = >99（无上标、无表脚注）",
        "value_raw": ">99",
        "value_is_lower_bound": true,
        "note": "原文未声明口径。Table 2 有脚注 a Isolated yield，Table 3 没有 → 不得推定沿用（A 2026-10-26 决定）"
      },
      "assay": {
        "method": "未声明",
        "internal_standard": null,
        "quantification_basis": null,
        "assay_basis_source": "Table 2 脚注 a = Isolated yield；Table 3 无任何脚注 → 不能沿用"
      },
      "conditions": {
        "catalyst": "Pd(0)-SMTU-boehmite",
        "catalyst_loading_raw": "5 mg",
        "catalyst_mol_pct": 0.94,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 1.5,
        "solvent": "PEG-400",
        "temp_c": 80,
        "time_h": 1.17,
        "time_raw": "70 min",
        "atmosphere": null,
        "scale_mmol": 1.0,
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
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "missing_fields": [
          "catalyst_mol_pct",
          "equivalents.boronic_acid",
          "atmosphere",
          "purification"
        ],
        "needs_A_check": [
          "Table 3 行号与 4-溴苯甲醚列的绑定",
          "5 mg 对应 mol%（缺则维度 6 只能记 L2）",
          "纯化手段",
          ">99% 的原文写法"
        ],
        "assay_basis_source": "Table 3 无脚注，无法确认口径",
        "verified_by_A": "2026-10-26 开放获取核验记录 §2.1",
        "derived": {
          "catalyst_mol_pct": "由 TON 105 反算 ≈0.94 mol%"
        }
      },
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      },
      "locator_zh": "表 3 第 7 条（无表脚注，Yield 列无上标）"
    },
    {
      "record_id": "P13",
      "source_id": "P13",
      "tier": "C",
      "citation": "Esteban et al., Polymers 2023, 15(20), 4143",
      "doi": "10.3390/polym15204143",
      "locator": "§2.5 General Procedure + 优化表（PMC10611190 全文已核）",
      "outcome": {
        "value": 100.0,
        "unit": "%",
        "outcome_type": "conversion",
        "label_raw": "conversion and yield reaction were obtained by GC-MS",
        "note": "原文报的是转化率而非收率；杂/同偶联 = 100:0。不得折算成分离收率"
      },
      "assay": {
        "method": "GC-MS",
        "internal_standard": null,
        "quantification_basis": "以全部峰之和为分母的 GC-MS 定量",
        "assay_basis_source": "§2.5: by taking all the peaks of the chromatogram as the sum of all reaction products"
      },
      "conditions": {
        "catalyst": "Pd@PP-3（微孔膦聚合物负载 Pd）",
        "catalyst_loading_raw": "1 mol%",
        "catalyst_mol_pct": 1.0,
        "ligand": "polymer-immobilised phosphine",
        "ligand_mol_pct": null,
        "base": "Na2CO3",
        "base_eq": 2.0,
        "solvent": "EtOH/H2O = 2 mL/3 mL",
        "temp_c": 80,
        "time_h": 2.0,
        "atmosphere": "N2",
        "scale_mmol": 0.5,
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
        "catalyst_form": "supported",
        "ligand_family": "phosphine",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "missing_fields": [
          "purification"
        ],
        "needs_A_check": [
          "100% 是 conversion 的确认（这条决定维度 3 判 FAIL）",
          "是否有对应的分离收率数字（若有，应另立一条记录）"
        ],
        "verified_by_A": "2026-10-26 开放获取核验记录 §2.2"
      },
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      },
      "locator_zh": "第 2.5 节通用步骤及优化表"
    },
    {
      "record_id": "P19a",
      "source_id": "P19",
      "tier": "C",
      "citation": "Jiang et al., Molecules 2012, 17, 12121-12139（C2 对称 Pd-NHC）",
      "doi": "10.3390/molecules171012121",
      "locator": "Table 1 entry 2 + 脚注 b (GC yield)；内标依据见同文 Table 4 脚注",
      "outcome": {
        "value": 95.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "Yield (%) entry 2 = 95；脚注 b: GC yield",
        "note": "同表 entry 1/2 的碱与时间同时变化（Na2CO3 24 h vs K2CO3 1.5 h）→ 可作同表同口径对照，不得支持『换碱导致差异』的因果"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "diethylene glycol di-n-butyl ether",
        "quantification_basis": "GC yield",
        "assay_basis_source": "同文 Table 4 脚注：GC yield using diethylene glycol di-n-butyl ether as an internal standard"
      },
      "conditions": {
        "catalyst": "Pd(OAc)2",
        "catalyst_loading_raw": "0.5 mol%",
        "catalyst_mol_pct": 0.5,
        "ligand": "7a（C2 对称 NHC 前体）",
        "ligand_mol_pct": 1.0,
        "base": "K2CO3",
        "base_eq": 3.0,
        "solvent": "toluene",
        "temp_c": 90,
        "time_h": 1.5,
        "atmosphere": null,
        "scale_mmol": 0.5,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.5
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified_with_caveat",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "non_phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "missing_fields": [
          "atmosphere",
          "assay.internal_standard",
          "assay.quantification_basis"
        ],
        "needs_A_check": [
          "表行号",
          "GC 内标与基准"
        ],
        "two_variables": true,
        "caveat": {
          "two_variables": true,
          "note": "碱与时间同时变化"
        },
        "verified_by_A": "2026-10-26 开放获取核验记录 §2.3"
      },
      "locator_zh": "表 1 第 2 条及脚注 b；内标依据见同文表 4 脚注"
    },
    {
      "record_id": "P19b",
      "source_id": "P19",
      "tier": "C",
      "citation": "Jiang et al., Molecules 2012, 17, 12121-12139（C2 对称 Pd-NHC）",
      "doi": "10.3390/molecules171012121",
      "locator": "Table 1 entry 1 + 脚注 b (GC yield)；内标依据见同文 Table 4 脚注",
      "outcome": {
        "value": 12.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "Yield (%) entry 1 = 12；脚注 b: GC yield",
        "note": "同表 entry 1/2 的碱与时间同时变化（Na2CO3 24 h vs K2CO3 1.5 h）→ 可作同表同口径对照，不得支持『换碱导致差异』的因果"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "diethylene glycol di-n-butyl ether",
        "quantification_basis": "GC yield",
        "assay_basis_source": "同文 Table 4 脚注：GC yield using diethylene glycol di-n-butyl ether as an internal standard"
      },
      "conditions": {
        "catalyst": "Pd(OAc)2",
        "catalyst_loading_raw": "0.5 mol%",
        "catalyst_mol_pct": 0.5,
        "ligand": "7a（C2 对称 NHC 前体）",
        "ligand_mol_pct": 1.0,
        "base": "Na2CO3",
        "base_eq": 3.0,
        "solvent": "toluene",
        "temp_c": 90,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": 0.5,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.5
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified_with_caveat",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "non_phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "missing_fields": [
          "atmosphere",
          "assay.internal_standard",
          "assay.quantification_basis"
        ],
        "needs_A_check": [
          "12% 的口径标记是否与同表其他行一致（本行由同表推定）",
          "24 h 是否为该行的真实时间列"
        ],
        "two_variables": true,
        "caveat": {
          "two_variables": true,
          "note": "碱与时间同时变化"
        },
        "verified_by_A": "2026-10-26 开放获取核验记录 §2.3"
      },
      "locator_zh": "表 1 第 1 条及脚注 b；内标依据见同文表 4 脚注"
    },
    {
      "record_id": "P20",
      "source_id": "P20",
      "tier": "C",
      "citation": "Talukder et al., ACS Omega 2020, 5(37), 24018-24032（alpha-二亚胺 Ni/Pd 配合物）",
      "doi": "10.1021/acsomega.0c03415",
      "locator": "Table 1 entry 5 + Table 2 entry 2 + Table 3 entry 1（三表脚注 b 均为 isolated）",
      "outcome": {
        "value": 85.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "Yields (%) 列 = 85；脚注 b: \"Isolated yields from flash column chromatography (n-hexane as the eluent).\"",
        "note": "Table 1 entry 5：DMF/K2CO3 → Pd A 85%（0.1 mol%、60 °C、4 h）。Table 2 确认温度为 60 °C（40 °C 时 53%、80 °C 时 77%）。Table 3 entry 13 在 0.2 mol% / 1.5 eq 碱下可达 96%。"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "Pd A (α-diimine, acenaphthene-based)",
        "catalyst_loading_raw": "0.1 mol%",
        "catalyst_mol_pct": 0.1,
        "ligand": "alpha-diimine",
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 1.0,
        "solvent": "DMF",
        "temp_c": 60,
        "time_h": 4.0,
        "atmosphere": null,
        "scale_mmol": 1.0,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.1
        },
        "concentration_m": null
      },
      "purification": "flash column chromatography (n-hexane)",
      "evidence_grade": "A_confirmed",
      "verification": "verified",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "non_phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "missing_fields": [
          "temp_c",
          "time_h",
          "atmosphere"
        ],
        "needs_A_check": [
          "反应温度与时间（表注未给，读 Experimental）",
          "本记录刻意保留 temp_c=null，用于测『软维度缺失不阻断』"
        ],
        "assay_basis_source": "三表脚注 b 明确为 flash column chromatography 的分离收率",
        "calibration": "calibrated",
        "verified_at": "2026-10-26"
      },
      "locator_zh": "表 1 第 5 条、表 2 第 2 条及表 3 第 1 条（三表脚注 b）"
    },
    {
      "record_id": "P24a",
      "source_id": "P24",
      "tier": "C",
      "citation": "Kaikake, Jou, Shitara & Jin, RSC Adv. 2021, 11(56), 35311-35320（PdBTC 7）",
      "doi": "10.1039/d1ra06177a",
      "locator": "Table 1 entry 1 + 脚注 a/c（PMC9042804 全文已核）",
      "outcome": {
        "value": 98.0,
        "unit": "%",
        "outcome_type": "nmr",
        "label_raw": "Yield (%) entry 1 = 98；脚注 c: Yields were determined by 1H-NMR analysis",
        "note": "与 entry 2（纯水 8%）、entry 6（THF 0%）构成唯一干净的单变量对照：同表同温同时同载量同碱，只换溶剂"
      },
      "assay": {
        "method": "1H NMR",
        "internal_standard": null,
        "quantification_basis": "产物/未反应原料积分比（估算）",
        "assay_basis_source": "脚注 c 只写 1H-NMR analysis；Experimental 无内标 → uncalibrated"
      },
      "conditions": {
        "catalyst": "PdBTC 7（双茶碱微花 Pd(II)）",
        "catalyst_loading_raw": "0.1 mol%",
        "catalyst_mol_pct": 0.1,
        "ligand": "theophylline-derived bis-N-donor",
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2.0,
        "solvent": "H2O-EtOH (1:1) 5.0 mL",
        "temp_c": 20,
        "time_h": 2.0,
        "atmosphere": null,
        "scale_mmol": 0.5,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.2
        },
        "concentration_m": null,
        "time_raw": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified_with_caveat",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "non_phosphine",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "missing_fields": [
          "atmosphere",
          "assay.internal_standard",
          "assay.quantification_basis"
        ],
        "needs_A_check": [
          "NMR 积分基准/内标（缺则维度 4 第三层 UNKNOWN）",
          "表行号"
        ],
        "verified_by_A": "2026-10-26 开放获取核验记录 §2.4"
      },
      "locator_zh": "表 1 第 1 条及脚注 a/c"
    },
    {
      "record_id": "P24b",
      "source_id": "P24",
      "tier": "C",
      "citation": "Kaikake, Jou, Shitara & Jin, RSC Adv. 2021, 11(56), 35311-35320（PdBTC 7）",
      "doi": "10.1039/d1ra06177a",
      "locator": "Table 1 entry 2 + 脚注 a/c（PMC9042804 全文已核）",
      "outcome": {
        "value": 8.0,
        "unit": "%",
        "outcome_type": "nmr",
        "label_raw": "Yield (%) entry 2 = 8；脚注 c: Yields were determined by 1H-NMR analysis",
        "note": "纯水 8% vs 含水醇 98%：同一催化剂，只换溶剂"
      },
      "assay": {
        "method": "1H NMR",
        "internal_standard": null,
        "quantification_basis": "产物/未反应原料积分比（估算）",
        "assay_basis_source": "脚注 c 只写 1H-NMR analysis；Experimental 无内标 → uncalibrated"
      },
      "conditions": {
        "catalyst": "PdBTC 7（双茶碱微花 Pd(II)）",
        "catalyst_loading_raw": "0.1 mol%",
        "catalyst_mol_pct": 0.1,
        "ligand": "theophylline-derived bis-N-donor",
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2.0,
        "solvent": "H2O 5.0 mL",
        "temp_c": 20,
        "time_h": 2.0,
        "time_raw": null,
        "atmosphere": null,
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
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "non_phosphine",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "missing_fields": [
          "atmosphere",
          "assay.internal_standard",
          "assay.quantification_basis"
        ],
        "needs_A_check": [
          "纯水行的口径标记",
          "纯水行的时间（本记录按同表 2 h 推定，属待核验）"
        ],
        "verified_by_A": "2026-10-26 开放获取核验记录 §2.4"
      },
      "locator_zh": "表 1 第 2 条及脚注 a/c"
    },
    {
      "record_id": "P24c",
      "source_id": "P24",
      "tier": "C",
      "citation": "Kaikake, Jou, Shitara & Jin, RSC Adv. 2021, 11(56), 35311-35320（PdBTC 7）",
      "doi": "10.1039/d1ra06177a",
      "locator": "Table 1 entry 6 + 脚注 a/c（PMC9042804 全文已核）",
      "outcome": {
        "value": 0.0,
        "unit": "%",
        "outcome_type": "nmr",
        "label_raw": "Yield (%) entry 6 = 0；脚注 c: Yields were determined by 1H-NMR analysis",
        "note": "THF 中 0%（entry 6），同表 entry 5 异丙醇亦为 0%。0 是测量值，不是缺失值"
      },
      "assay": {
        "method": "1H NMR",
        "internal_standard": null,
        "quantification_basis": "产物/未反应原料积分比（估算）",
        "assay_basis_source": "脚注 c 只写 1H-NMR analysis；Experimental 无内标 → uncalibrated"
      },
      "conditions": {
        "catalyst": "PdBTC 7（双茶碱微花 Pd(II)）",
        "catalyst_loading_raw": "0.1 mol%",
        "catalyst_mol_pct": 0.1,
        "ligand": "theophylline-derived bis-N-donor",
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2.0,
        "solvent": "THF 5.0 mL",
        "temp_c": 20,
        "time_h": 2.0,
        "time_raw": null,
        "atmosphere": null,
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
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "non_phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "missing_fields": [
          "atmosphere",
          "assay.internal_standard"
        ],
        "needs_A_check": [
          "THF 行确实是 0%（不是「未检出」），i-PrOH 同为 0%",
          "时间（按同表 2 h 推定）"
        ],
        "verified_by_A": "2026-10-26 开放获取核验记录 §2.4"
      },
      "locator_zh": "表 1 第 6 条及脚注 a/c"
    },
    {
      "record_id": "P35",
      "source_id": "P35",
      "tier": "B",
      "tier_note": "borderline B/C：无外加配体（B 族特征）但介质为乙二醇（非水相，C 族倾向）→ 双档敏感性分析",
      "citation": "Liu Chun, Han Na, Yuan Hao, He Xiaoyu, Jin Zilin. Chin. J. Catal. 2011, 32(7), 1204-1207（大连理工）",
      "doi": "10.3724/SP.J.1088.2011.10225",
      "locator": "Abstract（原文：\"...the Suzuki reaction between 4-bromoanisole and phenylboronic acid provided a 95% isolated yield in 20 min\"）；正文表格/SI 未核",
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      },
      "outcome": {
        "value": 95.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "Abstract: \"...provided a 95% isolated yield in 20 min\"",
        "note": "优化条件：ArBr 0.5 mmol、ArB(OH)2 0.75 mmol、0.5 mol% Pd(OAc)2、1.0 mmol K3PO4·7H2O、2 mL 乙二醇、室温、20 min。开放获取（cjcatal.com），摘要逐字给出 isolated yield。"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "Pd(OAc)2（无外加配体）",
        "catalyst_loading_raw": "0.5 mol%",
        "catalyst_mol_pct": 0.5,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K3PO4·7H2O",
        "base_eq": 2.0,
        "solvent": "ethylene glycol",
        "temp_c": 25,
        "time_h": 0.33,
        "atmosphere": "air (aerobic)",
        "scale_mmol": 0.5,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.5
        },
        "concentration_m": null
      },
      "purification": "not stated",
      "evidence_grade": "A_confirmed",
      "verification": "verified_abstract_only",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "摘要原句明确写 isolated yield（20 min，95%）",
        "verified_at": "2026-10-26",
        "calibration": "calibrated"
      },
      "locator_zh": "摘要（正文表格及补充资料未核）"
    },
    {
      "record_id": "P16",
      "source_id": "P16",
      "tier": "C",
      "citation": "Saputra, Sato, Kojima, Hara, Ichikuni & Shimazu, ACS Omega 2018, 3, 17528-17531（Pd-STO 钙钛矿）",
      "doi": "10.1021/acsomega.8b02622",
      "locator": "Table 1 entry 2 + 脚注 a; §2.2 定量方法（decaline 内标，GC-14B）（PMC6644052 全文已核）",
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      },
      "outcome": {
        "value": 99.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "Table 1 列名 conv./% = 99、yield/% = 99（entry 2, Pd-STO(12.5)）；§2.2: \"...decaline (internal standard) ... analyzed by gas chromatography (GC Shimadzu GC-14B, InertCap-624 column) ...\"",
        "note": "Table 1 entry 2；同表 entry 5（PA 过量 0.3 mmol）>99/>99。Table 2 才是溴苯数据（>99%），B 原记录把底物误标为 bromobenzene，已纠正为 4-溴苯甲醚。"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "decaline (decahydronaphthalene)",
        "quantification_basis": "product yield"
      },
      "conditions": {
        "catalyst": "Pd-STO(12.5) 钙钛矿（Pd 0.64 wt%，12.5 mg）",
        "catalyst_loading_raw": "mol BA/Pd = 273",
        "catalyst_mol_pct": null,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2.0,
        "solvent": "i-PrOH/H2O = 1:1",
        "temp_c": 80,
        "time_h": 0.5,
        "atmosphere": "air",
        "scale_mmol": 0.2,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.0
        },
        "concentration_m": null
      },
      "purification": "extracted with n-hexane (no chromatography stated)",
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "catalytic_profile": {
        "catalyst_form": "supported",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "§2.2 Catalytic Test 明确 decaline 为内标、GC 定量",
        "verified_at": "2026-10-28",
        "calibration": "calibrated"
      },
      "locator_zh": "表 1 第 2 条及脚注 a；第 2.2 节定量方法",
      "_source_file": "_selftest.json"
    },
    {
      "record_id": "P01",
      "source_id": "P01",
      "tier": "A",
      "citation": "Miyaura, Yanagi & Suzuki, Synth. Commun. 1981, 11(7), 513-519",
      "doi": "10.1080/00397918108063618",
      "locator": "Table I entry 8（X=Br, base 2M-Na2CO3/H2O, 6 h）+ 脚注 a/b（本地 PDF refs/miyaura1981.pdf p.515 已核）",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "outcome": {
        "value": 88.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "Table I 列名 Yield of biphenyl b (%)，entry 8（X=Br, base 2M-Na2CO3/H2O, time 6 h）= 88；脚注 b: \"Based on halobenzenes and determined by GLC.\"（GLC=气相色谱 GC）",
        "note": "核对单原写『Table II 溴苯行』有误——Table II 无溴苯行；88% 实为 Table I entry 8（与 pool30 原 locator 一致）。同一溴苯底物另有对照：NaOEt 3/6/10 h → 39/49/52%、NaOH 76%、NaOAc 11%。"
      },
      "assay": {
        "method": "GLC",
        "internal_standard": null,
        "quantification_basis": "aryl halide"
      },
      "conditions": {
        "catalyst": "Pd(PPh3)4",
        "catalyst_loading_raw": "3 mol%",
        "catalyst_mol_pct": 3.0,
        "ligand": "PPh3 (bound)",
        "ligand_mol_pct": null,
        "base": "Na2CO3 (2M aq.)",
        "base_eq": 2.0,
        "solvent": "benzene (reflux)",
        "temp_c": 80,
        "time_h": 6.0,
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
      "verification": "verified_fulltext",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "脚注 b: \"Based on halobenzenes and determined by GLC.\"（全文 p.513-519 未提及 GLC 内标）",
        "verified_at": "2026-10-28",
        "calibration": "uncalibrated"
      },
      "locator_zh": "表 I 第 8 行（X=Br, 2M-Na2CO3/H2O, 6 h）+ 脚注 a/b",
      "_source_file": "P01.json"
    },
    {
      "record_id": "P03",
      "source_id": "P03",
      "tier": "A",
      "citation": "Miyaura, Yanagi & Suzuki, Synth. Commun. 1981, 11(7), 513-519",
      "doi": "10.1080/00397918108063618",
      "locator": "Table II entry 3（o-MeOPhBr 行）+ 脚注 a/b（本地 PDF refs/miyaura1981.pdf p.517 已核）",
      "substrate": {
        "aryl_halide": "2-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "2-methoxybiphenyl"
      },
      "outcome": {
        "value": 99.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "Table II 列名 Yield of biaryl, (%) b，entry 3（o-MeOPhBr, Benzene, 6 h）= 99；脚注 b: \"Based on the haloarenes employed and determined by GLC. The isolated yields were indicated in parentheses.\"（GLC=气相色谱 GC）",
        "note": "Table II 该行无括号数值（即无分离收率报告）；GC 收率 99%，以卤代芳烃为基准。与 p-MeOPhBr 行 66 (40) 对比，说明甲氧基邻位位阻非低收率原因。"
      },
      "assay": {
        "method": "GLC",
        "internal_standard": null,
        "quantification_basis": "aryl halide"
      },
      "conditions": {
        "catalyst": "Pd(PPh3)4",
        "catalyst_loading_raw": "3 mol%",
        "catalyst_mol_pct": 3.0,
        "ligand": "PPh3 (bound)",
        "ligand_mol_pct": null,
        "base": "Na2CO3",
        "base_eq": 2.0,
        "solvent": "benzene (reflux)",
        "temp_c": 80,
        "time_h": 6.0,
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
      "verification": "verified_fulltext",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "脚注 b: \"Based on the haloarenes employed and determined by GLC.\"（全文未提及 GLC 内标）",
        "verified_at": "2026-10-28",
        "calibration": "uncalibrated"
      },
      "locator_zh": "表 II 第 3 行（o-MeOPhBr, Benzene, 6 h）+ 脚注 a/b",
      "_source_file": "P03.json"
    },
    {
      "record_id": "P04",
      "source_id": "P04",
      "tier": "A",
      "citation": "Miyaura, Yanagi & Suzuki, Synth. Commun. 1981, 11(7), 513-519",
      "doi": "10.1080/00397918108063618",
      "locator": "Table II entry 2（p-MePhBr 行）+ 脚注 a/b（本地 PDF refs/miyaura1981.pdf p.517 已核）",
      "substrate": {
        "aryl_halide": "4-bromotoluene",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methylbiphenyl"
      },
      "outcome": {
        "value": 88.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "Table II 列名 Yield of biaryl, (%) b，entry 2（p-MePhBr, Benzene, 6 h）= 88；脚注 b: \"Based on the haloarenes employed and determined by GLC. The isolated yields were indicated in parentheses.\"（GLC=气相色谱 GC）",
        "note": "Table II 该行无括号数值；GC 收率 88%，以卤代芳烃为基准。弱给电子底物，与 p-MeOPhBr 行 66 (40) 对比支撑取代基电子效应。"
      },
      "assay": {
        "method": "GLC",
        "internal_standard": null,
        "quantification_basis": "aryl halide"
      },
      "conditions": {
        "catalyst": "Pd(PPh3)4",
        "catalyst_loading_raw": "3 mol%",
        "catalyst_mol_pct": 3.0,
        "ligand": "PPh3 (bound)",
        "ligand_mol_pct": null,
        "base": "Na2CO3",
        "base_eq": 2.0,
        "solvent": "benzene (reflux)",
        "temp_c": 80,
        "time_h": 6.0,
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
      "verification": "verified_fulltext",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "脚注 b: \"Based on the haloarenes employed and determined by GLC.\"（全文未提及 GLC 内标）",
        "verified_at": "2026-10-28",
        "calibration": "uncalibrated"
      },
      "locator_zh": "表 II 第 2 行（p-MePhBr, Benzene, 6 h）+ 脚注 a/b",
      "_source_file": "P04.json"
    },
    {
      "record_id": "P17",
      "source_id": "P17",
      "tier": "C",
      "citation": "Liu & Liu, Polymers 2024, 16, 1080（两亲树枝化共聚物包裹 Au/Ag/Pd NPs）",
      "doi": "10.3390/polym16081080",
      "locator": "Table 3 entry 4（500 ppm ADC-PdNPs, K2CO3）+ 表下注（本地 PDF refs/pdf/P17_polymers-16-01080.pdf p.10 已核）",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "outcome": {
        "value": 99.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "Table 3 列名 Isolated Yield (%) entry 4（ADC-PdNPs 500 ppm, K2CO3, 10 h）= 99；表下注: \"Reaction conditions: bromobenzene (1.0 mmol), phenylboronic acid (1.2 mmol), ADC-PdNPs, base (2.0 mmol), EtOH (2.5 mL)/H2O (2.5 mL), 85 °C. * TOF was calculated after 4 h.\"",
        "note": "entry 4-8（500/400/300/200/100 ppm, K2CO3, 10 h）均为 99；pool30 载量 200-500 ppm 的 0.05 mol% 对应 entry 4 的 500 ppm。§2.6 通用操作: 产物经 CH2Cl2 萃取 + 柱层析纯化，1H NMR 表征。"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "ADC-PdNPs（两亲树枝化共聚物包裹 Pd）",
        "catalyst_loading_raw": "500 ppm（0.05 mol%）",
        "catalyst_mol_pct": 0.05,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2.0,
        "solvent": "EtOH/H2O = 2.5/2.5 mL",
        "temp_c": 85,
        "time_h": 10.0,
        "atmosphere": null,
        "scale_mmol": 1.0,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.2
        },
        "concentration_m": null
      },
      "purification": "column chromatography（§2.6）",
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "表列名即 Isolated Yield (%)，表下注给出反应条件",
        "verified_at": "2026-10-28",
        "calibration": "calibrated"
      },
      "locator_zh": "表 3 第 4 行（500 ppm K2CO3）+ 表下注",
      "_source_file": "P17.json"
    },
    {
      "record_id": "P21",
      "source_id": "P21",
      "tier": "C",
      "citation": "Sepehrfar et al., RSC Adv. 2026, 16(45), 58585-58605（噻吩基 Pd(II) 膦叶立德 D2）",
      "doi": "10.1039/d6ra06822g",
      "locator": "Table 5 entry 2（D2, EtOH/K2CO3/0.47 mol% Pd/60 °C 加粗行）+ 脚注 a/b（Europe PMC fullTextXML PMC13622566 全文已核）",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "outcome": {
        "value": 91.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "Table 5 列名 Yield b (%) entry 2（EtOH, K2CO3, D2, 0.47 mol% Pd, 60 °C）= 91；脚注 a: \"Reagents and conditions: 0.5 mmol of bromobenzene, 0.6 mmol of phenylboronic acid, 0.75 mmol of base, solvent (2 mL), catalyst, time 10 min, NC (no conversion).\"；脚注 b: \"Isolated yield.\"",
        "note": "正文称 'yielding 91% ... in 30 minutes'，而表注 a 写 time 10 min；Table 6 entry 10（R=H, X=Br, 10 min）同为 91% → 以表注 10 min 为准，时间差异已入 note。§2.4 通用操作: 60 °C 常压（atmospheric conditions）回流冷凝，n-hexane 萃取得到纯联苯。"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "D2（噻吩基 Pd(II) 膦叶立德）",
        "catalyst_loading_raw": "0.47 mol% Pd",
        "catalyst_mol_pct": 0.47,
        "ligand": "phosphonium ylide（非膦配体，见 note）",
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 1.5,
        "solvent": "EtOH 2 mL",
        "temp_c": 60,
        "time_h": 0.17,
        "time_raw": "10 min",
        "atmosphere": "air (atmospheric conditions)",
        "scale_mmol": 0.5,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.2
        },
        "concentration_m": null
      },
      "purification": "extraction（H2O / n-hexane），无柱层析（§2.4）",
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "non_phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "脚注 b: \"Isolated yield.\"",
        "verified_at": "2026-10-28",
        "calibration": "calibrated"
      },
      "locator_zh": "表 5 第 2 行（D2 加粗行）+ 脚注 a/b",
      "_source_file": "P21.json"
    },
    {
      "record_id": "P22",
      "source_id": "P22",
      "tier": "C",
      "citation": "Abradi et al., Sci. Rep. 2025, 15, 38622（MIL-101(Cr)-PUT-GLUT-Pd）",
      "doi": "10.1038/s41598-025-22375-7",
      "locator": "Table 5 entry 2（Ph-Br 行）+ 表注（本地 PDF refs/pdf/P22_10_1038_s41598-025-22375-7.pdf p.12 已核）",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "outcome": {
        "value": 91.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "Table 5 列名 Time (h): Yield (%) a，entry 2（Ph-Br, 联苯 I）= 1.0 : 91；表注: \"Reaction conditions: MIL-101(Cr)-PUT-GLUT-Pd (20 mg, 0.15 mol% Pd), aryl halide (0.5 mmol), phenylboronic acid (0.75 mmol), K2CO3 (1.0 mmol), EtOH: H2O (1:1) (Solvent, 10 mL), Reflux. aIsolated yields.\"",
        "note": "Table 2 温度优化 row 4（Reflux）同为 91%（同条件），此处锚定 Table 5 entry 2。通用操作: 反应进程 TLC 监测，CH2Cl2 萃取 + MgSO4 干燥，产物 NMR（SI）确认。"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "MIL-101(Cr)-PUT-GLUT-Pd",
        "catalyst_loading_raw": "20 mg（0.15 mol% Pd）",
        "catalyst_mol_pct": 0.15,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2.0,
        "solvent": "EtOH/H2O = 1:1 (10 mL)",
        "temp_c": 80,
        "temp_raw": "回流",
        "time_h": 1.0,
        "atmosphere": null,
        "scale_mmol": 0.5,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.5
        },
        "concentration_m": null
      },
      "purification": "extraction（CH2Cl2）+ MgSO4 干燥",
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "catalytic_profile": {
        "catalyst_form": "supported",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "表注: \"aIsolated yields.\"",
        "verified_at": "2026-10-28",
        "calibration": "calibrated"
      },
      "locator_zh": "表 5 第 2 行（Ph-Br）+ 表注",
      "_source_file": "P22.json"
    },
    {
      "record_id": "P28",
      "source_id": "P28",
      "tier": "A",
      "citation": "Leung et al., ACS Omega 2017, 2(5), 1930-1937（三蝶烯单膦配体，10 ppm Pd）",
      "doi": "10.1021/acsomega.7b00200",
      "locator": "Table 1 entry 2（Pd 10 mol ppm + 配体 1 20 mol ppm, 100 °C, 24 h）+ 脚注 a/b/c（PMC6640964 全文已核）",
      "substrate": {
        "aryl_halide": "4-bromotoluene",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methylbiphenyl"
      },
      "outcome": {
        "value": 99.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "Table 1 列名 yield (%) c entry 2（Pd 10 mol ppm, 100 °C, 24 h）= 99（正文: TON 99 000）；脚注 a（条件）: 4-bromotoluene (1.0 mmol), phenylboronic acid (1.25 mmol), K3PO4 (2.0 mmol), 2-methoxyethanol (6 mL), 100 °C, 24 h；脚注 b: \"1 mol ppm equals 0.0001 mol %\"；脚注 c: \"Isolated yield.\"",
        "note": "entry 1（1.0 mol% Pd, 2 h, 60 °C）亦 99%；entry 2 为超低载量（10 ppm, TON 99 000）。正文称 coupling 在 aerobic conditions 下进行。产物经柱层析分离（正文 General Experimental）。"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "Pd(OAc)2 + triptycene monophosphine (1)",
        "catalyst_loading_raw": "10 mol ppm（0.001 mol%）",
        "catalyst_mol_pct": 0.001,
        "ligand": "triptycene monophosphine",
        "ligand_mol_pct": 0.002,
        "base": "K3PO4",
        "base_eq": 2.0,
        "solvent": "2-methoxyethanol 6 mL",
        "temp_c": 100,
        "time_h": 24.0,
        "atmosphere": "air (aerobic)",
        "scale_mmol": 1.0,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.25
        },
        "concentration_m": null
      },
      "purification": "column chromatography（silica gel）",
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "脚注 c: \"Isolated yield.\"",
        "verified_at": "2026-10-28",
        "calibration": "calibrated"
      },
      "locator_zh": "表 1 第 2 行（Pd 10 mol ppm）+ 脚注 a/b/c",
      "_source_file": "P28.json"
    },
    {
      "record_id": "P29",
      "source_id": "P29",
      "tier": "C",
      "citation": "Hazarika et al., PLoS ONE 2017, 12(9), e0184936（生物合成 Pd NPs）",
      "doi": "10.1371/journal.pone.0184936",
      "locator": "Table 1（Recycling, Cycle 1 行）+ 脚注 a/b（PMC5619764 全文已核）",
      "substrate": {
        "aryl_halide": "4-bromotoluene",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methylbiphenyl"
      },
      "outcome": {
        "value": 95.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "Table 1 列名 Yield (%) Cycle 1（4-bromotoluene + phenylboronic acid 循环实验）= 95；脚注 a: \"Reaction condition: 4-bromotoluene (2.5mmol), phenylboronic acid (3.25 mmol), K2CO3 (7.5 mmol), H2O (20 mL), Pd catalyst (10 mg); 50°C\"；脚注 b: \"Isolated yield.\"",
        "note": "Table 1 为催化剂循环实验（Cycle 1-4: 95/92/91/89），锚定 Cycle 1。Table 1 脚注未列反应时间；正文称同模型体系 50 °C 'within 2h' 近定量（Fig 4 entry 2），未据此填 time_h。"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "生物合成 Pd NPs（淀粉稳定）",
        "catalyst_loading_raw": "10 mg",
        "catalyst_mol_pct": null,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 3.0,
        "solvent": "H2O 20 mL",
        "temp_c": 50,
        "time_h": null,
        "atmosphere": null,
        "scale_mmol": 2.5,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.3
        },
        "concentration_m": null
      },
      "purification": "extraction（ether）",
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "脚注 b: \"Isolated yield.\"",
        "verified_at": "2026-10-28",
        "calibration": "calibrated"
      },
      "locator_zh": "表 1 第 1 循环行 + 脚注 a/b",
      "_source_file": "P29.json"
    },
    {
      "record_id": "P30",
      "source_id": "P30",
      "tier": "A",
      "citation": "Yao, Cao & Zhang, RSC Adv. 2020, 10(70), 42874-42882（光加速能量转移催化 Ir(III)-Pd(II)）",
      "doi": "10.1039/d0ra08547b",
      "locator": "Table 2 entry 4（溶剂 DCE-EtOH 3:1 行）+ 正文 + Table 3 脚注（PMC9058252 全文已核）",
      "substrate": {
        "aryl_halide": "4-bromotoluene",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methylbiphenyl"
      },
      "outcome": {
        "value": 93.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "Table 2 列名 Yield b (%) entry 4（溶剂 DCE-EtOH 3:1, Cs2CO3）= 93；正文: \"...a mixture of DCE–EtOH (3 : 1) is the best one, affording an excellent isolated yield in 93%.\"；Table 3 脚注: \"Conditions: arylbromide (0.15 mmol), arylboronic acid (0.3 mmol), 3 (2.5 mol%), PPh3 (5 mol%), Cs2CO3 (0.3 mmol) in 4 mL of DCE–EtOH (3 : 1) with Ar atmosphere under a 45 W blue LED at room temperature; isolated yield.\"",
        "note": "entry 5（K2CO3）= 65 → 碱为第二变量；需 45 W 蓝光（dark 40%）。反应 5 h（Table 2 entry 1 起跑条件），产物硅胶柱层析（hexane/ethyl acetate 7:1）纯化。activation_mode = photochemical。"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "[Ir(pq)2(bpm)PdCl2](PF6)（3）",
        "catalyst_loading_raw": "2.5 mol%",
        "catalyst_mol_pct": 2.5,
        "ligand": "PPh3",
        "ligand_mol_pct": 5.0,
        "base": "Cs2CO3",
        "base_eq": 2.0,
        "solvent": "DCE-EtOH = 3:1 (4 mL)",
        "temp_c": 25,
        "temp_raw": "室温",
        "time_h": 5.0,
        "atmosphere": "Ar",
        "scale_mmol": 0.15,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 2.0
        },
        "concentration_m": null
      },
      "purification": "silica gel column chromatography（hexane/ethyl acetate 7:1）",
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "photochemical"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "正文明写 'excellent isolated yield in 93%' + Table 3 脚注 'isolated yield'",
        "verified_at": "2026-10-28",
        "calibration": "calibrated"
      },
      "locator_zh": "表 2 第 4 行（DCE-EtOH 3:1）+ 正文/表 3 脚注",
      "_source_file": "P30.json"
    },
    {
      "record_id": "P31",
      "source_id": "P31",
      "tier": "C",
      "citation": "Ren et al., RSC Adv. 2020, 10(39), 23080-23090（GO@Tpy-Pd1/Ni10 纳米片）",
      "doi": "10.1039/d0ra02195d",
      "locator": "Table 2 entry 6（GO@Tpy-Pd1/Ni10 行）+ 脚注 a/b（PMC9054763 全文已核）",
      "substrate": {
        "aryl_halide": "4-bromotoluene",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methylbiphenyl"
      },
      "outcome": {
        "value": 96.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "Table 2 列名 Yield b/% entry 6（GO@Tpy-Pd1/Ni10, Pd 1.82e-5 mol/g）= 96（TON 15824）；脚注 a: \"Reaction conditions: PhB(OH)2 (0.35 mmol), 4-bromotoluene (0.3 mmol), base (0.6 mmol), GO@Tpy-Pd1−x/Nix: 1 mg solvent (5.0 mL) at 35 °C for 8 h.\"；脚注 b: \"Isolated yield.\"",
        "note": "碱种类脚注未点名，正文优化段称 K2CO3 为最优碱 → base=K2CO3（0.6 mmol, 2 eq）。GO@Tpy-Pd1-xNix 为系列名，本行对应 GO@Tpy-Pd1/Ni10（1:10）。溶剂 H2O/EtOH (1:3)。"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "GO@Tpy-Pd1/Ni10 nanosheet",
        "catalyst_loading_raw": "1 mg（Pd 1.82e-5 mol/g）",
        "catalyst_mol_pct": null,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2.0,
        "solvent": "H2O/EtOH = 1:3 (5.0 mL)",
        "temp_c": 35,
        "time_h": 8.0,
        "atmosphere": null,
        "scale_mmol": 0.3,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.17
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "catalytic_profile": {
        "catalyst_form": "supported",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "脚注 b: \"Isolated yield.\"",
        "verified_at": "2026-10-28",
        "calibration": "calibrated"
      },
      "locator_zh": "表 2 第 6 行（GO@Tpy-Pd1/Ni10）+ 脚注 a/b",
      "_source_file": "P31.json"
    },
    {
      "record_id": "P36",
      "source_id": "P36",
      "tier": "A",
      "citation": "Weeden, Huang, Galloway, Gingrich & Frost, Molecules 2011, 16(8), 6215-6231",
      "doi": "10.3390/molecules16086215",
      "locator": "Table 3 entry 4 + 脚注 a/b；§3.2 General Procedure、§3.3 实验段（本地 PDF refs/pdf/molecules-16-06215.pdf p.6223-6226 已核）",
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      },
      "outcome": {
        "value": 50,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "Table 3 列名 Yield (%) a，entry 4（R=H3CO）= 50；脚注 a: \"Isolated yield after purification by column chromatography\"；实验段: \"4-Bromoanisole (125 μL, 1.00 mmol) and phenylboronic acid (183 mg, 1.50 mmol) ... A colorless oil was obtained (92 mg, 50%)\"（分离、称重）",
        "note": "92 mg / 184.23 = 0.499 mmol，与 1.00 mmol 投料自洽（50% 可复核）。同表 entry 1（溴苯）84%、entry 5（4-溴苯腈）90%、entry 6（2-溴苯甲醚）55%、entry 3 <5%——同一张表内底物电子效应跨度极大，不得跨行外推。"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "Pd(OAc)2 + RO-PTA",
        "catalyst_loading_raw": "5 mol%",
        "catalyst_mol_pct": 5,
        "ligand": "RO-PTA（P,N 配体）",
        "ligand_mol_pct": 10,
        "base": "Na2CO3",
        "base_eq": 2,
        "solvent": "H2O:CH3CN = 1:1（5 mL）",
        "temp_c": 80,
        "time_h": 6,
        "atmosphere": "N2（Schlenk/drybox）",
        "scale_mmol": 1,
        "equivalents": {
          "aryl_halide": 1,
          "boronic_acid": 1.5
        },
        "concentration_m": null
      },
      "purification": "column chromatography（silica gel，5% EtOAc/hexanes）",
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "脚注 a: Isolated yield after purification by column chromatography；实验段给出质量与百分数",
        "verified_at": "2026-10-28",
        "calibration": "calibrated",
        "note": "这是与演示案例完全同底物、且碱为 Na2CO3、溶剂为含水混合体系的独立报告，值仅 50%。"
      },
      "locator_zh": "表 3 第 4 行（4-溴苯甲醚）+ 脚注 a/b；实验部分 3.2/3.3",
      "_source_file": "P36.json"
    },
    {
      "record_id": "P37",
      "source_id": "P37",
      "tier": "C",
      "citation": "Panda, Patra, Acharya, Phaomei & Parida, RSC Adv. 2024, 14(30), 21269-21276",
      "doi": "10.1039/d4ra00686k",
      "locator": "Table 3 entry 3（1a + 2a, H2O, K2CO3, 70 °C, 4 h）+ 脚注 a/b；正文 p.21272（本地 PDF refs/pdf/d4ra00686k.pdf 已核）",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "outcome": {
        "value": 85,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "Table 3 列名 Yield a,b (%)，entry 3 = 85；脚注 b: \"Isolated yield\"；正文: \"At 70 °C 3a was isolated in 85% yield (entry 3, Table 3)\"",
        "note": "正文另明确 50 °C 为 62%；更低温（RT）与 80/100 °C 两行因 PDF 表格 OCR 串行，未采信具体数字，仅记 70 °C/4 h/85%。"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "LaF3·Pd 纳米催化剂",
        "catalyst_loading_raw": "15 mg（mol% 原文未给）",
        "catalyst_mol_pct": null,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2,
        "solvent": "H2O（3 mL）",
        "temp_c": 70,
        "time_h": 4,
        "atmosphere": null,
        "scale_mmol": 1,
        "equivalents": {
          "aryl_halide": 1,
          "boronic_acid": 1.5
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
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "脚注 b: Isolated yield；正文重复同一数值",
        "verified_at": "2026-10-28",
        "calibration": "calibrated",
        "missing_fields": [
          "catalyst_mol_pct",
          "atmosphere"
        ],
        "needs_A_check": [
          "15 mg 对应 mol%（缺则维度 6 只能记 L2）",
          "80/100 °C 两行的真实数值（表格 OCR 串行）"
        ],
        "note": "tier 由 engine/tier.py 从 catalytic_profile 计算（计算值 C），此字段不是证据等级；证据等级见 evidence_grade=A_confirmed。"
      },
      "locator_zh": "表 3 第 3 行（1a + 2a）+ 脚注 a/b；正文温度段",
      "_source_file": "P37.json"
    },
    {
      "record_id": "P38",
      "source_id": "P38",
      "tier": "C",
      "citation": "Lo, Yang, Wu & Chen, Molecules 2021, 26(15), 4426",
      "doi": "10.3390/molecules26154426",
      "locator": "Table 2 entry 5（催化剂 3, K2CO3/toluene, 70 °C, 1 h）+ 表注；§3 实验（本地 PDF refs/pdf/molecules-26-04426.pdf 与 JATS XML PMC8347745 双证）",
      "substrate": {
        "aryl_halide": "4-bromoacetophenone",
        "boronic_acid": "phenylboronic acid",
        "product": "4-acetylbiphenyl"
      },
      "outcome": {
        "value": 95,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "Table 2 entry 5 记「98(95)」：98 = ¹H NMR 转化率，括号内 95 = isolated yield；表注: 1.0 mmol aryl halide, 1.5 mmol phenylboronic acid, 2.0 mmol base, 2 mL solvent；括号内为分离收率",
        "note": "同一张表内 catalyst 1/2/4/5 与 Cs2CO3/DMSO、DMA、toluene 共 7 个条件并列；仅 catalyst 3 + K2CO3/toluene 达 98(95)。"
      },
      "assay": {
        "method": "1H NMR（转化率）+ isolated（称重）",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "indolyl-NNN Pd 配合物 3",
        "catalyst_loading_raw": "1 mol%",
        "catalyst_mol_pct": 1,
        "ligand": "indolyl-NNN（钳形，非膦）",
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2,
        "solvent": "toluene（2 mL）",
        "temp_c": 70,
        "time_h": 1,
        "atmosphere": null,
        "scale_mmol": 1,
        "equivalents": {
          "aryl_halide": 1,
          "boronic_acid": 1.5
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "non_phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "表注说明括号内为分离收率；PDF 正文确认 K2CO3/toluene 为最优组合、conversion up to 98%",
        "verified_at": "2026-10-28",
        "calibration": "calibrated",
        "note": "tier 由 engine/tier.py 从 catalytic_profile 计算（计算值 C），此字段不是证据等级；证据等级见 evidence_grade=A_confirmed。"
      },
      "locator_zh": "表 2 第 5 行（催化剂 3, K2CO3/toluene, 70 °C, 1 h）",
      "_source_file": "P38.json"
    },
    {
      "record_id": "P06",
      "source_id": "P06",
      "tier": "A",
      "citation": "Huff, Koenig, Mitchell & Staszak, Org. Synth. 1998, 75, 53 (Coll. Vol. 10, 102)",
      "doi": "10.15227/orgsyn.075.0053",
      "locator": "Procedure 1（Vol.75 p.53）：4-bromobenzaldehyde 50.0 g (0.270 mol)、PhB(OH)2 34.6 g (0.284 mol, 1.05 eq)、Pd(OAc)2 0.182 g (0.811 mmol, 0.3 mol%)、PPh3 0.638 g (0.9 mol%)、Na2CO3 0.324 mol (1.20 eq)、1-propanol 485 mL + H2O 95 mL、reflux 45 min；粗品 47.3 g (96.2%)、重结晶 42.5 g (86.3%)（本地 PDF refs/orgsyn_v75p0053.pdf 已核）",
      "outcome": {
        "value": 86.3,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "96.2% (crude) / 86.3% (after recrystallisation)；重结晶后 42.5 g 为分离、称重值（isolated, weighed）",
        "alternate_values": [
          {
            "value": 96.2,
            "label": "crude",
            "outcome_type": "isolated"
          }
        ],
        "value_selection_rule": "取纯化后值 86.3%；粗品值另存，不得与纯化值混用",
        "note": "record 取重结晶后的 86.3% 为 isolated 值；粗品 96.2% 为同一操作的另一口径，不得与 86.3% 混用。"
      },
      "assay": {
        "method": "isolated",
        "internal_standard": null,
        "quantification_basis": "isolated product mass"
      },
      "conditions": {
        "catalyst": "Pd(OAc)2",
        "catalyst_loading_raw": "0.3 mol%",
        "catalyst_mol_pct": 0.3,
        "ligand": "PPh3",
        "ligand_mol_pct": 0.9,
        "base": "Na2CO3",
        "base_eq": 1.2,
        "solvent": "1-propanol/H2O = 485 mL/95 mL",
        "temp_c": null,
        "temp_raw": "回流",
        "time_h": 0.75,
        "time_raw": "45 min",
        "atmosphere": "N2",
        "scale_mmol": 270.0,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.05
        },
        "concentration_m": null
      },
      "purification": "重结晶",
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "substrate": {
        "aryl_halide": "4-bromobenzaldehyde",
        "boronic_acid": "phenylboronic acid",
        "product": "4-biphenylcarboxaldehyde"
      },
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "transcribed",
        "missing_fields": [
          "temp_c"
        ],
        "needs_A_check": [
          "回流温度（1-ProOH/H2O 体系，原文是否给具体温度）",
          "两个收率哪个用于对外表述 —— 建议统一用 86.3% 并注明粗品 96.2%",
          "版权：Org Synth 禁止再分发 PDF，引用只走 DOI"
        ],
        "verified_at": "2026-10-28",
        "verified_by_A": "2026-10-28 逐表逐脚注复核（本地 PDF / JATS 全文）"
      },
      "locator_zh": "操作段 1（Vol.75 第 53 页）；粗品 96.2% / 重结晶 86.3%",
      "_source_file": "P06.json"
    },
    {
      "record_id": "P07",
      "source_id": "P07",
      "tier": "B",
      "citation": "Goodson, Wallow & Novak, Org. Synth. 1998, 75, 61 (Coll. Vol. 10, 501), ligandless Pd",
      "doi": "10.15227/orgsyn.075.0061",
      "locator": "Procedure 1（Vol.75 p.61）：4-iodoanisole 16.8 g (71.8 mmol)、o-tolylboronic acid 10.0 g (73.6 mmol, 1.03 eq)、Pd(OAc)2 3.30 mg (0.02 mmol, 0.2 mol%)、K2CO3 25.0 g (0.180 mol, 2.5 eq)、acetone 200 mL + H2O 200 mL、reflux 2 h；12.8 g (90.3%) 蒸馏（refs/orgsyn_v75p0061.pdf 已核）",
      "outcome": {
        "value": 90.3,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "90.3% (distillation)；蒸馏后 12.8 g 为分离、称重值（isolated, weighed）",
        "note": "离去基为碘（非溴），按闸门维度 1 不得与 4-溴苯甲醚记录同块比较。"
      },
      "assay": {
        "method": "isolated",
        "internal_standard": null,
        "quantification_basis": "isolated product mass"
      },
      "conditions": {
        "catalyst": "Pd(OAc)2",
        "catalyst_loading_raw": "0.2 mol%",
        "catalyst_mol_pct": 0.2,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2.5,
        "solvent": "acetone/H2O = 1:1",
        "temp_c": null,
        "temp_raw": "回流",
        "time_h": 2.0,
        "atmosphere": "Ar",
        "scale_mmol": 71.8,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.03
        },
        "concentration_m": null
      },
      "purification": "蒸馏",
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "boronic_acid": "2-methylphenylboronic acid",
        "product": "4-methoxy-2'-methylbiphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "transcribed",
        "missing_fields": [
          "temp_c"
        ],
        "needs_A_check": [
          "回流温度",
          "作者行 OCR 残缺（Goodson/Wallow/Novak 由页首残字合并），需以期刊页为准",
          "维度 1 必须判 FAIL：离去基为碘（D1 已定）"
        ],
        "verified_at": "2026-10-28",
        "verified_by_A": "2026-10-28 逐表逐脚注复核（本地 PDF / JATS 全文）"
      },
      "locator_zh": "操作段 1（Vol.75 第 61 页）；蒸馏后 90.3%",
      "_source_file": "P07.json"
    },
    {
      "record_id": "P18",
      "source_id": "P18",
      "tier": "C",
      "citation": "Molecules 2020, 25, 2951（机械化学 Pd(II)/碳复合材料）",
      "doi": "10.3390/molecules25122951",
      "locator": "Table 4 entry 7（AC-Pd2, 180 min 行）+ 脚注 a/b（PMC7355770 全文已核；本地 PDF refs/pdf/P18_molecules-25-02951.pdf p.12）",
      "outcome": {
        "value": 69.0,
        "unit": "%",
        "outcome_type": "nmr",
        "label_raw": "69% (1H NMR, based on PhBr)",
        "note": "脚注 b：NMR yield 以限量试剂 PhBr 与产物联苯的相对积分计，无内标 → uncalibrated；30 min 时 49%。"
      },
      "assay": {
        "method": "1H NMR",
        "internal_standard": null,
        "quantification_basis": "aryl halide"
      },
      "conditions": {
        "catalyst": "Pd(II)/carbon composite",
        "catalyst_loading_raw": "2.5 mg",
        "catalyst_mol_pct": null,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2.0,
        "solvent": "EtOH 2 mL（球磨）",
        "temp_c": null,
        "temp_raw": "球磨，未加热",
        "time_h": 3.0,
        "time_raw": "180 min",
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
      "verification": "verified_fulltext",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "supported",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "solventless",
        "activation_mode": "mechanochemical"
      },
      "_extraction": {
        "label_raw_provenance": "transcribed",
        "missing_fields": [
          "catalyst_mol_pct",
          "temp_c",
          "assay.internal_standard"
        ],
        "needs_A_check": [
          "NMR 积分基准（以 PhBr 为基准已在报告里写明，但内标是什么？）",
          "球磨是否算 medium=solventless（有 2 mL EtOH）—— 本记录按溶剂less 填，请 A 确认，这一格决定档归属"
        ],
        "verified_at": "2026-10-28",
        "verified_by_A": "2026-10-28 逐表逐脚注复核（本地 PDF / JATS 全文）"
      },
      "locator_zh": "表 4 第 7 行（AC-Pd2，180 min）+ 脚注 a/b",
      "_source_file": "P18.json"
    },
    {
      "record_id": "P23",
      "source_id": "P23",
      "tier": "C",
      "citation": "Kaikake, Takada, Soma & Jin, RSC Adv. 2018, 8(60), 34505-34513（茶碱微球负载 Pd, u-T2/Pd）",
      "doi": "10.1039/c8ra06476h",
      "locator": "Table 1（H2O, 20 °C 行）+ 脚注 a/b（PMC9087375 全文已核）",
      "outcome": {
        "value": 100.0,
        "unit": "%",
        "outcome_type": "nmr",
        "label_raw": "100% (1H NMR)",
        "note": "脚注 b 仅写 1H-NMR analysis，无内标 → uncalibrated；同表温度梯度 20→100 与催化剂载量 0.02–2 mol% 均已扫。"
      },
      "assay": {
        "method": "1H NMR",
        "internal_standard": null,
        "quantification_basis": null
      },
      "conditions": {
        "catalyst": "u-T2/Pd（茶碱微球负载 Pd）",
        "catalyst_loading_raw": "2.0 mol%",
        "catalyst_mol_pct": 2.0,
        "ligand": "theophylline-derived",
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2.5,
        "solvent": "H2O",
        "temp_c": 20,
        "time_h": 1.0,
        "atmosphere": null,
        "scale_mmol": 1.0,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.5
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "supported",
        "ligand_family": "non_phosphine",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "transcribed",
        "missing_fields": [
          "atmosphere",
          "assay.internal_standard",
          "assay.quantification_basis"
        ],
        "needs_A_check": [
          "与 P24b 同为『纯水 20 °C』却 100% vs 8% —— 请确认两条的催化剂载量与时间确实不同（这是 gold set 里最容易被质疑的一对）"
        ],
        "verified_at": "2026-10-28",
        "verified_by_A": "2026-10-28 逐表逐脚注复核（本地 PDF / JATS 全文）"
      },
      "locator_zh": "表 1（H2O、20 ℃ 行）+ 脚注 a/b",
      "_source_file": "P23.json"
    },
    {
      "record_id": "P25",
      "source_id": "P25",
      "tier": "C",
      "citation": "Cui et al., Nanomaterials 2024, 14(21), 1690（吡啶 N 富碳纳米片负载 Pd）",
      "doi": "10.3390/nano14211690",
      "locator": "Table 1 entry 5（Pd/N-CNS800, 50 min 行）+ 脚注 a（PMC11548024 全文已核）",
      "outcome": {
        "value": 99.6,
        "unit": "%",
        "outcome_type": "unknown",
        "label_raw": "99.6%",
        "note": "表头仅 Yield (%)，脚注未声明分离/GC/NMR → outcome_type=unknown（维持）。"
      },
      "assay": {
        "method": "未注明",
        "internal_standard": null,
        "quantification_basis": null
      },
      "conditions": {
        "catalyst": "吡啶 N 富碳纳米片负载 Pd",
        "catalyst_loading_raw": "0.05 mol%",
        "catalyst_mol_pct": 0.05,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2.0,
        "solvent": "H2O/EtOH = 1:3, 10 mL",
        "temp_c": 100,
        "time_h": 0.83,
        "time_raw": "50 min",
        "atmosphere": "Ar",
        "scale_mmol": 1.0,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.5
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "supported",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "transcribed",
        "missing_fields": [
          "outcome.outcome_type 已定 unknown"
        ],
        "needs_A_check": [
          "99.6% 的测定方式（原文表注）—— 一句话就能把这条从 UNKNOWN 变成可用"
        ],
        "verified_at": "2026-10-28",
        "verified_by_A": "2026-10-28 逐表逐脚注复核（本地 PDF / JATS 全文）"
      },
      "locator_zh": "表 1 第 5 行（Pd/N-CNS800，50 min）+ 脚注 a",
      "_source_file": "P25.json"
    },
    {
      "record_id": "P26",
      "source_id": "P26",
      "tier": "C",
      "citation": "Liu, Sun, Fan & Xu, Front. Chem. 2022, 10, 845274（Schiff 碱-咪唑功能化 MOF 载 Pd 簇）",
      "doi": "10.3389/fchem.2022.845274",
      "locator": "Table 2 entry 4（Pd0@UIO-66-SB-Im 行）+ 表注 a（PMC8921604 全文已核）",
      "outcome": {
        "value": 95.3,
        "unit": "%",
        "outcome_type": "unknown",
        "label_raw": "95.3%",
        "note": "表头仅 Yield (%)，无口径说明 → unknown（维持）。"
      },
      "assay": {
        "method": "未注明",
        "internal_standard": null,
        "quantification_basis": null
      },
      "conditions": {
        "catalyst": "Schiff 碱-咪唑功能化 MOF 载 Pd 簇",
        "catalyst_loading_raw": "0.103 mol%（基于溴苯）",
        "catalyst_mol_pct": 0.103,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "NaHCO3",
        "base_eq": 2.0,
        "solvent": "DMA 5 mL",
        "temp_c": 90,
        "time_h": 7.0,
        "atmosphere": null,
        "scale_mmol": 2.0,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.5
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "supported",
        "ligand_family": "non_phosphine",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "transcribed",
        "missing_fields": [
          "outcome.outcome_type 已定 unknown",
          "atmosphere"
        ],
        "needs_A_check": [
          "测定方式",
          "NaHCO3 与碳酸盐是否算「同族碱」（维度 7 判 L1 还是 L2）"
        ],
        "verified_at": "2026-10-28",
        "verified_by_A": "2026-10-28 逐表逐脚注复核（本地 PDF / JATS 全文）"
      },
      "locator_zh": "表 2 第 4 行 + 表注 a",
      "_source_file": "P26.json"
    },
    {
      "record_id": "P27",
      "source_id": "P27",
      "tier": "C",
      "citation": "Feketshane, Vatsha & Bingwa, RSC Adv. 2026, 16(11), 9692-9708（机械化学 Pd 尺寸效应）",
      "doi": "10.1039/d6ra00004e",
      "locator": "Table 3 entry 6（H2O:MeOH 1:1 行）+ 脚注 a（PMC12915526 全文已核）",
      "outcome": {
        "value": 77.0,
        "unit": "%",
        "outcome_type": "conversion",
        "label_raw": "77% conversion (GC, internal standard)",
        "note": "脚注 a 明写 Yield determined by GC analysis using an internal standard（未点名内标）；表头为 Conversion%，故按 conversion 记。"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "未注明种类",
        "quantification_basis": "conversion of aryl halide"
      },
      "conditions": {
        "catalyst": "Pd-based catalyst size series",
        "catalyst_loading_raw": "5 mol%",
        "catalyst_mol_pct": 5.0,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "Na2CO3",
        "base_eq": 3.0,
        "solvent": "无常规溶剂；液体添加剂 H2O:MeOH = 1:1, 0.1 mL",
        "temp_c": 25,
        "time_h": 1.0,
        "atmosphere": "air",
        "scale_mmol": 1.0,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 3.0
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "none",
        "medium": "solventless",
        "activation_mode": "mechanochemical"
      },
      "_extraction": {
        "label_raw_provenance": "transcribed",
        "missing_fields": [
          "purification"
        ],
        "needs_A_check": [
          "催化剂形态（尺寸系列 → 是否 nanoparticle 统一？）",
          "室温 25 °C 是否原文写 RT",
          "GC 内标种类"
        ],
        "verified_at": "2026-10-28",
        "verified_by_A": "2026-10-28 逐表逐脚注复核（本地 PDF / JATS 全文）"
      },
      "locator_zh": "表 3 第 6 行（H2O:MeOH 1:1）+ 脚注 a",
      "_source_file": "P27.json"
    },
    {
      "record_id": "P32",
      "source_id": "P32",
      "tier": "C",
      "citation": "Oswal et al., RSC Adv. 2019, 9(39), 22313-22319（超小 Pd NPs，S/Se/N 配体）",
      "doi": "10.1039/c9ra03498f",
      "locator": "优化表 S. no. 4（DMF:water 3:2 + K2CO3 行）+ 脚注 a（PMC9066620 全文已核）",
      "outcome": {
        "value": 95.0,
        "unit": "%",
        "outcome_type": "unknown",
        "label_raw": "95%",
        "note": "脚注未声明口径 → unknown（维持）；同表溶剂对照 THF 47%、甲苯 38%。"
      },
      "assay": {
        "method": "未注明",
        "internal_standard": null,
        "quantification_basis": null
      },
      "conditions": {
        "catalyst": "超小 Pd NPs（S/Se/N 配体）",
        "catalyst_loading_raw": "载量待核验",
        "catalyst_mol_pct": null,
        "ligand": "S/Se/N ligand",
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2.0,
        "solvent": "DMF:H2O = 3:2, 5 mL",
        "temp_c": 100,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": 1.0,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.1
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "substrate": {
        "aryl_halide": "4-bromobenzaldehyde",
        "boronic_acid": "phenylboronic acid",
        "product": "4-biphenylcarboxaldehyde"
      },
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "non_phosphine",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "transcribed",
        "missing_fields": [
          "outcome.outcome_type 已定 unknown",
          "catalyst_mol_pct",
          "atmosphere"
        ],
        "needs_A_check": [
          "Pd 载量（表注未给）",
          "测定方式",
          "溶剂对照四行能否逐行绑定（可扩篇内扫描对）"
        ],
        "verified_at": "2026-10-28",
        "verified_by_A": "2026-10-28 逐表逐脚注复核（本地 PDF / JATS 全文）"
      },
      "locator_zh": "优化表第 4 行（DMF:water 3:2 + K2CO3）+ 脚注 a",
      "_source_file": "P32.json"
    },
    {
      "record_id": "P33",
      "source_id": "P33",
      "tier": "C",
      "citation": "ACS Omega 2024, 9, 17817（磁载双配体 Pd）",
      "doi": "10.1021/acsomega.3c07133",
      "locator": "Table 1 entry 3（60 min 行）+ 脚注 a（PMC11044249 全文已核）",
      "outcome": {
        "value": 99.0,
        "unit": "%",
        "outcome_type": "unknown",
        "label_raw": "99% (isolated)",
        "note": "时间扫描 15 min 79% → 60 min 99% → 之后回落 93/84/82/79：产物降解的罕见直接证据 **核验纠错**：原记录按 isolated 入库，但全文检索无 isolated/GC/NMR 任何口径说明，且无 General Procedure → outcome_type 改为 unknown。"
      },
      "assay": {
        "method": "isolated",
        "internal_standard": null,
        "quantification_basis": "isolated product mass"
      },
      "conditions": {
        "catalyst": "磁载双配体 Pd（magnetic dual-ligand Pd）",
        "catalyst_loading_raw": "2.0 mg",
        "catalyst_mol_pct": null,
        "ligand": "dual ligand",
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 2.0,
        "solvent": "EtOH/H2O = 2:1",
        "temp_c": 60,
        "time_h": 1.0,
        "atmosphere": null,
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
      "substrate": {
        "aryl_halide": "4-bromoacetophenone",
        "boronic_acid": "phenylboronic acid",
        "product": "4-acetylbiphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "supported",
        "ligand_family": "non_phosphine",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "transcribed",
        "missing_fields": [
          "catalyst_mol_pct",
          "atmosphere",
          "purification"
        ],
        "needs_A_check": [
          "2.0 mg 对应 mol%",
          "时间扫描的完整数值（79/99/93/84/82/79）是否原文逐字，若是，可作为『过程非单调』的唯一证据"
        ],
        "verified_at": "2026-10-28",
        "verified_by_A": "2026-10-28 逐表逐脚注复核（本地 PDF / JATS 全文）"
      },
      "locator_zh": "表 1 第 3 行（60 min）+ 脚注 a",
      "_source_file": "P33.json"
    },
    {
      "record_id": "P34",
      "source_id": "P34",
      "tier": "C",
      "citation": "Yuan & Huynh, Molecules 2012, 17(3), 2491-2517（S 功能化 NHC-Pd(II)，0.001 mol%）",
      "doi": "10.3390/molecules17032491",
      "locator": "Table 1 entry 3（催化剂 14, 60 °C, 24 h 行）+ 脚注 a/b（PMC6268575 全文已核）",
      "outcome": {
        "value": 99.0,
        "unit": "%",
        "outcome_type": "nmr",
        "label_raw": ">99% (1H NMR)",
        "value_raw": ">99",
        "value_is_lower_bound": true,
        "note": "脚注 b 仅写 1H-NMR analysis，无内标 → uncalibrated；0.001 mol% 载量对应 TON≈10^5。"
      },
      "assay": {
        "method": "1H NMR",
        "internal_standard": null,
        "quantification_basis": null
      },
      "conditions": {
        "catalyst": "S 功能化 NHC-Pd(II)（催化剂 14）",
        "catalyst_loading_raw": "0.001 mol%",
        "catalyst_mol_pct": 0.001,
        "ligand": "S-functionalised NHC",
        "ligand_mol_pct": null,
        "base": "K2CO3",
        "base_eq": 1.4,
        "solvent": "H2O 1 mL",
        "temp_c": 60,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": 1.0,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.4
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "A_confirmed",
      "verification": "verified_fulltext",
      "substrate": {
        "aryl_halide": "4-bromobenzophenone",
        "boronic_acid": "phenylboronic acid",
        "product": "4-benzoylbiphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "non_phosphine",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "transcribed",
        "missing_fields": [
          "atmosphere",
          "assay.internal_standard"
        ],
        "needs_A_check": [
          "NMR 积分基准",
          "0.001 mol% 与 24 h 的原文确认（此条是全池载量最低值）"
        ],
        "verified_at": "2026-10-28",
        "verified_by_A": "2026-10-28 逐表逐脚注复核（本地 PDF / JATS 全文）"
      },
      "locator_zh": "表 1 第 3 行（催化剂 14，60 ℃，24 h）+ 脚注 a/b",
      "_source_file": "P34.json"
    },
    {
      "record_id": "P39",
      "source_id": "P39",
      "tier": "C",
      "citation": "Materials 2021, 14(4), 800（Pd 负载 AM4-HDA 多孔聚合物，sc-CO2）",
      "doi": "10.3390/ma14040800",
      "locator": "§3.4 Coupling Reaction（原文：\"Suzuki-Miyaura coupling reactions of 4-bromoanisole and phenylboronic acid, Scheme 3, were conducted using Pd-containing AM4-HDA porous polymer, catalyzed in sc-CO2 for 2 h ... The reaction conversion of 4-methoxybiphenyl in crude reaction mixtures, determined by 1H-NMR spectroscopy, was 82%, and the isolated yield was 40%.\"）（本地 PDF refs/pdf/NEW-materials-14-00800.pdf 已核）",
      "substrate": {
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl"
      },
      "outcome": {
        "value": 40.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "\"the isolated yield was 40%\"（同一句另报 NMR 转化率 82%）",
        "note": "同句给出两个数：1H-NMR 转化率 82%（crude mixture）、分离收率 40% → 两者相差 42 个百分点，是「转化率高≠拿到产物」的现成例子。"
      },
      "assay": {
        "method": "isolated (weighed)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "Pd/AM4-HDA 多孔聚合物",
        "catalyst_loading_raw": "原文未给 mol%",
        "catalyst_mol_pct": null,
        "ligand": null,
        "ligand_mol_pct": null,
        "base": "原文未给",
        "base_eq": null,
        "solvent": "sc-CO2（超临界 CO2）",
        "temp_c": null,
        "time_h": 2.0,
        "atmosphere": "sc-CO2",
        "scale_mmol": null,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": null
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
      "_extraction": {
        "label_raw_provenance": "verbatim",
        "assay_basis_source": "同句英文原文 \"isolated yield was 40%\"",
        "verified_at": "2026-10-28",
        "calibration": "calibrated",
        "missing_fields": [
          "catalyst_mol_pct",
          "base",
          "temp_c",
          "scale_mmol"
        ],
        "needs_A_check": [
          "催化剂 mol% / 碱 / 温度（正文该段未给，需读 Scheme 3 与实验部分）"
        ],
        "vocab_note": "受控词表 medium 仅 aqueous/organic/solventless，无 supercritical；sc-CO2 按非水介质映射为 organic。activation_mode 取 thermal（sc-CO2 加热）。建议后续把 supercritical 加入词表。"
      },
      "locator_zh": "第 3.4 节 偶联反应段（sc-CO2，2 h）",
      "_source_file": "P39.json"
    },
    {
      "record_id": "PMC-9043785-3c-01",
      "source_id": "PMC-9043785-3c",
      "tier": "C",
      "citation": "Çakır, Kavukcu, Karabıyık, Rethinam, Türkmen, RSC Adv. 2021, 11, 37684（腈基功能化 NHC-Pd）",
      "doi": "10.1039/d1ra07231e",
      "pmcid": "PMC9043785",
      "locator": "Table 3c, entry 1 (Cat. 2a / KOH / H2O-2-propanol row)",
      "outcome": {
        "value": 80.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "表头逐字: \"Entry | Cat. | Base | Solvent | Yield (%)\"；行逐字: \"1 | 2a | KOH | H2O/2-propanol | 80\"",
        "note": "表3c脚注未写口径。签核依据：同文正文 gram-scale 语句 \"biphenyl was obtained in an 86% isolated yield\"，且同文表3d（同类反应 scope 表）脚注 a 明写 \"Isolated yields\"。据此推定 isolated，evidence_grade 停在 candidate。"
      },
      "assay": {
        "method": "isolated (推定，依据见 outcome.note)",
        "internal_standard": null,
        "quantification_basis": "isolated product"
      },
      "conditions": {
        "catalyst": "NHC-Pd 2a（腈基功能化 NHC 钯配合物）",
        "catalyst_loading_raw": "0.2 mol%",
        "catalyst_mol_pct": 0.2,
        "ligand": "nitrile-functionalized NHC",
        "ligand_mol_pct": null,
        "base": "KOH",
        "base_eq": 0.5,
        "solvent": "H2O/2-propanol",
        "temp_c": 82,
        "time_h": 0.5,
        "atmosphere": "air",
        "scale_mmol": 1.0,
        "equivalents": {
          "aryl_halide": 1.0,
          "boronic_acid": 1.0
        },
        "concentration_m": null
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "candidate（表行+脚注逐字核对；口径为有原文依据的推定，待 A 级复核表3c原刊确认）",
      "substrate": {
        "aryl_halide": "bromobenzene",
        "boronic_acid": "phenylboronic acid",
        "product": "biphenyl"
      },
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "non_phosphine",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "_extraction": {
        "label_raw_provenance": "verbatim (PMC publisher XML → typeset replica PDF → pdfplumber)",
        "pipeline": "refs/pmc9043785_table3c.pdf → extract.py → 人工签核",
        "missing_fields": [
          "purification"
        ],
        "assay_basis_source": "正文 isolated yield 语句 + 表3d脚注",
        "calibration": "calibrated",
        "verified_at": "2026-10-30"
      },
      "locator_zh": "表 3c 第 1 条（Cat. 2a / KOH / H2O-2-propanol 行）"
    },
    {
      "record_id": "CORP-PMC9301730-T1-01",
      "source_id": "PMC9301730-T1",
      "citation": "Phenylboronic Ester-Activated Aryl Iodide-Selective Buchwald–Hartwig-Type Amination toward Bioactivity Assay",
      "doi": "10.1021/acsomega.2c01092",
      "pmcid": "PMC9301730",
      "locator": "T1, entry 1",
      "locator_zh": "表 1，entry 1（ACS Omega，2022）",
      "outcome": {
        "value": 94.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": ". The yield was determined by GC analysis using mesitylene as an extern | 表头/题注原文：Screening of Nickel and Palladium Catalysts for the Activator-Promoted Buchwald–Hartwig-Type Aminationa",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "external standard",
        "quantification_basis": "product",
        "calibration_note": "原文报告：external standard"
      },
      "conditions": {
        "catalyst": "Ni(acac)2",
        "base": "K3PO4",
        "solvent": null,
        "temp_c": null,
        "time_h": null,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9301730，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "1-chloro-4-iodobenzene",
        "amine": "toluidine",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9301730",
        "unit_id": "PMC9301730-T1",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9301730-T1-02",
      "source_id": "PMC9301730-T1",
      "citation": "Phenylboronic Ester-Activated Aryl Iodide-Selective Buchwald–Hartwig-Type Amination toward Bioactivity Assay",
      "doi": "10.1021/acsomega.2c01092",
      "pmcid": "PMC9301730",
      "locator": "T1, entry 2b",
      "locator_zh": "表 1，entry 2b（ACS Omega，2022）",
      "outcome": {
        "value": 0.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": ". The yield was determined by GC analysis using mesitylene as an extern | 表头/题注原文：Screening of Nickel and Palladium Catalysts for the Activator-Promoted Buchwald–Hartwig-Type Aminationa",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "external standard",
        "quantification_basis": "product",
        "calibration_note": "原文报告：external standard"
      },
      "conditions": {
        "catalyst": "Ni(acac)2",
        "base": "K3PO4",
        "solvent": null,
        "temp_c": null,
        "time_h": null,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9301730，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "1-chloro-4-iodobenzene",
        "amine": "toluidine",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9301730",
        "unit_id": "PMC9301730-T1",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9301730-T1-03",
      "source_id": "PMC9301730-T1",
      "citation": "Phenylboronic Ester-Activated Aryl Iodide-Selective Buchwald–Hartwig-Type Amination toward Bioactivity Assay",
      "doi": "10.1021/acsomega.2c01092",
      "pmcid": "PMC9301730",
      "locator": "T1, entry 3",
      "locator_zh": "表 1，entry 3（ACS Omega，2022）",
      "outcome": {
        "value": 21.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": ". The yield was determined by GC analysis using mesitylene as an extern | 表头/题注原文：Screening of Nickel and Palladium Catalysts for the Activator-Promoted Buchwald–Hartwig-Type Aminationa",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "external standard",
        "quantification_basis": "product",
        "calibration_note": "原文报告：external standard"
      },
      "conditions": {
        "catalyst": "NiI2",
        "base": "K3PO4",
        "solvent": null,
        "temp_c": null,
        "time_h": null,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9301730，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "1-chloro-4-iodobenzene",
        "amine": "toluidine",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9301730",
        "unit_id": "PMC9301730-T1",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9301730-T1-04",
      "source_id": "PMC9301730-T1",
      "citation": "Phenylboronic Ester-Activated Aryl Iodide-Selective Buchwald–Hartwig-Type Amination toward Bioactivity Assay",
      "doi": "10.1021/acsomega.2c01092",
      "pmcid": "PMC9301730",
      "locator": "T1, entry 4",
      "locator_zh": "表 1，entry 4（ACS Omega，2022）",
      "outcome": {
        "value": 59.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": ". The yield was determined by GC analysis using mesitylene as an extern | 表头/题注原文：Screening of Nickel and Palladium Catalysts for the Activator-Promoted Buchwald–Hartwig-Type Aminationa",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "external standard",
        "quantification_basis": "product",
        "calibration_note": "原文报告：external standard"
      },
      "conditions": {
        "catalyst": "NiCl2",
        "base": "K3PO4",
        "solvent": null,
        "temp_c": null,
        "time_h": null,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9301730，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "1-chloro-4-iodobenzene",
        "amine": "toluidine",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9301730",
        "unit_id": "PMC9301730-T1",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9301730-T1-05",
      "source_id": "PMC9301730-T1",
      "citation": "Phenylboronic Ester-Activated Aryl Iodide-Selective Buchwald–Hartwig-Type Amination toward Bioactivity Assay",
      "doi": "10.1021/acsomega.2c01092",
      "pmcid": "PMC9301730",
      "locator": "T1, entry 5",
      "locator_zh": "表 1，entry 5（ACS Omega，2022）",
      "outcome": {
        "value": 72.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": ". The yield was determined by GC analysis using mesitylene as an extern | 表头/题注原文：Screening of Nickel and Palladium Catalysts for the Activator-Promoted Buchwald–Hartwig-Type Aminationa",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "external standard",
        "quantification_basis": "product",
        "calibration_note": "原文报告：external standard"
      },
      "conditions": {
        "catalyst": "NiCl2(dme)",
        "base": "K3PO4",
        "solvent": null,
        "temp_c": null,
        "time_h": null,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9301730，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "1-chloro-4-iodobenzene",
        "amine": "toluidine",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9301730",
        "unit_id": "PMC9301730-T1",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9301730-T1-06",
      "source_id": "PMC9301730-T1",
      "citation": "Phenylboronic Ester-Activated Aryl Iodide-Selective Buchwald–Hartwig-Type Amination toward Bioactivity Assay",
      "doi": "10.1021/acsomega.2c01092",
      "pmcid": "PMC9301730",
      "locator": "T1, entry 6",
      "locator_zh": "表 1，entry 6（ACS Omega，2022）",
      "outcome": {
        "value": 19.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": ". The yield was determined by GC analysis using mesitylene as an extern | 表头/题注原文：Screening of Nickel and Palladium Catalysts for the Activator-Promoted Buchwald–Hartwig-Type Aminationa",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "external standard",
        "quantification_basis": "product",
        "calibration_note": "原文报告：external standard"
      },
      "conditions": {
        "catalyst": "Ni(PPh3)4",
        "base": "K3PO4",
        "solvent": null,
        "temp_c": null,
        "time_h": null,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "ligand_family": "phosphine",
        "support": "none",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9301730，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "1-chloro-4-iodobenzene",
        "amine": "toluidine",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9301730",
        "unit_id": "PMC9301730-T1",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9301730-T1-07",
      "source_id": "PMC9301730-T1",
      "citation": "Phenylboronic Ester-Activated Aryl Iodide-Selective Buchwald–Hartwig-Type Amination toward Bioactivity Assay",
      "doi": "10.1021/acsomega.2c01092",
      "pmcid": "PMC9301730",
      "locator": "T1, entry 7",
      "locator_zh": "表 1，entry 7（ACS Omega，2022）",
      "outcome": {
        "value": 11.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": ". The yield was determined by GC analysis using mesitylene as an extern | 表头/题注原文：Screening of Nickel and Palladium Catalysts for the Activator-Promoted Buchwald–Hartwig-Type Aminationa",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "external standard",
        "quantification_basis": "product",
        "calibration_note": "原文报告：external standard"
      },
      "conditions": {
        "catalyst": "Ni(cod)2",
        "base": "K3PO4",
        "solvent": null,
        "temp_c": null,
        "time_h": null,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "molecular_complex",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9301730，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "1-chloro-4-iodobenzene",
        "amine": "toluidine",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9301730",
        "unit_id": "PMC9301730-T1",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9301730-T1-08",
      "source_id": "PMC9301730-T1",
      "citation": "Phenylboronic Ester-Activated Aryl Iodide-Selective Buchwald–Hartwig-Type Amination toward Bioactivity Assay",
      "doi": "10.1021/acsomega.2c01092",
      "pmcid": "PMC9301730",
      "locator": "T1, entry 8",
      "locator_zh": "表 1，entry 8（ACS Omega，2022）",
      "outcome": {
        "value": 62.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": ". The yield was determined by GC analysis using mesitylene as an extern | 表头/题注原文：Screening of Nickel and Palladium Catalysts for the Activator-Promoted Buchwald–Hartwig-Type Aminationa",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "external standard",
        "quantification_basis": "product",
        "calibration_note": "原文报告：external standard"
      },
      "conditions": {
        "catalyst": "Ni(acac)2·2H2O",
        "base": "K3PO4",
        "solvent": null,
        "temp_c": null,
        "time_h": null,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9301730，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "1-chloro-4-iodobenzene",
        "amine": "toluidine",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9301730",
        "unit_id": "PMC9301730-T1",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9301730-T1-09",
      "source_id": "PMC9301730-T1",
      "citation": "Phenylboronic Ester-Activated Aryl Iodide-Selective Buchwald–Hartwig-Type Amination toward Bioactivity Assay",
      "doi": "10.1021/acsomega.2c01092",
      "pmcid": "PMC9301730",
      "locator": "T1, entry 9",
      "locator_zh": "表 1，entry 9（ACS Omega，2022）",
      "outcome": {
        "value": 0.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": ". The yield was determined by GC analysis using mesitylene as an extern | 表头/题注原文：Screening of Nickel and Palladium Catalysts for the Activator-Promoted Buchwald–Hartwig-Type Aminationa",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "external standard",
        "quantification_basis": "product",
        "calibration_note": "原文报告：external standard"
      },
      "conditions": {
        "catalyst": "Pd(acac)2",
        "base": "K3PO4",
        "solvent": null,
        "temp_c": null,
        "time_h": null,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9301730，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "1-chloro-4-iodobenzene",
        "amine": "toluidine",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9301730",
        "unit_id": "PMC9301730-T1",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9301730-T1-10",
      "source_id": "PMC9301730-T1",
      "citation": "Phenylboronic Ester-Activated Aryl Iodide-Selective Buchwald–Hartwig-Type Amination toward Bioactivity Assay",
      "doi": "10.1021/acsomega.2c01092",
      "pmcid": "PMC9301730",
      "locator": "T1, entry 11",
      "locator_zh": "表 1，entry 11（ACS Omega，2022）",
      "outcome": {
        "value": 0.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": ". The yield was determined by GC analysis using mesitylene as an extern | 表头/题注原文：Screening of Nickel and Palladium Catalysts for the Activator-Promoted Buchwald–Hartwig-Type Aminationa",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": "external standard",
        "quantification_basis": "product",
        "calibration_note": "原文报告：external standard"
      },
      "conditions": {
        "catalyst": "Pd(OAc)2",
        "base": "K3PO4",
        "solvent": null,
        "temp_c": null,
        "time_h": null,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9301730，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "1-chloro-4-iodobenzene",
        "amine": "toluidine",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9301730",
        "unit_id": "PMC9301730-T1",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC12305527-T2-01",
      "source_id": "PMC12305527-T2",
      "citation": "Application of palladium nanoparticles supported on ordered mesoporous oxides for C–N and C <svg xmlns=\"http://www.w3.or",
      "doi": "10.1039/d5ra02824h",
      "pmcid": "PMC12305527",
      "locator": "T2, entry 3",
      "locator_zh": "表 2，entry 3（RSC Advances，2025）",
      "outcome": {
        "value": 24.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "GC yield (%) | 表头/题注原文：Evaluation of the optimal reaction parameters, including bases and temperaturea",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd–CeO2",
        "base": "K2CO3",
        "solvent": "toluene",
        "temp_c": 25.0,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC12305527，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-bromotoluene",
        "amine": "aniline",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC12305527",
        "unit_id": "PMC12305527-T2",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC12305527-T2-02",
      "source_id": "PMC12305527-T2",
      "citation": "Application of palladium nanoparticles supported on ordered mesoporous oxides for C–N and C <svg xmlns=\"http://www.w3.or",
      "doi": "10.1039/d5ra02824h",
      "pmcid": "PMC12305527",
      "locator": "T2, entry 4",
      "locator_zh": "表 2，entry 4（RSC Advances，2025）",
      "outcome": {
        "value": 60.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "GC yield (%) | 表头/题注原文：Evaluation of the optimal reaction parameters, including bases and temperaturea",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd–CeO2",
        "base": "K2CO3",
        "solvent": "toluene",
        "temp_c": 50.0,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC12305527，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-bromotoluene",
        "amine": "aniline",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC12305527",
        "unit_id": "PMC12305527-T2",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC12305527-T2-03",
      "source_id": "PMC12305527-T2",
      "citation": "Application of palladium nanoparticles supported on ordered mesoporous oxides for C–N and C <svg xmlns=\"http://www.w3.or",
      "doi": "10.1039/d5ra02824h",
      "pmcid": "PMC12305527",
      "locator": "T2, entry 5",
      "locator_zh": "表 2，entry 5（RSC Advances，2025）",
      "outcome": {
        "value": 97.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "GC yield (%) | 表头/题注原文：Evaluation of the optimal reaction parameters, including bases and temperaturea",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd–CeO2",
        "base": "K2CO3",
        "solvent": "toluene",
        "temp_c": 80.0,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC12305527，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-bromotoluene",
        "amine": "aniline",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC12305527",
        "unit_id": "PMC12305527-T2",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC12305527-T2-04",
      "source_id": "PMC12305527-T2",
      "citation": "Application of palladium nanoparticles supported on ordered mesoporous oxides for C–N and C <svg xmlns=\"http://www.w3.or",
      "doi": "10.1039/d5ra02824h",
      "pmcid": "PMC12305527",
      "locator": "T2, entry 6",
      "locator_zh": "表 2，entry 6（RSC Advances，2025）",
      "outcome": {
        "value": 61.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "GC yield (%) | 表头/题注原文：Evaluation of the optimal reaction parameters, including bases and temperaturea",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd–CeO2",
        "base": "KOH",
        "solvent": "toluene",
        "temp_c": 80.0,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC12305527，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-bromotoluene",
        "amine": "aniline",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC12305527",
        "unit_id": "PMC12305527-T2",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC12305527-T2-05",
      "source_id": "PMC12305527-T2",
      "citation": "Application of palladium nanoparticles supported on ordered mesoporous oxides for C–N and C <svg xmlns=\"http://www.w3.or",
      "doi": "10.1039/d5ra02824h",
      "pmcid": "PMC12305527",
      "locator": "T2, entry 7",
      "locator_zh": "表 2，entry 7（RSC Advances，2025）",
      "outcome": {
        "value": 23.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "GC yield (%) | 表头/题注原文：Evaluation of the optimal reaction parameters, including bases and temperaturea",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd–CeO2",
        "base": "NEt3",
        "solvent": "toluene",
        "temp_c": 80.0,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC12305527，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-bromotoluene",
        "amine": "aniline",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC12305527",
        "unit_id": "PMC12305527-T2",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC12305527-T2-06",
      "source_id": "PMC12305527-T2",
      "citation": "Application of palladium nanoparticles supported on ordered mesoporous oxides for C–N and C <svg xmlns=\"http://www.w3.or",
      "doi": "10.1039/d5ra02824h",
      "pmcid": "PMC12305527",
      "locator": "T2, entry 8",
      "locator_zh": "表 2，entry 8（RSC Advances，2025）",
      "outcome": {
        "value": 55.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "GC yield (%) | 表头/题注原文：Evaluation of the optimal reaction parameters, including bases and temperaturea",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd–CeO2",
        "base": "Cs2CO3",
        "solvent": "toluene",
        "temp_c": 80.0,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC12305527，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-bromotoluene",
        "amine": "aniline",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC12305527",
        "unit_id": "PMC12305527-T2",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC12305527-T2-07",
      "source_id": "PMC12305527-T2",
      "citation": "Application of palladium nanoparticles supported on ordered mesoporous oxides for C–N and C <svg xmlns=\"http://www.w3.or",
      "doi": "10.1039/d5ra02824h",
      "pmcid": "PMC12305527",
      "locator": "T2, entry 9",
      "locator_zh": "表 2，entry 9（RSC Advances，2025）",
      "outcome": {
        "value": 63.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "GC yield (%) | 表头/题注原文：Evaluation of the optimal reaction parameters, including bases and temperaturea",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd–CeO2",
        "base": "Na2CO3",
        "solvent": "toluene",
        "temp_c": 80.0,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC12305527，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-bromotoluene",
        "amine": "aniline",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC12305527",
        "unit_id": "PMC12305527-T2",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC12305527-T3-01",
      "source_id": "PMC12305527-T3",
      "citation": "Application of palladium nanoparticles supported on ordered mesoporous oxides for C–N and C <svg xmlns=\"http://www.w3.or",
      "doi": "10.1039/d5ra02824h",
      "pmcid": "PMC12305527",
      "locator": "T3, entry 1",
      "locator_zh": "表 3，entry 1（RSC Advances，2025）",
      "outcome": {
        "value": 90.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "GC yield (%) | 表头/题注原文：The yields, conversions, and selectivity obtained for each reaction during the catalyst screening study for Sonogashira ",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd–Co3O4",
        "base": "K2CO3",
        "solvent": null,
        "temp_c": 80.0,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC12305527，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-bromoacetophenone",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC12305527",
        "unit_id": "PMC12305527-T3",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC12305527-T3-02",
      "source_id": "PMC12305527-T3",
      "citation": "Application of palladium nanoparticles supported on ordered mesoporous oxides for C–N and C <svg xmlns=\"http://www.w3.or",
      "doi": "10.1039/d5ra02824h",
      "pmcid": "PMC12305527",
      "locator": "T3, entry 2",
      "locator_zh": "表 3，entry 2（RSC Advances，2025）",
      "outcome": {
        "value": 88.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "GC yield (%) | 表头/题注原文：The yields, conversions, and selectivity obtained for each reaction during the catalyst screening study for Sonogashira ",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd–Mn2O4",
        "base": "K2CO3",
        "solvent": null,
        "temp_c": 80.0,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC12305527，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-bromoacetophenone",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC12305527",
        "unit_id": "PMC12305527-T3",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC12305527-T3-03",
      "source_id": "PMC12305527-T3",
      "citation": "Application of palladium nanoparticles supported on ordered mesoporous oxides for C–N and C <svg xmlns=\"http://www.w3.or",
      "doi": "10.1039/d5ra02824h",
      "pmcid": "PMC12305527",
      "locator": "T3, entry 3",
      "locator_zh": "表 3，entry 3（RSC Advances，2025）",
      "outcome": {
        "value": 30.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "GC yield (%) | 表头/题注原文：The yields, conversions, and selectivity obtained for each reaction during the catalyst screening study for Sonogashira ",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd–NiO",
        "base": "K2CO3",
        "solvent": null,
        "temp_c": 80.0,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC12305527，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-bromoacetophenone",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC12305527",
        "unit_id": "PMC12305527-T3",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC12305527-T3-04",
      "source_id": "PMC12305527-T3",
      "citation": "Application of palladium nanoparticles supported on ordered mesoporous oxides for C–N and C <svg xmlns=\"http://www.w3.or",
      "doi": "10.1039/d5ra02824h",
      "pmcid": "PMC12305527",
      "locator": "T3, entry 4",
      "locator_zh": "表 3，entry 4（RSC Advances，2025）",
      "outcome": {
        "value": 60.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "GC yield (%) | 表头/题注原文：The yields, conversions, and selectivity obtained for each reaction during the catalyst screening study for Sonogashira ",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd–CeO2",
        "base": "K2CO3",
        "solvent": null,
        "temp_c": 80.0,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC12305527，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-bromoacetophenone",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC12305527",
        "unit_id": "PMC12305527-T3",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC12305527-T3-05",
      "source_id": "PMC12305527-T3",
      "citation": "Application of palladium nanoparticles supported on ordered mesoporous oxides for C–N and C <svg xmlns=\"http://www.w3.or",
      "doi": "10.1039/d5ra02824h",
      "pmcid": "PMC12305527",
      "locator": "T3, entry 5",
      "locator_zh": "表 3，entry 5（RSC Advances，2025）",
      "outcome": {
        "value": 58.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "GC yield (%) | 表头/题注原文：The yields, conversions, and selectivity obtained for each reaction during the catalyst screening study for Sonogashira ",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd–TiO2",
        "base": "K2CO3",
        "solvent": null,
        "temp_c": 80.0,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC12305527，buchwald_hartwig）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-bromoacetophenone",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC12305527",
        "unit_id": "PMC12305527-T3",
        "reaction_class": "buchwald_hartwig",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9086392-T1-01",
      "source_id": "PMC9086392-T1",
      "citation": "Preparation of a novel palladium catalytic hydrogel based on graphene oxide/chitosan NPs and cellulose nanowhiskers†",
      "doi": "10.1039/c8ra06623j",
      "pmcid": "PMC9086392",
      "locator": "T1, entry 1",
      "locator_zh": "表 1，entry 1（RSC Advances，2018）",
      "outcome": {
        "value": 75.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": ", catalyst, base and solvent.bIsolated yield. | 表头/题注原文：Optimization of reaction temperature for the model coupling reactiona",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "K2CO3",
        "solvent": "Toluene",
        "temp_c": null,
        "time_h": 1.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9086392，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "methyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9086392",
        "unit_id": "PMC9086392-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9086392-T1-02",
      "source_id": "PMC9086392-T1",
      "citation": "Preparation of a novel palladium catalytic hydrogel based on graphene oxide/chitosan NPs and cellulose nanowhiskers†",
      "doi": "10.1039/c8ra06623j",
      "pmcid": "PMC9086392",
      "locator": "T1, entry 2",
      "locator_zh": "表 1，entry 2（RSC Advances，2018）",
      "outcome": {
        "value": 79.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": ", catalyst, base and solvent.bIsolated yield. | 表头/题注原文：Optimization of reaction temperature for the model coupling reactiona",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "K2CO3",
        "solvent": "DMF",
        "temp_c": null,
        "time_h": 0.83,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9086392，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "methyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9086392",
        "unit_id": "PMC9086392-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9086392-T1-03",
      "source_id": "PMC9086392-T1",
      "citation": "Preparation of a novel palladium catalytic hydrogel based on graphene oxide/chitosan NPs and cellulose nanowhiskers†",
      "doi": "10.1039/c8ra06623j",
      "pmcid": "PMC9086392",
      "locator": "T1, entry 3",
      "locator_zh": "表 1，entry 3（RSC Advances，2018）",
      "outcome": {
        "value": 80.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": ", catalyst, base and solvent.bIsolated yield. | 表头/题注原文：Optimization of reaction temperature for the model coupling reactiona",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "K2CO3",
        "solvent": "EtOH",
        "temp_c": null,
        "time_h": 0.67,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9086392，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "methyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9086392",
        "unit_id": "PMC9086392-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9086392-T1-04",
      "source_id": "PMC9086392-T1",
      "citation": "Preparation of a novel palladium catalytic hydrogel based on graphene oxide/chitosan NPs and cellulose nanowhiskers†",
      "doi": "10.1039/c8ra06623j",
      "pmcid": "PMC9086392",
      "locator": "T1, entry 4",
      "locator_zh": "表 1，entry 4（RSC Advances，2018）",
      "outcome": {
        "value": 60.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": ", catalyst, base and solvent.bIsolated yield. | 表头/题注原文：Optimization of reaction temperature for the model coupling reactiona",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "K2CO3",
        "solvent": "H2O",
        "temp_c": null,
        "time_h": 1.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9086392，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "methyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9086392",
        "unit_id": "PMC9086392-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9086392-T1-05",
      "source_id": "PMC9086392-T1",
      "citation": "Preparation of a novel palladium catalytic hydrogel based on graphene oxide/chitosan NPs and cellulose nanowhiskers†",
      "doi": "10.1039/c8ra06623j",
      "pmcid": "PMC9086392",
      "locator": "T1, entry 5",
      "locator_zh": "表 1，entry 5（RSC Advances，2018）",
      "outcome": {
        "value": 96.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": ", catalyst, base and solvent.bIsolated yield. | 表头/题注原文：Optimization of reaction temperature for the model coupling reactiona",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "K2CO3",
        "solvent": "EtOH/H2O",
        "temp_c": null,
        "time_h": 0.58,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9086392，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "methyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9086392",
        "unit_id": "PMC9086392-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9086392-T1-06",
      "source_id": "PMC9086392-T1",
      "citation": "Preparation of a novel palladium catalytic hydrogel based on graphene oxide/chitosan NPs and cellulose nanowhiskers†",
      "doi": "10.1039/c8ra06623j",
      "pmcid": "PMC9086392",
      "locator": "T1, entry 6",
      "locator_zh": "表 1，entry 6（RSC Advances，2018）",
      "outcome": {
        "value": 77.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": ", catalyst, base and solvent.bIsolated yield. | 表头/题注原文：Optimization of reaction temperature for the model coupling reactiona",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "NaOAc",
        "solvent": "EtOH/H2O",
        "temp_c": null,
        "time_h": 1.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9086392，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "methyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9086392",
        "unit_id": "PMC9086392-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9086392-T1-07",
      "source_id": "PMC9086392-T1",
      "citation": "Preparation of a novel palladium catalytic hydrogel based on graphene oxide/chitosan NPs and cellulose nanowhiskers†",
      "doi": "10.1039/c8ra06623j",
      "pmcid": "PMC9086392",
      "locator": "T1, entry 7",
      "locator_zh": "表 1，entry 7（RSC Advances，2018）",
      "outcome": {
        "value": 65.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": ", catalyst, base and solvent.bIsolated yield. | 表头/题注原文：Optimization of reaction temperature for the model coupling reactiona",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "NEt3",
        "solvent": "EtOH/H2O",
        "temp_c": null,
        "time_h": 1.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9086392，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "methyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9086392",
        "unit_id": "PMC9086392-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9086392-T1-08",
      "source_id": "PMC9086392-T1",
      "citation": "Preparation of a novel palladium catalytic hydrogel based on graphene oxide/chitosan NPs and cellulose nanowhiskers†",
      "doi": "10.1039/c8ra06623j",
      "pmcid": "PMC9086392",
      "locator": "T1, entry 8",
      "locator_zh": "表 1，entry 8（RSC Advances，2018）",
      "outcome": {
        "value": 85.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": ", catalyst, base and solvent.bIsolated yield. | 表头/题注原文：Optimization of reaction temperature for the model coupling reactiona",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "NaHCO3",
        "solvent": "EtOH/H2O",
        "temp_c": null,
        "time_h": 1.08,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9086392，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "methyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9086392",
        "unit_id": "PMC9086392-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9086392-T1-09",
      "source_id": "PMC9086392-T1",
      "citation": "Preparation of a novel palladium catalytic hydrogel based on graphene oxide/chitosan NPs and cellulose nanowhiskers†",
      "doi": "10.1039/c8ra06623j",
      "pmcid": "PMC9086392",
      "locator": "T1, entry 9",
      "locator_zh": "表 1，entry 9（RSC Advances，2018）",
      "outcome": {
        "value": 79.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": ", catalyst, base and solvent.bIsolated yield. | 表头/题注原文：Optimization of reaction temperature for the model coupling reactiona",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "t-BuOK",
        "solvent": "EtOH/H2O",
        "temp_c": null,
        "time_h": 0.83,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9086392，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "methyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9086392",
        "unit_id": "PMC9086392-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9086392-T1-10",
      "source_id": "PMC9086392-T1",
      "citation": "Preparation of a novel palladium catalytic hydrogel based on graphene oxide/chitosan NPs and cellulose nanowhiskers†",
      "doi": "10.1039/c8ra06623j",
      "pmcid": "PMC9086392",
      "locator": "T1, entry 11",
      "locator_zh": "表 1，entry 11（RSC Advances，2018）",
      "outcome": {
        "value": 83.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": ", catalyst, base and solvent.bIsolated yield. | 表头/题注原文：Optimization of reaction temperature for the model coupling reactiona",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "K2CO3",
        "solvent": "EtOH/H2O",
        "temp_c": null,
        "time_h": 0.58,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9086392，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "methyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9086392",
        "unit_id": "PMC9086392-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9086392-T1-11",
      "source_id": "PMC9086392-T1",
      "citation": "Preparation of a novel palladium catalytic hydrogel based on graphene oxide/chitosan NPs and cellulose nanowhiskers†",
      "doi": "10.1039/c8ra06623j",
      "pmcid": "PMC9086392",
      "locator": "T1, entry 12",
      "locator_zh": "表 1，entry 12（RSC Advances，2018）",
      "outcome": {
        "value": 75.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": ", catalyst, base and solvent.bIsolated yield. | 表头/题注原文：Optimization of reaction temperature for the model coupling reactiona",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "K2CO",
        "solvent": "EtOH/H2O",
        "temp_c": null,
        "time_h": 0.67,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9086392，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "methyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9086392",
        "unit_id": "PMC9086392-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC9086392-T1-12",
      "source_id": "PMC9086392-T1",
      "citation": "Preparation of a novel palladium catalytic hydrogel based on graphene oxide/chitosan NPs and cellulose nanowhiskers†",
      "doi": "10.1039/c8ra06623j",
      "pmcid": "PMC9086392",
      "locator": "T1, entry 13",
      "locator_zh": "表 1，entry 13（RSC Advances，2018）",
      "outcome": {
        "value": 90.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": ", catalyst, base and solvent.bIsolated yield. | 表头/题注原文：Optimization of reaction temperature for the model coupling reactiona",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "K2CO",
        "solvent": "EtOH/H2O",
        "temp_c": null,
        "time_h": 0.5,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC9086392，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "methyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC9086392",
        "unit_id": "PMC9086392-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T1-01",
      "source_id": "PMC7511807-T1",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T1, entry 1",
      "locator_zh": "表 1，entry 1（Heliyon，2020）",
      "outcome": {
        "value": 90.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "nd solvent (2 ml). bYields of isolated product. | 表头/题注原文：Optimization of the reaction conditions.a",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核；原始单元格非纯数值：'90%'（数值为按首位数字提取，需人工确认）"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "DMF",
        "base": "Et3N",
        "solvent": "DMF",
        "temp_c": 90.0,
        "time_h": 7.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodotoluene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T1-02",
      "source_id": "PMC7511807-T1",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T1, entry 2",
      "locator_zh": "表 1，entry 2（Heliyon，2020）",
      "outcome": {
        "value": 73.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "nd solvent (2 ml). bYields of isolated product. | 表头/题注原文：Optimization of the reaction conditions.a",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核；原始单元格非纯数值：'73%'（数值为按首位数字提取，需人工确认）"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "DMSO",
        "base": "Et3N",
        "solvent": "DMSO",
        "temp_c": 90.0,
        "time_h": 8.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodotoluene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T1-03",
      "source_id": "PMC7511807-T1",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T1, entry 3",
      "locator_zh": "表 1，entry 3（Heliyon，2020）",
      "outcome": {
        "value": 61.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "nd solvent (2 ml). bYields of isolated product. | 表头/题注原文：Optimization of the reaction conditions.a",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核；原始单元格非纯数值：'61%'（数值为按首位数字提取，需人工确认）"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "CH3CN",
        "base": "Et3N",
        "solvent": "CH3CN",
        "temp_c": 90.0,
        "time_h": 7.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodotoluene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T1-04",
      "source_id": "PMC7511807-T1",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T1, entry 4",
      "locator_zh": "表 1，entry 4（Heliyon，2020）",
      "outcome": {
        "value": 80.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "nd solvent (2 ml). bYields of isolated product. | 表头/题注原文：Optimization of the reaction conditions.a",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核；原始单元格非纯数值：'80%'（数值为按首位数字提取，需人工确认）"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "EtOH",
        "base": "Et3N",
        "solvent": "EtOH",
        "temp_c": 90.0,
        "time_h": 9.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodotoluene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T1-05",
      "source_id": "PMC7511807-T1",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T1, entry 5",
      "locator_zh": "表 1，entry 5（Heliyon，2020）",
      "outcome": {
        "value": 85.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "nd solvent (2 ml). bYields of isolated product. | 表头/题注原文：Optimization of the reaction conditions.a",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核；原始单元格非纯数值：'85%'（数值为按首位数字提取，需人工确认）"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "EtOH/H2O",
        "base": "Et3N",
        "solvent": "EtOH/H2O",
        "temp_c": 90.0,
        "time_h": 8.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodotoluene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T1-06",
      "source_id": "PMC7511807-T1",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T1, entry 6",
      "locator_zh": "表 1，entry 6（Heliyon，2020）",
      "outcome": {
        "value": 95.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "nd solvent (2 ml). bYields of isolated product. | 表头/题注原文：Optimization of the reaction conditions.a",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核；原始单元格非纯数值：'95%'（数值为按首位数字提取，需人工确认）"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "H2O",
        "base": "Et3N",
        "solvent": "H2O",
        "temp_c": 90.0,
        "time_h": 8.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodotoluene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T1-07",
      "source_id": "PMC7511807-T1",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T1, entry 7",
      "locator_zh": "表 1，entry 7（Heliyon，2020）",
      "outcome": {
        "value": 95.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "nd solvent (2 ml). bYields of isolated product. | 表头/题注原文：Optimization of the reaction conditions.a",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核；原始单元格非纯数值：'95%'（数值为按首位数字提取，需人工确认）"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "DMF/H2O",
        "base": "Et3N",
        "solvent": "DMF/H2O",
        "temp_c": 90.0,
        "time_h": 9.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodotoluene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T1-08",
      "source_id": "PMC7511807-T1",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T1, entry 8",
      "locator_zh": "表 1，entry 8（Heliyon，2020）",
      "outcome": {
        "value": 78.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "nd solvent (2 ml). bYields of isolated product. | 表头/题注原文：Optimization of the reaction conditions.a",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核；原始单元格非纯数值：'78%'（数值为按首位数字提取，需人工确认）"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "H2O",
        "base": "K2CO3",
        "solvent": "H2O",
        "temp_c": 90.0,
        "time_h": 9.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodotoluene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T1-09",
      "source_id": "PMC7511807-T1",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T1, entry 9",
      "locator_zh": "表 1，entry 9（Heliyon，2020）",
      "outcome": {
        "value": 85.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "nd solvent (2 ml). bYields of isolated product. | 表头/题注原文：Optimization of the reaction conditions.a",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核；原始单元格非纯数值：'85%'（数值为按首位数字提取，需人工确认）"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "H2O",
        "base": "Cs2CO3",
        "solvent": "H2O",
        "temp_c": 90.0,
        "time_h": 8.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodotoluene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T1-10",
      "source_id": "PMC7511807-T1",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T1, entry 10",
      "locator_zh": "表 1，entry 10（Heliyon，2020）",
      "outcome": {
        "value": 61.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "nd solvent (2 ml). bYields of isolated product. | 表头/题注原文：Optimization of the reaction conditions.a",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核；原始单元格非纯数值：'61%'（数值为按首位数字提取，需人工确认）"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "H2O",
        "base": "Na2CO3",
        "solvent": "H2O",
        "temp_c": 90.0,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodotoluene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T1-11",
      "source_id": "PMC7511807-T1",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T1, entry 11",
      "locator_zh": "表 1，entry 11（Heliyon，2020）",
      "outcome": {
        "value": 95.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "nd solvent (2 ml). bYields of isolated product. | 表头/题注原文：Optimization of the reaction conditions.a",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核；原始单元格非纯数值：'95%'（数值为按首位数字提取，需人工确认）"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "H2O",
        "base": "Et3N",
        "solvent": "H2O",
        "temp_c": 100.0,
        "time_h": 7.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodotoluene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T1-12",
      "source_id": "PMC7511807-T1",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T1, entry 12",
      "locator_zh": "表 1，entry 12（Heliyon，2020）",
      "outcome": {
        "value": 98.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "nd solvent (2 ml). bYields of isolated product. | 表头/题注原文：Optimization of the reaction conditions.a",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核；原始单元格非纯数值：'98%'（数值为按首位数字提取，需人工确认）"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "H2O",
        "base": "Et3N",
        "solvent": "H2O",
        "temp_c": 110.0,
        "time_h": 7.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodotoluene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T1-13",
      "source_id": "PMC7511807-T1",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T1, entry 13",
      "locator_zh": "表 1，entry 13（Heliyon，2020）",
      "outcome": {
        "value": 80.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "nd solvent (2 ml). bYields of isolated product. | 表头/题注原文：Optimization of the reaction conditions.a",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核；原始单元格非纯数值：'80%'（数值为按首位数字提取，需人工确认）"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "H2O",
        "base": "Et3N",
        "solvent": "H2O",
        "temp_c": 90.0,
        "time_h": 10.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodotoluene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T1-14",
      "source_id": "PMC7511807-T1",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T1, entry 14",
      "locator_zh": "表 1，entry 14（Heliyon，2020）",
      "outcome": {
        "value": 90.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "nd solvent (2 ml). bYields of isolated product. | 表头/题注原文：Optimization of the reaction conditions.a",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核；原始单元格非纯数值：'90%'（数值为按首位数字提取，需人工确认）"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "H2O",
        "base": "Et3N",
        "solvent": "H2O",
        "temp_c": 90.0,
        "time_h": 9.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodotoluene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T3-01",
      "source_id": "PMC7511807-T3",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T3, entry 1",
      "locator_zh": "表 3，entry 1（Heliyon，2020）",
      "outcome": {
        "value": 98.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "alyst and solvent. bYields of isolated product. | 表头/题注原文：Comparison of catalytic activity for the Heck cross coupling reaction using Fe3O4@Si–NHC–Pd catalyst with those obtained",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Fe3O4@Si–NHC–Pd",
        "base": null,
        "solvent": "H2O",
        "temp_c": 90.0,
        "time_h": 8.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "non_phosphine",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "Bromobenzene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T3",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T3-02",
      "source_id": "PMC7511807-T3",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T3, entry 2",
      "locator_zh": "表 3，entry 2（Heliyon，2020）",
      "outcome": {
        "value": 92.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "alyst and solvent. bYields of isolated product. | 表头/题注原文：Comparison of catalytic activity for the Heck cross coupling reaction using Fe3O4@Si–NHC–Pd catalyst with those obtained",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "NO2–NHC–Pd@Fe3O4",
        "base": null,
        "solvent": "MeCN",
        "temp_c": 80.0,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "non_phosphine",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "Bromobenzene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T3",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T3-03",
      "source_id": "PMC7511807-T3",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T3, entry 3",
      "locator_zh": "表 3，entry 3（Heliyon，2020）",
      "outcome": {
        "value": 90.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "alyst and solvent. bYields of isolated product. | 表头/题注原文：Comparison of catalytic activity for the Heck cross coupling reaction using Fe3O4@Si–NHC–Pd catalyst with those obtained",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd NPs@NHC@ZIF-8",
        "base": null,
        "solvent": "DMF-H2O",
        "temp_c": 110.0,
        "time_h": 2.5,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "non_phosphine",
        "support": "solid_support",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "Bromobenzene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T3",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T3-04",
      "source_id": "PMC7511807-T3",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T3, entry 4",
      "locator_zh": "表 3，entry 4（Heliyon，2020）",
      "outcome": {
        "value": 87.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "alyst and solvent. bYields of isolated product. | 表头/题注原文：Comparison of catalytic activity for the Heck cross coupling reaction using Fe3O4@Si–NHC–Pd catalyst with those obtained",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "MnPs–NHC–Pd(II)",
        "base": null,
        "solvent": "DMF",
        "temp_c": 70.0,
        "time_h": 0.33,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "nanoparticle",
        "ligand_family": "non_phosphine",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "Bromobenzene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T3",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T3-05",
      "source_id": "PMC7511807-T3",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T3, entry 5",
      "locator_zh": "表 3，entry 5（Heliyon，2020）",
      "outcome": {
        "value": 85.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "alyst and solvent. bYields of isolated product. | 表头/题注原文：Comparison of catalytic activity for the Heck cross coupling reaction using Fe3O4@Si–NHC–Pd catalyst with those obtained",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Hydrazon-pd@ZnO",
        "base": null,
        "solvent": "DMF",
        "temp_c": 130.0,
        "time_h": 1.5,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "supported",
        "ligand_family": "none",
        "support": "solid_support",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "Bromobenzene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T3",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC7511807-T3-06",
      "source_id": "PMC7511807-T3",
      "citation": "Synthesis of nano magnetic supported NHC-palladium and investigation of its applications as a catalyst in the Mizoroki-H",
      "doi": "10.1016/j.heliyon.2020.e04946",
      "pmcid": "PMC7511807",
      "locator": "T3, entry 6",
      "locator_zh": "表 3，entry 6（Heliyon，2020）",
      "outcome": {
        "value": 16.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "alyst and solvent. bYields of isolated product. | 表头/题注原文：Comparison of catalytic activity for the Heck cross coupling reaction using Fe3O4@Si–NHC–Pd catalyst with those obtained",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "PdCl2Kryptofix-5",
        "base": null,
        "solvent": "DMF",
        "temp_c": 130.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC7511807，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "Bromobenzene",
        "alkene": "styrene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC7511807",
        "unit_id": "PMC7511807-T3",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T1-01",
      "source_id": "PMC8402173-T1",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T1, entry 1",
      "locator_zh": "表 1，entry 1（Polymers，2021）",
      "outcome": {
        "value": 99.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 48 h. (b) Determined by gas chromatography. (c) [45]. | 表头/题注原文：Mizoroki–Heck reaction in water using the copolymers a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "NEt(i-Pr)2",
        "solvent": "benzene",
        "temp_c": 70.0,
        "time_h": 48.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "n-butyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T1-02",
      "source_id": "PMC8402173-T1",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T1, entry 2",
      "locator_zh": "表 1，entry 2（Polymers，2021）",
      "outcome": {
        "value": 52.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 48 h. (b) Determined by gas chromatography. (c) [45]. | 表头/题注原文：Mizoroki–Heck reaction in water using the copolymers a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "NEt(i-Pr)2",
        "solvent": "benzene",
        "temp_c": 70.0,
        "time_h": 48.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "n-butyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T1-03",
      "source_id": "PMC8402173-T1",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T1, entry 3",
      "locator_zh": "表 1，entry 3（Polymers，2021）",
      "outcome": {
        "value": 73.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 48 h. (b) Determined by gas chromatography. (c) [45]. | 表头/题注原文：Mizoroki–Heck reaction in water using the copolymers a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "NEt(i-Pr)2",
        "solvent": "benzene",
        "temp_c": 70.0,
        "time_h": 48.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "n-butyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T1-04",
      "source_id": "PMC8402173-T1",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T1, entry 4",
      "locator_zh": "表 1，entry 4（Polymers，2021）",
      "outcome": {
        "value": 99.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 48 h. (b) Determined by gas chromatography. (c) [45]. | 表头/题注原文：Mizoroki–Heck reaction in water using the copolymers a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "NEt(i-Pr)2",
        "solvent": "benzene",
        "temp_c": 70.0,
        "time_h": 48.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "n-butyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T1-05",
      "source_id": "PMC8402173-T1",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T1, entry 5",
      "locator_zh": "表 1，entry 5（Polymers，2021）",
      "outcome": {
        "value": 49.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 48 h. (b) Determined by gas chromatography. (c) [45]. | 表头/题注原文：Mizoroki–Heck reaction in water using the copolymers a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "NEt(i-Pr)2",
        "solvent": "benzene",
        "temp_c": 70.0,
        "time_h": 48.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "n-butyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T1-06",
      "source_id": "PMC8402173-T1",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T1, entry 6",
      "locator_zh": "表 1，entry 6（Polymers，2021）",
      "outcome": {
        "value": 99.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 48 h. (b) Determined by gas chromatography. (c) [45]. | 表头/题注原文：Mizoroki–Heck reaction in water using the copolymers a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "NEt(i-Pr)2",
        "solvent": "benzene",
        "temp_c": 70.0,
        "time_h": 48.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "n-butyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T1-07",
      "source_id": "PMC8402173-T1",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T1, entry 7",
      "locator_zh": "表 1，entry 7（Polymers，2021）",
      "outcome": {
        "value": 47.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 48 h. (b) Determined by gas chromatography. (c) [45]. | 表头/题注原文：Mizoroki–Heck reaction in water using the copolymers a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "NEt(i-Pr)2",
        "solvent": "benzene",
        "temp_c": 70.0,
        "time_h": 48.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "n-butyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T1-08",
      "source_id": "PMC8402173-T1",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T1, entry 8",
      "locator_zh": "表 1，entry 8（Polymers，2021）",
      "outcome": {
        "value": 61.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 48 h. (b) Determined by gas chromatography. (c) [45]. | 表头/题注原文：Mizoroki–Heck reaction in water using the copolymers a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "NEt(i-Pr)2",
        "solvent": "benzene",
        "temp_c": 70.0,
        "time_h": 48.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "n-butyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T1-09",
      "source_id": "PMC8402173-T1",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T1, entry 9 c",
      "locator_zh": "表 1，entry 9 c（Polymers，2021）",
      "outcome": {
        "value": 43.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 48 h. (b) Determined by gas chromatography. (c) [45]. | 表头/题注原文：Mizoroki–Heck reaction in water using the copolymers a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "NEt(i-Pr)2",
        "solvent": "benzene",
        "temp_c": 70.0,
        "time_h": 48.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "n-butyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T4-01",
      "source_id": "PMC8402173-T4",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T4, entry 1",
      "locator_zh": "表 4，entry 1（Polymers，2021）",
      "outcome": {
        "value": 38.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with Pd catalysts a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd(OAc)2",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T4",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T4-02",
      "source_id": "PMC8402173-T4",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T4, entry 2",
      "locator_zh": "表 4，entry 2（Polymers，2021）",
      "outcome": {
        "value": 86.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with Pd catalysts a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "PdCl2(PPh3)2",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T4",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T4-03",
      "source_id": "PMC8402173-T4",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T4, entry 3",
      "locator_zh": "表 4，entry 3（Polymers，2021）",
      "outcome": {
        "value": 81.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with Pd catalysts a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "PdCl2(PPh3)2",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T4",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T4-04",
      "source_id": "PMC8402173-T4",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T4, entry 4",
      "locator_zh": "表 4，entry 4（Polymers，2021）",
      "outcome": {
        "value": 63.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with Pd catalysts a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "PdCl2(dppf)",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T4",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T4-05",
      "source_id": "PMC8402173-T4",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T4, entry 5",
      "locator_zh": "表 4，entry 5（Polymers，2021）",
      "outcome": {
        "value": 62.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with Pd catalysts a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "PdCl2(dppf)",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T4",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T4-06",
      "source_id": "PMC8402173-T4",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T4, entry 6",
      "locator_zh": "表 4，entry 6（Polymers，2021）",
      "outcome": {
        "value": 50.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with Pd catalysts a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd(OAc)2",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T4",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T4-07",
      "source_id": "PMC8402173-T4",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T4, entry 7",
      "locator_zh": "表 4，entry 7（Polymers，2021）",
      "outcome": {
        "value": 14.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with Pd catalysts a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd(OAc)2",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T4",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T4-08",
      "source_id": "PMC8402173-T4",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T4, entry 8",
      "locator_zh": "表 4，entry 8（Polymers，2021）",
      "outcome": {
        "value": 99.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with Pd catalysts a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Pd(OAc)2",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T4",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T4-09",
      "source_id": "PMC8402173-T4",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T4, entry 9",
      "locator_zh": "表 4，entry 9（Polymers，2021）",
      "outcome": {
        "value": 97.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with Pd catalysts a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "PdCl2(cod)",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T4",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T4-10",
      "source_id": "PMC8402173-T4",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T4, entry 10",
      "locator_zh": "表 4，entry 10（Polymers，2021）",
      "outcome": {
        "value": 74.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with Pd catalysts a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Catalysts",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T4",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T4-11",
      "source_id": "PMC8402173-T4",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T4, entry 11",
      "locator_zh": "表 4，entry 11（Polymers，2021）",
      "outcome": {
        "value": 99.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with Pd catalysts a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "PdCl2(PPh3)2",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T4",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T4-12",
      "source_id": "PMC8402173-T4",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T4, entry 12",
      "locator_zh": "表 4，entry 12（Polymers，2021）",
      "outcome": {
        "value": 7.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with Pd catalysts a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Ni(acac)2",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "catalyst_form": "in_situ_salt",
        "ligand_family": "none",
        "support": "none",
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T4",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T5-01",
      "source_id": "PMC8402173-T5",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T5, entry 1",
      "locator_zh": "表 5，entry 1（Polymers，2021）",
      "outcome": {
        "value": 77.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with surfactants a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "None",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T5",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T5-02",
      "source_id": "PMC8402173-T5",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T5, entry 2",
      "locator_zh": "表 5，entry 2（Polymers，2021）",
      "outcome": {
        "value": 86.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with surfactants a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "NS",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T5",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T5-03",
      "source_id": "PMC8402173-T5",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T5, entry 3",
      "locator_zh": "表 5，entry 3（Polymers，2021）",
      "outcome": {
        "value": 84.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with surfactants a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "NA",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T5",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T5-04",
      "source_id": "PMC8402173-T5",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T5, entry 4",
      "locator_zh": "表 5，entry 4（Polymers，2021）",
      "outcome": {
        "value": 81.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with surfactants a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "DS",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T5",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T5-05",
      "source_id": "PMC8402173-T5",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T5, entry 5",
      "locator_zh": "表 5，entry 5（Polymers，2021）",
      "outcome": {
        "value": 82.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with surfactants a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "DA",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T5",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T5-06",
      "source_id": "PMC8402173-T5",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T5, entry 6",
      "locator_zh": "表 5，entry 6（Polymers，2021）",
      "outcome": {
        "value": 61.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with surfactants a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "SDS",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T5",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC8402173-T5-07",
      "source_id": "PMC8402173-T5",
      "citation": "Palladium-Catalyzed Mizoroki–Heck and Copper-Free Sonogashira Coupling Reactions in Water Using Thermoresponsive Polymer",
      "doi": "10.3390/polym13162717",
      "pmcid": "PMC8402173",
      "locator": "T5, entry 7",
      "locator_zh": "表 5，entry 7（Polymers，2021）",
      "outcome": {
        "value": 78.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "0 °C, 24 h. (b) Determined by gas chromatography. | 表头/题注原文：Copper-free Sonogashira coupling in water with surfactants a.",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Triton X-100",
        "base": null,
        "solvent": "water",
        "temp_c": 70.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC8402173，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-iodoanisole",
        "alkyne": "phenylacetylene",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC8402173",
        "unit_id": "PMC8402173-T5",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC13325132-T1-01",
      "source_id": "PMC13325132-T1",
      "citation": "Sustainable Mizoroki–Heck Cross-Coupling Using a Pd(II)-Polymer as Precatalyst in 1‑Butanol",
      "doi": "10.1021/acsomega.6c01810",
      "pmcid": "PMC13325132",
      "locator": "T1, entry 1",
      "locator_zh": "表 1，entry 1（ACS Omega，2026）",
      "outcome": {
        "value": 17.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "o 10 mg).bYield determined by GC/MS with a calibration curve for | 表头/题注原文：Optimization of Reaction Conditions for MH Reaction between iodobenzene and Ethyl Acrylate Catalyzed by Pd/PECIm",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "Et3N",
        "solvent": "iPrOH",
        "temp_c": 80.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC13325132，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "ethyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC13325132",
        "unit_id": "PMC13325132-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC13325132-T1-02",
      "source_id": "PMC13325132-T1",
      "citation": "Sustainable Mizoroki–Heck Cross-Coupling Using a Pd(II)-Polymer as Precatalyst in 1‑Butanol",
      "doi": "10.1021/acsomega.6c01810",
      "pmcid": "PMC13325132",
      "locator": "T1, entry 2",
      "locator_zh": "表 1，entry 2（ACS Omega，2026）",
      "outcome": {
        "value": 66.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "o 10 mg).bYield determined by GC/MS with a calibration curve for | 表头/题注原文：Optimization of Reaction Conditions for MH Reaction between iodobenzene and Ethyl Acrylate Catalyzed by Pd/PECIm",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "Et3N",
        "solvent": "iPrOH/H2O",
        "temp_c": 80.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC13325132，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "ethyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC13325132",
        "unit_id": "PMC13325132-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC13325132-T1-03",
      "source_id": "PMC13325132-T1",
      "citation": "Sustainable Mizoroki–Heck Cross-Coupling Using a Pd(II)-Polymer as Precatalyst in 1‑Butanol",
      "doi": "10.1021/acsomega.6c01810",
      "pmcid": "PMC13325132",
      "locator": "T1, entry 3",
      "locator_zh": "表 1，entry 3（ACS Omega，2026）",
      "outcome": {
        "value": 67.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "o 10 mg).bYield determined by GC/MS with a calibration curve for | 表头/题注原文：Optimization of Reaction Conditions for MH Reaction between iodobenzene and Ethyl Acrylate Catalyzed by Pd/PECIm",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "Et3N",
        "solvent": "H2O",
        "temp_c": 80.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC13325132，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "ethyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC13325132",
        "unit_id": "PMC13325132-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC13325132-T1-04",
      "source_id": "PMC13325132-T1",
      "citation": "Sustainable Mizoroki–Heck Cross-Coupling Using a Pd(II)-Polymer as Precatalyst in 1‑Butanol",
      "doi": "10.1021/acsomega.6c01810",
      "pmcid": "PMC13325132",
      "locator": "T1, entry 4",
      "locator_zh": "表 1，entry 4（ACS Omega，2026）",
      "outcome": {
        "value": 69.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "o 10 mg).bYield determined by GC/MS with a calibration curve for | 表头/题注原文：Optimization of Reaction Conditions for MH Reaction between iodobenzene and Ethyl Acrylate Catalyzed by Pd/PECIm",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "Et3N",
        "solvent": "iPrOH/H2O",
        "temp_c": 80.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC13325132，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "ethyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC13325132",
        "unit_id": "PMC13325132-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC13325132-T1-05",
      "source_id": "PMC13325132-T1",
      "citation": "Sustainable Mizoroki–Heck Cross-Coupling Using a Pd(II)-Polymer as Precatalyst in 1‑Butanol",
      "doi": "10.1021/acsomega.6c01810",
      "pmcid": "PMC13325132",
      "locator": "T1, entry 5",
      "locator_zh": "表 1，entry 5（ACS Omega，2026）",
      "outcome": {
        "value": 70.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "o 10 mg).bYield determined by GC/MS with a calibration curve for | 表头/题注原文：Optimization of Reaction Conditions for MH Reaction between iodobenzene and Ethyl Acrylate Catalyzed by Pd/PECIm",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "Et3N",
        "solvent": "iPrOH/H2O",
        "temp_c": 80.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC13325132，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "ethyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC13325132",
        "unit_id": "PMC13325132-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC13325132-T1-06",
      "source_id": "PMC13325132-T1",
      "citation": "Sustainable Mizoroki–Heck Cross-Coupling Using a Pd(II)-Polymer as Precatalyst in 1‑Butanol",
      "doi": "10.1021/acsomega.6c01810",
      "pmcid": "PMC13325132",
      "locator": "T1, entry 6",
      "locator_zh": "表 1，entry 6（ACS Omega，2026）",
      "outcome": {
        "value": 69.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "o 10 mg).bYield determined by GC/MS with a calibration curve for | 表头/题注原文：Optimization of Reaction Conditions for MH Reaction between iodobenzene and Ethyl Acrylate Catalyzed by Pd/PECIm",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "Et3N",
        "solvent": "iPrOH/H2O",
        "temp_c": 80.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC13325132，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "ethyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC13325132",
        "unit_id": "PMC13325132-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC13325132-T1-07",
      "source_id": "PMC13325132-T1",
      "citation": "Sustainable Mizoroki–Heck Cross-Coupling Using a Pd(II)-Polymer as Precatalyst in 1‑Butanol",
      "doi": "10.1021/acsomega.6c01810",
      "pmcid": "PMC13325132",
      "locator": "T1, entry 7",
      "locator_zh": "表 1，entry 7（ACS Omega，2026）",
      "outcome": {
        "value": 88.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "o 10 mg).bYield determined by GC/MS with a calibration curve for | 表头/题注原文：Optimization of Reaction Conditions for MH Reaction between iodobenzene and Ethyl Acrylate Catalyzed by Pd/PECIm",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "Et3N",
        "solvent": "iPrOH/H2O",
        "temp_c": 90.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC13325132，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "ethyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC13325132",
        "unit_id": "PMC13325132-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC13325132-T1-08",
      "source_id": "PMC13325132-T1",
      "citation": "Sustainable Mizoroki–Heck Cross-Coupling Using a Pd(II)-Polymer as Precatalyst in 1‑Butanol",
      "doi": "10.1021/acsomega.6c01810",
      "pmcid": "PMC13325132",
      "locator": "T1, entry 8",
      "locator_zh": "表 1，entry 8（ACS Omega，2026）",
      "outcome": {
        "value": 87.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "o 10 mg).bYield determined by GC/MS with a calibration curve for | 表头/题注原文：Optimization of Reaction Conditions for MH Reaction between iodobenzene and Ethyl Acrylate Catalyzed by Pd/PECIm",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "Et3N",
        "solvent": "iPrOH/H2O",
        "temp_c": 90.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "aqueous",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC13325132，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "ethyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC13325132",
        "unit_id": "PMC13325132-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC13325132-T1-09",
      "source_id": "PMC13325132-T1",
      "citation": "Sustainable Mizoroki–Heck Cross-Coupling Using a Pd(II)-Polymer as Precatalyst in 1‑Butanol",
      "doi": "10.1021/acsomega.6c01810",
      "pmcid": "PMC13325132",
      "locator": "T1, entry 9",
      "locator_zh": "表 1，entry 9（ACS Omega，2026）",
      "outcome": {
        "value": 79.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "o 10 mg).bYield determined by GC/MS with a calibration curve for | 表头/题注原文：Optimization of Reaction Conditions for MH Reaction between iodobenzene and Ethyl Acrylate Catalyzed by Pd/PECIm",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "Et3N",
        "solvent": "EtOH",
        "temp_c": 80.0,
        "time_h": 24.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC13325132，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "ethyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC13325132",
        "unit_id": "PMC13325132-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC13325132-T1-10",
      "source_id": "PMC13325132-T1",
      "citation": "Sustainable Mizoroki–Heck Cross-Coupling Using a Pd(II)-Polymer as Precatalyst in 1‑Butanol",
      "doi": "10.1021/acsomega.6c01810",
      "pmcid": "PMC13325132",
      "locator": "T1, entry 10",
      "locator_zh": "表 1，entry 10（ACS Omega，2026）",
      "outcome": {
        "value": 95.0,
        "unit": "%",
        "outcome_type": "gc",
        "label_raw": "o 10 mg).bYield determined by GC/MS with a calibration curve for | 表头/题注原文：Optimization of Reaction Conditions for MH Reaction between iodobenzene and Ethyl Acrylate Catalyzed by Pd/PECIm",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "GC",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": null,
        "base": "Et3N",
        "solvent": "1-BuOH",
        "temp_c": 95.0,
        "time_h": 1.5,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC13325132，heck）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "iodobenzene",
        "alkene": "ethyl acrylate",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC13325132",
        "unit_id": "PMC13325132-T1",
        "reaction_class": "heck",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC5225227-T1-01",
      "source_id": "PMC5225227-T1",
      "citation": "A triazine-based Ni(II) PNP pincer complex as catalyst for Kumada–Corriu and Negishi cross-coupling reactions",
      "doi": "10.1007/s00706-016-1878-4",
      "pmcid": "PMC5225227",
      "locator": "T1, entry 1",
      "locator_zh": "表 1，entry 1（Monatshefte Fur Chemie，2016）",
      "outcome": {
        "value": 47.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "t r.t. for 6 h aYield of pure isolated product after column chromatography | 表头/题注原文：Catalyst screening for the Kumada–Corriu cross coupling",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Catalyst",
        "base": "PhMgBr",
        "solvent": "THF",
        "temp_c": null,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC5225227，negishi）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-Bromotoluene",
        "organometallic": "PhMgBr",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC5225227",
        "unit_id": "PMC5225227-T1",
        "reaction_class": "negishi",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC5225227-T1-02",
      "source_id": "PMC5225227-T1",
      "citation": "A triazine-based Ni(II) PNP pincer complex as catalyst for Kumada–Corriu and Negishi cross-coupling reactions",
      "doi": "10.1007/s00706-016-1878-4",
      "pmcid": "PMC5225227",
      "locator": "T1, entry 2",
      "locator_zh": "表 1，entry 2（Monatshefte Fur Chemie，2016）",
      "outcome": {
        "value": 91.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "t r.t. for 6 h aYield of pure isolated product after column chromatography | 表头/题注原文：Catalyst screening for the Kumada–Corriu cross coupling",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Catalyst",
        "base": "PhMgBr",
        "solvent": "THF",
        "temp_c": null,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC5225227，negishi）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-Bromotoluene",
        "organometallic": "PhMgBr",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC5225227",
        "unit_id": "PMC5225227-T1",
        "reaction_class": "negishi",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC5225227-T1-03",
      "source_id": "PMC5225227-T1",
      "citation": "A triazine-based Ni(II) PNP pincer complex as catalyst for Kumada–Corriu and Negishi cross-coupling reactions",
      "doi": "10.1007/s00706-016-1878-4",
      "pmcid": "PMC5225227",
      "locator": "T1, entry 3",
      "locator_zh": "表 1，entry 3（Monatshefte Fur Chemie，2016）",
      "outcome": {
        "value": 86.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "t r.t. for 6 h aYield of pure isolated product after column chromatography | 表头/题注原文：Catalyst screening for the Kumada–Corriu cross coupling",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Catalyst",
        "base": "PhMgBr",
        "solvent": "THF",
        "temp_c": null,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC5225227，negishi）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-Bromotoluene",
        "organometallic": "PhMgBr",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC5225227",
        "unit_id": "PMC5225227-T1",
        "reaction_class": "negishi",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    },
    {
      "record_id": "CORP-PMC5225227-T1-04",
      "source_id": "PMC5225227-T1",
      "citation": "A triazine-based Ni(II) PNP pincer complex as catalyst for Kumada–Corriu and Negishi cross-coupling reactions",
      "doi": "10.1007/s00706-016-1878-4",
      "pmcid": "PMC5225227",
      "locator": "T1, entry 4",
      "locator_zh": "表 1，entry 4（Monatshefte Fur Chemie，2016）",
      "outcome": {
        "value": 96.0,
        "unit": "%",
        "outcome_type": "isolated",
        "label_raw": "t r.t. for 6 h aYield of pure isolated product after column chromatography | 表头/题注原文：Catalyst screening for the Kumada–Corriu cross coupling",
        "note": "语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
      },
      "assay": {
        "method": "isolated (as stated in source)",
        "internal_standard": null,
        "quantification_basis": "product",
        "calibration_note": "原文未报告内标"
      },
      "conditions": {
        "catalyst": "Catalyst",
        "base": "PhMgBr",
        "solvent": "THF",
        "temp_c": null,
        "time_h": 6.0,
        "atmosphere": null,
        "scale_mmol": null,
        "equivalents": {}
      },
      "purification": null,
      "evidence_grade": "candidate",
      "verification": "unverified",
      "catalytic_profile": {
        "medium": "organic",
        "activation_mode": "thermal"
      },
      "evidence_note": "2026-10-31 从语料层（PMC5225227，negishi）晋升；规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。",
      "substrate": {
        "aryl_halide": "4-Bromotoluene",
        "organometallic": "PhMgBr",
        "product": null
      },
      "provenance": {
        "corpus_id": "PMC5225227",
        "unit_id": "PMC5225227-T1",
        "reaction_class": "negishi",
        "promoted_by": "scripts/promote_corpus.py",
        "merged_by": "scripts/merge_corpus_records.py",
        "promoted_on": "2026-10-31"
      }
    }
  ],
  "merge_note": "2026-10-28 并入 pool30 中 evidence_grade=a_confirmed 的记录：P11, P12, P13, P19a, P19b, P20, P24a, P24b, P24c, P35。仅已回原文核验者进入引擎；候选池其余条目仍留在 data/pool30.json。\n2026-10-31：从 literature corpus 晋升 96 条 candidate（11 张表）。全部 evidence_grade=candidate，未经 PDF 逐字核验，口径由表头/题注逐字支撑；晋升与拒绝原因见 data/incoming/corpus_promote_2026-10-31.json 与 docs/plan/2026-10-31-语料层晋升.md。"
}"""

# 语料索引（194 张表 / 114 篇，表级）。judgeable=false：只用于检索"库里有没有相关数据"。
CORPUS_INDEX = json.loads(r"""{"schema":"chemcritic.corpus_index.v1","generated":"2026-10-31","n_tables":194,"n_articles":114,"notice":"文献语料索引（表级）。judgeable=false：这里的数值只用于回答『库里有没有相关文献数据』，不得作为参照值参与可比性判定或差值计算。判定只认同仓库 data/records.json 里已核验的 entry。","tables":[{"t":"PMC5882074-T1","cls":"asym_hydrogenation","lab":"Table 1.","yr":"2018","jr":"Microbiology","doi":"10.1099/mic.0.000568","cap":"Substrate range of the DSM 795 FldZ 2-enoate reductase. Cell-free protein extracts of E. coli expressing fldZ were used to oxidise NADH in the presence of unsat","n":11,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC8456900-T1","cls":"asym_hydrogenation","lab":"Table 1","yr":"2021","jr":"Angewandte Chemie (International Ed. in English)","doi":"10.1002/anie.202107267","cap":"Optimization of the reaction conditions.[a]","n":9,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC8596914-T1","cls":"asym_hydrogenation","lab":"Table 1","yr":"2021","jr":"Angewandte Chemie (International Ed. in English)","doi":"10.1002/anie.202108503","cap":"Optimization of the reaction conditions.[a]","n":9,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC11668043-T1","cls":"asym_hydrogenation","lab":"Table 1","yr":"2024","jr":"Organic Letters","doi":"10.1021/acs.orglett.4c04076","cap":"Optimization of Pressure, Solvent, and Catalyst Loading Parametersa","n":11,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC12933482-T1","cls":"asym_hydrogenation","lab":"1","yr":"2025","jr":"Precision Chemistry","doi":"10.1021/prechem.5c00087","cap":"Reaction Condition Screening","n":7,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC12933482-T2","cls":"asym_hydrogenation","lab":"3","yr":"2025","jr":"Precision Chemistry","doi":"10.1021/prechem.5c00087","cap":"Optimization of the Double Hydrogenation Reaction Conditions","n":5,"tier":"B_partial","sub":[],"judgeable":false,"ymin":3.0,"ymax":5.0,"nnum":5},{"t":"PMC9299238-T1","cls":"borylation","lab":"Table 1","yr":"2021","jr":"Chemistry (Weinheim an Der Bergstrasse, Germany)","doi":"10.1002/chem.202102961","cap":"Catalyst (5 mol %) screening for the borylation of benzene with B2pin2.","n":14,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":97.0,"nnum":14},{"t":"PMC9299238-T2","cls":"borylation","lab":"Table 2","yr":"2021","jr":"Chemistry (Weinheim an Der Bergstrasse, Germany)","doi":"10.1002/chem.202102961","cap":"Arene and alkane borylation using [Rh(Ind)(SIPr)(COE)].","n":10,"tier":"B_partial","sub":[],"judgeable":false,"ymin":7.0,"ymax":81.0,"nnum":10},{"t":"PMC10366130-T1","cls":"borylation","lab":"Table 1","yr":"2023","jr":"Communications Chemistry","doi":"10.1038/s42004-023-00960-z","cap":"Optimization of reaction conditionsa.","n":20,"tier":"B_partial","sub":[],"judgeable":false,"ymin":15.0,"ymax":81.0,"nnum":12},{"t":"PMC10563123-T1","cls":"borylation","lab":"Table 1","yr":"2023","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.3c01534","cap":"Screening of Ligands for Directed Borylation Reaction with Substrate 3a","n":9,"tier":"B_partial","sub":[],"judgeable":false,"ymin":67.0,"ymax":94.0,"nnum":8},{"t":"PMC10563123-T2","cls":"borylation","lab":"Table 2","yr":"2023","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.3c01534","cap":"Cross-coupling Reactions with Borylated Atropisomers","n":8,"tier":"B_partial","sub":[],"judgeable":false,"ymin":10.0,"ymax":85.0,"nnum":8},{"t":"PMC12147128-T1","cls":"borylation","lab":"1","yr":"2025","jr":"Journal of the American Chemical Society","doi":"10.1021/jacs.4c17943","cap":"Optimization of the Reaction Conditions for the Miyaura Borylation Reaction","n":24,"tier":"B_partial","sub":[],"judgeable":false,"ymin":5.0,"ymax":94.0,"nnum":24},{"t":"PMC12293755-T1","cls":"borylation","lab":"Table 2","yr":"2025","jr":"Biomolecules","doi":"10.3390/biom15071014","cap":"Optimization of reaction conditions for compound 2.","n":9,"tier":"B_partial","sub":[],"judgeable":false,"ymin":50.8,"ymax":50.8,"nnum":1},{"t":"PMC12293755-T2","cls":"borylation","lab":"Table 3","yr":"2025","jr":"Biomolecules","doi":"10.3390/biom15071014","cap":"Optimization of reaction conditions for carboxyl group hydrolysis.","n":10,"tier":"B_partial","sub":[],"judgeable":false,"ymin":10.0,"ymax":10.0,"nnum":9},{"t":"PMC12007006-T1","cls":"borylation","lab":"Table 1","yr":"2025","jr":"Journal of the American Chemical Society","doi":"10.1021/jacs.4c18569","cap":"Catalytic Benzene Borylation by Organolanthanum Speciesa","n":12,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":40.0,"nnum":12},{"t":"PMC13320156-T1","cls":"borylation","lab":"Table 1","yr":"2026","jr":"Communications Chemistry","doi":"10.1038/s42004-026-02044-0","cap":"Polymeric Ir catalyzed C–H borylation under batch conditions","n":12,"tier":"B_partial","sub":[],"judgeable":false,"ymin":6.0,"ymax":74.0,"nnum":12},{"t":"PMC8949195-T1","cls":"buchwald_hartwig","lab":"Table 1","yr":"2022","jr":"Molecules","doi":"10.3390/molecules27061999","cap":"Screening of catalytic systems in the BHA reaction 1.","n":10,"tier":"A_ready","sub":[],"judgeable":false,"ymin":5.0,"ymax":97.0,"nnum":10},{"t":"PMC9301730-T1","cls":"buchwald_hartwig","lab":"Table 1","yr":"2022","jr":"ACS Omega","doi":"10.1021/acsomega.2c01092","cap":"Screening of Nickel and Palladium Catalysts for the Activator-Promoted Buchwald–Hartwig-Type Aminationa","n":11,"tier":"B_partial","sub":["1-chloro-4-iodobenzene","toluidine"],"judgeable":false,"ymin":0.0,"ymax":98.0,"nnum":19,"cal":"gc"},{"t":"PMC11684481-T1","cls":"buchwald_hartwig","lab":"Table 1","yr":"2024","jr":"ACS Omega","doi":"10.1021/acsomega.4c07563","cap":"Optimization of the Buchwald–Hartwig Double Amination Reaction of Phenoxazine (PO-H) with 1,4-Dibromobenzene, 9(a), Assisted by Microwave Irradiation","n":8,"tier":"B_partial","sub":[],"judgeable":false,"ymin":80.0,"ymax":93.0,"nnum":8},{"t":"PMC11085325-T1","cls":"buchwald_hartwig","lab":"Table 1","yr":"2024","jr":"Molecules","doi":"10.3390/molecules29092151","cap":"The long-wavelength absorption and emission bands, quantum yields, the frontier molecular orbitals (FMOs) energies a for compounds 4b, 5f, 6d, 6g, and 7a in DCM","n":5,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC12305527-T1","cls":"buchwald_hartwig","lab":"Table 3","yr":"2025","jr":"RSC Advances","doi":"10.1039/d5ra02824h","cap":"The yield, conversion and selectivity obtained for each reaction during the catalyst screening study","n":5,"tier":"B_partial","sub":[],"judgeable":false,"ymin":20.0,"ymax":97.0,"nnum":5,"cal":"gc"},{"t":"PMC12305527-T2","cls":"buchwald_hartwig","lab":"Table 4","yr":"2025","jr":"RSC Advances","doi":"10.1039/d5ra02824h","cap":"Evaluation of the optimal reaction parameters, including bases and temperaturea","n":9,"tier":"B_partial","sub":["4-bromotoluene","aniline"],"judgeable":false,"ymin":23.0,"ymax":97.0,"nnum":7,"cal":"gc"},{"t":"PMC12305527-T3","cls":"buchwald_hartwig","lab":"Table 6","yr":"2025","jr":"RSC Advances","doi":"10.1039/d5ra02824h","cap":"The yields, conversions, and selectivity obtained for each reaction during the catalyst screening study for Sonogashira coupling reactionsa","n":5,"tier":"B_partial","sub":["4-bromoacetophenone","phenylacetylene"],"judgeable":false,"ymin":30.0,"ymax":90.0,"nnum":5,"cal":"gc"},{"t":"PMC12333095-T1","cls":"buchwald_hartwig","lab":"1","yr":"2025","jr":"Chemical Reviews","doi":"10.1021/acs.chemrev.5c00088","cap":"Comparison of Pd/Phosphine and Pd/NHC Ligand Systems by Trudell","n":7,"tier":"C_reject","sub":[],"judgeable":false,"ymin":11.0,"ymax":67.0,"nnum":7},{"t":"PMC12333095-T2","cls":"buchwald_hartwig","lab":"2","yr":"2025","jr":"Chemical Reviews","doi":"10.1021/acs.chemrev.5c00088","cap":"Comparison between Different NHC–Pd Complexes in BHA reaction by Nechaev","n":14,"tier":"C_reject","sub":[],"judgeable":false,"ymin":93.0,"ymax":99.0,"nnum":3},{"t":"PMC12333095-T3","cls":"buchwald_hartwig","lab":"3","yr":"2025","jr":"Chemical Reviews","doi":"10.1021/acs.chemrev.5c00088","cap":"Comparison between Different NHCs in [Pd­(NHC)­(acac)­Cl] Complexes in BHA Reaction by Nolan","n":7,"tier":"C_reject","sub":[],"judgeable":false,"ymin":0.0,"ymax":98.0,"nnum":7},{"t":"PMC12333095-T4","cls":"buchwald_hartwig","lab":"4","yr":"2025","jr":"Chemical Reviews","doi":"10.1021/acs.chemrev.5c00088","cap":"Comparison between Different Pd–NHC Complexes in BHA Reaction by Bermeshev","n":10,"tier":"C_reject","sub":[],"judgeable":false,"ymin":0.0,"ymax":98.0,"nnum":10},{"t":"PMC9059666-T1","cls":"carbonylation","lab":"","yr":"2019","jr":"RSC Advances","doi":"10.1039/c8ra10212k","cap":"Reduction of nitroarenes by NaBH4 catalyzed by Pd–NHC-γ-Fe2O3-n-butyl-SO3H in aqueous media at ambient temperature","n":12,"tier":"B_partial","sub":[],"judgeable":false,"ymin":70.0,"ymax":99.0,"nnum":12},{"t":"PMC9059666-T2","cls":"carbonylation","lab":"","yr":"2019","jr":"RSC Advances","doi":"10.1039/c8ra10212k","cap":"One-pot reduction-Schiff base condensation of nitroarenes with arylaldehydes catalyzed by Pd–NHC-γ-Fe2O3-n-butyl-SO3H as a bifunctional heterogeneous catalysta","n":13,"tier":"B_partial","sub":[],"judgeable":false,"ymin":60.0,"ymax":98.0,"nnum":13},{"t":"PMC9059666-T3","cls":"carbonylation","lab":"","yr":"2019","jr":"RSC Advances","doi":"10.1039/c8ra10212k","cap":"Comparison of the catalytic activity of Pd–NHC-γ-Fe2O3-n-butyl-SO3H with some other reported catalysts for the reduction of nitroarenes, reduction-Schiff base c","n":16,"tier":"C_reject","sub":[],"judgeable":false,"ymin":50.0,"ymax":99.0,"nnum":16},{"t":"PMC7155033-T1","cls":"carbonylation","lab":"Table 1","yr":"2020","jr":"Journal of Labelled Compounds & Radiopharmaceuticals","doi":"10.1002/jlcr.3805","cap":"Investigation of solvent effects on the radiochemical yield of [11C]N‐benzylbenzamide","n":5,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC9964802-T1","cls":"carbonylation","lab":"Table 2","yr":"2023","jr":"Molecules","doi":"10.3390/molecules28041795","cap":"Model reaction of (A) with hexylamine a: conditions optimization.","n":15,"tier":"B_partial","sub":[],"judgeable":false,"ymin":42.0,"ymax":92.0,"nnum":15},{"t":"PMC9964802-T2","cls":"carbonylation","lab":"Table 3","yr":"2023","jr":"Molecules","doi":"10.3390/molecules28041795","cap":"FPHUs and PHUs synthesis from (A) or (B) with hexamethylenediamine a.","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":57.0,"ymax":82.0,"nnum":4},{"t":"PMC13564410-T1","cls":"carbonylation","lab":"1","yr":"2026","jr":"Journal of the American Chemical Society","doi":"10.1021/jacs.6c10525","cap":"One-Pot Cascade Reaction Utilizing Kraft Lignin as a Green CO Source for Aminocarbonylation ,","n":8,"tier":"B_partial","sub":[],"judgeable":false,"ymin":3.0,"ymax":68.0,"nnum":3},{"t":"PMC13200246-T1","cls":"carbonylation","lab":"1","yr":"2026","jr":"Organic Letters","doi":"10.1021/acs.orglett.6c01481","cap":"Optimization of the Reaction Conditions","n":11,"tier":"B_partial","sub":[],"judgeable":false,"ymin":13.0,"ymax":60.0,"nnum":8},{"t":"PMC8928494-T1","cls":"ch_activation","lab":"Table 1","yr":"2022","jr":"ACS Omega","doi":"10.1021/acsomega.1c06664","cap":"Relative Free Energy (kcal·mol–1) for the Four Different Isomers Calculated at Two Different Temperatures, 298.15 and 443.0 K, Respectively","n":4,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC8928494-T2","cls":"ch_activation","lab":"Table 2","yr":"2022","jr":"ACS Omega","doi":"10.1021/acsomega.1c06664","cap":"Catalytic Insertion Reaction of Diphenylethyne and 4-Octyne with 1a","n":14,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":15.0,"nnum":13},{"t":"PMC8928494-T3","cls":"ch_activation","lab":"Table 3","yr":"2022","jr":"ACS Omega","doi":"10.1021/acsomega.1c06664","cap":"C–H Activation-Based Annulations of 11 with Diphenylethyne and 4-Octyne to 12","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":59.0,"nnum":4},{"t":"PMC11731305-T1","cls":"ch_activation","lab":"Table 1","yr":"2024","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.4c02266","cap":"Optimization of the Reaction Conditions","n":11,"tier":"B_partial","sub":[],"judgeable":false,"ymin":40.0,"ymax":88.0,"nnum":10},{"t":"PMC11528444-T1","cls":"ch_activation","lab":"Table 1","yr":"2024","jr":"Journal of the American Chemical Society","doi":"10.1021/jacs.4c09621","cap":"Selected Optimization and Control Data for the Fe and Ni Decarboxylative Couplinga","n":12,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":90.0,"nnum":12},{"t":"PMC11811124-T1","cls":"ch_activation","lab":"Table 1","yr":"2025","jr":"Communications Chemistry","doi":"10.1038/s42004-025-01434-0","cap":"The overlooked reaction combinations using DMF","n":8,"tier":"B_partial","sub":[],"judgeable":false,"ymin":5.1,"ymax":59.8,"nnum":6},{"t":"PMC12261849-T1","cls":"ch_activation","lab":"Table 3","yr":"2025","jr":"Journal of Cheminformatics","doi":"10.1186/s13321-025-01043-y","cap":"Results for the models trained on three subsets selected from s1, s3, s7, and s9 in HTE datasets, evaluated on the held-out test set of the C-N cross-coupling d","n":6,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC13074239-T1","cls":"ch_activation","lab":"Table 6","yr":"2026","jr":"Molecules","doi":"10.3390/molecules31071176","cap":"Summary of Phenanthridinone Derivatives Synthesized via Substrate Scope Expansion.","n":13,"tier":"B_partial","sub":[],"judgeable":false,"ymin":51.0,"ymax":92.0,"nnum":13},{"t":"PMC13074239-T2","cls":"ch_activation","lab":"Table 7","yr":"2026","jr":"Molecules","doi":"10.3390/molecules31071176","cap":"Effect of Substituent Position on the Yields of Phenanthridinone Derivatives.","n":8,"tier":"B_partial","sub":[],"judgeable":false,"ymin":64.0,"ymax":86.0,"nnum":8},{"t":"PMC13569938-T1","cls":"ch_activation","lab":"TABLE 2","yr":"2026","jr":"Chemistry (Weinheim an Der Bergstrasse, Germany)","doi":"10.1002/chem.71118","cap":"Singlet oxygen quantum yields of MTF4PPP and MTPPP. a","n":7,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC6272186-T1","cls":"cuacc","lab":"Table 1","yr":"2015","jr":"Molecules","doi":"10.3390/molecules20022837","cap":"Screening of ball features.","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":10.0,"ymax":10.0,"nnum":2},{"t":"PMC6272186-T2","cls":"cuacc","lab":"Table 2","yr":"2015","jr":"Molecules","doi":"10.3390/molecules20022837","cap":"Scale-up of mechanochemical CuAAC.","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.2,"ymax":7.8,"nnum":3},{"t":"PMC6272186-T3","cls":"cuacc","lab":"Table 3","yr":"2015","jr":"Molecules","doi":"10.3390/molecules20022837","cap":"Synthetic results of mechanochemical CuAAC a.","n":12,"tier":"C_reject","sub":[],"judgeable":false,"ymin":81.0,"ymax":98.0,"nnum":12},{"t":"PMC6274337-T1","cls":"cuacc","lab":"Table 1","yr":"2016","jr":"Molecules","doi":"10.3390/molecules21121697","cap":"The effect of TBTA, TEA, and 2-picoline on oxidative homocoupling of phenylacetylene a.","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":57.0,"nnum":4},{"t":"PMC5365239-T1","cls":"cuacc","lab":"Table 2","yr":"2017","jr":"Chemical Science","doi":"10.1039/c6sc02297a","cap":"Comparison of CuAAC reaction yields on/in live OVCAR5 cells between HPG–proteins and the biotin-azide 9 in the presence of the ligand 1 vs. 3","n":9,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC8123656-T1","cls":"cuacc","lab":"Table 1","yr":"2021","jr":"Polymers","doi":"10.3390/polym13091403","cap":"Isolated yield and end-group conversion of the functionalized polyglycols.","n":9,"tier":"B_partial","sub":[],"judgeable":false,"ymin":96.6,"ymax":99.9,"nnum":8},{"t":"PMC12284613-T1","cls":"cuacc","lab":"Table 1","yr":"2025","jr":"Chemistry (Weinheim an Der Bergstrasse, Germany)","doi":"10.1002/chem.202501732","cap":"Results obtained in the different one‐pot double‐click approaches[ a ]","n":17,"tier":"B_partial","sub":[],"judgeable":false,"ymin":39.0,"ymax":65.0,"nnum":6},{"t":"PMC13296720-T1","cls":"cuacc","lab":"2","yr":"2026","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.5c02481","cap":"Summary of Key Information for the Developed Approaches of CuAAC Click-Mechanochemical Reactions","n":24,"tier":"B_partial","sub":[],"judgeable":false,"ymin":19.0,"ymax":94.0,"nnum":21},{"t":"PMC13435305-T1","cls":"cuacc","lab":"1","yr":"2026","jr":"Organic Letters","doi":"10.1021/acs.orglett.6c02540","cap":"Variations in the Reactant Stoichiometry and Experimental Conditions in the Synthesis of [2]-Rotaxane 5","n":6,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC7891567-T1","cls":"cycloaddition","lab":"Table 1","yr":"2020","jr":"Angewandte Chemie (International Ed. in English)","doi":"10.1002/anie.202009704","cap":"Optimization: photocyclization of 2‐acryloyl indole derivative.[a]","n":7,"tier":"A_ready","sub":[],"judgeable":false,"ymin":0.0,"ymax":95.0,"nnum":7},{"t":"PMC8419836-T1","cls":"cycloaddition","lab":"Table 1","yr":"2021","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.1c00778","cap":"Cycloaddition of β-Ketoester 1a with Aryl Azides 2a","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":80.0,"nnum":4},{"t":"PMC8419836-T2","cls":"cycloaddition","lab":"Table 2","yr":"2021","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.1c00778","cap":"Cycloaddition of β-Ketoesters and Aryl Azides to Give 5-Hydroxytriazolesa","n":13,"tier":"B_partial","sub":[],"judgeable":false,"ymin":53.0,"ymax":98.0,"nnum":10},{"t":"PMC8419836-T3","cls":"cycloaddition","lab":"Table 3","yr":"2021","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.1c00778","cap":"Synthesis of 5-Hydroxytriazoles: PTC-Mediated Cycloaddition of β-Ketoester 1b and Aryl Azides 4a","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":76.0,"ymax":95.0,"nnum":3},{"t":"PMC8419836-T4","cls":"cycloaddition","lab":"Table 4","yr":"2021","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.1c00778","cap":"Synthesis of Long-Chain 5-Hydroxytriazolesa","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":29.0,"ymax":79.0,"nnum":4},{"t":"PMC10386021-T1","cls":"cycloaddition","lab":"Table 1","yr":"2023","jr":"Molecules","doi":"10.3390/molecules28145372","cap":"Optimization of reaction conditions [a].","n":13,"tier":"B_partial","sub":[],"judgeable":false,"ymin":30.0,"ymax":96.0,"nnum":13},{"t":"PMC10386021-T2","cls":"cycloaddition","lab":"Table 2","yr":"2023","jr":"Molecules","doi":"10.3390/molecules28145372","cap":"Substrate scope investigation of metal-free diastereoselective 1,3-dipolar cycloaddition of N-2,2,2-trifluoroethylisatin ketimines 1a and diverse cyclopentene-1","n":13,"tier":"B_partial","sub":[],"judgeable":false,"ymin":69.0,"ymax":99.0,"nnum":13},{"t":"PMC11053432-T1","cls":"cycloaddition","lab":"Table 1","yr":"2024","jr":"Molecules","doi":"10.3390/molecules29081902","cap":"NMR yields for Diels–Alder reactions of vinylimimdazoles 1a–c with NPM as a function of time.","n":8,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC11382162-T1","cls":"cycloaddition","lab":"Table 2","yr":"2024","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.4c00843","cap":"ωB97X-D/6-31+G** Relative Gibbs Free Energies in kcal·mol–1, Computed at 298 K in Toluene, of the Stationary Points Involved in the Diels–Alder Reaction of 4-Al","n":16,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC13077698-T1","cls":"cycloaddition","lab":"1","yr":"2026","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.5c03172","cap":"Screening of Reaction Parameters for the Diels-Alder reaction/Decarboxylation/Aromatization Steps","n":14,"tier":"B_partial","sub":[],"judgeable":false,"ymin":22.0,"ymax":92.0,"nnum":16},{"t":"PMC13185131-T1","cls":"cycloaddition","lab":"1","yr":"2026","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.6c00313","cap":"Optimization of the Reaction Conditions for the Azo-Povarov Reaction","n":5,"tier":"B_partial","sub":[],"judgeable":false,"ymin":67.0,"ymax":98.0,"nnum":5},{"t":"PMC9086392-T1","cls":"heck","lab":"","yr":"2018","jr":"RSC Advances","doi":"10.1039/c8ra06623j","cap":"Optimization of reaction temperature for the model coupling reactiona","n":14,"tier":"B_partial","sub":["iodobenzene","methyl acrylate"],"judgeable":false,"blank_controls":1,"ymin":60.0,"ymax":96.0,"nnum":12,"cal":"isolated"},{"t":"PMC9086392-T2","cls":"heck","lab":"","yr":"2018","jr":"RSC Advances","doi":"10.1039/c8ra06623j","cap":"Mizoroki–Heck reaction coupling reaction of different aryl halides with olefinsa","n":12,"tier":"B_partial","sub":[],"judgeable":false,"sub_scope":true,"scope_note":"底物随行变化（Olefin、X、R1），表级不给底物对；需行级解析，本轮不入判定层","range_withheld":"底物随行变化，区间跨不同底物，不作为单一底物区间给出","cal":"isolated"},{"t":"PMC9086392-T3","cls":"heck","lab":"","yr":"2018","jr":"RSC Advances","doi":"10.1039/c8ra06623j","cap":"Comparative study of the performance of various catalytic systems in the coupling reaction of chlorobenzene, bromobenzene and iodobenzene with olefins","n":8,"tier":"B_partial","sub":[],"judgeable":false,"ymin":71.8,"ymax":96.9,"nnum":8},{"t":"PMC7511807-T1","cls":"heck","lab":"Table 1","yr":"2020","jr":"Heliyon","doi":"10.1016/j.heliyon.2020.e04946","cap":"Optimization of the reaction conditions.a","n":14,"tier":"B_partial","sub":["4-iodotoluene","styrene"],"judgeable":false,"ymin":61.0,"ymax":98.0,"nnum":14,"cal":"isolated"},{"t":"PMC7511807-T2","cls":"heck","lab":"Table 2","yr":"2020","jr":"Heliyon","doi":"10.1016/j.heliyon.2020.e04946","cap":"Heck reaction catalyst by Fe3O4@Si–NHC–Pda","n":20,"tier":"B_partial","sub":[],"judgeable":false,"sub_scope":true,"scope_note":"底物随行变化（R1、X、R2、Product），表级不给底物对；需行级解析，本轮不入判定层","range_withheld":"底物随行变化，区间跨不同底物，不作为单一底物区间给出","cal":"isolated"},{"t":"PMC7511807-T3","cls":"heck","lab":"Table 3","yr":"2020","jr":"Heliyon","doi":"10.1016/j.heliyon.2020.e04946","cap":"Comparison of catalytic activity for the Heck cross coupling reaction using Fe3O4@Si–NHC–Pd catalyst with those obtained by reported catalysts.a","n":6,"tier":"A_ready","sub":["Bromobenzene","styrene"],"judgeable":false,"ymin":16.0,"ymax":98.0,"nnum":6,"cal":"isolated"},{"t":"PMC8402173-T1","cls":"heck","lab":"Table 1","yr":"2021","jr":"Polymers","doi":"10.3390/polym13162717","cap":"Mizoroki–Heck reaction in water using the copolymers a.","n":9,"tier":"A_ready","sub":["iodobenzene","n-butyl acrylate"],"judgeable":false,"ymin":43.0,"ymax":99.0,"nnum":9,"cal":"gc"},{"t":"PMC8402173-T2","cls":"heck","lab":"Table 2","yr":"2021","jr":"Polymers","doi":"10.3390/polym13162717","cap":"Mizoroki–Heck reaction in water with 1 a.","n":5,"tier":"A_ready","sub":[],"judgeable":false,"ymin":46.0,"ymax":99.0,"nnum":5},{"t":"PMC8402173-T3","cls":"heck","lab":"Table 3","yr":"2021","jr":"Polymers","doi":"10.3390/polym13162717","cap":"Mizoroki–Heck reactions with various aryl halides and alkenes in water.","n":19,"tier":"B_partial","sub":[],"judgeable":false,"ymin":4.0,"ymax":99.0,"nnum":16,"cal":"nmr"},{"t":"PMC8402173-T4","cls":"heck","lab":"Table 5","yr":"2021","jr":"Polymers","doi":"10.3390/polym13162717","cap":"Copper-free Sonogashira coupling in water with Pd catalysts a.","n":13,"tier":"A_ready","sub":["4-iodoanisole","phenylacetylene"],"judgeable":false,"ymin":7.0,"ymax":99.0,"nnum":12,"cal":"gc"},{"t":"PMC8402173-T5","cls":"heck","lab":"Table 6","yr":"2021","jr":"Polymers","doi":"10.3390/polym13162717","cap":"Copper-free Sonogashira coupling in water with surfactants a.","n":7,"tier":"A_ready","sub":["4-iodoanisole","phenylacetylene"],"judgeable":false,"ymin":61.0,"ymax":86.0,"nnum":7,"cal":"gc"},{"t":"PMC8402173-T6","cls":"heck","lab":"Table 7","yr":"2021","jr":"Polymers","doi":"10.3390/polym13162717","cap":"Copper-free Sonogashira coupling in water using thermoresponsive copolymers a.","n":13,"tier":"A_ready","sub":[],"judgeable":false,"sub_scope":true,"scope_note":"底物随行变化（R），表级不给底物对；需行级解析，本轮不入判定层","range_withheld":"底物随行变化，区间跨不同底物，不作为单一底物区间给出","cal":"isolated"},{"t":"PMC9101052-T1","cls":"heck","lab":"TABLE 1","yr":"2022","jr":"Frontiers in Chemistry","doi":"10.3389/fchem.2022.872366","cap":"Mizoroki-Heck reaction (MH), 4-bromoanisol and acrylic acid as a model substrate. Catalyst screening.","n":15,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC9101052-T2","cls":"heck","lab":"TABLE 2","yr":"2022","jr":"Frontiers in Chemistry","doi":"10.3389/fchem.2022.872366","cap":"Mizoroki-Heck reaction of 4-bromoanisol (1a) and acrylic acid (2) in aqueous solution using best catalyst HRG-Py-Pd; Optimization for K3PO4, SDS, and reaction t","n":26,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC9101052-T3","cls":"heck","lab":"TABLE 3","yr":"2022","jr":"Frontiers in Chemistry","doi":"10.3389/fchem.2022.872366","cap":"Mizoroki-Heck reaction catalyzed by HRG-Py-Pd and Pd-NPs nanocatalysts, Substrate scope.","n":7,"tier":"B_partial","sub":["Aryl halides","Acrylic acid"],"judgeable":false,"ymin":83.0,"ymax":98.0,"nnum":11,"cal":"isolated"},{"t":"PMC9101052-T4","cls":"heck","lab":"TABLE 4","yr":"2022","jr":"Frontiers in Chemistry","doi":"10.3389/fchem.2022.872366","cap":"Comparison of the results obtained with the HRG-Py-Pd nanocatalyst for the Mizoroki-Heck reaction of 4-bromoanisol and acrylic acid in an aqueous solution with ","n":7,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC9476516-T1","cls":"heck","lab":"Table 1","yr":"2022","jr":"ACS Omega","doi":"10.1021/acsomega.2c04111","cap":"Optimization of Reaction Conditions for Intramolecular Mizoroki–Heck Spirocyclization of 6/8aa","n":15,"tier":"B_partial","sub":[],"judgeable":false,"ymin":64.0,"ymax":83.0,"nnum":2,"cal":"isolated"},{"t":"PMC11557397-T1","cls":"heck","lab":"TABLE 5","yr":"2024","jr":"Frontiers in Chemistry","doi":"10.3389/fchem.2024.1496234","cap":"Comparison of Present vs. Reported Methods for Cross-Coupling Reactions.","n":12,"tier":"C_reject","sub":[],"judgeable":false,"ymin":6.0,"ymax":86.0,"nnum":12},{"t":"PMC13325132-T1","cls":"heck","lab":"1","yr":"2026","jr":"ACS Omega","doi":"10.1021/acsomega.6c01810","cap":"Optimization of Reaction Conditions for MH Reaction between iodobenzene and Ethyl Acrylate Catalyzed by Pd/PECIm","n":12,"tier":"A_ready","sub":["iodobenzene","ethyl acrylate"],"judgeable":false,"blank_controls":2,"ymin":17.0,"ymax":95.0,"nnum":10,"cal":"gc"},{"t":"PMC13325132-T2","cls":"heck","lab":"2","yr":"2026","jr":"ACS Omega","doi":"10.1021/acsomega.6c01810","cap":"Scope of MH Coupling Reaction Catalyzed by Pd/PECIm","n":17,"tier":"A_ready","sub":[],"judgeable":false,"sub_scope":true,"scope_note":"底物随行变化（X），表级不给底物对；需行级解析，本轮不入判定层","range_withheld":"底物随行变化，区间跨不同底物，不作为单一底物区间给出","cal":"isolated"},{"t":"PMC8529890-T1","cls":"hydroamination","lab":"Table 1","yr":"2021","jr":"ACS Omega","doi":"10.1021/acsomega.1c04826","cap":"Calculated rDCV2 and MAEDCV of the Best Models for Each Yield Using Our Dataset","n":7,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC8529890-T2","cls":"hydroamination","lab":"Table 2","yr":"2021","jr":"ACS Omega","doi":"10.1021/acsomega.1c04826","cap":"Predicted and Experimental Yields for New Combinations of Substrates and Catalysts","n":4,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC8529890-T3","cls":"hydroamination","lab":"Table 4","yr":"2021","jr":"ACS Omega","doi":"10.1021/acsomega.1c04826","cap":"Estimated and Experimental Yields for Each Experimental Condition","n":21,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":36.0,"nnum":40},{"t":"PMC10731675-T1","cls":"hydroamination","lab":"TABLE 2","yr":"2023","jr":"Frontiers in Chemistry","doi":"10.3389/fchem.2023.1260726","cap":"Screening, promoted by 7b, of the silver salt co-catalyst for the hydroamination reaction of phenylacetylene with aniline.","n":4,"tier":"A_ready","sub":[],"judgeable":false,"ymin":15.0,"ymax":36.0,"nnum":3},{"t":"PMC10731675-T2","cls":"hydroamination","lab":"TABLE 3","yr":"2023","jr":"Frontiers in Chemistry","doi":"10.3389/fchem.2023.1260726","cap":"Screening of the solvent in the reaction of hydroamination promoted by 2b.","n":6,"tier":"A_ready","sub":[],"judgeable":false,"ymin":13.0,"ymax":70.0,"nnum":6},{"t":"PMC10731675-T3","cls":"hydroamination","lab":"TABLE 4","yr":"2023","jr":"Frontiers in Chemistry","doi":"10.3389/fchem.2023.1260726","cap":"Catalytic activity of NHC complexes in the hydroamination reaction of phenylacetylene with aniline.","n":13,"tier":"A_ready","sub":[],"judgeable":false,"ymin":10.0,"ymax":99.0,"nnum":13},{"t":"PMC10731675-T4","cls":"hydroamination","lab":"TABLE 6","yr":"2023","jr":"Frontiers in Chemistry","doi":"10.3389/fchem.2023.1260726","cap":"Comparison among percentage yield of hydroamination and calculated free energies and Au charges for intermediate d (d-H, d-iPr, d-Cl, d-NO2) and TS [d-e]≠ ([d-e","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":20.0,"ymax":99.0,"nnum":4},{"t":"PMC11720983-T1","cls":"hydroamination","lab":"Table 1","yr":"2024","jr":"Molecules","doi":"10.3390/molecules30010004","cap":"Optimization of the reaction conditions a.","n":16,"tier":"B_partial","sub":[],"judgeable":false,"ymin":2.0,"ymax":88.0,"nnum":15},{"t":"PMC11191698-T1","cls":"hydroamination","lab":"Table 1","yr":"2024","jr":"Journal of the American Chemical Society","doi":"10.1021/jacs.4c02319","cap":"Optimization of the Reaction Conditions for Monomer M1 to Obtain 1a","n":24,"tier":"B_partial","sub":[],"judgeable":false,"ymin":6.0,"ymax":99.0,"nnum":21},{"t":"PMC11191698-T2","cls":"hydroamination","lab":"Table 4","yr":"2024","jr":"Journal of the American Chemical Society","doi":"10.1021/jacs.4c02319","cap":"Photophysical Properties of Selected APCs, i.e., 1, 3, 10, 11, 15, and 16, Absorption and Emission Maximum Wavelengths, Lifetime (τ), Fluorescence Quantum Yield","n":14,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC5838540-T1","cls":"hydrogenation","lab":"Table 1","yr":"2018","jr":"Chemcatchem","doi":"10.1002/cctc.201701329","cap":"Overview of LA hydrogenation over Ru (5 wt. %) based catalyst in aqueous solution. C 0(LA), initial LA concentration; X(LA), conversion in %; S(1,2‐PDO), select","n":7,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC7186356-T1","cls":"hydrogenation","lab":"Table 1","yr":"2020","jr":"Frontiers in Chemistry","doi":"10.3389/fchem.2020.00221","cap":"Ruthenium-based heterogeneous catalysts on solid supports and water-dispersible catalytic nanoparticles for the hydrogenation of LA toward GVL.","n":26,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC9224514-T1","cls":"hydrogenation","lab":"Table 1","yr":"2022","jr":"International Journal of Molecular Sciences","doi":"10.3390/ijms23126535","cap":"Summary table for the physical and chemical properties of the magnetic nanoparticles prepared: mean particle sizes and the phase composition of the manganese fe","n":7,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC10870019-T1","cls":"hydrogenation","lab":"Table 1","yr":"2023","jr":"Advanced Science","doi":"10.1002/advs.202305271","cap":"Optimization of hydrogenation conditions of alkynes to olefins.","n":16,"tier":"B_partial","sub":[],"judgeable":false,"ymin":39.0,"ymax":93.0,"nnum":12},{"t":"PMC12177647-T1","cls":"hydrogenation","lab":"1","yr":"2025","jr":"ACS Omega","doi":"10.1021/acsomega.4c09211","cap":"Palladium Nanoparticle-Catalyzed Hydrogenation of Nitro Compound (1a): Conditions Screening","n":13,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":96.0,"nnum":12},{"t":"PMC13265633-T1","cls":"hydrogenation","lab":"TABLE 1","yr":"2026","jr":"Chemistry, an Asian Journal","doi":"10.1002/asia.70823","cap":"Oxidation of C‐2 alcohols 8 and 19.","n":12,"tier":"B_partial","sub":[],"judgeable":false,"ymin":32.0,"ymax":94.0,"nnum":6},{"t":"PMC6644540-T1","cls":"metathesis","lab":"Table 1","yr":"2018","jr":"ACS Omega","doi":"10.1021/acsomega.8b01642","cap":"Optimization of Reaction Conditionsa","n":24,"tier":"B_partial","sub":[],"judgeable":false,"ymin":4.0,"ymax":99.0,"nnum":12},{"t":"PMC6648348-T1","cls":"metathesis","lab":"Table 1","yr":"2019","jr":"ACS Omega","doi":"10.1021/acsomega.8b03027","cap":"Results of RCM/Isomerization in 2-Methyltetrahydrofuran at 80 °Ca","n":19,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC6648348-T2","cls":"metathesis","lab":"Table 2","yr":"2019","jr":"ACS Omega","doi":"10.1021/acsomega.8b03027","cap":"Results of RCM/Isomerization in Different Solvents at 80 °Ca","n":9,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC6648348-T3","cls":"metathesis","lab":"Table 3","yr":"2019","jr":"ACS Omega","doi":"10.1021/acsomega.8b03027","cap":"Results of RCM/Isomerization in 2-Methyltetrahydrofuran at 120 °Ca","n":14,"tier":"B_partial","sub":[],"judgeable":false,"ymin":80.0,"ymax":94.0,"nnum":5},{"t":"PMC6648348-T4","cls":"metathesis","lab":"Table 4","yr":"2019","jr":"ACS Omega","doi":"10.1021/acsomega.8b03027","cap":"Computational Estimates of the Difference in Gibbs Free Energies between Isomers and Their Corresponding Relative Ratios Calculated by the DFT and DLPNO-CCSD(T)","n":11,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC7670115-T1","cls":"metathesis","lab":"Table 1","yr":"2020","jr":"Beilstein Journal of Organic Chemistry","doi":"10.3762/bjoc.16.226","cap":"Optimization of the catalyst loading depending on compound 2 triple bond substitution (NMR yields)a.","n":7,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC7670115-T2","cls":"metathesis","lab":"Table 2","yr":"2020","jr":"Beilstein Journal of Organic Chemistry","doi":"10.3762/bjoc.16.226","cap":"RCEYM of oxaenediynes 2 (NMR yields).","n":13,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC8289337-T1","cls":"metathesis","lab":"Table 2","yr":"2021","jr":"Organometallics","doi":"10.1021/acs.organomet.1c00253","cap":"mRCM Performance of Iodide versus Chloride Catalysts","n":8,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC9820898-T1","cls":"metathesis","lab":"Table 2","yr":"2022","jr":"International Journal of Molecular Sciences","doi":"10.3390/ijms24010671","cap":"The solvent, the catalyst type, the conversion, and the turnover number for the ring-opening metathesis polymerization of norbornene (c0 = 106 mM) conducted in ","n":6,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC9820898-T2","cls":"metathesis","lab":"Table 3","yr":"2022","jr":"International Journal of Molecular Sciences","doi":"10.3390/ijms24010671","cap":"The solvent, the catalyst, rate constant, and the conversions after 20 min and 24 h in the ring-closing metathesis reactions of diethyl 2,2-diallylmalonate (c0 ","n":6,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC9820898-T3","cls":"metathesis","lab":"Table 4","yr":"2022","jr":"International Journal of Molecular Sciences","doi":"10.3390/ijms24010671","cap":"The solvent, the catalyst, rate constant, and the conversions after 20 min and 24 h of the isomerization of (Z)-but-2-ene-1,4-diyl diacetate (c0 = 55.2 mM) in b","n":6,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC9698981-T1","cls":"metathesis","lab":"Table 2","yr":"2022","jr":"Molecules","doi":"10.3390/molecules27227696","cap":"The optimization study [a].","n":10,"tier":"B_partial","sub":[],"judgeable":false,"ymin":5.0,"ymax":93.0,"nnum":20},{"t":"PMC5225227-T1","cls":"negishi","lab":"Table 1","yr":"2016","jr":"Monatshefte Fur Chemie","doi":"10.1007/s00706-016-1878-4","cap":"Catalyst screening for the Kumada–Corriu cross coupling","n":4,"tier":"B_partial","sub":["4-Bromotoluene","PhMgBr"],"judgeable":false,"ymin":47.0,"ymax":96.0,"nnum":4,"cal":"isolated"},{"t":"PMC5225227-T2","cls":"negishi","lab":"Table 2","yr":"2016","jr":"Monatshefte Fur Chemie","doi":"10.1007/s00706-016-1878-4","cap":"Catalyst screening for the Negishi cross coupling","n":4,"tier":"B_partial","sub":["4-Bromotoluene"],"judgeable":false,"ymin":52.0,"ymax":94.0,"nnum":4,"cal":"isolated"},{"t":"PMC5788718-T1","cls":"negishi","lab":"Table 1","yr":"2017","jr":"The Journal of antibiotics","doi":"10.1038/ja.2017.96","cap":"Temperature and solvent optimization studies for yield and selectivity in olefination reaction.","n":5,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC5788718-T2","cls":"negishi","lab":"Table 2","yr":"2017","jr":"The Journal of antibiotics","doi":"10.1038/ja.2017.96","cap":"Phosphonium ylide and base optimization studies for selectivity in olefination reaction.","n":9,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC6851999-T1","cls":"negishi","lab":"Table 1","yr":"2019","jr":"Chemistry (Weinheim an Der Bergstrasse, Germany)","doi":"10.1002/chem.201902255","cap":"Summary of 1H NMR signals of the Cp rings in C6D6 and the highest conversion [%] for the zirconacyclopentadienes 11 a–l synthesized by using the Rosenthal or Ne","n":14,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC9619403-T1","cls":"negishi","lab":"Table 1","yr":"2022","jr":"Journal of the American Chemical Society","doi":"10.1021/jacs.2c07517","cap":"Optimization of Negishi Cross-Coupling with 6a","n":5,"tier":"B_partial","sub":[],"judgeable":false,"ymin":42.0,"ymax":64.0,"nnum":3},{"t":"PMC12354725-T1","cls":"negishi","lab":"Table 2","yr":"2025","jr":"Scientific Reports","doi":"10.1038/s41598-025-14080-2","cap":"Training and test MAE and RMSE values of the MLP (9:9–20–9–1:1) and RBF (9:9–70–1:1) models for yields below and above 79%.","n":8,"tier":"B_partial","sub":[],"judgeable":false,"ymin":3.6,"ymax":79.0,"nnum":8},{"t":"PMC12354725-T2","cls":"negishi","lab":"Table 3","yr":"2025","jr":"Scientific Reports","doi":"10.1038/s41598-025-14080-2","cap":"Overview of ML models for predicting yields in cross-coupling reactions.","n":34,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC12287326-T1","cls":"negishi","lab":"Table 1","yr":"2025","jr":"Scientific Reports","doi":"10.1038/s41598-025-10180-1","cap":"Screening of cobalt(II) complexes as potential (pre)catalysts.","n":10,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC13569947-T1","cls":"negishi","lab":"TABLE 1","yr":"2026","jr":"Chemistry (Weinheim an Der Bergstrasse, Germany)","doi":"10.1002/chem.71177","cap":"Scope of alkyl iodides in the Negishi cross‐coupling of Ac‐Ala‐Trp(6‐Br)‐Gly‐NH2 (Scheme 2, see also Supporting Information).","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":55.0,"ymax":97.0,"nnum":4},{"t":"PMC13569947-T2","cls":"negishi","lab":"TABLE 2","yr":"2026","jr":"Chemistry (Weinheim an Der Bergstrasse, Germany)","doi":"10.1002/chem.71177","cap":"Negishi cross‐coupling of 7‐bromo‐l‐tryptophan containing pentapeptides with Nα‐Boc‐l‐3‐iodoalanine methyl ester in DMF to investigate the influence of differen","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":21.0,"ymax":29.0,"nnum":3},{"t":"PMC13569947-T3","cls":"negishi","lab":"TABLE 3","yr":"2026","jr":"Chemistry (Weinheim an Der Bergstrasse, Germany)","doi":"10.1002/chem.71177","cap":"Negishi cross‐coupling of bromo‐l‐tryptophan containing pentapeptides in DMSO: Scope of alkyl iodides, and influence of different amino acids side chains, and o","n":6,"tier":"B_partial","sub":[],"judgeable":false,"ymin":15.0,"ymax":53.0,"nnum":6},{"t":"PMC13569947-T4","cls":"negishi","lab":"TABLE 4","yr":"2026","jr":"Chemistry (Weinheim an Der Bergstrasse, Germany)","doi":"10.1002/chem.71177","cap":"Negishi cross‐coupling of Ac‐Leu‐Val‐Trp(6‐Br)‐Ala‐Gly‐NH2 with various alkyl iodides in mixtures of DMF/DMSO (Scheme 5, see also Supporting Information).","n":5,"tier":"B_partial","sub":[],"judgeable":false,"ymin":55.0,"ymax":90.0,"nnum":5},{"t":"PMC8397428-T1","cls":"olefination","lab":"Table 1","yr":"2021","jr":"Organic Letters","doi":"10.1021/acs.orglett.1c02099","cap":"Optimization of Reaction Conditionsa","n":8,"tier":"B_partial","sub":[],"judgeable":false,"ymin":3.0,"ymax":92.0,"nnum":6},{"t":"PMC8397428-T2","cls":"olefination","lab":"Table 2","yr":"2021","jr":"Organic Letters","doi":"10.1021/acs.orglett.1c02099","cap":"Preliminary Investigation of the Asymmetric Electrocyclization of Substrate 3ba","n":9,"tier":"B_partial","sub":[],"judgeable":false,"ymin":40.0,"ymax":90.0,"nnum":8},{"t":"PMC9964018-T1","cls":"olefination","lab":"Table 1","yr":"2023","jr":"Molecules","doi":"10.3390/molecules28041958","cap":"One-pot synthesis of unsaturated esters 25a–g [67].","n":7,"tier":"B_partial","sub":[],"judgeable":false,"ymin":21.0,"ymax":91.0,"nnum":7},{"t":"PMC10258914-T1","cls":"olefination","lab":"Table 1","yr":"2023","jr":"Current Organic Synthesis","doi":"10.2174/1570179420666221006113032","cap":"Preparation of diazioxalimides 2a-2h.","n":8,"tier":"B_partial","sub":[],"judgeable":false,"ymin":62.0,"ymax":82.0,"nnum":8},{"t":"PMC11110061-T1","cls":"olefination","lab":"Table 2","yr":"2024","jr":"Organic Process Research & Development","doi":"10.1021/acs.oprd.3c00414","cap":"Isolation of Michael Addition Product 7b and Wittig Intermediate 11 in Absence of Base","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":7.0,"ymax":42.0,"nnum":4},{"t":"PMC11110061-T2","cls":"olefination","lab":"Table 3","yr":"2024","jr":"Organic Process Research & Development","doi":"10.1021/acs.oprd.3c00414","cap":"DBU-Improved Formation of C-Glycosyl Compounds from 3 and 8","n":5,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.1,"ymax":0.1,"nnum":4},{"t":"PMC11110061-T3","cls":"olefination","lab":"Table 4","yr":"2024","jr":"Organic Process Research & Development","doi":"10.1021/acs.oprd.3c00414","cap":"Reactions of 13 and 15","n":5,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.1,"ymax":0.1,"nnum":4},{"t":"PMC10819551-T1","cls":"olefination","lab":"Table 1","yr":"2024","jr":"Molecules","doi":"10.3390/molecules29020501","cap":"Preparation of pyrrole derivatives.","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":11.0,"ymax":98.0,"nnum":4},{"t":"PMC10819551-T2","cls":"olefination","lab":"Table 2","yr":"2024","jr":"Molecules","doi":"10.3390/molecules29020501","cap":"Protection of indole and pyrrole derivatives.","n":10,"tier":"B_partial","sub":[],"judgeable":false,"ymin":62.0,"ymax":98.0,"nnum":10},{"t":"PMC10819551-T3","cls":"olefination","lab":"Table 3","yr":"2024","jr":"Molecules","doi":"10.3390/molecules29020501","cap":"Wittig olefination of heterocyclic ketones.","n":15,"tier":"B_partial","sub":[],"judgeable":false,"ymin":48.0,"ymax":97.0,"nnum":14},{"t":"PMC12522154-T1","cls":"olefination","lab":"Table 1","yr":"2025","jr":"Beilstein Journal of Organic Chemistry","doi":"10.3762/bjoc.21.157","cap":"Yields of compounds 5a–l.","n":14,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC12522154-T2","cls":"olefination","lab":"Table 2","yr":"2025","jr":"Beilstein Journal of Organic Chemistry","doi":"10.3762/bjoc.21.157","cap":"Yields of compounds 6a–d.","n":6,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC12522154-T3","cls":"olefination","lab":"Table 3","yr":"2025","jr":"Beilstein Journal of Organic Chemistry","doi":"10.3762/bjoc.21.157","cap":"Yields of compounds 8a,b,d,f–h and 9a–h.","n":16,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC12728619-T1","cls":"olefination","lab":"1","yr":"2025","jr":"JACS Au","doi":"10.1021/jacsau.5c01138","cap":"Reaction Condition Optimization","n":11,"tier":"B_partial","sub":[],"judgeable":false,"ymin":22.0,"ymax":80.0,"nnum":5},{"t":"PMC6941188-T1","cls":"oxidation","lab":"Table 2","yr":"2019","jr":"ACS Omega","doi":"10.1021/acsomega.9b03096","cap":"Optimization of Reaction Conditions for the Oxidation of Benzyl Alcohola","n":15,"tier":"B_partial","sub":[],"judgeable":false,"ymin":10.0,"ymax":99.0,"nnum":14},{"t":"PMC7914554-T1","cls":"oxidation","lab":"Table 1","yr":"2021","jr":"Polymers","doi":"10.3390/polym13040498","cap":"Homogeneous and heterogeneous reactions in ethanol a.","n":13,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC8735764-T1","cls":"oxidation","lab":"Table 1","yr":"2021","jr":"ACS Sustainable Chemistry & Engineering","doi":"10.1021/acssuschemeng.1c02605","cap":"Optimization of Atmospheric-Pressure Conditions Used for Co(II)/Mn(II) Catalyzed Oxidation of p-Cymene into p-MA, p-Toluic Acid, and Terephthalic Acid","n":8,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":60.0,"nnum":24},{"t":"PMC8735764-T2","cls":"oxidation","lab":"Table 2","yr":"2021","jr":"ACS Sustainable Chemistry & Engineering","doi":"10.1021/acssuschemeng.1c02605","cap":"Optimization of the Elevated-Pressure Oxidation Reaction of p-Cymene to TA","n":6,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":70.0,"nnum":6},{"t":"PMC8735764-T3","cls":"oxidation","lab":"Table 3","yr":"2021","jr":"ACS Sustainable Chemistry & Engineering","doi":"10.1021/acssuschemeng.1c02605","cap":"Single-Step Catalytic Aerobic Oxidation Conditions Used to Transform p-Cymene into TA","n":9,"tier":"B_partial","sub":[],"judgeable":false,"ymin":12.0,"ymax":67.0,"nnum":8},{"t":"PMC11547704-T1","cls":"oxidation","lab":"Table 4","yr":"2024","jr":"Molecules","doi":"10.3390/molecules29215114","cap":"Catalytic behavior of various catalysts for benzyl alcohol oxidation-condensation reactions.","n":11,"tier":"B_partial","sub":[],"judgeable":false,"ymin":89.3,"ymax":99.0,"nnum":11},{"t":"PMC11955942-T1","cls":"oxidation","lab":"Table 1","yr":"2025","jr":"ACS Applied Materials & Interfaces","doi":"10.1021/acsami.4c21206","cap":"Control Experiments for Thiol Oxidation to Disulfide","n":5,"tier":"B_partial","sub":[],"judgeable":false,"ymin":15.0,"ymax":70.0,"nnum":4},{"t":"PMC11955942-T2","cls":"oxidation","lab":"Table 2","yr":"2025","jr":"ACS Applied Materials & Interfaces","doi":"10.1021/acsami.4c21206","cap":"Photocatalytic Activity Comparison of Different Cu2O Crystals","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":54.0,"ymax":70.0,"nnum":4},{"t":"PMC11955942-T3","cls":"oxidation","lab":"Table 3","yr":"2025","jr":"ACS Applied Materials & Interfaces","doi":"10.1021/acsami.4c21206","cap":"Solvent Optimization Test","n":7,"tier":"B_partial","sub":[],"judgeable":false,"ymin":31.0,"ymax":86.0,"nnum":7},{"t":"PMC11955942-T4","cls":"oxidation","lab":"Table 5","yr":"2025","jr":"ACS Applied Materials & Interfaces","doi":"10.1021/acsami.4c21206","cap":"Active Species Trapping Experiments","n":5,"tier":"B_partial","sub":[],"judgeable":false,"ymin":45.0,"ymax":79.0,"nnum":5},{"t":"PMC12811662-T1","cls":"oxidation","lab":"Table 2","yr":"2025","jr":"Angewandte Chemie (International Ed. in English)","doi":"10.1002/anie.202523426","cap":"Yields and reaction rates for the formation of azoxybenzene 2 under different reaction conditions.","n":6,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC13363593-T1","cls":"oxidation","lab":"Table 1","yr":"2026","jr":"Nanomaterials","doi":"10.3390/nano16130835","cap":"Photocatalytic oxidation of thioether over different photocatalysts.","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":99.0,"nnum":4},{"t":"PMC6720061-T1","cls":"photoredox","lab":"Table 2","yr":"2019","jr":"Beilstein Journal of Organic Chemistry","doi":"10.3762/bjoc.15.201","cap":"Photoredox catalytic conversions of diethyl 2-bromomalonate (13) with 1-octanal (12) and yields of product 14 by different photoredox catalysts (X).","n":14,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC7252948-T1","cls":"photoredox","lab":"Table 1","yr":"2020","jr":"Journal of the American Chemical Society","doi":"10.1021/jacs.0c00629","cap":"Screening of Reaction Conditions for Thiocarboxylation of N-Tosylhydrazonea","n":8,"tier":"B_partial","sub":[],"judgeable":false,"ymin":80.0,"ymax":81.0,"nnum":2},{"t":"PMC7252948-T2","cls":"photoredox","lab":"Table 5","yr":"2020","jr":"Journal of the American Chemical Society","doi":"10.1021/jacs.0c00629","cap":"Screening of Reaction Conditions for the 1,1-Difluoroolefination of N-Tosylhydrazonea","n":6,"tier":"B_partial","sub":[],"judgeable":false,"ymin":70.0,"ymax":77.0,"nnum":3},{"t":"PMC8179447-T1","cls":"photoredox","lab":"","yr":"2021","jr":"Chemical Science","doi":"10.1039/d0sc06306a","cap":"Comparison of hydrodehalogenation yields for select substrates, using fac-Ir(ppy)3, Ir1, and Ir2 as photosensitizers","n":7,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC11020166-T1","cls":"photoredox","lab":"Table 1","yr":"2023","jr":"Organic Letters","doi":"10.1021/acs.orglett.3c02389","cap":"Optimization of the Reaction Conditions","n":21,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":64.0,"nnum":21},{"t":"PMC11020166-T2","cls":"photoredox","lab":"Table 2","yr":"2023","jr":"Organic Letters","doi":"10.1021/acs.orglett.3c02389","cap":"Deuterated Formamide Synthesis: Optimizationa","n":10,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":44.0,"nnum":10},{"t":"PMC12340967-T1","cls":"photoredox","lab":"1","yr":"2025","jr":"Organic Letters","doi":"10.1021/acs.orglett.5c02079","cap":"Optimization Studies for the Synthesis of Indolizine 2a","n":6,"tier":"B_partial","sub":[],"judgeable":false,"ymin":16.0,"ymax":96.0,"nnum":6},{"t":"PMC12856654-T1","cls":"photoredox","lab":"3","yr":"2025","jr":"ACS Physical Chemistry Au","doi":"10.1021/acsphyschemau.5c00103","cap":"MV•+ Production and Cage Escape Quantum Yields for the Ru–MV2+ Investigated Pairs","n":4,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC6017456-T1","cls":"sonogashira","lab":"Table 1","yr":"2018","jr":"Molecules : A Journal of Synthetic Chemistry and Natural Product Chemistry","doi":"10.3390/molecules23010154","cap":"Optimization of the base used in the Sonogashira reaction under typical reaction conditions.","n":6,"tier":"B_partial","sub":[],"judgeable":false,"ymin":19.0,"ymax":42.0,"nnum":6},{"t":"PMC9073152-T1","cls":"sonogashira","lab":"","yr":"2019","jr":"RSC Advances","doi":"10.1039/c9ra07393k","cap":"Frontier orbital energies of Mg-TEP-(X-DPP-Ar)4 in solution as determined by electrochemical measurement and in solids as determined by photoelectron yield spec","n":5,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC10254696-T1","cls":"sonogashira","lab":"Table 1","yr":"2023","jr":"Molecules","doi":"10.3390/molecules28114364","cap":"The optimization of the palladium-catalyzed cross-coupling of cinnamic triazine ester 1a and phenylacetylene 3a.","n":17,"tier":"B_partial","sub":["phenylacetylene"],"judgeable":false,"ymin":2.0,"ymax":96.0,"nnum":14,"cal":"gc"},{"t":"PMC11667721-T1","cls":"sonogashira","lab":"Table 1","yr":"2024","jr":"Organic Letters","doi":"10.1021/acs.orglett.4c03519","cap":"Optimization of the Reaction Conditionsa","n":19,"tier":"B_partial","sub":[],"judgeable":false,"ymin":39.0,"ymax":78.0,"nnum":12,"cal":"isolated"},{"t":"PMC13097261-T1","cls":"sonogashira","lab":"1","yr":"2026","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.6c00362","cap":"Screening of the Reaction Conditions for Sonogashira Alkynylation","n":19,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC13097261-T2","cls":"sonogashira","lab":"2","yr":"2026","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.6c00362","cap":"Coupling of Diverse o-Iodoaryl Carboxamides with Terminal Alkynes under Cu2O-Catalyzed Conditions","n":9,"tier":"B_partial","sub":[],"judgeable":false,"sub_scope":true,"scope_note":"底物随行变化（R1、R2），表级不给底物对；需行级解析，本轮不入判定层","range_withheld":"底物随行变化，区间跨不同底物，不作为单一底物区间给出","cal":"isolated"},{"t":"PMC13097261-T3","cls":"sonogashira","lab":"3","yr":"2026","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.6c00362","cap":"Substrate Scope for Cu2O-Mediated Synthesis of 3-Alkyl/Arylideneisoindolin-1-ones","n":21,"tier":"B_partial","sub":[],"judgeable":false,"sub_scope":true,"scope_note":"底物随行变化（R1、R2），表级不给底物对；需行级解析，本轮不入判定层","range_withheld":"底物随行变化，区间跨不同底物，不作为单一底物区间给出","cal":"isolated"},{"t":"PMC13097261-T4","cls":"sonogashira","lab":"4","yr":"2026","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.6c00362","cap":"TBAF-Mediated Removal of the N-Mesyl Group from 3-Alkylideneisoindolin-1-ones","n":9,"tier":"B_partial","sub":[],"judgeable":false,"sub_scope":true,"scope_note":"底物随行变化（R1），表级不给底物对；需行级解析，本轮不入判定层","range_withheld":"底物随行变化，区间跨不同底物，不作为单一底物区间给出","cal":"isolated"},{"t":"PMC13158381-T1","cls":"sonogashira","lab":"TABLE 3","yr":"2026","jr":"ChemistryOpen","doi":"10.1002/open.70217","cap":"A comparison of the Pd/NiFe2O4 catalyst and earlier catalysts used in the Sonogashira coupling between phenylacetylene and iodobenzene.","n":10,"tier":"C_reject","sub":[],"judgeable":false,"ymin":2.0,"ymax":99.0,"nnum":10},{"t":"PMC13158381-T2","cls":"sonogashira","lab":"TABLE 6","yr":"2026","jr":"ChemistryOpen","doi":"10.1002/open.70217","cap":"A comparison of the Fe3O4@SiO2@Cyt‐Ni/Cu catalyst and earlier catalysts utilized in the Sonogashira coupling between phenylacetylene and 4‐iodotoluene.","n":4,"tier":"C_reject","sub":[],"judgeable":false,"ymin":72.0,"ymax":99.0,"nnum":4},{"t":"PMC13158381-T3","cls":"sonogashira","lab":"TABLE 8","yr":"2026","jr":"ChemistryOpen","doi":"10.1002/open.70217","cap":"A comparison of the Fe3O4@SiO2@4‐ABPT/Cu–Ni catalyst and earlier reported catalysts utilized for the Sonogashira coupling between phenylacetylene and iodobenzen","n":6,"tier":"C_reject","sub":[],"judgeable":false,"ymin":88.0,"ymax":99.0,"nnum":6},{"t":"PMC13158381-T4","cls":"sonogashira","lab":"TABLE 9","yr":"2026","jr":"ChemistryOpen","doi":"10.1002/open.70217","cap":"A comparison of the FeSi/CL/Ni nanocatalyst and earlier catalysts utilized in the Sonogashira reaction.","n":7,"tier":"C_reject","sub":[],"judgeable":false,"ymin":80.0,"ymax":99.0,"nnum":7},{"t":"PMC13158381-T5","cls":"sonogashira","lab":"TABLE 11","yr":"2026","jr":"ChemistryOpen","doi":"10.1002/open.70217","cap":"The comparison of the catalytic activity of Pd@NiCo and Pd‐ppm level catalysts in the Sonogashira reaction of iodobenzene with phenylacetylene.","n":5,"tier":"C_reject","sub":[],"judgeable":false,"ymin":64.0,"ymax":95.0,"nnum":5},{"t":"PMC6222659-T1","cls":"suzuki","lab":"Table 1","yr":"2018","jr":"Molecules : A Journal of Synthetic Chemistry and Natural Product Chemistry","doi":"10.3390/molecules23102435","cap":"Suzuki–Miyaura cross-coupling of 4-bromoanisole with phenylboronic acid in methanol-induced by Pd-bpydc-Ln catalysts 1–3.","n":12,"tier":"B_partial","sub":[],"judgeable":false,"ymin":25.0,"ymax":95.0,"nnum":3},{"t":"PMC6222659-T2","cls":"suzuki","lab":"Table 3","yr":"2018","jr":"Molecules : A Journal of Synthetic Chemistry and Natural Product Chemistry","doi":"10.3390/molecules23102435","cap":"Suzuki–Miyaura cross-coupling of 4-bromoanisole with phenylboronic acid in the presence of Pd-bpydc-Nd (3) when various bases are employed a.","n":5,"tier":"B_partial","sub":[],"judgeable":false,"ymin":95.0,"ymax":95.0,"nnum":1},{"t":"PMC6222659-T3","cls":"suzuki","lab":"Table 4","yr":"2018","jr":"Molecules : A Journal of Synthetic Chemistry and Natural Product Chemistry","doi":"10.3390/molecules23102435","cap":"Suzuki–Miyaura cross-coupling of 4-bromobenzonitrile with phenylboronic acid in the presence of Pd-bpydc-Nd (3) when various bases are used a.","n":5,"tier":"B_partial","sub":[],"judgeable":false,"ymin":94.2,"ymax":94.2,"nnum":1},{"t":"PMC9079834-T1","cls":"suzuki","lab":"","yr":"2018","jr":"RSC Advances","doi":"10.1039/c8ra01819g","cap":"Main geometrical parameters of the obtained monoribbed-functionalized iron(ii) clathrochelates","n":15,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC9075783-T1","cls":"suzuki","lab":"","yr":"2019","jr":"RSC Advances","doi":"10.1039/c9ra07044c","cap":"Obtained yields for the Suzuki–Miyaura reactions","n":12,"tier":"B_partial","sub":[],"judgeable":false,"ymin":2.0,"ymax":93.0,"nnum":12,"cal":"isolated"},{"t":"PMC11199998-T1","cls":"suzuki","lab":"Table 1","yr":"2024","jr":"Advanced Science","doi":"10.1002/advs.202309706","cap":"Optimization of the reaction conditions.","n":12,"tier":"B_partial","sub":[],"judgeable":false,"ymin":10.0,"ymax":96.0,"nnum":12},{"t":"PMC11434556-T1","cls":"suzuki","lab":"Table 1","yr":"2024","jr":"Molecules","doi":"10.3390/molecules29184398","cap":"Tested conditions and yields of the model reaction.","n":10,"tier":"B_partial","sub":[],"judgeable":false,"ymin":70.0,"ymax":95.0,"nnum":5},{"t":"PMC11434556-T2","cls":"suzuki","lab":"Table 3","yr":"2024","jr":"Molecules","doi":"10.3390/molecules29184398","cap":"Tested conditions, reaction time and yield for the deacetylation reaction.","n":4,"tier":"B_partial","sub":[],"judgeable":false,"ymin":72.0,"ymax":92.0,"nnum":4},{"t":"PMC13010367-T1","cls":"suzuki","lab":"1","yr":"2026","jr":"Organic Process Research & Development","doi":"10.1021/acs.oprd.5c00298","cap":"High-Yielding Reactions (for 3) with Altering Organohalide Type (1a–1d) and Indazole Boronic Acid 2a in Four Solvents","n":6,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC13010305-T1","cls":"suzuki","lab":"3","yr":"2026","jr":"Organic Process Research & Development","doi":"10.1021/acs.oprd.5c00443","cap":"Efficiency Improvement of Pd Removal in 7-K","n":7,"tier":"B_partial","sub":[],"judgeable":false,"ymin":83.0,"ymax":95.0,"nnum":6},{"t":"PMC6641154-T1","cls":"transfer_hydrogenation","lab":"Table 1","yr":"2017","jr":"ACS Omega","doi":"10.1021/acsomega.7b00545","cap":"Ni-Catalyzed TH of BN with 1,4-Butanediol","n":5,"tier":"C_reject","sub":[],"judgeable":false},{"t":"PMC6207922-T1","cls":"transfer_hydrogenation","lab":"Table 1","yr":"2018","jr":"Angewandte Chemie (International Ed. in English)","doi":"10.1002/anie.201808800","cap":"Optimization of the FLP‐catalyzed transfer hydrogenation.[a]","n":16,"tier":"B_partial","sub":[],"judgeable":false,"ymin":2.0,"ymax":96.0,"nnum":16},{"t":"PMC7450644-T1","cls":"transfer_hydrogenation","lab":"Table 1","yr":"2020","jr":"ACS Omega","doi":"10.1021/acsomega.0c02957","cap":"Reaction Optimizationa","n":22,"tier":"B_partial","sub":[],"judgeable":false,"ymin":14.0,"ymax":85.0,"nnum":20},{"t":"PMC8697831-T1","cls":"transfer_hydrogenation","lab":"","yr":"2021","jr":"RSC Advances","doi":"10.1039/d1ra01102b","cap":"Examples for the selective hydrogenation CO bond of cinnamaldehyde","n":7,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":20.0,"nnum":6},{"t":"PMC8697831-T2","cls":"transfer_hydrogenation","lab":"","yr":"2021","jr":"RSC Advances","doi":"10.1039/d1ra01102b","cap":"Selective photocatalytic hydrogenation of unsaturated carbonylsa","n":20,"tier":"B_partial","sub":[],"judgeable":false,"ymin":61.0,"ymax":94.0,"nnum":20},{"t":"PMC11459429-T1","cls":"transfer_hydrogenation","lab":"Table 1","yr":"2024","jr":"The Journal of Organic Chemistry","doi":"10.1021/acs.joc.4c01701","cap":"Reaction Optimizationa","n":15,"tier":"B_partial","sub":[],"judgeable":false,"ymin":2.0,"ymax":98.0,"nnum":15},{"t":"PMC11279712-T1","cls":"transfer_hydrogenation","lab":"Table 1","yr":"2024","jr":"Molecules","doi":"10.3390/molecules29143420","cap":"Synthesis of the α-benzyl-β-ketoaldehydes (rac)-10 from chalcones (8).","n":10,"tier":"B_partial","sub":[],"judgeable":false,"ymin":10.0,"ymax":92.0,"nnum":13},{"t":"PMC11279712-T2","cls":"transfer_hydrogenation","lab":"Table 2","yr":"2024","jr":"Molecules","doi":"10.3390/molecules29143420","cap":"Optimization of the conditions for the ATH-DKR of (rac)-10j a.","n":17,"tier":"B_partial","sub":[],"judgeable":false,"ymin":29.0,"ymax":92.0,"nnum":7},{"t":"PMC12609509-T1","cls":"transfer_hydrogenation","lab":"Table 1","yr":"2025","jr":"Molecules","doi":"10.3390/molecules30214212","cap":"Vapor-phase transfer hydrogenation of n-butyloxirane with 2-propanol in the presence of magnesium oxide as the catalyst. Influence of donor–acceptor molar ratio","n":17,"tier":"B_partial","sub":[],"judgeable":false,"ymin":0.0,"ymax":20.0,"nnum":16},{"t":"PMC12609509-T2","cls":"transfer_hydrogenation","lab":"Table 2","yr":"2025","jr":"Molecules","doi":"10.3390/molecules30214212","cap":"Time-on-stream test of vapor-phase transfer hydrogenation of n-butyloxirane with 2-propanol in the presence of magnesium oxide as the catalyst. D/A = 6, TR = 67","n":8,"tier":"B_partial","sub":[],"judgeable":false,"ymin":1.0,"ymax":15.0,"nnum":8},{"t":"PMC12609509-T3","cls":"transfer_hydrogenation","lab":"Table 3","yr":"2025","jr":"Molecules","doi":"10.3390/molecules30214212","cap":"Liquid-phase transfer hydrogenation of phenyloxirane with 2-pentanol in the presence of magnesium oxide as the catalyst. D/A = 6, TR = 392 K.","n":6,"tier":"B_partial","sub":[],"judgeable":false,"ymin":1.0,"ymax":12.0,"nnum":6}]}""")

# 版本号（ENGINE_VERSION / RULES_VERSION / CONTRACT_VERSION）来自前方 version.py 小节，
# 此处不再重复写死——两处写死的版本号一定会漂，且包内模式读不到。


# ============================================================================
# 统一入口 · 平台契约（C-2 / C-3）
# ============================================================================

def record_from_payload(raw: Dict[str, Any]) -> Record:
    """用户输入 / 单条参照 → Record；缺 catalytic_profile 时查补丁表，再算档位。"""
    record = Record.from_dict(raw)
    record.input_audit = audit_input_payload(raw)
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
    else:
        subject, records = record_from_payload(data), None
    if subject.input_audit["status"] == "rejected":
        raise ValueError("输入来源审计失败：" + "；".join(subject.input_audit["errors"]))
    return subject, records


def build_payload(subject: Record, records: List[Record]) -> Dict[str, Any]:
    """与 engine/run_demo.py --json 完全同构的输出（这是验收 "diff 应为空" 的依据）。"""
    return {
        "subject": subject.record_id,
        "inputs_source": subject.inputs_source,
        "input_audit": subject.input_audit,
        "classes": class_summary(records),
        "coverage": coverage(records),
        "verdicts": [judge(subject, r).to_dict() for r in records],
        "placement": placement(subject, records),
        "leverage": leverage_report(subject, records),
    }


def evidence_payload(subject: Record, records: List[Record]) -> Dict[str, Any]:
    """补证杠杆：用户侧"补哪一条解锁最多" + 语料级"补哪几条记录口径可比对翻倍"。"""
    return {
        "engine": ENGINE_VERSION,
        "rules": RULES_VERSION,
        "subject": subject.record_id,
        "leverage": leverage_report(subject, records),
        "corpus": corpus_leverage(records),
    }


def list_records_payload(records: List[Record]) -> Dict[str, Any]:
    """知识库盘点：一次返回所有参照，无需用户实测值，也无需读取引擎源码。"""
    return {
        "engine": ENGINE_VERSION,
        "rules": RULES_VERSION,
        "count": len(records),
        "records": [
            {
                "record_id": r.record_id,
                "source_id": r.source_id,
                "citation": r.citation,
                "doi": r.doi,
                "locator": r.locator,
                "locator_zh": r.locator_zh,
                "value": r.outcome_value,
                "outcome_type": r.outcome_type,
                "label_raw": r.label_raw,
                "calibration": calibration_level(r),
                "tier": r.tier,
                # 档位为空不是缺字段，是"按受控词表算不出来"。必须显式说明原因，
                # 否则 agent 读到 null 会自己补一个档位——补就是编造（铁律 6）。
                "tier_status": "determined" if r.tier else "undetermined",
                "tier_problem": (r.tier_meta or {}).get("problems") or (
                    None if r.tier else ["未提供 catalytic_profile（催化剂物理形态/配体族缺失）"]),
                "substrate": r.substrate,
            }
            for r in records
        ],
    }


# 双侧 95% t 临界值（df 1–30，三位小数）。内嵌以摆脱 scipy 依赖（沙箱确定性）。
_T_CRIT_95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056,
    27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}


def replicates_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """重复实验统计（Track B3）：用户自报 n 次同口径重复 → 均值 / 样本标准差 / t 置信区间。

    边界（铁律 4 不受影响）：这里只描述**用户自己这组数据**的分散程度，
    不构成对文献值的区间推断，也不是"正常范围"；与文献的可比性判定
    仍必须走 --json 主入口。输出文案不得写成"预期/正常可达"。
    """
    import statistics as _stats
    raw = data.get("values") if isinstance(data, dict) else None
    values: List[float] = []
    for v in raw or []:
        try:
            values.append(float(v))
        except (TypeError, ValueError):
            continue
    if len(values) < 2:
        raise ValueError("重复实验统计至少需要 2 个数值（values 数组）")
    n = len(values)
    mean = _stats.fmean(values)
    if n == 2:
        # n=2 时样本标准差仍可算，但置信区间极宽——照实给并提示
        pass
    sd = _stats.stdev(values) if n >= 2 else None
    sem = sd / (n ** 0.5) if sd is not None else None
    df = n - 1
    t = _T_CRIT_95.get(df, 1.960)  # df>30 用正态近似，注明
    ci_half = t * sem if (sem is not None) else None
    return {
        "replicates": {
            "n": n,
            "values": values,
            "mean": round(mean, 2),
            "sd_sample": round(sd, 3) if sd is not None else None,
            "sem": round(sem, 3) if sem is not None else None,
            "t_crit_95": t,
            "df": df,
            "ci95": [round(mean - ci_half, 2), round(mean + ci_half, 2)]
            if ci_half is not None else None,
            "range": [min(values), max(values)],
        },
        "boundary": [
            "这是对用户自报重复数据的描述统计，不是与文献值的比较结论。",
            "置信区间不得写进『能不能比』的判定；判定仍以 --json 主入口证书为准。",
            "不得把区间表述为『正常范围』或『预期可达』。",
            "n<3 时区间极宽，建议如实告知样本量过小。",
        ],
    }


def judge_pair_payload(records: List[Record], id_a: str, id_b: str) -> Dict[str, Any]:
    """文献—文献两两判定：回答「这两条数值能不能放进同一句话」。

    与用户判定共用同一 judge()，无新增判定逻辑；输出双向证书
    （assay 措辞审计不对称时，两向结论可能不同，对外取较严者）。
    """
    by_id = {r.record_id: r for r in records}
    missing = [i for i in (id_a, id_b) if i not in by_id]
    if missing:
        raise ValueError("未知记录 ID：" + ", ".join(missing) + "（用 --list-records 查全部 ID）")
    fwd = judge(by_id[id_a], by_id[id_b])
    rev = judge(by_id[id_b], by_id[id_a])
    return {
        "engine": ENGINE_VERSION,
        "rules": RULES_VERSION,
        "pair": [id_a, id_b],
        "comparable": fwd.verdict == COMPARABLE and rev.verdict == COMPARABLE,
        "verdict_consistent": fwd.verdict == rev.verdict,
        "forward": fwd.to_dict(),
        "reverse": rev.to_dict(),
        "note": "forward=以第一条为判定主体；两向结论不一致时，对外以较严者为准，不合并。",
    }


def _row_to_payload(raw: Dict[str, Any], index: int) -> Dict[str, Any]:
    """整表审计的行归一：接受完整记录 JSON，或 extract.py 的候选行。"""
    row = dict(raw)
    if "outcome" not in row and (row.get("values_found") or row.get("row_raw")):
        # 结果列约定在表尾（Entry 编号在首列）；先试行尾单元格，退回 values_found 末个
        value = None
        raw_row = row.get("row_raw") or ""
        cells = [c.strip() for c in raw_row.split("|")] if raw_row else []
        for cell in reversed(cells):
            try:
                x = float(cell)
            except ValueError:
                continue
            if 0 < x <= 100:
                value = x
                break
        if value is None:
            for v in reversed(row.get("values_found") or []):
                try:
                    x = float(v)
                except (TypeError, ValueError):
                    continue
                if 0 < x <= 100:
                    value = x
                    break
        row = {
            "record_id": row.get("record_id"),
            "source_id": row.get("source_id"),
            "locator": row.get("locator"),
            "evidence_grade": row.get("evidence_grade") or "imported_unchecked",
            "outcome": {
                "value": value,
                "outcome_type": row.get("outcome_type") or "unknown",
                "label_raw": row.get("row_raw") or row.get("label_raw"),
            },
        }
    if not row.get("record_id"):
        row["record_id"] = f"ROW-{index:02d}"
    return row


def audit_table_payload(rows_raw: List[Dict[str, Any]], records: List[Record]) -> Dict[str, Any]:
    """整表审计：一张文献结果表里，哪些行能同框、卡在哪一维、对库有没有可比值。

    行的 evidence_grade 由操作者声明（extract 候选默认 imported_unchecked），
    引擎不猜：未核验行按 NEEDS_EVIDENCE 处理，这是诚实性的落点。
    """
    rows = [record_from_payload(_row_to_payload(r, i)) for i, r in enumerate(rows_raw, start=1)]
    if len(rows) < 2:
        raise ValueError("整表审计至少需要 2 行")

    within = []
    blocked_by: Dict[str, int] = {}
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            v = judge(rows[i], rows[j])
            within.append({
                "a": rows[i].record_id,
                "b": rows[j].record_id,
                "verdict": v.verdict,
                "blocking_dimension": v.blocking_dimension,
                "non_independent": v.non_independent,
            })
            if v.blocking_dimension:
                blocked_by[v.blocking_dimension] = blocked_by.get(v.blocking_dimension, 0) + 1

    vs_library = []
    for row in rows:
        comparable_refs = []
        checked = 0
        for r in records:
            if r.record_id == row.record_id:
                continue
            checked += 1
            v = judge(row, r)
            if v.verdict == COMPARABLE:
                comparable_refs.append({
                    "ref": r.record_id,
                    "value": r.outcome_value,
                    "outcome_type": r.outcome_type,
                    "citation": r.citation,
                    "locator_zh": r.locator_zh,
                })
        vs_library.append({
            "row": row.record_id,
            "value": row.outcome_value,
            "outcome_type": row.outcome_type,
            "evidence_grade": row.evidence_grade,
            "label_raw": row.label_raw,
            "library_size": checked,
            "comparable_count": len(comparable_refs),
            "comparable_refs": comparable_refs[:3],
        })

    return {
        "engine": ENGINE_VERSION,
        "rules": RULES_VERSION,
        "rows": [
            {
                "row": r.record_id,
                "value": r.outcome_value,
                "outcome_type": r.outcome_type,
                "evidence_grade": r.evidence_grade,
            }
            for r in rows
        ],
        "within_pairs": within,
        "vs_library": vs_library,
        "summary": {
            "row_count": len(rows),
            "within_pair_count": len(within),
            "within_comparable": sum(1 for w in within if w["verdict"] == COMPARABLE),
            "within_blocked": sum(1 for w in within if w["verdict"] == BLOCKED),
            "within_needs_evidence": sum(1 for w in within if w["verdict"] == NEEDS_EVIDENCE),
            "blocked_by": blocked_by,
            "rows_with_library_match": sum(1 for x in vs_library if x["comparable_count"]),
        },
    }


def brief_payload(subject: Record, records: List[Record]) -> Dict[str, Any]:
    """降级路径用：几百字节的证书摘要，可直接贴回会话（本地跑 + 会话编排）。"""
    pl = placement(subject, records)
    return {
        "engine": ENGINE_VERSION,
        "rules": RULES_VERSION,
        "subject": subject.record_id,
        "inputs_source": subject.inputs_source,
        "input_audit": subject.input_audit,
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
                "loc_zh": v.reference_meta.get("locator_zh"),
            }
            for v in (judge(subject, r) for r in records)
        ],
        "next_evidence": leverage_report(subject, records)["next"],
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
leverage_mod = SimpleNamespace(
    leverage_report=leverage_report,
    corpus_leverage=corpus_leverage,
    pair_profile=pair_profile,
    render=render,
    render_corpus=render_corpus,
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
    parser.add_argument("--records", metavar="PATH", help="替换参照记录集（默认使用内嵌记录）")
    parser.add_argument("--list-records", action="store_true", help="列出全部参照的引用、定位、口径与档位")
    parser.add_argument("--find-substrate", nargs="+", metavar="NAME",
                        help="按底物名精确盘点参照：条数/来源/反应类型/档位，并列出被排除的近名")
    parser.add_argument("--leverage", action="store_true", help="补证杠杆：补哪一条解锁最多（含语料级）")
    parser.add_argument("--judge-pair", nargs=2, metavar=("ID_A", "ID_B"),
                        help="文献—文献两两判定：这两条数值能不能放进同一句话")
    parser.add_argument("--audit-table", metavar="PATH",
                        help="整表审计：表格行 JSON（extract.py 候选或记录行）逐行判定同框与卡点")
    parser.add_argument("--replicates", metavar="PATH",
                        help="重复实验统计：n 次同口径重复 → 均值/标准差/95%% t 置信区间（描述用户数据，非判定）")
    parser.add_argument("--chemsense", nargs="+", metavar="NAME",
                        help="化学感知：底物名 → 规范 SMILES/官能团；≥2 名时两两判同")
    parser.add_argument("--corpus-query", nargs="+", metavar="KEYWORD",
                        help="检索文献语料索引（表级；judgeable=false，不参与判定）")
    parser.add_argument("--selftest", action="store_true", help="只跑 T1–T23c 验收矩阵")
    parser.add_argument("--version", action="store_true", help="打印引擎与规则版本")
    args = parser.parse_args(argv)

    if args.version:
        print(f"{ENGINE_VERSION} · rules={RULES_VERSION} · contract={CONTRACT_VERSION}")
        return 0

    records = load_records(args.records)

    if args.list_records:
        print(json.dumps(list_records_payload(records), ensure_ascii=False, indent=2))
        return 0

    if args.find_substrate:
        print(json.dumps(find_substrate_payload(records, list(args.find_substrate)),
                         ensure_ascii=False, indent=2))
        return 0

    if args.judge_pair:
        try:
            payload = judge_pair_payload(records, args.judge_pair[0], args.judge_pair[1])
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.audit_table:
        try:
            with open(args.audit_table, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"表格文件读取失败：{exc}", file=sys.stderr)
            return 2
        if isinstance(data, dict) and isinstance(data.get("candidates"), list):
            rows_raw = data["candidates"]      # extract.py --json 输出（单 PDF dict）
        elif isinstance(data, list) and data and isinstance(data[0], dict) \
                and isinstance(data[0].get("candidates"), list):
            rows_raw = [c for item in data for c in item["candidates"]]  # extract 多 PDF 数组
        elif isinstance(data, dict) and isinstance(data.get("rows"), list):
            rows_raw = data["rows"]
        elif isinstance(data, list):
            rows_raw = data
        else:
            print("表格格式不合法：需要行数组，或含 rows/candidates 数组的对象", file=sys.stderr)
            return 2
        try:
            payload = audit_table_payload(rows_raw, records)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.replicates:
        try:
            with open(args.replicates, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"重复实验文件读取失败：{exc}", file=sys.stderr)
            return 2
        try:
            payload = replicates_payload(data)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.chemsense:
        fn = globals().get("chemsense_payload")  # bundle：chemsense 段在同命名空间
        if fn is None:
            try:
                from engine.chemsense import chemsense_payload as fn  # 仓库内运行
            except Exception as exc:
                print(f"chemsense 不可用：{exc}", file=sys.stderr)
                return 2
        print(json.dumps(fn(list(args.chemsense)), ensure_ascii=False, indent=2))
        return 0

    if args.corpus_query:
        fn = globals().get("corpus_query")
        if fn is None:
            try:
                from engine.corpus_query import corpus_query as fn  # 仓库内运行
            except Exception as exc:
                print(f"语料索引不可用：{exc}", file=sys.stderr)
                return 2
        idx = globals().get("CORPUS_INDEX")
        if idx is None:
            try:
                import json as _json
                from pathlib import Path as _Path
                _p = _Path(__file__).resolve().parent.parent / "data" / "corpus_index.json"
                idx = _json.loads(_p.read_text(encoding="utf-8"))
            except Exception:
                idx = None
        print(json.dumps(fn(idx, list(args.corpus_query)), ensure_ascii=False, indent=2))
        return 0

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

    if args.leverage:
        print(json.dumps(evidence_payload(subject, records), ensure_ascii=False, indent=2))
        return 0

    if args.brief:
        print(json.dumps(brief_payload(subject, records), ensure_ascii=False, indent=2))
        return 0

    if args.json or args.input:
        print(json.dumps(build_payload(subject, records), ensure_ascii=False, indent=2))
        return 0

    screen_one(records)
    screen_two(records)
    screen_three(records)
    screen_five(records)
    screen_four(records)
    ok = run_selftest(records)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

