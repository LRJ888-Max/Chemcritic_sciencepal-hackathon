"""化学感知层（chemsense）：RDKit 驱动的底物同一性与分类。

定位（对应 2026-10-30 开发单 Track B1）：
- **同一性**：别名表（name_aliases）兜不住的写法变体（如 "p-bromoanisole" vs
  "1-bromo-4-methoxybenzene"），通过规范 SMILES 判同。别名表是白名单，
  漏一个别名就误判成"不同底物"；SMILES 判同是化学事实，不靠枚举。
- **分类**：电子效应基团（EDG/EWG）、杂环、卤素种类——供后续把"同底物"从
  字符串相等升级为"同类底物"做准备（当前只用同一性，分类先作为能力暴露）。

设计约束：
1. **可降级**：沙箱可能没装 rdkit。所有入口在 import 失败时返回 None / False，
   引擎行为回落到别名表判同——化学感知是增强，不是依赖。
2. **不推断**：本模块只回答"这两个名字是不是同一个化合物"和"它有哪些官能团"，
   不输出任何可比性结论。可比性判定仍然只属于 gates。
3. 词表映射手工维护（没有 OPSIN 的沙箱里解析任意 IUPAC 名不现实），
   映射表本身进入代码评审；不在表里的名字返回 None，绝不猜。

用法：
    python engine/chemsense.py "4-bromoanisole" "p-bromoanisole"
    python engine/chemsense.py --classify "3-bromothiophene"
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

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
    t = str(text or "").strip()
    return bool(t) and " " not in t and bool(_SMILES_HINT.search(t))

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


if __name__ == "__main__":
    import json
    import sys
    args = [a for a in sys.argv[1:] if a != "--classify"]
    if not args:
        print("用法: python engine/chemsense.py <name1> [name2 ...]  （≥2 名时输出两两判同）")
        sys.exit(2)
    print(json.dumps(chemsense_payload(args), ensure_ascii=False, indent=2))
