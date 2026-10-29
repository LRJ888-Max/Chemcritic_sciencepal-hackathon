"""从 PMC 开放获取全文中收集「某底物 + 苯硼酸」的 entry 级候选记录。

用法：
    python scripts/harvest_pmc_candidates.py --substrate 4-bromoanisole --out src_pmc/hits.json

设计原则：
1. 只做检索与定位，不做判定：输出保留原文措辞，口径（isolated/GC/NMR/conversion）
   一律不猜；抓不到就留空，由人回去看 PDF。
2. 保留 PMCID + 表号 + 行文本 + 表脚注，便于逐条回原文核对。
3. 命中会写进 candidate 批次文件，不直接改 data/pool30.json。

依赖：lxml（系统 Python 已装）。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

from lxml import etree

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def text_of(node):
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def esearch(term, retmax=200):
    url = f"{EUTILS}/esearch.fcgi?db=pmc&retmax={retmax}&retmode=json&term={urllib.parse.quote(term)}"
    with urllib.request.urlopen(url, timeout=40) as r:
        return json.load(r)["esearchresult"]


def efetch_xml(pid, cache_dir):
    path = os.path.join(cache_dir, f"{pid}.xml")
    if os.path.exists(path):
        return path
    url = f"{EUTILS}/efetch.fcgi?db=pmc&id={pid}&retmode=xml"
    for attempt in range(2):
        try:
            with urllib.request.urlopen(url, timeout=45) as r:
                open(path, "wb").write(r.read())
            time.sleep(0.35)
            return path
        except Exception as e:
            if attempt == 1:
                print(f"  ! {pid}: {e}", file=sys.stderr)
                return None
            time.sleep(2)


def meta_of(root):
    front = root.find(".//front")
    doi = ""
    for a in root.iter("article-id"):
        if a.get("pub-id-type") == "doi":
            doi = text_of(a)
    authors = []
    for c in front.iter("contrib") if front is not None else []:
        sn = text_of(c.find(".//surname"))
        gn = text_of(c.find(".//given-names"))
        if sn:
            authors.append(f"{sn} {gn[:1]}." if gn else sn)
        if len(authors) >= 3:
            break
    return {
        "title": text_of(front.find(".//article-title")) if front is not None else "",
        "journal": text_of(front.find(".//journal-title")) if front is not None else "",
        "year": text_of(front.find(".//pub-date/year")) if front is not None else "",
        "volume": text_of(front.find(".//volume")) if front is not None else "",
        "pages": "-".join(x for x in [text_of(front.find(".//fpage")),
                                      text_of(front.find(".//lpage"))] if x) if front is not None else "",
        "doi": doi,
        "authors": authors,
    }


def scan(root, target):
    hits = []
    for tw in root.iter("table-wrap"):
        label = text_of(tw.find("label"))
        caption = text_of(tw.find("caption"))
        foot = " ".join(text_of(f) for f in tw.findall("table-wrap-foot"))
        rows = [" | ".join(text_of(td) for td in tr) for tr in tw.iter("tr")]
        matched = [r for r in rows if target.search(r)]
        if matched:
            hits.append({
                "kind": "table",
                "label": label,
                "caption": caption[:300],
                "header": rows[0][:300] if rows else "",
                "rows": matched[:5],
                "footnote": foot[:600],
                # 口径只能来自脚注/表题，抓不到就是 None——不许猜
                "caliber_hint": next(
                    (k for k in ("isolated", "GC", "NMR", "conversion", "HPLC")
                     if k.lower() in (foot + " " + caption).lower()), None),
            })
    for p in root.iter("p"):
        s = text_of(p)
        if target.search(s) and re.search(r"\d{1,3}(?:\.\d+)?\s*%", s):
            hits.append({"kind": "text", "sentence": s[:500]})
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--substrate", default="4-bromoanisole")
    ap.add_argument("--extra", default="suzuki", help="额外检索词")
    ap.add_argument("--out", default="src_pmc/hits.json")
    ap.add_argument("--max", type=int, default=120)
    args = ap.parse_args()

    cache = os.path.join(os.path.dirname(args.out), "xml")
    os.makedirs(cache, exist_ok=True)

    term = f'"{args.substrate}" AND {args.extra}'
    res = esearch(term)
    ids = res["idlist"][: args.max]
    print(f"query: {term}\nhits: {res['count']}, scanning: {len(ids)}")

    target = re.compile(re.escape(args.substrate).replace(r"\-", "[- ]?"), re.I)
    out = []
    for i, pid in enumerate(ids, 1):
        path = efetch_xml(pid, cache)
        if not path:
            continue
        try:
            root = etree.parse(path).getroot()
        except Exception:
            continue
        hits = scan(root, target)
        if hits:
            out.append({"pmcid": pid, **meta_of(root), "hits": hits})
            print(f"[{i}/{len(ids)}] {pid}  hits={len(hits)}  {out[-1]['title'][:60]}")

    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\narticles with target evidence: {len(out)}")
    print("下一步：逐条回 PDF 核表头/脚注，再写进 data/candidates_b_<date>.json（不要直接改 pool30.json）")


if __name__ == "__main__":
    main()
