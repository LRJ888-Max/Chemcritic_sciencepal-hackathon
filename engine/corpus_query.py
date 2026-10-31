"""语料索引检索：让"库里有 194 张表"这件事对 agent 变成可调用的能力。

定位（2026-10-31）：
- 这一层回答的是「文献语料里有没有相关数据、报了多少、区间多少」。
- 它**不回答**「这两个数值能不能比」——旁边永远带 `judgeable: false`。
- 判定只认同仓库 records.json 里已核验的 entry；索引里的数值是检索线索，不是参照值。

为什么要这层：语料 1981 行如果只在 data/corpus/ 里，SciencePal 沙箱里没有那个目录，
agent 看不到，"知识库"就只是仓库里的一堆文件。索引内嵌进单文件引擎后才真正可查。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
