"""档位字段补丁表。

来源：`test_code/cases/library/suzuki_handoff_v1.json` 的 7 条记录 + SM-006（本项目新增）。
用途：C 的案例库尚未包含这 5 个字段，本表按 record_id 兜底，避免他必须先改 JSON 才能跑。
落地方式：长期应把 `catalytic_profile` 直接写进案例库每条记录（见 docs/integration/ 的合并指令）。

判据口径见 `docs/rules/分档定义-待签字.md` §2 / §4。
"""

from __future__ import annotations

from typing import Dict, Optional

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
