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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
INCOMING = ROOT / "data" / "incoming"

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


def _caliber(text: str) -> Tuple[Optional[str], Optional[str]]:
    """返回 (outcome_type, 逐字片段)。多口径并存时取最先出现的，并保留原文供人工判断。"""
    best: Tuple[Optional[str], Optional[str]] = (None, None)
    best_pos = 10 ** 9
    for label, pat in CALIBER_PATTERNS:
        m = pat.search(text)
        if m and m.start() < best_pos:
            best_pos = m.start()
            start = max(0, m.start() - 30)
            best = (label, text[start:m.end() + 30].strip())
    return best


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
            cal_type, cal_quote = _caliber(" ".join([m["caption"], m["footnote"]]))
            cal_from = "caption_or_footnote"
        if cal_type is None:
            continue  # 口径未明 → 不进判定层（只能进语料层）
        if cal_type == "conversion":
            # 转化率不是收率，口径不同不可比；本管道只晋升真正报了 yield 的
            continue
        units.append({**u, "cal_type": cal_type, "cal_quote": cal_quote,
                      "cal_from": cal_from, "cond_text": cond_text})
    return units


def extract(unit: Dict[str, Any]) -> Dict[str, Any]:
    m = unit["material"]
    header = m["header"]
    rows = m["rows"]
    fn = m["footnote"]
    cap = m["caption"]
    ctx = m["table_context"]

    ycols = [i for i, h in enumerate(header) if YIELD_COL.search(h or "")]
    ycol = ycols[0] if ycols else None

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

    cat_cells = [str(r[1]) for r in rows if len(r) > 1]
    catalyst = cat_cells[0] if cat_cells else None

    tbl = (m.get("label") or "").strip().rstrip(".")
    if not tbl:
        tbl = unit["unit_id"].split("-")[-1]

    entries = []
    for r in rows:
        if not r or ycol is None or ycol >= len(r):
            continue
        raw = str(r[ycol])
        val = _num(raw)
        if val is None or val > 100.5:
            continue
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
            "catalyst_raw": str(r[1]).strip() if len(r) > 1 else None,
        })

    return {
        "unit_id": unit["unit_id"],
        "corpus_id": unit["corpus_id"],
        "reaction_class": unit["reaction_class"],
        "header": header,
        "source": {
            "citation": f"{m.get('article_title','')[:120]}",
            "journal": m.get("journal"),
            "year": m.get("year"),
            "doi": m.get("doi"),
            "pmcid": unit["corpus_id"],
            "label": m.get("label"),
            "caption": cap,
            "footnote": fn,
        },
        "caliber": {"outcome_type": unit["cal_type"], "quote": unit["cal_quote"],
                    "from": unit["cal_from"]},
        "parsed": {
            "aryl_halide": halide,
            "partner_field": partner_field,
            "partner": partner,
            "generic_substrate": generic,
            "bare_names": bare,
            "base": base,
            "solvent": solvent,
            "temp_c": float(temp.group(1)) if temp else None,
            "time_h": float(t_h.group(1)) if t_h else (round(float(t_min.group(1)) / 60, 2) if t_min else None),
            "internal_standard": std.group(0) if std else None,
            "catalyst": catalyst,
            "feeds": feeds,
            "label_map_size": len(labels),
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
