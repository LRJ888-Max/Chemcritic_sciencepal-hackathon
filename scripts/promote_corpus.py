"""语料层 → 判定层的晋升管道（候选生成，非自动入库）。

为什么需要这一步（用户 2026-10-31 的原话）：
    「不要只把 PDF 加到文档里就不管了，还要核验加到 records.json 和 engine 的 py 里，
      agent 才可以调用。」
语料库 1981 行如果只是躺在 docs/ 里，agent 查不到、判定用不上，等于装饰。
本脚本负责把「分诊为 A 档」的表按严格规则抽成 records.json 的 entry 形状，
输出到 data/incoming/ 供人工逐条核验；**不直接写 records.json**。

三条铁律：
1. 抽不出就写 null。绝不用默认值补成事实（schemas.py 的约束同款）。
2. evidence_grade 最高 candidate。语料层只有 efetch 表格文本，没有 PDF 图证，
   给 candidate 已经是按「原文逐字可追」给的上限；给 A_confirmed 就是造假。
3. label_raw 必须是题注/脚注里的**逐字片段**，不做改写——gate 4 用它判口径一致性。

用法：
    python scripts/promote_corpus.py --dry          # 只打印抽到的样子
    python scripts/promote_corpus.py --write        # 落盘 data/incoming/
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
INCOMING = ROOT / "data" / "incoming"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from quote_text import clean_title, excerpt, one_line, quote_around  # noqa: E402  引文/摘录/标题的唯一实现

# 只晋升「双底物交叉偶联」——只有这类能填满 gate 1 要求的底物对
CROSS_COUPLING = {"suzuki", "buchwald_hartwig", "sonogashira", "heck", "negishi"}

YIELD_COL = re.compile(r"\byield|\b收率", re.I)

# 口径：只认原文明确写的词，逐字记录
CALIBER_PATTERNS = [
    ("isolated", re.compile(r"isolated\s+(?:yield|product)|分离收率|Isolated yields?", re.I)),
    ("gc", re.compile(r"\bGC(?:\s|/|-)?(?:yield|analysis|MS)|gas\s+chromatog|GLC|气相", re.I)),
    ("nmr", re.compile(r"\b1?H[-\s]?NMR\s+yield|NMR\s+(?:yield|analysis|determined)|核磁", re.I)),
    ("conversion", re.compile(r"conversion|转化率", re.I)),
]

INTERNAL_STD = re.compile(
    r"(?:internal|external)\s+standard[^.;]{0,40}|(?:vs\.?|versus)\s+([A-Za-z0-9,\- ]{3,25})\s+as\s+(?:an?\s+)?"
    r"(?:internal|external)\s+standard", re.I)

BASE_NAMES = ["K2CO3", "K3PO4", "Cs2CO3", "Na2CO3", "NaHCO3", "NaOH", "KOH", "KOtBu", "t-BuONa",
              "tBuONa", "NaOtBu", "Et3N", "NEt3", "DIPEA", "NEt(i-Pr)2", "DBU", "KOMe", "NaOMe",
              "pyridine", "TMEDA", "LiHMDS", "NaH"]
SOLVENTS = ["1,4-dioxane", "dioxane", "THF", "toluene", "benzene", "DMF", "DMA", "DMSO", "CH3CN",
            "acetonitrile", "EtOH", "MeOH", "H2O", "water", "iPrOH", "2-propanol", "n-BuOH",
            "AcOEt", "EtOAc", "dichloromethane", "DCE", "chloroform", "glycerol", "PEG"]

HALIDE = re.compile(r"brom|iod|chlor|triflate|tosylate|\bAr[-\s]?X\b|aryl halide", re.I)
PARTNER = [
    ("boronic_acid", re.compile(r"boronic|boronate|B\(OH\)2|Bpin|pinacol", re.I)),
    ("organometallic", re.compile(r"MgBr|MgCl|ZnCl|ZnBr|SnBu3|B\(OH\)2|Grignard", re.I)),
    ("alkene", re.compile(r"styrene|acrylate|acryl|alkene|olefin|methyl acrylate|ethyl acrylate", re.I)),
    ("alkyne", re.compile(r"acetylene|alkyne|ethynyl", re.I)),
    ("amine", re.compile(r"aniline|toluidine|amine|amide|phenoxazine|carbazole|triazol", re.I)),
]

# 反应类型 → 亲核试剂应有的类别（用于纠正「抽到的是添加剂而非底物」）
EXPECT_PARTNER = {
    "suzuki": {"boronic_acid"},
    "heck": {"alkene"},
    "sonogashira": {"alkyne"},
    "buchwald_hartwig": {"amine"},
    "kumada": {"organometallic"},
    "negishi": {"organometallic"},
}
# 底物类别 → 反应类型（反过来定类型：Heck 不用炔，芳卤+末端炔就是 Sonogashira）
KIND_BY_PARTNER = {
    "boronic_acid": "suzuki",
    "alkene": "heck",
    "alkyne": "sonogashira",
    "amine": "buchwald_hartwig",
    "organometallic": "kumada",
}

# 底物通称：表里用它代表一类，真实分子在行内。抽到这些一律标 needs_review，不入库。
GENERIC_SUBSTRATE = re.compile(r"^(?:ar[-\s]?x|aryl\s+halide[s]?|aryl\s+bromide[s]?|"
                              r"alkyne[s]?|alkene[s]?|halide[s]?)$", re.I)
# 裸名单里值得当底物的骨架词
SUBSTRATE_CHEM = re.compile(
    r"benzene|aniline|styrene|acryl|iod|brom|chlor|acetylene|alkyne|amine|toluene|"
    r"acid|ester|ether|pyridine|thiophene|phenol|naphthalene|indole|carbazole|triazol",
    re.I)

FEED = re.compile(
    r"([A-Za-z0-9µαβγ][A-Za-z0-9µαβγ\-]*(?:[ ,\-][A-Za-z0-9µαβγ\-]{1,20}){0,5})"
    r"\s*\(\s*(\d+(?:\.\d+)?)\s*(mmol|equiv|eq\.?|mol\s*%|mol%)\s*\)", re.I)
NUM_ANY = re.compile(r"^-?\d+(?:[.,]\d+)?$")
TEMP = re.compile(r"(\d{2,3})\s*(?:°C|℃|oC)")
TIME_H = re.compile(r"(\d+(?:\.\d+)?)\s*h\b")
TIME_MIN = re.compile(r"(\d+(?:\.\d+)?)\s*min")

# 名字片段里不该出现的叙述词——命中说明正则跨句吞了半句话
_JUNK_NAME = re.compile(r"\b(when|reaction|section|conditions|were|was|carried|out|"
                        r"with|using|see|following|mixture|added|then|and)\b", re.I)
# 候选名里出现这些 = 抽到的是叙述残片或投料量尾巴，不是化合物
_JUNK_CAND = re.compile(r"mmol|equiv|\btime\b|depend|condition|section|table|entry|"
                        r"supporting|see\b|details|depends", re.I)


def _norm_name(s: str) -> str:
    s = re.sub(r"^[\s,;:\.\[\]\(\)]+|[\s,;:\.\[\]\(\)]+$", "", s)
    s = re.sub(r"^(?:and|with|in|using|of|the)\s+", "", s, flags=re.I)
    return " ".join(s.split())


def _clean_name(s: Optional[str]) -> Optional[str]:
    """把「4-bromotoluene (1 equiv.」「phenylacetylene 3a.」这类尾巴清干净。

    清不干净就返回 None——一个带残渣的名字在 gate 1 里比对不上是小事，
    但被当成真名写进库，就是给后面的判定埋一个假事实。
    """
    if not s:
        return None
    t = str(s)
    t = re.sub(r"\([^)]*$", "", t)              # 未闭合括号起的尾巴
    t = re.sub(r"\s*\([^)]*\)\s*", " ", t)       # 完整括注
    t = re.sub(r"\s+\d{1,2}[a-z]?\.?$", "", t)   # 结尾编号（3a / 1b）
    t = re.sub(r"^(?:and|with|in|using|of|the)\s+", "", t, flags=re.I)
    t = " ".join(t.split()).strip(" ,;.:")
    if len(t) < 3 or _JUNK_NAME.search(t):
        return None
    return t


def _num(cell: str) -> Optional[float]:
    c = str(cell).strip()
    c = re.sub(r"^[<>~≈]\s*", "", c)
    m = re.search(r"-?\d+(?:[.,]\d+)?", c)
    if not m:
        return None
    v = m.group(0).replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def _caliber(*texts: str) -> Tuple[Optional[str], Optional[str]]:
    """返回 (outcome_type, 逐字引文)。多口径并存时取最先出现的，并保留原文供人工判断。

    按传入顺序逐段找（调用方传 footnote 在前、caption 在后）：脚注才是写口径的地方，
    题注常常只写"Optimization of reaction conditions"。引文取**整句**（`quote_text`），
    旧实现是固定 ±30 字符窗口，截出来会是 `, catalyst, base and solvent.bIsolated yield.`
    这种以标点开头、脚注上标粘连的残片。
    """
    for text in texts:
        line = one_line(text)
        best: Tuple[Optional[str], Optional[str]] = (None, None)
        best_pos = 10 ** 9
        for label, pat in CALIBER_PATTERNS:
            m = pat.search(line)
            if m and m.start() < best_pos:
                best_pos = m.start()
                best = (label, quote_around(line, m.start(), m.end()))
        if best[0]:
            return best
    return (None, None)


def _caliber_from_header(header: List[str], ycol: int) -> Tuple[Optional[str], Optional[str]]:
    """收率列名本身写口径的（"GC yield (%)" / "1H NMR yield [%]" / "Isolated yield"），
    比脚注更靠得住——脚注常把 yield 与 conversion 并列叙述，先到先得会误判。"""
    if ycol is None or ycol >= len(header):
        return (None, None)
    h = header[ycol] or ""
    for label, pat in CALIBER_PATTERNS:
        m = pat.search(h)
        if m:
            return (label, h.strip())
    return (None, None)


def _label_map(*texts: str) -> Dict[str, str]:
    """正文里的「化合物名 (1b)」→ 编号映射；脚注常只用编号投料。

    名字模式与 FEED 保持同款（不允许括号、不允许叙述词），否则会把
    "(Section 2)). When the reaction of ..." 这种残片当成化合物名。
    """
    out: Dict[str, str] = {}
    blob = " ".join(t or "" for t in texts)
    # 从「(1b)」往回截取词序列，遇叙述词即停——比整段正则可靠，
    # 因为 "the reaction of 1-chloro-4-iodobenzene (1b)" 里正则从 the 起匹配就会整段作废。
    for m in re.finditer(r"\(\s*(\d{1,2}[a-z]?)\s*\)", blob):
        tag = m.group(1)
        head = blob[max(0, m.start() - 90):m.start()]
        toks = re.findall(r"[A-Za-z0-9µαβγ\-]+", head)[-5:]
        picked: List[str] = []
        for tok in reversed(toks):
            if _JUNK_NAME.fullmatch(tok):
                break
            picked.insert(0, tok)
        name = _norm_name(" ".join(picked))
        if len(name) < 3 or re.fullmatch(r"\d{1,2}[a-z]?", name):
            continue
        out.setdefault(tag, name)
    return out


def _first(patterns: List[str], text: str) -> Optional[str]:
    low = text.lower()
    for p in patterns:
        m = re.search(re.escape(p.lower()), low)
        if m:
            return p
    return None


def _feeds(text: str) -> List[Tuple[str, float, str]]:
    out = []
    for m in FEED.finditer(text):
        name = _norm_name(m.group(1))
        if len(name) < 2 or name.lower() in ("entry", "conditions", "reaction"):
            continue
        if _JUNK_NAME.search(name):
            continue  # 跨句吞的残片，宁可丢也不猜
        out.append((name, float(m.group(2)), m.group(3).lower()))
    return out


def _resolve_feeds(feeds: List[Tuple[str, float, str]],
                   labels: Dict[str, str]) -> List[Tuple[str, float, str]]:
    """脚注里的 "1b (1.0 mmol)" 用正文映射换回真名；换不回的保留编号。"""
    out = []
    for name, qty, unit in feeds:
        if name in labels:
            out.append((labels[name], qty, unit))
        elif re.fullmatch(r"\d{1,2}[a-z]?", name):
            out.append((name, qty, unit))  # 保持编号，后面判不了就留 null
        else:
            out.append((name, qty, unit))
    return out


def _bare_feeds(text: str) -> List[str]:
    """脚注只列裸名、不写投料量时的兜底：

        "Reaction conditions: styrene, 4-iodotoluene, base (2 mmol), ..."
    逗号切分后取含化学骨架词的片段。这些条目只用于识别底物，不参与当量计算。
    """
    m = re.search(r"reaction conditions?\s*[:：]?\s*(.{0,320})", text, re.I | re.S)
    seg = m.group(1) if m else text[:320]
    seg = re.split(r"\.\s|;\s|[；。]", seg)[0]
    out: List[str] = []
    for part in re.split(r",|\band\b", seg):
        p = re.sub(r"\([^)]*\)", "", part).strip()
        p = re.sub(r"^(?:in|with|using|of|the|a|an)\s+", "", p, flags=re.I).strip()
        p = re.sub(r"^\d+\s*(?:cm3|mL|ml|mg|equiv|mmol)\s*", "", p, flags=re.I).strip()
        if len(p) < 3 or _JUNK_NAME.search(p):
            continue
        if not SUBSTRATE_CHEM.search(p):
            continue
        out.append(p)
    return out


def _profile(catalyst: str, ligand: str) -> Dict[str, Any]:
    """催化体系五字段。判不准就留空——留空只是 tier 未定，猜错是假精确。"""
    blob = f"{catalyst or ''} {ligand or ''}".lower()
    if not blob.strip():
        return {}
    prof: Dict[str, Any] = {}
    if re.search(r"np\b|nanoparticle|nanocatal|pd[–-]c|pd[–-]ni|pd[–-]mn|magnetic|fe3o4", blob):
        prof["catalyst_form"] = "nanoparticle"
    elif re.search(r"@|mof|zif|sba|sio2|al2o3|supported|charcoal|\bc\b|resin|polymer", blob):
        prof["catalyst_form"] = "supported"
    elif re.search(r"complex|\(nhc\)|\[pd|pincer|nHC", catalyst or "", re.I):
        prof["catalyst_form"] = "molecular_complex"
    elif re.search(r"pd\(oac\)2|pdcl2|pd\(acac\)2|pd\(no3\)2", blob):
        prof["catalyst_form"] = "in_situ_salt"

    if re.search(r"pph3|phosphine|sphos|xphos|dppp|dppb|dppf|xantphos|binap|p\(t-bu\)3|ptbu3", blob):
        prof["ligand_family"] = "phosphine"
    elif re.search(r"nhc|imes|ipr|imidazol|sipr|dipp", blob):
        prof["ligand_family"] = "phosphine" if "phosphine" in blob else "non_phosphine"
    elif re.search(r"bipy|phenanthroline|bpy|amine ligand", blob):
        prof["ligand_family"] = "non_phosphine"
    prof["support"] = "solid_support" if prof.get("catalyst_form") in ("supported", "nanoparticle") else "none"
    solv_blob = ""
    prof["medium"] = "aqueous" if re.search(r"h2o|water|aqueous|水", solv_blob) else None
    if re.search(r"led|light|hv|photoredox|photo-", blob):
        prof["activation_mode"] = "photochemical"
    elif re.search(r"microwave|ultrasound|sonicat|heating", blob):
        prof["activation_mode"] = "thermal"
    return {k: v for k, v in prof.items() if v is not None}


def build_units(triage: Dict[str, Any]) -> List[Dict[str, Any]]:
    units = []
    for u in triage["units"]:
        if u["verdict"] == "C_reject":
            continue
        if u["reaction_class"] not in CROSS_COUPLING:
            continue
        m = u["material"]
        head = " ".join(m["header"])
        if not YIELD_COL.search(head):
            continue
        cond_text = " ".join([m["caption"], m["footnote"], m["table_context"]])
        ycols = [i for i, h in enumerate(m["header"]) if YIELD_COL.search(h or "")]
        cal_type, cal_quote = _caliber_from_header(m["header"], ycols[0] if ycols else None)
        cal_from = "header"
        if cal_type is None:
            cal_type, cal_quote = _caliber(m["footnote"], m["caption"])
            cal_from = "caption_or_footnote"
        if cal_type is None:
            continue  # 口径未明 → 不进判定层（只能进语料层）
        if cal_type == "conversion":
            # 转化率不是收率，口径不同不可比；本管道只晋升真正报了 yield 的
            continue
        units.append({**u, "cal_type": cal_type, "cal_quote": cal_quote,
                      "cal_from": cal_from, "cond_text": cond_text})
    return units


SCOPE_COL = re.compile(
    r"^(olefin|alkene|alkyne|amine|aryl\s*halide|arylhalide|halide|substrate|boronic(\s*acid)?"
    r"|x|r1|r2|r3|r|ar|ar-?x|product)$", re.I)
LOADING_COL = re.compile(r"mol\s*%|cat(\.|alyst)?|loading|amount", re.I)


def _catalyst_col(header: List[str]) -> Optional[int]:
    """按表头定位催化剂列；认不出就返回 None（宁可没有，不要拿别的列顶替）。

    排除「用量列」（`Pd/mol %`、`Catalyst (mol%)`）：那一格是数字，不是名字。
    """
    for i, h in enumerate(header or []):
        name = h or ""
        if re.search(r"catalyst|cat\.|\[pd\]|\[ni\]|\[cu\]|precatalyst|催化", name, re.I) \
                and not re.search(r"mol\s*%|loading|amount|用量", name, re.I):
            return i
    return None


def _scope_columns(header: List[str], rows: List[List[str]]) -> List[str]:
    """表头出现「底物维度」列，且该列在行内取 ≥2 个不同值 → 底物随行走。

    这类是底物范围表（substrate scope）：脚注常写一句模板
    「Reaction conditions: iodobenzene, methyl acrylate, ...」，
    但那只是 model reaction 的条件，不是整表的底物。按脚注给整表打同一个
    底物对，会把「苯乙烯 / 丙烯腈 / 溴代物」全说成「碘苯 + 丙烯酸甲酯」。
    """
    out = []
    for i, h in enumerate(header or []):
        name = (h or "").strip()
        if not SCOPE_COL.match(name):
            continue
        vals = {str(r[i]).strip().lower() for r in rows if len(r) > i and str(r[i]).strip()}
        if len(vals) >= 2:
            out.append(name)
    return out


def _blank_control_rows(header: List[str], rows: List[List[str]], ycol: Optional[int]) -> List[str]:
    """空白/阴性对照行：催化剂用量为 0 或明确缺失，且收率为 0。

    这类行收率常为 0，留在参照池里会把区间下界拉到 0，
    让「0–96%」这种区间看着像文献分散度，其实是「不加催化剂的必然结果」。
    两种写法都要认：用量列写 0.0，或写 "-" / "none" 同时收率 0。
    """
    idx = [i for i, h in enumerate(header or []) if LOADING_COL.search(h or "")]
    if not idx or ycol is None:
        return []
    hits = []
    for r in rows:
        if len(r) <= ycol:
            continue
        y = _num(str(r[ycol]))
        if y is None or y > 0.001:
            continue  # 只有收率为 0 的行才可能是阴性对照
        for i in idx:
            if len(r) <= i:
                continue
            cell = str(r[i]).strip()
            if re.fullmatch(r"0+(\.0+)?", cell):
                hits.append(str(r[0]).strip() or "?")
                break
            if re.fullmatch(r"[-–—]|n\.?d\.?|none|no\s+catalyst", cell, re.I):
                hits.append(str(r[0]).strip() or "?")
                break
    return hits


def extract(unit: Dict[str, Any]) -> Dict[str, Any]:
    m = unit["material"]
    header = m["header"]
    rows = m["rows"]
    fn = m["footnote"]
    cap = m["caption"]
    ctx = m["table_context"]

    scope_cols = _scope_columns(header, rows)

    ycols = [i for i, h in enumerate(header) if YIELD_COL.search(h or "")]
    ycol = ycols[0] if ycols else None
    blanks = set(_blank_control_rows(header, rows, ycol))

    labels = _label_map(cap, fn, ctx)
    feeds = _resolve_feeds(_feeds(" ".join([fn, cap])), labels)
    bare = _bare_feeds(fn) or _bare_feeds(cap)

    def _pick(pool: List[str]) -> Tuple[Optional[str], Optional[str]]:
        h = next((n for n in pool if HALIDE.search(n)), None)
        pf, pn = None, None
        for field, pat in PARTNER:
            hit = next((n for n in pool if n != h and pat.search(n)), None)
            if hit:
                pf, pn = field, hit
                break
        return h, (f"{pf}|{pn}" if pf else None)

    halide = next((n for n, q, u in feeds if HALIDE.search(n)), None)
    partner_field, partner = None, None
    # 按 PARTNER 的优先级取（硼酸 > 有机金属 > 烯烃 > 炔 > 胺）：交叉偶联里亲核试剂的类别顺序
    # 比"脚注里谁先出现"稳，否则胺类词会把硼酸挤掉。
    for field, pat in PARTNER:
        hit = next((n for n, q, u in feeds if n != halide and pat.search(n)), None)
        if hit:
            partner_field, partner = field, hit
            break
    # 裸名单兜底：带投料量的写法抽不到时，用逗号切分的名单补齐（只补缺的一侧）
    if halide is None or partner is None:
        pool = [n for n, q, u in feeds] + bare
        h2, _ = _pick(pool)
        if halide is None:
            halide = h2
        if partner is None:
            for field, pat in PARTNER:
                hit = next((n for n in pool if n != halide and pat.search(n)), None)
                if hit:
                    partner_field, partner = field, hit
                    break

    # ---- 亲核试剂重选：底物证据 > 标题关键词 ----
    # 两个方向都会错，不能只朝一边修：
    #   PMC9301730  标题是 BH 胺化，苯硼酸酯只是活化剂 → 亲核试剂应是甲苯胺；
    #   PMC8402173  一篇里 Heck 与 Sonogashira 并存，表级关键词写成 heck，
    #               但表里是 4-碘苯甲醚 + 苯乙炔 → 按炔改回 Sonogashira。
    # 规则：候选来自底物本身（芳香卤以外的试剂）；反应类型只用来在**多候选**时打破平局。
    pools: List[List[str]] = [[n for n, q, u in feeds], bare,
                              _bare_feeds(ctx), _bare_feeds(cap)]
    cands: List[Tuple[str, str]] = []
    for pool in pools:
        for field, pat in PARTNER:
            for n in pool:
                if n == halide or not pat.search(n):
                    continue
                if GENERIC_SUBSTRATE.match(str(n).strip()):
                    continue
                if _JUNK_CAND.search(str(n)):
                    continue
                # 碱/溶剂被 PATTERN 误当亲核试剂（"triethyl amine 1.0 mmol)"）
                if any(b.lower() in str(n).lower() for b in BASE_NAMES):
                    continue
                if not any(f == field and nm == n for f, nm in cands):
                    cands.append((field, n))
    rc = unit.get("reaction_class")
    want = EXPECT_PARTNER.get(rc or "", set())
    fields = {f for f, _ in cands}
    if cands:
        if want & fields:
            partner_field, partner = next(c for c in cands if c[0] in want)
        else:
            partner_field, partner = cands[0]
    kind_from_data = KIND_BY_PARTNER.get(partner_field or "")
    class_fix = f"{rc}→{kind_from_data}" if (rc and kind_from_data and rc != kind_from_data) else None

    generic = bool(GENERIC_SUBSTRATE.match((halide or "").strip())) or \
              bool(GENERIC_SUBSTRATE.match((partner or "").strip()))
    halide, partner = _clean_name(halide), _clean_name(partner)
    if halide is None or partner is None:
        generic = True  # 清不干净 = 当通称处理，不进判定层

    base = _first(BASE_NAMES, " ".join([fn, cap]))
    if not base:
        for r in rows:
            for cell in r:
                b = _first(BASE_NAMES, str(cell))
                if b:
                    base = b
                    break
            if base:
                break
    solvent = _first(SOLVENTS, " ".join([fn, cap]))
    temp = TEMP.search(fn) or TEMP.search(cap)
    t_h = TIME_H.search(fn)
    t_min = TIME_MIN.search(fn)
    std = INTERNAL_STD.search(fn)

    # 催化剂列必须按表头找，不能默认第 1 列：那张表第 1 列可能是 Solvent / Olefin / R1 /
    # 底物编号，甚至就是收率列。旧实现取 r[1]，于是 records 里出现 catalyst="DMF"、
    # "Styrene"、"NS"（表面活性剂）、"Catalyst"（表头本身）——26 条记录的催化剂名是错的。
    cat_col = _catalyst_col(header)
    cat_cells = [str(r[cat_col]).strip() for r in rows
                 if cat_col is not None and cat_col < len(r) and str(r[cat_col]).strip()]
    catalyst = cat_cells[0] if cat_cells else None

    tbl = (m.get("label") or "").strip().rstrip(".")
    if not tbl:
        tbl = unit["unit_id"].split("-")[-1]

    entries = []
    dropped_blank = []
    for r in rows:
        if not r or ycol is None or ycol >= len(r):
            continue
        raw = str(r[ycol])
        val = _num(raw)
        if val is None or val > 100.5:
            continue
        tag = str(r[0]).strip()
        if tag in blanks:
            dropped_blank.append(f"{tbl}, entry {tag or '?'}")
            continue  # 空白对照（催化剂 0）不是催化结果，不进参照池
        notes = []
        if not NUM_ANY.search(re.sub(r"[a-zA-Z\*\[\]\(\)]", "", raw.strip())):
            notes.append(f"原始单元格非纯数值：{raw!r}（数值为按首位数字提取，需人工确认）")
        entries.append({
            "unit_id": unit["unit_id"],
            "row": r,
            "locator": f"{tbl}, entry {str(r[0]).strip() or '?'}",
            "value": val,
            "value_raw": raw,
            "notes": notes,
            "catalyst_raw": (str(r[cat_col]).strip()
                             if cat_col is not None and cat_col < len(r) else None),
        })

    return {
        "unit_id": unit["unit_id"],
        "corpus_id": unit["corpus_id"],
        "reaction_class": unit["reaction_class"],
        "header": header,
        "scope_columns": scope_cols,
        "source": {
            "citation": clean_title(m.get("article_title"), 200),
            "journal": m.get("journal"),
            "year": m.get("year"),
            "doi": m.get("doi"),
            "pmcid": unit["corpus_id"],
            "label": m.get("label"),
            "caption": cap,
            "footnote": fn,
            # 催化剂形态常只写在题注/正文首段（"hydrogel nanocatalyst"、"supported Pd"），
            # 表里只有 mol% 用量。不带上下文，96 条里 40 条档位算不出来。
            "table_context": (ctx or "")[:600],
        },
        "caliber": {"outcome_type": unit["cal_type"], "quote": unit["cal_quote"],
                    "from": unit["cal_from"]},
        "parsed": {
            "aryl_halide": halide,
            "partner_field": partner_field,
            "partner": partner,
            "generic_substrate": generic,
            "substrate_in_rows": bool(scope_cols),
            "scope_columns": scope_cols,
            "blank_controls": dropped_blank,
            "bare_names": bare,
            "base": base,
            "solvent": solvent,
            "temp_c": float(temp.group(1)) if temp else None,
            "time_h": float(t_h.group(1)) if t_h else (round(float(t_min.group(1)) / 60, 2) if t_min else None),
            "internal_standard": std.group(0) if std else None,
            "catalyst": catalyst,
            "feeds": feeds,
            "label_map_size": len(labels),
            "partner_candidates": [f"{f}:{n}" for f, n in cands][:6],
            # 底物证据推翻标题关键词时记下（"heck→sonogashira"），供审计与文档引用
            "class_corrected": class_fix,
            "reaction_class_data": kind_from_data,
        },
        "entries": entries,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--triage", default="data/corpus/triage_2026-10-31.json")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()

    triage = json.loads((ROOT / args.triage).read_text(encoding="utf-8"))
    units = build_units(triage)
    extracted = [extract(u) for u in units]

    total = sum(len(e["entries"]) for e in extracted)
    print(f"进入晋升候选的表 {len(extracted)} 张，可抽出的 entry {total} 条")
    missing = {"aryl_halide": 0, "partner": 0, "base": 0, "solvent": 0, "temp_c": 0}
    for e in extracted:
        for k in missing:
            if not e["parsed"].get(k):
                missing[k] += 1
    print("字段缺失（表数）:", missing)

    if args.dry:
        for e in extracted[:3]:
            print("=" * 70)
            print(e["unit_id"], e["reaction_class"], "|", e["caliber"])
            print("  parsed:", json.dumps(e["parsed"], ensure_ascii=False)[:500])
            for x in e["entries"][:4]:
                print("   ", x["locator"], "→", x["value"], "|", x["catalyst_raw"])

    if args.write:
        out = {
            "schema": "chemcritic.promote.v1",
            "notice": ("晋升候选：脚本按规则从语料层抽出的 entry 形状，尚未人工核验。"
                       "核验通过才写 data/records.json，evidence_grade 最高 candidate。"),
            "counts": {"tables": len(extracted), "entries": total},
            "tables": extracted,
        }
        INCOMING.mkdir(parents=True, exist_ok=True)
        dst = INCOMING / "corpus_promote_2026-10-31.json"
        dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print("→", dst.relative_to(ROOT))


if __name__ == "__main__":
    main()
