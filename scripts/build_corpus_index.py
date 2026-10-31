"""生成语料索引（data/corpus_index.json）：让 agent 在沙箱里也能"看见"语料库。

背景（用户 2026-10-31）：
    「不要只把 PDF 加到文档里就不管了……agent 才可以调用。」
语料库 194 张表 / 1981 行如果只在 data/corpus/ 里，SciencePal 沙箱里没有这个目录，
agent 就查不到。所以抽一层**表级索引**内嵌进单文件引擎：能回答"库里有没有关于这个
底物/这类反应的文献数据、报了多少、区间多少"，但不给逐行数值——逐行判定仍必须走
records.json 里已核验的条目。

铁律：索引里的每一个数字都标注 `judgeable: false`。索引是检索面，不是判定面。

用法：
    python scripts/build_corpus_index.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
TRIAGE = ROOT / "data" / "corpus" / "triage_2026-10-31.json"
PROMOTE = ROOT / "data" / "incoming" / "corpus_promote_2026-10-31.json"
OUT = ROOT / "data" / "corpus_index.json"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from promote_corpus import KIND_BY_PARTNER  # noqa: E402  底物键名→反应类型，与入库纠正同表
from quote_text import drop_trailing_marker, excerpt  # noqa: E402

YIELD_COL = re.compile(r"yield|收率", re.I)
NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def _nums(cells: List[str]) -> List[float]:
    out = []
    for c in cells:
        m = NUM.search(str(c).replace(" ", ""))
        if not m:
            continue
        try:
            v = float(m.group(0).replace(",", "."))
        except ValueError:
            continue
        if 0 <= v <= 100:
            out.append(v)
    return out


def main() -> None:
    triage = json.loads(TRIAGE.read_text(encoding="utf-8"))
    promote = {t["unit_id"]: t for t in json.loads(PROMOTE.read_text(encoding="utf-8"))["tables"]}

    tables: List[Dict[str, Any]] = []
    for u in triage["units"]:
        m = u["material"]
        header = m["header"]
        ycols = [i for i, h in enumerate(header) if YIELD_COL.search(h or "")]
        p = promote.get(u["unit_id"], {})
        parsed = p.get("parsed") or {}
        blanks = set(parsed.get("blank_controls") or [])
        # 空白对照的定位是 "T1, entry 14"，行首只有 "14"；两种写法都要认
        blank_ids = set()
        for b in blanks:
            blank_ids.add(str(b).strip())
            blank_ids.add(str(b).split("entry")[-1].strip())
        scope = bool(parsed.get("substrate_in_rows"))

        vals: List[float] = []
        for row in m["rows"]:
            if row and str(row[0]).strip() in blank_ids:
                continue  # 空白/阴性对照不进区间，否则下界被拉到 0
            for i in ycols:
                if i < len(row):
                    vals += _nums([row[i]])

        # 范围表的底物随行走，表级给一个"底物对"就是错的（PMC9086392-T2：
        # 12 行 = 4 种烯烃 × 3 种卤，脚注却只写了一句模板条件）。
        sub = [] if scope else [x for x in (parsed.get("aryl_halide"), parsed.get("partner")) if x]
        # 表级反应类型：底物键名 > 文章级标题关键词（与入库时 promote 的纠正同一条原则）。
        # 反例 PMC12305527-T3：题注明写 "Sonogashira coupling"、底物 4-bromoacetophenone +
        # phenylacetylene，但文章里还有一张 C–N 表，文章级分类把整篇都标成 buchwald_hartwig，
        # 于是这张表在 --find-substrate 里被算进 Buchwald 组。
        cls_article = u.get("reaction_class")
        cls_from_data = KIND_BY_PARTNER.get(str(parsed.get("partner_field") or ""))
        entry = {
            "t": u["unit_id"],
            "cls": cls_from_data or cls_article,
            "lab": (u.get("label") or "").strip(),
            "yr": m.get("year"),
            "jr": m.get("journal"),
            "doi": m.get("doi"),
            "cap": excerpt(drop_trailing_marker(m.get("caption"), m.get("footnote")), 160),
            "n": u["row_count"],
            "tier": u["verdict"],
            "sub": sub,
            "judgeable": False,
        }
        if cls_from_data and cls_article and cls_from_data != cls_article:
            # 保留文章级原标签：搜索 "buchwald" 时这张表仍该被找到，只是不该被算进 Buchwald 组
            entry["cls_article"] = cls_article
            entry["cls_conflict"] = True
        if scope:
            entry["sub_scope"] = True
            entry["scope_note"] = "底物随行变化（" + "、".join(parsed.get("scope_columns") or []) + \
                                  "），表级不给底物对；需行级解析，本轮不入判定层"
        if blanks:
            entry["blank_controls"] = len(blanks)
        if vals and not scope:
            entry["ymin"] = round(min(vals), 1)
            entry["ymax"] = round(max(vals), 1)
            entry["nnum"] = len(vals)
        elif vals and scope:
            # 跨不同底物的区间看着像"文献分散度"，其实是不同反应拼起来的一个范围
            entry["range_withheld"] = "底物随行变化，区间跨不同底物，不作为单一底物区间给出"
        if p:
            entry["cal"] = (p.get("caliber") or {}).get("outcome_type")
        tables.append(entry)

    payload = {
        "schema": "chemcritic.corpus_index.v1",
        "generated": triage.get("generated"),
        "n_tables": len(tables),
        "n_articles": len({t["t"].split("-")[0] for t in tables}),
        "notice": (
            "文献语料索引（表级）。judgeable=false：这里的数值只用于回答"
            "『库里有没有相关文献数据』，不得作为参照值参与可比性判定或差值计算。"
            "判定只认同仓库 data/records.json 里已核验的 entry。"
        ),
        "tables": tables,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"表 {len(tables)}，文章 {payload['n_articles']}，产物 {kb:.0f} KB → {OUT.relative_to(ROOT)}")
    with_vals = sum(1 for t in tables if t.get("nnum"))
    print(f"含可用数值的表 {with_vals}，judgeable 全部为 false")


if __name__ == "__main__":
    main()
