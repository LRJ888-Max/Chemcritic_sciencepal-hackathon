"""档位判定：受控词表 → 规则 → 主判 / 备选（确定性、可审计）。

设计要点（对应 `docs/rules/分档定义-待签字.md` §4）：
1. 判定只吃受控词表字段，不吃自由文本，因此可复现。
2. 多条规则命中且结论不同 → 强制 `borderline`，禁止"取第一条"静默通过。
3. 主判优先级 = 物理形态 > 配体族 > 介质。理由：活性物种是不是分离相，
   比"有没有配体"更能解释收率差异与非均相行为（可回收性、烧结、浸出）。
4. 档的不确定性不阻断流程，由 placement.sensitivity() 做双档敏感性分析。

⚠️ 本文件的规则与阈值**由技术侧代签**（2026-10-25），A 可随时推翻重定。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
    "activation_mode": ["thermal", "mechanochemical", "microwave"],
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
