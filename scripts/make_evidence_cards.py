"""把语料库渲染成可归档的 PDF：每篇一张证据卡 + 一本总目录。

输入：data/corpus/<date>.json
输出：docs/corpus/cards/PMCxxxxxx.pdf、docs/corpus/2026-10-31-语料库目录.pdf
性质：卡片内容为 PMC 开放获取 XML 的**逐字摘录**（标题/作者/表头/数据行/脚注/上下文句），
      未人工核验，label 为 imported_unchecked，不参与引擎判定。
用法：python scripts/make_evidence_cards.py [--date 2026-10-31] [--limit N]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (Flowable, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
F = "STSong-Light"

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

H1 = ParagraphStyle("h1", fontName=F, fontSize=15, leading=20, textColor=colors.HexColor("#0C447C"))
H2 = ParagraphStyle("h2", fontName=F, fontSize=10.5, leading=15, textColor=colors.HexColor("#185FA5"))
BODY = ParagraphStyle("body", fontName=F, fontSize=8.6, leading=12.6, alignment=TA_LEFT)
SMALL = ParagraphStyle("small", fontName=F, fontSize=7.4, leading=10.4,
                       textColor=colors.HexColor("#5F5E5A"))
CELL = ParagraphStyle("cell", fontName=F, fontSize=6.9, leading=8.8)
CELLH = ParagraphStyle("cellh", fontName=F, fontSize=7.0, leading=8.8,
                       textColor=colors.HexColor("#042C53"))

CLASS_ZH = {
    "suzuki": "Suzuki–Miyaura 偶联",
    "buchwald_hartwig": "Buchwald–Hartwig 胺化",
    "sonogashira": "Sonogashira 偶联",
    "heck": "Mizoroki–Heck 反应",
    "negishi": "Negishi 偶联",
    "borylation": "Miyaura 硼化 / C–H 硼化",
    "ch_activation": "C–H 活化 / 芳基化",
    "transfer_hydrogenation": "转移氢化",
    "photoredox": "光氧化还原催化",
    "cuacc": "CuAAC 点击化学",
    "metathesis": "烯烃复分解",
    "asym_hydrogenation": "不对称氢化",
    "carbonylation": "羰基化",
    "oxidation": "氧化 / 环氧化",
    "hydroamination": "氢胺化 / C–N 偶联",
    "cycloaddition": "环加成",
    "olefination": "烯化（Wittig 等）",
    "hydrogenation": "催化氢化",
}


class Rule(Flowable):
    def __init__(self, width, thick=0.8, color="#185FA5"):
        super().__init__()
        self.width, self.thick, self.color = width, thick, color
        self.height = thick + 2

    def draw(self):
        self.canv.setStrokeColor(colors.HexColor(self.color))
        self.canv.setLineWidth(self.thick)
        self.canv.line(0, 1, self.width, 1)


def esc(s: str) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_table(t: dict) -> Table | None:
    rows = t.get("rows") or []
    header = t.get("header") or []
    if not rows:
        return None
    ncol = max(len(header), max(len(r) for r in rows))
    ncol = min(ncol, 9)
    body = []
    if header:
        body.append([Paragraph(esc(c), CELLH) for c in (header + [""] * ncol)[:ncol]])
    for r in rows[:26]:
        body.append([Paragraph(esc(c), CELL) for c in (r + [""] * ncol)[:ncol]])
    widths = [mm * 176 / ncol] * ncol
    tb = Table(body, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B4B2A9")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6F1FB")))
    tb.setStyle(TableStyle(style))
    return tb


def card(art: dict, path: str) -> None:
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=mm * 17, rightMargin=mm * 17,
                            topMargin=mm * 15, bottomMargin=mm * 14,
                            title=f"{art['corpus_id']} evidence card",
                            author="ChemCritic corpus pipeline")
    story = []
    cls = CLASS_ZH.get(art["reaction_class"], art["reaction_class"])
    story.append(Paragraph("ChemCritic 文献证据卡", H1))
    story.append(Paragraph(f"{art['corpus_id']} ｜ {cls} ｜ 采集状态 imported_unchecked", H2))
    story.append(Rule(mm * 176))
    story.append(Spacer(1, 3 * mm))

    meta = [
        ("标题", art.get("title", "")),
        ("作者", ", ".join(art.get("authors") or []) + (f" 等 {art.get('author_count')} 人"
                                                     if (art.get("author_count") or 0) > 6 else "")),
        ("出处", f"{art.get('journal', '')} {art.get('year', '')}"),
        ("DOI", art.get("doi", "")),
        ("原文链接", art.get("pmc_url", "")),
        ("许可", art.get("license", "") or "见原文 permissions 声明"),
    ]
    mrows = [[Paragraph(f"<b>{esc(k)}</b>", BODY), Paragraph(esc(v), BODY)] for k, v in meta]
    mt = Table(mrows, colWidths=[mm * 22, mm * 154])
    mt.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#D3D1C7")),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]))
    story.append(mt)
    story.append(Spacer(1, 3 * mm))

    if art.get("abstract"):
        story.append(Paragraph("摘要（原文节选）", H2))
        story.append(Paragraph(esc(art["abstract"])[:520] + " …", BODY))
        story.append(Spacer(1, 3 * mm))

    for i, t in enumerate(art.get("tables", [])[:2], 1):
        label = t.get("label") or f"表 {i}"
        story.append(Paragraph(f"数据表 · {esc(label)}（{t.get('row_count')} 行，"
                               f"含数值行 {t.get('numeric_rows')} 行）", H2))
        if t.get("caption"):
            story.append(Paragraph(esc(t["caption"])[:260], SMALL))
            story.append(Spacer(1, 1.2 * mm))
        tb = build_table(t)
        if tb:
            story.append(tb)
        if t.get("footnote"):
            story.append(Spacer(1, 1.2 * mm))
            story.append(Paragraph("脚注（逐字）：" + esc(t["footnote"])[:300], SMALL))
        if t.get("table_context"):
            story.append(Spacer(1, 1.2 * mm))
            story.append(Paragraph("正文对应句（逐字）：" + esc(t["table_context"])[:300], SMALL))
        story.append(Spacer(1, 3 * mm))

    story.append(Rule(mm * 176, 0.5, "#B4B2A9"))
    story.append(Spacer(1, 1.5 * mm))
    story.append(Paragraph(
        "本卡为 NCBI PMC 开放获取 XML 的逐字摘录（标题、作者、表格行列、脚注、正文句均未改写），"
        "用于参照集候选与覆盖面登记；未逐条人工核验，标记 imported_unchecked，"
        "不写入判定记录、不参与 ChemCritic 引擎的可比性判定。"
        "对外引用一律回到 DOI 原文核对页码与表号。", SMALL))
    doc.build(story)


def catalog(data: dict, path: str) -> None:
    arts = data["articles"]
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=mm * 14, rightMargin=mm * 14,
                            topMargin=mm * 14, bottomMargin=mm * 13,
                            title="ChemCritic 文献语料库目录")
    story = []
    c = data["counts"]
    story.append(Paragraph("ChemCritic 文献语料库 · 目录", H1))
    story.append(Paragraph(f"生成日期 {data['generated']} ｜ 来源：NCBI PMC 开放获取子集（E-utilities）", H2))
    story.append(Rule(mm * 182))
    story.append(Spacer(1, 2.5 * mm))

    stat = [
        ("文献篇数", str(c["articles"])),
        ("数值数据表", str(c["tables"])),
        ("表格数据行", str(c["rows"])),
        ("反应类型", f"{len(c['by_class'])} 类"),
        ("证据状态", "imported_unchecked（未人工核验，不参与判定）"),
    ]
    st = Table([[Paragraph(f"<b>{esc(k)}</b>", BODY), Paragraph(esc(v), BODY)] for k, v in stat],
               colWidths=[mm * 30, mm * 152])
    st.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1EFE8")),
                            ("LEFTPADDING", (0, 0), (-1, -1), 4),
                            ("TOPPADDING", (0, 0), (-1, -1), 2),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story.append(st)
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("一、按反应类型分布", H2))
    rows = [[Paragraph("反应类型", CELLH), Paragraph("篇数", CELLH),
             Paragraph("表", CELLH), Paragraph("行", CELLH)]]
    for cls in sorted(c["by_class"], key=lambda k: -c["by_class"][k]):
        sub = [a for a in arts if a["reaction_class"] == cls]
        rows.append([Paragraph(esc(CLASS_ZH.get(cls, cls)), CELL),
                     Paragraph(str(len(sub)), CELL),
                     Paragraph(str(sum(len(a["tables"]) for a in sub)), CELL),
                     Paragraph(str(sum(t["row_count"] for a in sub for t in a["tables"])), CELL)])
    t1 = Table(rows, colWidths=[mm * 110, mm * 20, mm * 20, mm * 32], repeatRows=1)
    t1.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#B4B2A9")),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E1F5EE")),
                            ("TOPPADDING", (0, 0), (-1, -1), 1.6),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.6)]))
    story.append(t1)
    story.append(PageBreak())

    story.append(Paragraph("二、文献清单", H2))
    rows = [[Paragraph("ID", CELLH), Paragraph("年", CELLH), Paragraph("期刊", CELLH),
             Paragraph("DOI", CELLH), Paragraph("表", CELLH), Paragraph("行", CELLH),
             Paragraph("标题", CELLH)]]
    for a in sorted(arts, key=lambda x: (x["reaction_class"], x["year"])):
        rows.append([Paragraph(esc(a["corpus_id"]), CELL), Paragraph(esc(a["year"]), CELL),
                     Paragraph(esc(a["journal"])[:26], CELL), Paragraph(esc(a["doi"]), CELL),
                     Paragraph(str(len(a["tables"])), CELL),
                     Paragraph(str(sum(t["row_count"] for t in a["tables"])), CELL),
                     Paragraph(esc(a["title"])[:78], CELL)])
    t2 = Table(rows, colWidths=[mm * 20, mm * 10, mm * 32, mm * 40, mm * 8, mm * 8, mm * 64],
               repeatRows=1)
    t2.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D3D1C7")),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E6F1FB")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("TOPPADDING", (0, 0), (-1, -1), 1.2),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2)]))
    story.append(t2)
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        "说明：本目录登记的是参照集候选语料，全部标记 imported_unchecked；表为关键词命中的数值数据表（收率/转化率/活性等），反应类型为检索关键词归类，纳入判定前需人工核验。"
        "ChemCritic 的判定只使用经过核验的记录（当前为 Suzuki 体系的 A_confirmed / candidate 记录）。"
        "目录规模反映可扩展的参照面，不等于已完成的判定覆盖面。", SMALL))
    doc.build(story)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(ROOT, "data", "corpus", "*.json")))
    if args.date:
        files = [f for f in files if args.date in f]
    if not files:
        print("没有语料库文件")
        return 1
    data = json.load(open(files[-1], encoding="utf-8"))

    out_dir = os.path.join(ROOT, "docs", "corpus")
    cards_dir = os.path.join(out_dir, "cards")
    os.makedirs(cards_dir, exist_ok=True)

    arts = data["articles"]
    if args.limit:
        arts = arts[: args.limit]
    ok = 0
    for a in arts:
        try:
            card(a, os.path.join(cards_dir, f"{a['corpus_id']}.pdf"))
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {a['corpus_id']} {exc}")
        if ok % 20 == 0 and ok:
            print(f"  {ok}/{len(arts)}", flush=True)

    cat = os.path.join(out_dir, f"{data['generated']}-语料库目录.pdf")
    catalog(data, cat)
    print(f"卡片 {ok} 张 -> {cards_dir}")
    print(f"目录 -> {cat}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
