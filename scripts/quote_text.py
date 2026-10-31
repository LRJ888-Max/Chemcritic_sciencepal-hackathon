"""口径引文与题注摘录的统一实现（promote / merge / refix 共用）。

为什么单独成一个模块：`label_raw` 里的引文会被**逐字复述**给用户（提示词"口径未决"
那一档要求复述原文）。旧实现用固定 ±30 字符窗口截取，于是：

    ', catalyst, base and solvent.bIsolated yield.'     ← 以标点开头 + 脚注上标粘连
    '. The yield was determined by GC analysis ...'      ← 前半句被切掉，只剩个句号

题注摘录用 `text[:160]` 硬截断，则会在单词中间断开（'…in the pres'）。

规则（都用确定性文本处理，不做语义猜测）：
1. 先塌成单行（多空白 → 单空格），再在**同一份归一化文本**上算位置，避免下标错位。
2. 引文按**整句**取：向前到上一个句末标点之后，向后到下一个句末标点（含）。
3. 句点后紧接单个 a–e 字母、再跟大写词首 —— 这是 HTML 上标被抽平后的脚注标记，
   改写成 `〔脚注 b〕`，不删字符（删了就成了"改原文"）。
4. 题注摘录按**词边界**截断并加 `…`，不留半截单词。
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# 句末标点（后面跟空白或到结尾才算，避免把 "1.5" 这种小数当句末）。
# 冒号不算：脚注几乎都是 "Reaction conditions: 1b (1.0 mmol), ..." 这种结构，
# 在冒号处断开会让引文变成 "iodobenzene, methyl acrylate, ..." 这样一个无主语的残片。
_SENT_END = re.compile(r"(?:[.;!?](?=\s|$))|(?:\.(?=[a-e][A-Z]))")
# 开头的孤立标点（截断残留）
_LEAD_JUNK = re.compile(r"^[\s,.;:·•\-–—]+")
# 句点 + 单个小写脚注字母 + 大写开头的词 → "…standard.bIsolated" / "…yield.cTOF" 这类粘连。
_GLUED_MARK = re.compile(r"\.([a-e])(?=[A-Z])")
# 脚注顶端的标记字母："aReaction conditions:" / "bYields of ..." / "c Isolated yields;"
_LEAD_MARK = re.compile(r"^([a-e])(?=[A-Z])|^([a-e])\s+(?=[A-Z][a-z])")
# 句中：空格 + 单个小写字母 + 紧跟大写词首（"for 6 h aYield of …"）。
# 后面不留空格才动手，英文冠词 "a Yield" 有空格，不会中招。
_MID_MARK = re.compile(r"(?<=\s)([a-e])(?=[A-Z][a-z]{2,})")


def one_line(text: Optional[str]) -> str:
    """塌成单行。位置计算必须与最终输出在同一份字符串上做。"""
    return re.sub(r"\s+", " ", text or "").strip()


def repair_markers(text: str) -> str:
    """修掉截断残留与脚注上标粘连（只加标记，不删内容）。"""
    s = _LEAD_JUNK.sub("", one_line(text))
    s = _LEAD_MARK.sub(lambda m: f"〔脚注 {m.group(1) or m.group(2)}〕", s)
    s = _GLUED_MARK.sub(lambda m: f".〔脚注 {m.group(1)}〕", s)
    s = _MID_MARK.sub(lambda m: f"〔脚注 {m.group(1)}〕", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def quote_around(text: str, start: int, end: int, max_len: int = 200) -> str:
    """返回包住 text[start:end] 的**完整一句**；超长时按词边界截，且**必须保留匹配片段**。

    text 必须已经是 `one_line()` 的结果，否则 start/end 会对不上。
    """
    head = 0
    for m in _SENT_END.finditer(text, 0, start):
        head = m.end()
    tail = len(text)
    m2 = _SENT_END.search(text, end)
    if m2:
        tail = m2.end()
    quote = text[head:tail].strip()
    if len(quote) <= max_len:
        out = repair_markers(quote)
    else:
        # 超长：以匹配片段为锚开窗（不能因为截断把口径那一句切掉）
        rel_s = max(0, start - head)
        left = max(0, rel_s - 40)
        window = quote[left:left + max_len]
        if left > 0:
            window = "…" + window.split(" ", 1)[-1]
        if left + max_len < len(quote):
            window = window.rsplit(" ", 1)[0] + "…"
        out = repair_markers(window)
    # 不以句末标点收尾 = 原文在这里就断了（上游按字符数截过），标出来而不是让它像结论
    if out and not re.search(r"[.;!?…]$", out):
        out += "…"
    return out


def drop_trailing_marker(caption: Optional[str], footnote: Optional[str]) -> str:
    """去掉题注末尾粘连的脚注上标字母（'…coupling reactiona' → '…coupling reaction'）。

    只在两个条件同时成立时动手，避免把以字母结尾的普通单词改坏：
      ① 题注末尾是「≥2 个字母 + 单个 a–e」且题注不以标点收尾；
      ② 脚注（去空白）正好以同一个字母开头 —— 说明那个字母确实是这条脚注的标记。
    实测 194 张表里命中 23 处，逐个看都是标记（Parameters / Species / reactions /
    olefins / Pd …），没有一个是单词本身的结尾。
    """
    s = one_line(caption)
    fn = (footnote or "").lstrip()
    m = re.search(r"([A-Za-z]{2,})([a-e])$", s)
    if not m or not fn or fn[0] != m.group(2):
        return s
    return s[: m.start(2)].rstrip()


def excerpt(text: Optional[str], limit: int = 120) -> str:
    """题注摘录：按词边界截断，超长加省略号，不留半截单词。"""
    s = repair_markers(text or "")
    if len(s) <= limit:
        return s
    cut = s.rfind(" ", 0, limit)
    if cut < limit // 2:  # 找不到合适空格（长化学名连写）就硬切
        cut = limit
    return s[:cut].rstrip(" ,;:") + "…"


# 抓取时混进标题的出版方样板文字（PMC5365239 的标题末尾就是 "… DOI: 10.1039/xxx Click here for"）
_BOILERPLATE = re.compile(
    r"(?:\s*DOI:\s*10\.\S+.*$)|(?:\s*Click here for.*$)|(?:\s*View Article.*$)"
    r"|(?:\s*PubMed\s*$)|(?:\s*Google Scholar\s*$)|(?:\s*Full Text\s*$)"
    r"|(?:†?\s*Electronic supplementary information.*$)", re.I)
_TAG = re.compile(r"<[^>]*>?")          # 含未闭合的残标签（标题被按字符数截断时会留下）
_ENT = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'", "&#x2013;": "–"}


def clean_title(text: Optional[str], limit: int = 200) -> str:
    """标题清洗：去标签/实体/出版方样板，按词边界截断并标省略号。

    标题里可能出现内嵌 `<svg>`（上下标被 PMC 转成矢量图，文字不可恢复），
    也可能被上游按字符数硬截断在标签中间——两种都只能去掉标记并标明被截断，
    不能猜原文。
    """
    raw = text or ""
    had_markup = bool(_TAG.search(raw))
    s = _TAG.sub(" ", raw)
    for k, v in _ENT.items():
        s = s.replace(k, v)
    s = _BOILERPLATE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip(" ,;:·•-–—†‡*")
    # 上游按 220 字符截过的标题（含样板文字时尤其明显）：截断处绝不在词尾，
    # 加上省略号说明"这是被截断的"，而不是让读者以为标题就这么结束。
    if had_markup or len(s) > limit or len(raw) >= 200:
        s = excerpt(s, limit)
        if not s.endswith("…"):
            s += "…"
    return s


def locate_quote(text: Optional[str], pattern: "re.Pattern[str]",
                 max_len: int = 180) -> Tuple[Optional["re.Match[str]"], Optional[str]]:
    """在归一化文本里定位 pattern，并返回 (match, 整句引文)。"""
    t = one_line(text)
    m = pattern.search(t)
    if not m:
        return (None, None)
    return (m, quote_around(t, m.start(), m.end(), max_len=max_len))


# 本模块的规则是"要么确定性、要么不做"。判据一旦被改松（例如把 drop_trailing_marker
# 的双条件简化成单条件），data/ 里不会立刻出现脏数据——要等下次重新晋升才复发。
# 所以把当初那 5 个真实故障样本固化成断言，跟着数据卫生检查一起跑。
_CASES = [
    # (说明, 函数, 输入, 断言)
    ("开头的孤立标点",
     lambda: repair_markers(", catalyst, base and solvent.bIsolated yield."),
     lambda o: not o.startswith(",")),
    ("句点后粘连的脚注 b",
     lambda: repair_markers("solvent.bIsolated yield."),
     lambda o: "〔脚注 b〕" in o and ".bI" not in o),
    ("脚注顶端的标记字母",
     lambda: repair_markers("bYields of 3a are isolated."),
     lambda o: o.startswith("〔脚注 b〕")),
    ("句中粘连的脚注 a",
     lambda: repair_markers("The reaction was run for 6 h aYield of 92%."),
     lambda o: "〔脚注 a〕" in o),
    ("题注尾标记（脚注首字母相同 → 剥离）",
     lambda: drop_trailing_marker("Scope of the Sonogashira coupling reactiona",
                                 "aReaction conditions: 1b"),
     lambda o: o.endswith("reaction")),
    ("题注尾字母（脚注首字母不同 → 不动）",
     lambda: drop_trailing_marker("Scope of the coupling reactiona", "bYields of 3a"),
     lambda o: o.endswith("reactiona")),
    ("题注以普通单词结尾（无脚注 → 不动）",
     lambda: drop_trailing_marker("Reactions performed under visible light", None),
     lambda o: o.endswith("light")),
    ("摘录不留半截单词",
     lambda: excerpt("x " * 100, 60),
     lambda o: o.endswith("…")),
    ("标题去内嵌 SVG 与出版方样板",
     lambda: clean_title("A title <svg><text>x</text></svg> DOI: 10.1039/abc Click here for"),
     lambda o: "<" not in o and "DOI" not in o and "Click here" not in o),
]


def selftest() -> List[str]:
    """跑全部断言，返回失败说明（空列表 = 规则未被改坏）。"""
    fails: List[str] = []
    for name, fn, ok in _CASES:
        out = fn()
        if not ok(out):
            fails.append(f"quote_text.{name} 走样了：{out!r}")
    # 截断时不能把匹配片段切掉 —— 这是"引文"存在的唯一理由
    t = one_line("Short. " + "x " * 60 + "target phrase. " + "y " * 60 + "Done.")
    m = re.search(r"target", t)
    q = quote_around(t, m.start(), m.end(), max_len=60)
    if "target" not in q:
        fails.append(f"quote_text.超长截断丢了匹配片段：{q!r}")
    # 冒号不算句末：脚注式引文要留住主语 "Reaction conditions:"
    t2 = one_line("Reaction conditions: 1b (1.0 mmol), catalyst and base in DMF.")
    m2 = re.search(r"base", t2)
    q2 = quote_around(t2, m2.start(), m2.end())
    if not q2.startswith("Reaction conditions"):
        fails.append(f"quote_text.在冒号处断句（引文丢了主语）：{q2!r}")
    return fails


def selftest_count() -> int:
    return len(_CASES) + 2
