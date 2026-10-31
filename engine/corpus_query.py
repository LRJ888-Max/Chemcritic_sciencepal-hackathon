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

try:  # 作为子包导入
    from .placement import _chem_same, _reaction_kind
    from .schemas import Record
    from .version import ENGINE_VERSION
except ImportError:  # 独立脚本运行
    from placement import _chem_same, _reaction_kind  # type: ignore
    from schemas import Record  # type: ignore
    from version import ENGINE_VERSION  # type: ignore

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

