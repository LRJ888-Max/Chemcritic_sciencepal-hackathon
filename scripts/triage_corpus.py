"""语料库分诊：把 165 张「含收率列」的表分成三档，供人工/AI 逐条核验。

定位（2026-10-31，接 docs/plan 的「语料层 → 判定层晋升通道」）：
- 语料层（data/corpus/*.json）是**采集面**，1981 行里绝大多数不能直接当参照值。
- 判定层（data/records.json）只收「底物对明确 + 口径明确 + 条件可填」的 entry。
- 本脚本只做**分诊（triage）**，不做核验：它给出的 A/B/C 是「值得花人力去看」的排序，
  不是「可以入库」的结论。入库必须过一次人工/AI 逐条核验（promote_corpus.py）。

三档定义：
- A_ready     表内/脚注自带了底物与条件，且收率列口径可查 → 送核验
- B_partial   有收率列，但底物或条件要靠正文/scheme 补 → 送核验（核验时允许判 reject）
- C_reject    明确不是「一个反应实例的收率」：活性/TOF/ee/晶体学/二次文献转引 → 不送

用法：
    python scripts/triage_corpus.py                      # 默认读最新 corpus
    python scripts/triage_corpus.py --date 2026-10-31
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "data" / "corpus"
OUT_DIR = CORPUS_DIR

# 「一个反应实例的收率」的列名特征
YIELD_COL = re.compile(r"\byield|\b收率", re.I)
# 不是收率、但常与 yield 同表出现的干扰列
NON_YIELD_HINTS = re.compile(r"activity|TOF|ee\b|dr\b|conv\.|conversion|selectivity|"
                             r"bond length|Å|melting|δ\b|ppm", re.I)
COND_BLOCK = re.compile(r"reaction conditions|conditions\s*[:：]|条件", re.I)
QUANTITY = re.compile(r"\d+(?:\.\d+)?\s*(?:mmol|equiv|mol\s*%|mol%|°C|℃|h\b|min)", re.I)
# 化学名样实体（保守：足够长、含取代位号或常见骨架词）
SUBSTRATE_WORD = re.compile(
    r"benzene|aniline|phenol|pyridine|thiophene|furan|naphthalene|indole|"
    r"benzoic|boronic|halide|bromide|iodide|chloride|triflate|tosylate|"
    r"styrene|acryl|alkene|alkyne|acetylene|ketone|aldehyde|amine|phenoxazine|"
    r"carbazole|triazole|pyrimidine|quinoline|oxazole|imidazole",
    re.I,
)
# 二次文献/综述式转引的标志：caption 明写"他人工作对比"，且没有 reaction conditions 脚注
SECONDARY = re.compile(r"comparison (of|between)|by (trudell|nolan|nechaev|bermeshev)", re.I)


def _blob(table: Dict[str, Any]) -> str:
    parts = [
        " ".join(table.get("header") or []),
        table.get("caption") or "",
        table.get("footnote") or "",
        table.get("table_context") or "",
    ]
    return " ".join(parts)


def _yield_columns(header: List[str]) -> List[Tuple[int, str]]:
    return [(i, h) for i, h in enumerate(header) if YIELD_COL.search(h or "")]


def triage_table(article: Dict[str, Any], table: Dict[str, Any], idx: int) -> Dict[str, Any]:
    header = [str(h) for h in (table.get("header") or [])]
    caption = table.get("caption") or ""
    footnote = table.get("footnote") or ""
    context = table.get("table_context") or ""
    blob = _blob(table)
    rows = table.get("rows") or []

    reasons: List[str] = []
    ycols = _yield_columns(header)

    # —— 硬拒：表里根本没有收率列 ——
    if not ycols:
        return _unit(article, table, idx, "C_reject", ["表头无 yield 列"], rows, header)

    # —— 硬拒：整表是物理/谱学性质 ——
    if re.search(r"bond length|melting point|δ\b|chemical shift", blob, re.I):
        return _unit(article, table, idx, "C_reject", ["物性/谱学表，非收率"], rows, header)

    # —— 硬拒：只有 ee / dr / activity 而无独立 yield 语义 ——
    only_non_yield = all(NON_YIELD_HINTS.search(h) for _, h in ycols)
    if only_non_yield:
        return _unit(article, table, idx, "C_reject", ["收率列实为 activity/ee/选择性"], rows, header)

    has_cond = bool(COND_BLOCK.search(footnote) or COND_BLOCK.search(caption))
    has_qty = bool(QUANTITY.search(footnote) or QUANTITY.search(caption) or QUANTITY.search(context))
    subj_in_foot = len(set(m.group(0).lower() for m in SUBSTRATE_WORD.finditer(footnote + " " + caption)))
    secondary = bool(SECONDARY.search(caption)) and not has_cond

    if secondary:
        return _unit(article, table, idx, "C_reject",
                     ["caption 明写他人工作对比且无本土 reaction conditions（二次转引）"], rows, header)
    if not rows:
        return _unit(article, table, idx, "C_reject", ["空表"], rows, header)

    if has_cond and has_qty and subj_in_foot >= 2:
        return _unit(article, table, idx, "A_ready",
                     [f"yield 列 {len(ycols)} 个", "脚注/题注含 reaction conditions 与投料量",
                      f"题注内底物骨架词 {subj_in_foot} 类"], rows, header)
    if has_cond or subj_in_foot >= 2:
        reasons = []
        if not has_cond:
            reasons.append("无 reaction conditions 块")
        if subj_in_foot < 2:
            reasons.append("题注底物线索不足（需读正文/scheme）")
        return _unit(article, table, idx, "B_partial", reasons, rows, header)
    return _unit(article, table, idx, "B_partial",
                 ["仅有收率列，底物与条件都要从正文补"], rows, header)


def _unit(article, table, idx, verdict, reasons, rows, header) -> Dict[str, Any]:
    return {
        "unit_id": f"{article['corpus_id']}-T{idx + 1}",
        "corpus_id": article["corpus_id"],
        "reaction_class": article.get("reaction_class"),
        "label": table.get("label") or "",
        "verdict": verdict,
        "reasons": reasons,
        "row_count": len(rows),
        # 送核验时需要的原文材料（不加工、不改写）
        "material": {
            "header": header,
            "caption": table.get("caption") or "",
            "footnote": table.get("footnote") or "",
            "table_context": table.get("table_context") or "",
            "rows": rows,
            "article_title": article.get("title") or "",
            "journal": article.get("journal") or "",
            "year": article.get("year") or "",
            "doi": article.get("doi") or "",
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    args = ap.parse_args()

    src = CORPUS_DIR / (f"{args.date}.json" if args.date else "2026-10-31.json")
    payload = json.loads(src.read_text(encoding="utf-8"))

    units: List[Dict[str, Any]] = []
    for art in payload["articles"]:
        for i, tab in enumerate(art.get("tables") or []):
            units.append(triage_table(art, tab, i))

    verdicts = Counter(u["verdict"] for u in units)
    rows_by_verdict = Counter()
    for u in units:
        rows_by_verdict[u["verdict"]] += u["row_count"]
    classes_ready = Counter(u["reaction_class"] for u in units if u["verdict"] in ("A_ready", "B_partial"))

    out = {
        "schema": "chemcritic.triage.v1",
        "generated": payload.get("generated"),
        "source_corpus": src.name,
        "notice": (
            "分诊结果，非核验结论。A/B 档只是「值得花人力看」的排序；"
            "入库必须过 promote_corpus.py 的逐条核验，且 evidence_grade 最高只能到 candidate。"
        ),
        "counts": {
            "tables": len(units),
            "by_verdict": dict(verdicts),
            "rows_by_verdict": dict(rows_by_verdict),
            "ready_by_class": dict(classes_ready),
        },
        "units": units,
    }
    dst = OUT_DIR / f"triage_{payload.get('generated')}.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"表总数 {len(units)}")
    for v in ("A_ready", "B_partial", "C_reject"):
        print(f"  {v:10s} 表 {verdicts.get(v,0):3d}  行 {rows_by_verdict.get(v,0):4d}")
    print("A/B 档反应类型分布:", dict(classes_ready))
    print("→", dst.relative_to(ROOT))


if __name__ == "__main__":
    main()
