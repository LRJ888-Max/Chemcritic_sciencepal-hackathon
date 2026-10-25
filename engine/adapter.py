"""适配器：把 C 的案例库记录（`test_code/cases/library/*.json`）转成本引擎的 `Record`。

用途：C 的 `app/chemcritic/` 不必改自己的 JSON 结构，只需在调用判定前过一次本适配器。

字段映射（C 的结构 → 引擎字段）
--------------------------------------------------------------------
reaction.substrates[]        → substrate.aryl_halide / substrate.boronic_acid（按离去基与 boronic 关键字拆分）
reaction.target_product      → substrate.product
conditions.catalyst          → conditions.catalyst
conditions.catalyst_loading_mol_pct → conditions.catalyst_mol_pct
conditions.ligand            → conditions.ligand
conditions.base_name         → conditions.base            （base_status=unknown 时置 None）
conditions.base_equivalents  → conditions.base_eq
conditions.solvent           → conditions.solvent
conditions.temperature_C     → conditions.temp_c
conditions.time_h            → conditions.time_h
conditions.atmosphere        → conditions.atmosphere
conditions.scale_mmol        → conditions.scale_mmol
conditions.partner_equivalents → conditions.equivalents.boronic_acid
outcome.value_pct            → outcome_value
outcome.yield_type           → outcome_type
outcome.assay (free text)    → assay.method
outcome.internal_standard    → assay.internal_standard
record_id                    → record_id（并在 profiles.PROFILES 中查档位字段）

设计说明
--------------------------------------------------------------------
- `assay` 在 C 的结构里是自由文本，没有"定量基准"字段 → `quantification_basis` 一律置 None。
  因此 GC/NMR 记录会稳定落到 `NEEDS_EVIDENCE（维度 4）`——这是**预期的保守行为**，不是 bug。
- `base_status = absent` 与 `unknown` 必须区分：absent 记为字面量 `"none (absent)"`，
  unknown 记为 None。否则"未加碱"会被误判成"未记录碱"。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

try:  # 作为子包导入
    from . import profiles as profiles_mod
    from .schemas import Record
except ImportError:  # 独立脚本运行
    import profiles as profiles_mod  # type: ignore
    from schemas import Record  # type: ignore

_LEAVING_GROUP_HINTS = ("bromo", "iodo", "chloro", "triflate", "tosylate", "溴", "碘", "氯")


def split_substrates(substrates: Optional[List[str]]) -> Tuple[Optional[str], Optional[str]]:
    """把 substrate 列表拆成 (芳基卤, 硼酸)。无法判定时返回 (None, None)。"""
    if not substrates:
        return None, None
    items = [str(s).strip() for s in substrates if s]
    if len(items) == 1:
        return items[0], None
    halide = next((s for s in items if any(h in s.lower() for h in _LEAVING_GROUP_HINTS)), None)
    boronic = next((s for s in items if "boron" in s.lower() or "硼" in s), None)
    if halide and boronic and halide != boronic:
        return halide, boronic
    return None, None


def _base_value(conditions: Dict[str, Any]) -> Optional[str]:
    status = (conditions.get("base_status") or "").lower()
    name = conditions.get("base_name")
    if status == "absent":
        return "none (absent)"
    if status == "unknown" or not name:
        return None
    return name


def from_library(rec: Dict[str, Any], allow_profile_lookup: bool = True) -> Record:
    """把一条案例库记录转成引擎 `Record`。"""
    reaction = rec.get("reaction") or {}
    cond = rec.get("conditions") or {}
    out = rec.get("outcome") or {}

    aryl_halide, boronic_acid = split_substrates(reaction.get("substrates"))

    probe_assay = {"method": out.get("assay") or "", "internal_standard": out.get("internal_standard"),
                   "quantification_basis": None}

    verification = "unverified"
    if (rec.get("verification") or {}).get("original_source_verified_locally"):
        verification = "verified"
    elif out.get("interpretation_pending"):
        verification = "verified_with_caveat"

    profile = rec.get("catalytic_profile")
    if not profile and allow_profile_lookup:
        profile = profiles_mod.profile_for(rec.get("record_id"))

    return Record(
        record_id=rec.get("record_id") or "UNKNOWN",
        outcome_type=(out.get("yield_type") or "unknown").lower(),
        outcome_value=out.get("value_pct"),
        assay=probe_assay,
        conditions={
            "catalyst": cond.get("catalyst"),
            "catalyst_mol_pct": cond.get("catalyst_loading_mol_pct"),
            "ligand": cond.get("ligand"),
            "ligand_mol_pct": None,
            "base": _base_value(cond),
            "base_eq": cond.get("base_equivalents"),
            "solvent": cond.get("solvent"),
            "temp_c": cond.get("temperature_C"),
            "time_h": cond.get("time_h"),
            "atmosphere": cond.get("atmosphere"),
            "scale_mmol": cond.get("scale_mmol"),
            "equivalents": {"aryl_halide": 1.0, "boronic_acid": cond.get("partner_equivalents")},
            "concentration_m": None,
        },
        substrate={
            "aryl_halide": aryl_halide,
            "boronic_acid": boronic_acid,
            "product": reaction.get("target_product"),
        },
        tier=None,
        source_id=rec.get("source_id"),
        locator=rec.get("locator"),
        purification=None,
        verification=verification,
        catalytic_profile=profile or {},
    )


def from_library_file(payload: Dict[str, Any]) -> List[Record]:
    """整库转换。`payload` 为案例库 JSON 的顶层对象。"""
    return [from_library(r) for r in (payload.get("records") or [])]
