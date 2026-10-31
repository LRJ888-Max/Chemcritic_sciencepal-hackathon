"""多反应类型文献语料库采集（PMC 开放获取子集，只读，不改项目判定数据）。

用途：把"参照集"从单一 Suzuki 扩到多类反应，产出可回溯的语料库索引。
边界：本脚本只做**采集与定位**，不做任何判定；所有条目一律 imported_unchecked，
      不写入 data/records.json，不参与 ChemCritic 引擎的可比性判定。

输出：
  data/corpus/<YYYY-MM-DD>.json   每篇文献的元数据 + 命中表格（逐字行）
  data/corpus/<YYYY-MM-DD>.md     人读清单
用法：
  python scripts/harvest_corpus.py                # 全量采集
  python scripts/harvest_corpus.py --per-class 6  # 每类最多 6 篇
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT_DIR = os.path.join(ROOT, "data", "corpus")

CLASSES = {
    "suzuki": ["\"Suzuki-Miyaura\"", "\"Suzuki coupling\""],
    "buchwald_hartwig": ["\"Buchwald-Hartwig\""],
    "sonogashira": ["\"Sonogashira\""],
    "heck": ["\"Mizoroki-Heck\"", "\"Heck reaction\""],
    "negishi": ["\"Negishi\"", "\"Negishi coupling\""],
    "borylation": ["\"Miyaura borylation\"", "\"borylation\""],
    "ch_activation": ["\"C-H activation\"", "\"C-H arylation\""],
    "transfer_hydrogenation": ["\"transfer hydrogenation\""],
    "photoredox": ["\"photoredox\""],
    "cuacc": ["\"copper-catalyzed azide-alkyne\"", "\"click reaction\""],
    "metathesis": ["\"ring-closing metathesis\""],
    "asym_hydrogenation": ["\"enantioselective hydrogenation\"", "\"asymmetric hydrogenation\""],
    "carbonylation": ["\"carbonylation\""],
    "oxidation": ["\"aerobic oxidation\"", "\"epoxidation\""],
    "hydroamination": ["\"hydroamination\"", "\"C-N coupling\""],
    "cycloaddition": ["\"Diels-Alder\"", "\"cycloaddition\""],
    "olefination": ["\"Wittig\"", "\"olefination\""],
    "hydrogenation": ["\"catalytic hydrogenation\""],
}

YIELD_WORDS = re.compile(r"yield|收率|conversion|conv\.|ee\b|d\.r\.|selectivity", re.I)
NUM = re.compile(r"^\s*(?:[<>~]?\s*)?(\d{1,3}(?:\.\d+)?)\s*%?\s*$")


def fetch(url: str, tries: int = 3) -> bytes:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ChemCritic-corpus/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.2 * (i + 1))
    raise RuntimeError(f"fetch failed: {url} ({last})")


def esearch(term: str, retmax: int, mindate: int = 2015) -> list[str]:
    q = urllib.parse.quote(f"({term}) AND (yield) AND (open access[filter])")
    url = (f"{BASE}/esearch.fcgi?db=pmc&term={q}&retmax={retmax}"
           f"&sort=relevance&mindate={mindate}&maxdate={date.today().year}&datetype=pdat")
    text = fetch(url).decode("utf-8", "replace")
    return re.findall(r"<Id>(\d+)</Id>", text)


def text_of(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def table_rows(tw) -> tuple[list[str], list[list[str]], str]:
    header: list[str] = []
    rows: list[list[str]] = []
    for tr in tw.findall(".//tr"):
        cells = [text_of(c) for c in list(tr) if c.tag in ("td", "th")]
        if not cells:
            continue
        if not header and any(c.tag == "th" for c in list(tr)):
            header = cells
        else:
            rows.append(cells)
    foot = " ".join(text_of(f) for f in tw.findall(".//table-wrap-foot"))
    return header, rows, foot


def numeric_hits(rows: list[list[str]]) -> int:
    n = 0
    for r in rows:
        if any(NUM.match(c) for c in r):
            n += 1
    return n


def parse_article(xml_bytes: bytes, cls: str, pmcid: str) -> dict | None:
    try:
        tree = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    title = text_of(tree.find(".//article-title"))
    if not title:
        return None
    journal = text_of(tree.find(".//journal-title"))
    year = ""
    for tag in (".//article-meta//pub-date/year", ".//pub-date/year"):
        node = tree.find(tag)
        if node is not None and text_of(node):
            year = text_of(node)
            break
    doi = ""
    for aid in tree.findall(".//article-id"):
        if aid.get("pub-id-type") == "doi":
            doi = text_of(aid)
            break
    tables = []
    for tw in tree.findall(".//table-wrap"):
        label = text_of(tw.find("label"))
        caption = text_of(tw.find("caption"))
        header, rows, foot = table_rows(tw)
        head_txt = " ".join(header + [caption])
        n_hit = numeric_hits(rows)
        yield_like = bool(YIELD_WORDS.search(head_txt)) and n_hit >= 3 and len(rows) >= 4
        if not yield_like:
            continue
        tables.append({
            "label": label,
            "caption": caption[:300],
            "header": header[:12],
            "rows": [r[:12] for r in rows[:40]],
            "row_count": len(rows),
            "numeric_rows": n_hit,
            "footnote": foot[:400],
        })
    if not tables:
        return None
    return {
        "corpus_id": f"PMC{pmcid}",
        "reaction_class": cls,
        "title": title[:220],
        "journal": journal,
        "year": year,
        "doi": doi,
        "pmc_url": f"https://pmc.ncbi.nlm.nih.gov/articles/PMC{pmcid}/",
        "status": "imported_unchecked",
        "tables": tables,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=6)
    ap.add_argument("--min-years", type=int, default=2015)
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = date.today().isoformat()
    seen: dict[str, dict] = {}
    log = []

    for cls, terms in CLASSES.items():
        got = 0
        ids: list[str] = []
        for term in terms:
            try:
                ids.extend(esearch(term, args.per_class * 2, args.min_years))
            except Exception as exc:  # noqa: BLE001
                log.append(f"{cls}: esearch 失败 {term} -> {exc}")
            time.sleep(0.4)
        for pmcid in dict.fromkeys(ids):
            if got >= args.per_class:
                break
            if pmcid in seen and seen[pmcid]["reaction_class"] == cls:
                continue
            try:
                xml = fetch(f"{BASE}/efetch.fcgi?db=pmc&id={pmcid}&rettype=xml")
            except Exception as exc:  # noqa: BLE001
                log.append(f"{cls}: efetch 失败 {pmcid} -> {exc}")
                continue
            art = parse_article(xml, cls, pmcid)
            time.sleep(0.4)
            if not art:
                continue
            key = art["doi"] or art["corpus_id"]
            if key in seen:
                continue
            seen[key] = art
            got += 1
            print(f"  [{cls}] {art['corpus_id']} {art['year']} {art['journal'][:28]} "
                  f"tables={len(art['tables'])} :: {art['title'][:60]}", flush=True)
        print(f"{cls}: {got} 篇", flush=True)

    articles = list(seen.values())
    payload = {
        "schema": "chemcritic.corpus.v1",
        "generated": stamp,
        "source": "NCBI PMC open-access subset via E-utilities (efetch XML)",
        "notice": ("采集语料库：仅用于扩充参照集候选与对外展示覆盖面。全部条目为 "
                   "imported_unchecked，未逐条人工核验，不写入 data/records.json，"
                   "不参与引擎的可比性判定。任何引用必须回到 DOI 原文核对。"),
        "counts": {
            "articles": len(articles),
            "tables": sum(len(a["tables"]) for a in articles),
            "rows": sum(t["row_count"] for a in articles for t in a["tables"]),
            "by_class": {},
        },
        "log": log,
        "articles": sorted(articles, key=lambda a: (a["reaction_class"], a["year"])),
    }
    for a in articles:
        payload["counts"]["by_class"][a["reaction_class"]] = \
            payload["counts"]["by_class"].get(a["reaction_class"], 0) + 1

    path = os.path.join(OUT_DIR, f"{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    md = [f"# 语料库清单 {stamp}", "",
          f"- 文献 {payload['counts']['articles']} 篇 · 收率表 {payload['counts']['tables']} 张 · "
          f"数据行 {payload['counts']['rows']} 行",
          "- 来源：NCBI PMC 开放获取子集（E-utilities efetch XML）",
          "- 状态：全部 imported_unchecked，未人工核验，不参与判定", ""]
    for cls in sorted(payload["counts"]["by_class"]):
        md.append(f"\n## {cls}（{payload['counts']['by_class'][cls]} 篇）\n")
        md.append("| ID | 年 | 期刊 | DOI | 表 | 行 | 标题 |")
        md.append("|---|---|---|---|---|---|---|")
        for a in articles:
            if a["reaction_class"] != cls:
                continue
            t = a["tables"][0]
            md.append(f"| {a['corpus_id']} | {a['year']} | {a['journal'][:24]} | "
                      f"{a['doi']} | {len(a['tables'])} | {t['row_count']} | {a['title'][:52]} |")
    with open(os.path.join(OUT_DIR, f"{stamp}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    print(f"\n完成：{payload['counts']}")
    print(f"输出：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
