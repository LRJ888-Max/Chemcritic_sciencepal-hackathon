"""语料库补全：为已采集文献补作者、许可、摘要与表格上下文句（只读 PMC，不改判定数据）。

输入：data/corpus/<date>.json
输出：同文件就地补字段（authors / license / abstract / table_context）
用法：python scripts/enrich_corpus.py [--date 2026-10-31] [--limit N]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


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


def text_of(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def enrich(art: dict) -> bool:
    pid = art["corpus_id"].removeprefix("PMC")
    xml = fetch(f"{BASE}/efetch.fcgi?db=pmc&id={pid}&rettype=xml")
    root = ET.fromstring(xml)

    names = []
    for c in root.findall(".//contrib"):
        sn = text_of(c.find(".//surname"))
        gn = text_of(c.find(".//given-names"))
        if sn:
            names.append(f"{sn} {gn[:1]}." if gn else sn)
    art["authors"] = names[:6]
    art["author_count"] = len(names)

    art["license"] = text_of(root.find(".//permissions/license"))[:120]
    art["abstract"] = text_of(root.find(".//abstract"))[:600]

    paras = [text_of(p) for p in root.findall(".//body//p")]
    paras = [p for p in paras if len(p) > 60]

    for t in art["tables"]:
        label = (t.get("label") or "").strip()
        ctx = []
        if label:
            for p in paras:
                if re.search(rf"Table\s*{re.escape(label)}\b", p, re.I) or \
                   re.search(rf"表\s*{re.escape(label)}\b", p):
                    ctx.append(p[:420])
                    break
        if not ctx:
            for p in paras:
                if re.search(r"isolated yield|yield was|yield of", p, re.I):
                    ctx.append(p[:420])
                    break
        t["table_context"] = ctx[0] if ctx else ""
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(ROOT, "data", "corpus", "*.json")))
    files = [f for f in files if not f.endswith("_meta.json")]
    if args.date:
        files = [f for f in files if args.date in f]
    if not files:
        print("没有语料库文件")
        return 1
    path = files[-1]
    data = json.load(open(path, encoding="utf-8"))

    todo = [a for a in data["articles"] if not a.get("authors")]
    if args.limit:
        todo = todo[: args.limit]
    print(f"待补全 {len(todo)} / {len(data['articles'])} 篇")

    ok = 0
    for i, art in enumerate(todo, 1):
        try:
            enrich(art)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {art['corpus_id']} {exc}")
        if i % 10 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
            json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        time.sleep(0.4)

    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"完成 {ok} 篇 -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
