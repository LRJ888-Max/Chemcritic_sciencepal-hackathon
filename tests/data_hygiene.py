#!/usr/bin/env python3
"""数据卫生检查：文本字段不带截断残渣，表级反应类型不与题注矛盾。

为什么需要（2026-10-31 收尾）：
`label_raw` 会被**逐字复述**给用户（提示词"口径未决"那一档要求复述原文），
但旧实现在固定 ±30 字符窗口截取，于是 22 条记录写成：

    ', catalyst, base and solvent.bIsolated yield.'      ← 以标点开头 + 脚注上标粘连
    '. The yield was determined by GC analysis ...'       ← 前半句被切掉，只剩个句号

`citation` 用 `[:120]` 硬截，60 条在单词中间断掉，其中 12 条还带着 PMC 内嵌 `<svg>` 标记。
另有 5 张表的 `cls` 与题注矛盾（文章级分类被套到不同反应的子表上）——PMC12305527-T3
题注写着 "Sonogashira coupling"、底物是 4-bromoacetophenone + phenylacetylene，
却被算进 Buchwald 组。

这些都不是"数据显示不好看"，是会变成错话的：复述出乱码、把 Sonogashira 数据
归成 C–N 偶联。所以让 CI 直接红，而不是靠人记得看。

检查项（都是确定性判据，不做语义猜测）：
1. label_raw 不以标点开头；
2. label_raw / citation 不含 HTML/SVG 标记；
3. label_raw / citation 不含脚注上标粘连（`.bIsolated` / `yield.cTOF`）；
4. citation 不以连接词/介词收尾（截断signature，如 "… for C–N and C"）；
5. 索引 cls 与题注里明确写出的反应类型不矛盾（氢化家族内部不算冲突）；
6. 索引里 cls_conflict 必须同时带 cls_article（保留原文标签以便检索）；
7. `scripts/quote_text.py` 自身的规则没被改松（那 5 个真实故障样本固化成断言）。

第 7 条不是重复劳动：上面 1–4 条只能发现**已经写进 data/** 的脏数据，
而规则被改松时要等下次重新晋升才会复发，期间 CI 全绿。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
RECORDS = ROOT / "data" / "records.json"
INDEX = ROOT / "data" / "corpus_index.json"

sys.path.insert(0, str(ROOT / "scripts"))
import quote_text  # noqa: E402  （自检用，位置依赖 ROOT，故放在 sys.path 之后）

# 判据 1：开头的孤立标点
LEAD_PUNCT = re.compile(r"^\s*[,.;:]")
# 判据 2：残留标记
MARKUP = re.compile(r"<[a-zA-Z/!]")
# 判据 3：句点 + 单个小写字母 + 大写开头 = 脚注上标被抽平后粘连
GLUED_FN = re.compile(r"\.[a-e][A-Z]")
# 判据 4：以这些词收尾 = 上游按字符数截断了
DANGLING = re.compile(r"\b(and|or|of|for|the|with|in|to|on|by|at|from|as)\s*$", re.I)

# 判据 5：题注里辨识度高的类别标志词（氢化家族内部互换不算冲突）
STRONG_CLASS = {
    "sonogashira": r"\bsonogashira\b",
    "suzuki": r"\bsuzuki\b",
    "heck": r"\b(heck|mizoroki)\b",
    "buchwald_hartwig": r"\b(buchwald|hartwig)\b",
    "borylation": r"\bborylation\b",
    "carbonylation": r"\bcarbonylation\b",
}
HYDRO_FAMILY = {"hydrogenation", "transfer_hydrogenation", "asym_hydrogenation"}


# 判据 6：catalyst 只能是名字，不能是别的列（旧实现取第 1 列，串了 26 条）
CAT_HEADER_WORD = re.compile(r"^(catalyst[s]?|cat\.?|metal|precatalyst)$", re.I)
CAT_NUMERIC = re.compile(r"^[\d.,\s%]+$")


def check_records(problems: List[str]) -> int:
    doc = json.loads(RECORDS.read_text(encoding="utf-8"))
    checked = 0
    for r in doc["records"]:
        rid = r.get("record_id", "?")
        label = (r.get("outcome") or {}).get("label_raw") or ""
        cit = r.get("citation") or ""
        cond = r.get("conditions") or {}
        cat = (cond.get("catalyst") or "").strip()
        sol = (cond.get("solvent") or "").strip()
        for field, text in (("label_raw", label), ("citation", cit)):
            checked += 1
            if LEAD_PUNCT.match(text):
                problems.append(f"{rid} 的 {field} 以标点开头（截断残渣）：{text[:60]!r}")
            if MARKUP.search(text):
                problems.append(f"{rid} 的 {field} 含 HTML/SVG 标记：{text[:60]!r}")
            if GLUED_FN.search(text):
                problems.append(f"{rid} 的 {field} 含脚注上标粘连：{text[:60]!r}")
        if DANGLING.search(cit):
            problems.append(f"{rid} 的 citation 以连接词收尾（多半被截断）：{cit[-40:]!r}")
        if cat:
            checked += 1
            if sol and cat == sol:
                problems.append(f"{rid} 的 catalyst 与 solvent 同值（{cat!r}，列串了）")
            if CAT_HEADER_WORD.match(cat):
                problems.append(f"{rid} 的 catalyst 是列名不是化合物：{cat!r}")
            if CAT_NUMERIC.match(cat):
                problems.append(f"{rid} 的 catalyst 是数字/用量：{cat!r}")
            if re.fullmatch(r"[A-Z]{1,2}", cat):
                problems.append(f"{rid} 的 catalyst 只有一个字母（多半是 X=Cl/Br/I 列）：{cat!r}")
    return checked


def check_index(problems: List[str]) -> int:
    idx = json.loads(INDEX.read_text(encoding="utf-8"))
    checked = 0
    for t in idx["tables"]:
        tid = t.get("t", "?")
        cls = t.get("cls")
        cap = (t.get("cap") or "").lower()
        checked += 1
        if t.get("cls_conflict") and not t.get("cls_article"):
            problems.append(f"{tid} 标了 cls_conflict 但没保留 cls_article（原文标签丢了，检索会漏）")
        if cls in HYDRO_FAMILY or not cls:
            continue
        # 题注里写明的类别必须与 cls 一致；同时提到两类时（综述表）不判
        named = {c for c, p in STRONG_CLASS.items() if re.search(p, cap)}
        if named and cls not in named:
            problems.append(f"{tid} 的 cls={cls} 与题注写明的 {sorted(named)} 矛盾：{cap[:70]!r}")
    return checked


def main() -> int:
    problems: List[str] = []
    n1 = check_records(problems)
    n2 = check_index(problems)
    problems.extend(quote_text.selftest())
    print(f"  数据卫生：记录字段 {n1} 处 / 索引表 {n2} 行 "
          f"/ 文本规则自检 {quote_text.selftest_count()} 项")

    if problems:
        print()
        for p in problems[:20]:
            print(f"  [FAIL] {p}")
        if len(problems) > 20:
            print(f"  …另有 {len(problems) - 20} 处")
        print(f"\n  结果：{len(problems)} 处文本/分类问题")
        return 1
    print("  [PASS] 文本字段无截断残渣，表级反应类型与题注一致，文本规则未走样")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
