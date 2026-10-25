"""文献表格抽取（候选生成器）。

定位：**只产出候选，不产出结论。**

- 每一条候选都带 `label_raw`（原文单元格逐字）与 `locator`（页/表/行），
  但 `outcome_type` 一律留 `unknown`，`evidence_grade` 一律 `imported_unchecked`。
- 口径（isolated / gc / nmr / conversion）必须由人确认，或由后续的
  `gates._label_consistency` 与原文措辞比对后才能确定。
- 这是"不许猜"原则在抽取层的落点：**抽不到就留空，不推断。**

后端（按可用性自动选择）：
1. `docling`（IBM，MIT）——首选。表格结构识别强，能把合并单元格还原成二维表。
   安装：`pip install docling`（依赖 torch，体积较大，建议装在平台沙箱里）
2. `pdfplumber`——轻量兜底，对规则表格够用。
   安装：`pip install pdfplumber`

用法：
    python engine/extract.py refs/miyaura1981.pdf
    python engine/extract.py refs/miyaura1981.pdf --pages 3,4 --json
    python engine/extract.py --all          # 抽 refs/ 下全部 PDF
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
# 表头含这些词时，单元格里的裸数字也被视为结果值（很多表格把 % 只写在表头）
YIELD_HEADER_HINTS = ("yield", "收率", "产率", "%", "conv", "转化")
BARE_NUM_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)(?![\d.])")
# 原文里出现这些词，说明该行可能含结果口径线索
OUTCOME_HINTS = ("isolated", "yield", "gc", "glc", "nmr", "conversion", "分离", "收率", "转化")


def _backend() -> str:
    import importlib.util as u
    if u.find_spec("docling"):
        return "docling"
    if u.find_spec("pdfplumber"):
        return "pdfplumber"
    raise RuntimeError(
        "没有可用的 PDF 后端。请安装其中之一：\n"
        "  pip install pdfplumber   # 轻量\n"
        "  pip install docling      # 表格结构更强，依赖 torch\n"
    )


def extract_tables(pdf_path: str, pages: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """返回 [{'page': int, 'table_index': int, 'rows': [[cell,...],...]}]。"""
    backend = _backend()
    if backend == "docling":
        return _extract_docling(pdf_path)
    return _extract_pdfplumber(pdf_path, pages)


def _extract_pdfplumber(pdf_path: str, pages: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    import pdfplumber

    out: List[Dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            if pages and pno not in pages:
                continue
            for ti, table in enumerate(page.extract_tables() or [], start=1):
                rows = [[(c or "").strip() for c in row] for row in table if row]
                if len(rows) >= 2:
                    out.append({"page": pno, "table_index": ti, "rows": rows})
    return out


def _extract_docling(pdf_path: str) -> List[Dict[str, Any]]:
    """docling 后端：把每个表格导出为二维列表。

    注意：docling 的 API 在不同版本间有变动，这里只依赖最稳定的
    `DocumentConverter` + `iterate_items()`；失败时抛出明确错误，由调用方降级。
    """
    try:
        from docling.document_converter import DocumentConverter
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"docling 导入失败，请检查安装：{exc}") from exc

    conv = DocumentConverter()
    result = conv.convert(pdf_path)
    out: List[Dict[str, Any]] = []
    for ti, table in enumerate(result.document.tables, start=1):
        try:
            frame = table.export_to_dataframe()  # pandas.DataFrame
            rows = [[("" if v is None else str(v)).strip() for v in row]
                    for row in frame.values.tolist()]
        except Exception:
            continue
        page = getattr(getattr(table, "prov", None), "__iter__", None)
        page_no = None
        try:
            page_no = table.prov[0].page_no
        except Exception:
            page = None
        if len(rows) >= 2:
            out.append({"page": page_no, "table_index": ti, "rows": rows})
    return out


def candidate_entries(tables: List[Dict[str, Any]], source_id: str) -> List[Dict[str, Any]]:
    """把表格行变成"待确认"的文献记录候选。

    只收录含百分数的行——没有数值的行不是结果行。
    """
    cands: List[Dict[str, Any]] = []
    for t in tables:
        header = " | ".join(t["rows"][0])[:200]
        header_is_yield = any(h in header.lower() for h in YIELD_HEADER_HINTS)
        for ri, row in enumerate(t["rows"][1:], start=2):
            joined = " | ".join(row)
            values = PCT_RE.findall(joined)
            note = None
            if not values and header_is_yield:
                # 表头声明是收率列，但单元格里没写 %（SM-004 的 35.0 / 91.9 就是这种）
                values = [v for v in BARE_NUM_RE.findall(joined) if 0 < float(v) <= 100]
                if values:
                    note = "数值无 % 号，依据表头判定为结果值；请确认单位"
            if not values:
                continue
            locator = f"p.{t['page']} table#{t['table_index']} row{ri}"
            cands.append({
                "source_id": source_id,
                "locator": locator,
                "table_header": header,
                "row_raw": joined,
                "values_found": values,
                "extraction_note": note,
                # 以下三项刻意留空/降级 —— 抽取器不做判断
                "outcome_type": "unknown",
                "label_raw": joined,
                "evidence_grade": "imported_unchecked",
                "needs_confirmation": True,
                "confirmation_questions": [
                    "这一行的数字是什么口径（isolated / GC / NMR / conversion）？",
                    "原文有没有说明内标或定量基准？",
                    "locator 能否直接翻到该行？",
                ],
            })
    return cands


def run(pdf_path: str, pages: Optional[List[int]] = None, as_json: bool = False) -> Any:
    tables = extract_tables(pdf_path, pages)
    source_id = os.path.splitext(os.path.basename(pdf_path))[0]
    cands = candidate_entries(tables, source_id)
    if as_json:
        return {"pdf": pdf_path, "backend": _backend(), "tables": len(tables), "candidates": cands}

    print(f"文件：{pdf_path}")
    print(f"后端：{_backend()}｜表格数：{len(tables)}｜候选行：{len(cands)}\n")
    for t in tables:
        print(f"--- p.{t['page']} table#{t['table_index']} （{len(t['rows'])} 行）---")
        for row in t["rows"][:6]:
            print("   " + " | ".join(row)[:150])
        if len(t["rows"]) > 6:
            print(f"   ...（另有 {len(t['rows']) - 6} 行）")
        print()
    print("=== 待确认候选（含有百分数的行）===")
    for c in cands[:20]:
        print(f"[{c['locator']}] {c['row_raw'][:120]}")
        print(f"    值：{c['values_found']}｜口径：待确认｜证据等级：imported_unchecked")
    if len(cands) > 20:
        print(f"...（另有 {len(cands) - 20} 条）")
    return cands


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    pages = None
    if "--pages" in args:
        i = args.index("--pages")
        pages = [int(x) for x in args[i + 1].split(",")]
        args = args[:i] + args[i + 2:]

    targets: List[str] = []
    if args and args[0] == "--all":
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "refs")
        targets = [os.path.join(root, f) for f in os.listdir(root) if f.lower().endswith(".pdf")]
    else:
        targets = args

    results = [run(t, pages, as_json) for t in targets]
    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
