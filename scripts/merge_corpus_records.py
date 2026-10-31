"""把晋升候选合并进 data/records.json（判定层）。

与 promote_corpus.py 的分工：
- promote_corpus.py：语料层 → 候选（规则抽取，可能抽错，落 data/incoming/）
- 本脚本：候选 → 判定层（逐条过 schema 与 gates 的同款口径检查，不过就拒，带原因）

为什么不能直接 append：
records.json 里每一条都会真的参与 verdict。一条口径对不上、底物名字带残渣的记录，
不会"只是数据脏"，它会变成一次错误的 COMPARABLE。所以这里的拒绝率是特性，不是 bug。

用法：
    python scripts/merge_corpus_records.py --dry
    python scripts/merge_corpus_records.py --write
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))

from gates import label_supported  # noqa: E402  入库门槛直接复用判定门槛，避免两处词表漂移
from schemas import Record  # noqa: E402

RECORDS = ROOT / "data" / "records.json"
INCOMING = ROOT / "data" / "incoming"
SRC = INCOMING / "corpus_promote_2026-10-31.json"

ALLOWED = {"isolated", "gc", "nmr", "conversion"}


def _profile(cat: Optional[str], base: Optional[str], solvent: Optional[str],
             reaction_class: str) -> Dict[str, Any]:
    """催化体系五字段。判不准的键一律不写（tier 会退化成未定，而不是假档位）。"""
    blob = f"{cat or ''}".lower()
    prof: Dict[str, Any] = {}
    if not blob.strip() or blob in ("none", "no catalyst"):
        prof["catalyst_form"] = "none"
    elif any(k in blob for k in ("nanoparticle", "np", "magnetic", "fe3o4", "pd–c", "pd-c")):
        prof["catalyst_form"] = "nanoparticle"
    elif any(k in blob for k in ("@", "mof", "sio2", "supported", "resin", "polymer")):
        prof["catalyst_form"] = "supported"
    elif any(k in blob for k in ("pd(oac)2", "pdcl2", "pd(acac)2", "ni(acac)2", "pd/c")):
        prof["catalyst_form"] = "in_situ_salt"
    elif "(" in blob or "complex" in blob or "nhc" in blob:
        prof["catalyst_form"] = "molecular_complex"

    if any(k in blob for k in ("pph3", "phosphine", "sphos", "xphos", "dppp", "dppf", "xantphos", "binap")):
        prof["ligand_family"] = "phosphine"
    elif any(k in blob for k in ("nhc", "imes", "ipr", "sipr")):
        prof["ligand_family"] = "non_phosphine"
    elif prof.get("catalyst_form") in ("none", "in_situ_salt", "nanoparticle", "supported"):
        # 原位盐 / 负载 / 纳米颗粒且文中没提配体 → 无外加配体。
        # 不猜的情形只有分子配合物（Pd(PPh3)4 的配体写在括号里，抽不到就是抽不到）。
        prof["ligand_family"] = "none"
    if "ligand_family" in prof:
        prof["support"] = "solid_support" if prof.get("catalyst_form") in ("supported", "nanoparticle") else "none"
    if solvent:
        s = solvent.lower()
        prof["medium"] = "aqueous" if any(k in s for k in ("h2o", "water", "水")) else "organic"
    prof["activation_mode"] = "thermal"
    return prof


def _assay(outcome_type: str, std: Optional[str]) -> Dict[str, Any]:
    method = {"isolated": "isolated (as stated in source)", "gc": "GC", "nmr": "1H NMR",
              "conversion": "conversion"}.get(outcome_type, "unspecified")
    return {
        "method": method,
        "internal_standard": std,
        "quantification_basis": "product",
        "calibration_note": ("原文未报告内标" if not std else f"原文报告：{std}"),
    }


def _locator_zh(table: Dict[str, Any], entry: Dict[str, Any]) -> str:
    """中文定位。v8 行 99/191 要求用户文案引用文献一律用 locator_zh——
    缺了它，agent 只能退回英文 locator 或自己编一个，两者都违反铁律。"""
    tag = table["unit_id"].split("-")[-1]           # T1 → 表 1
    num = tag.lstrip("T") or "?"
    s = table["source"]
    row_no = entry["locator"].split("entry")[-1].strip()
    head = f"表 {num}，entry {row_no}"
    tail = "，".join(str(x) for x in (s.get("journal"), s.get("year")) if x)
    return f"{head}（{tail}）" if tail else head


def build_record(table: Dict[str, Any], entry: Dict[str, Any], seq: int) -> Dict[str, Any]:
    p = table["parsed"]
    s = table["source"]
    cal = table["caliber"]
    partner_field = p["partner_field"] or "partner"

    substrate = {
        "aryl_halide": p["aryl_halide"],
        partner_field: p["partner"],
        "product": None,  # 语料层的表里通常只给产物编号，不给名字；留空不猜
    }
    cat = entry.get("catalyst_raw") or p.get("catalyst")
    if cat and re.fullmatch(r"[\d.,\s]+", str(cat).strip()):
        # 单元格里是 mol%（"0.20"），催化剂名在表头 —— 别把用量当催化剂名
        hdr = table.get("header") or []
        cat = next((h for h in hdr if re.search(r"catalyst|\[Pd\]|\[Ni\]|pd/|cat\.", h, re.I)), None)
    conditions = {
        "catalyst": cat,
        "base": p.get("base"),
        "solvent": p.get("solvent"),
        "temp_c": p.get("temp_c"),
        "time_h": p.get("time_h"),
        "atmosphere": None,
        "scale_mmol": None,
        "equivalents": {k: v for k, v, u in [(n, q, un) for n, q, un in p.get("feeds", []) if un == "equiv"]},
    }
    return {
        "record_id": f"CORP-{table['corpus_id']}-{table['unit_id'].split('-')[-1]}-{seq:02d}",
        "source_id": table["unit_id"],
        "citation": s.get("citation"),
        "doi": s.get("doi"),
        "pmcid": s.get("pmcid"),
        "locator": entry["locator"],
        "locator_zh": _locator_zh(table, entry),
        "outcome": {
            "value": entry["value"],
            "unit": "%",
            "outcome_type": cal["outcome_type"],
            "label_raw": f'{cal["quote"]} | 表头/题注原文：{s.get("caption", "")[:120]}',
            "note": ("语料层晋升条目：数值与口径均取自 efetch 表格文本，未对照 PDF 图证逐字复核"
                     + ("；" + "；".join(entry["notes"]) if entry.get("notes") else "")),
        },
        "assay": _assay(cal["outcome_type"], p.get("internal_standard")),
        "conditions": conditions,
        "purification": None,
        "evidence_grade": "candidate",
        "verification": "unverified",
        "catalytic_profile": _profile(cat, p.get("base"), p.get("solvent"),
                                      table["reaction_class"]),
        "evidence_note": (f"2026-10-31 从语料层（{table['corpus_id']}，{table['reaction_class']}）晋升；"
                          "规则抽取，未经 PDF 逐字核验；evidence_grade 上限 candidate。"),
        "substrate": substrate,
        "provenance": {
            "corpus_id": table["corpus_id"],
            "unit_id": table["unit_id"],
            "reaction_class": table["reaction_class"],
            "promoted_by": "scripts/promote_corpus.py",
            "merged_by": "scripts/merge_corpus_records.py",
            "promoted_on": str(date.today()),
        },
    }


def check(rec: Dict[str, Any], seen: set) -> Tuple[bool, str]:
    if rec["record_id"] in seen:
        return False, "record_id 重复"
    sub = {k: v for k, v in rec["substrate"].items() if k != "product" and v}
    if len(sub) < 2:
        return False, "底物不足两个（gate 1 无法判定）"
    val = rec["outcome"]["value"]
    if not isinstance(val, (int, float)) or not (0 <= float(val) <= 100):
        return False, f"数值越界：{val}"
    t = rec["outcome"]["outcome_type"]
    if t not in ALLOWED:
        return False, f"口径不在受控词表：{t}"
    probe = Record(record_id=rec["record_id"], outcome_type=t,
                   outcome_value=val, label_raw=rec["outcome"]["label_raw"])
    if not label_supported(probe):
        return False, f"label_raw 不支撑口径 {t}（gates.label_supported 同款判定）"
    return True, ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(SRC))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--reset", action="store_true",
                    help="先删掉上次合并进来的 CORP-* 条目再重跑（幂等重入）")
    args = ap.parse_args()

    data = json.loads(RECORDS.read_text(encoding="utf-8"))
    existing = data["records"]
    if args.reset:
        existing = [r for r in existing if not str(r.get("record_id", "")).startswith("CORP-")]
    seen = {r["record_id"] for r in existing}

    promote = json.loads(Path(args.src).read_text(encoding="utf-8"))
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, str]] = []
    for table in promote["tables"]:
        p = table["parsed"]
        if p.get("generic_substrate") or not p.get("aryl_halide") or not p.get("partner"):
            rejected.append({"unit": table["unit_id"], "reason": "底物为通称或缺失，需读行内结构"})
            continue
        for i, e in enumerate(table["entries"], 1):
            rec = build_record(table, e, i)
            ok, why = check(rec, seen)
            if ok:
                seen.add(rec["record_id"])
                accepted.append(rec)
            else:
                rejected.append({"unit": table["unit_id"], "reason": f"{rec['locator']}: {why}"})

    print(f"现有 {len(existing)} 条 + 接受 {len(accepted)} 条 = {len(existing) + len(accepted)} 条")
    print(f"拒绝 {len(rejected)} 条")
    for r in rejected:
        print("  ✗", r["unit"], r["reason"])

    if args.write:
        merged = existing + accepted
        data["records"] = merged
        base_note = (data.get("merge_note") or "").split("\n2026-10-31：从 literature corpus")[0]
        data["merge_note"] = (
            base_note
            + f"\n2026-10-31：从 literature corpus 晋升 {len(accepted)} 条 candidate（"
              f"{len({a['source_id'] for a in accepted})} 张表）。全部 evidence_grade=candidate，"
              "未经 PDF 逐字核验，口径由表头/题注逐字支撑；晋升与拒绝原因见 "
              "data/incoming/corpus_promote_2026-10-31.json 与 docs/plan/2026-10-31-语料层晋升.md。"
        ).strip()
        RECORDS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print("→", RECORDS.relative_to(ROOT))


if __name__ == "__main__":
    main()
