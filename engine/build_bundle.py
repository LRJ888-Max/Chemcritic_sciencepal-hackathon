"""把 engine/ 打包成平台沙箱可用的单文件（零第三方依赖）。

为什么需要它：
- 平台沙箱（或任何只能写一个文件的环境）没有 engine/ 包结构，`import gates` 会失败；
- 单文件也让"把源码贴进会话/上传一个附件"成为可行路径，而不必依赖目录结构。

产物：
    skills/chemcritic-comparability/scripts/chemcritic_engine.py

用法：
    python engine/build_bundle.py            # 生成
    python engine/build_bundle.py --check    # 只校验现有产物是否与源文件同步（CI 用）

维护约定：engine/ 是唯一事实来源；产物文件头写明"请勿直接编辑"。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "engine"
RECORDS_PATH = ROOT / "data" / "records.json"
OUT_PATH = ROOT / "skills" / "chemcritic-comparability" / "scripts" / "chemcritic_engine.py"

SECTIONS = [
    ("schemas", "数据结构（无业务判断；原文未报告的字段一律 None）"),
    ("input_audit", "用户输入来源审计（禁止历史记忆拼装判定输入）"),
    ("profiles", "档位字段补丁表（记录缺 catalytic_profile 时兜底）"),
    ("tier", "档位判定（受控词表 → 规则 → 主判 / 备选）"),
    ("chemsense", "化学感知层（RDKit 同一性 / 官能团描述；无 rdkit 自动降级）"),
    ("gates", "8 维门槛判定（确定性，LLM 无权覆盖）"),
    ("placement", "等价类 · 档内定位 · 双档敏感性"),
    ("leverage", "补证杠杆（补哪一条解锁最多）· 语料级口径杠杆"),
    ("run_demo", "三屏演示与规则表验收矩阵（T1–T23c）"),
]

BANNER = "# " + "=" * 76


def split_docstring(text: str) -> tuple[str, str]:
    """拆出模块开头的文档字符串，正文留下（避免生成文件里出现游离字符串）。"""
    match = re.match(r'\A\s*"""(.*?)"""\s*\n', text, re.S)
    if not match:
        return "", text
    return match.group(1).strip("\n"), text[match.end():]


def as_comments(doc: str) -> str:
    if not doc:
        return ""
    return "\n".join("# " + line if line.strip() else "#" for line in doc.splitlines())


def clean(name: str, body: str) -> str:
    """去掉包内导入与 `sys.path` 操作——合并后这些符号已在同一命名空间。"""
    body = re.sub(r"^from __future__ import annotations\n", "", body, flags=re.M)
    body = re.sub(r"^import (?:json|os|sys)\n", "", body, flags=re.M)
    # 头部的统一导入已经覆盖这些重复导入
    body = re.sub(r"^from dataclasses import .*\n", "", body, flags=re.M)
    body = re.sub(r"^from typing import .*\n", "", body, flags=re.M)

    if name == "gates":
        body = re.sub(
            r"try:  # 作为子包.*?\nUNLOCK_MESSAGES = \{",
            "UNLOCK_MESSAGES = {",
            body,
            flags=re.S,
        )
    elif name == "placement":
        body = re.sub(
            r"try:  # 作为子包导入.*?\nOUTCOME_ORDER = \(",
            "OUTCOME_ORDER = (",
            body,
            flags=re.S,
        )
        body = body.replace("tier_mod.tier_of(", "tier_of(")
        # 文件版加载器改名为私有，避免与单文件版（读内嵌数据）同名覆盖。
        body = body.replace("def load_records(", "def _load_records_from_file(")
    elif name == "chemsense":
        # 剥离独立 CLI 入口——bundle 是 __main__，留着会劫持命令行参数。
        body = re.sub(r'\nif __name__ == "__main__":.*$', "\n", body, flags=re.S)
    elif name == "leverage":
        body = re.sub(
            r"try:  # 作为子包导入.*?\nFIELD_ORDER = \{",
            "FIELD_ORDER = {",
            body,
            flags=re.S,
        )
    elif name == "run_demo":
        body = body.split("\ndef main() -> int:")[0]
        body = re.sub(r"sys\.path\.insert\(.*\n", "", body)
        body = re.sub(r"try:  # 作为子包被.*?replacement = None\n", "", body, flags=re.S)
        body = re.sub(r"^import gates.*\n", "", body, flags=re.M)
        body = re.sub(r"^import input_audit.*\n", "", body, flags=re.M)
        body = re.sub(r"^import leverage.*\n", "", body, flags=re.M)
        body = body.replace("input_audit.audit_input_payload(", "audit_input_payload(")
        body = re.sub(r"^import placement as placement_mod.*\n", "", body, flags=re.M)
        body = re.sub(r"^from schemas import .*\n", "", body, flags=re.M)

    return body.strip("\n")


def records_json() -> str:
    payload = json.loads(RECORDS_PATH.read_text(encoding="utf-8"))
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if "'''" in text:
        raise SystemExit("records.json 含三引号，需要另选内嵌方式")
    return text


HEADER = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ChemCritic 判定引擎 · 单文件自包含版（零第三方依赖）。

⚠️ 本文件由 engine/build_bundle.py 自动生成，请勿直接编辑。
   改判定逻辑请改 engine/*.py，然后重新运行 `python engine/build_bundle.py`。

内嵌来源：engine/schemas.py · engine/input_audit.py · engine/profiles.py · engine/tier.py ·
          engine/gates.py · engine/placement.py · engine/leverage.py · engine/run_demo.py ·
          data/records.json

契约（平台侧唯一允许的判定入口）：
    输入  Record JSON（见 docs/integration/2026-10-27-给C-的合并指令-引擎判定接入.md §2.1）
    输出  {"subject", "inputs_source", "input_audit", "classes", "coverage", "verdicts",
           "placement", "leverage"}

铁律：verdict 三值 COMPARABLE / BLOCKED / NEEDS_EVIDENCE 只来自本文件的 judge()；
      LLM 只负责把证书翻译成用户文案，不得改写、不得补"我认为"的结论。
      `leverage` 只统计 judge() 的结果（补哪一条解锁最多），同样不由 LLM 生成。

用法：
    python chemcritic_engine.py                          # 三屏 + 补证杠杆 + 23 项自检
    python chemcritic_engine.py --selftest               # 只跑 T1–T23c（26 passed, 0 failed）
    python chemcritic_engine.py --json                   # 内置 35% 案例的结构化结论
    python chemcritic_engine.py --json --input user.json # 判定你自己的实验记录
    python chemcritic_engine.py --json --input user.json --records refs.json
    python chemcritic_engine.py --leverage               # 补证杠杆：补哪一条解锁最多（含语料级）
    python chemcritic_engine.py --judge-pair SM-001a P20 # 文献—文献两两判定（双向证书）
    python chemcritic_engine.py --audit-table table.json # 整表审计：哪些行能同框、卡在哪一维
    python chemcritic_engine.py --replicates reps.json   # 重复实验统计（均值/SD/95% CI，描述性）
    python chemcritic_engine.py --chemsense "p-bromoanisole" "4-bromoanisole"  # 化学同一性/官能团
    python chemcritic_engine.py --list-records           # 全部参照的引用/定位/口径/档位
    python chemcritic_engine.py --brief --input user.json # 证书摘要（几百字节，降级贴回会话）
    python chemcritic_engine.py --version

退出码：0 判定成功 / 自检全过；1 自检失败；2 输入不合法。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field, replace as replacement
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
'''

RUNNER = r'''

# ============================================================================
# 内嵌数据 · data/records.json（8 条已核验 L2 记录，随文件走，不依赖工作目录）
# ============================================================================

RECORDS_JSON = r"""__RECORDS__"""

ENGINE_VERSION = "chemcritic-engine/1.4.0"
RULES_VERSION = "gates v0.5 · tier-0.1"
CONTRACT_VERSION = "sciencepal-verdict/2"


# ============================================================================
# 统一入口 · 平台契约（C-2 / C-3）
# ============================================================================

def record_from_payload(raw: Dict[str, Any]) -> Record:
    """用户输入 / 单条参照 → Record；缺 catalytic_profile 时查补丁表，再算档位。"""
    record = Record.from_dict(raw)
    record.input_audit = audit_input_payload(raw)
    if not record.catalytic_profile:
        profile = PROFILES.get(record.record_id or "")
        if profile:
            record.catalytic_profile = dict(profile)
    if record.catalytic_profile:
        meta = tier_of(record.catalytic_profile)
        meta["annotated"] = record.tier
        record.tier_meta = meta
        if meta["tier"]:
            record.tier = meta["tier"]
    return record


def load_records(path: Optional[str] = None) -> List[Record]:
    """参照记录集：默认读内嵌数据；给了 --records 则读外部文件（保留可替换性）。"""
    if path:
        with open(path, "r", encoding="utf-8-sig") as fh:
            payload = json.load(fh)
    else:
        payload = json.loads(RECORDS_JSON)
    subject = payload.get("subject") or {}
    rows = payload["records"] if isinstance(payload, dict) else payload
    records: List[Record] = []
    for raw in rows:
        if not raw.get("substrate"):
            raw["substrate"] = subject
        records.append(record_from_payload(raw))
    return records


def load_input(path: str) -> tuple:
    """读一条用户记录；也接受 {"subject": {...}, "references": [...]} 的整包形式。"""
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and isinstance(data.get("subject"), dict):
        subject = record_from_payload(data["subject"])
        refs = data.get("references") or None
        records = [record_from_payload(r) for r in refs] if refs else None
    else:
        subject, records = record_from_payload(data), None
    if subject.input_audit["status"] == "rejected":
        raise ValueError("输入来源审计失败：" + "；".join(subject.input_audit["errors"]))
    return subject, records


def build_payload(subject: Record, records: List[Record]) -> Dict[str, Any]:
    """与 engine/run_demo.py --json 完全同构的输出（这是验收 "diff 应为空" 的依据）。"""
    return {
        "subject": subject.record_id,
        "inputs_source": subject.inputs_source,
        "input_audit": subject.input_audit,
        "classes": class_summary(records),
        "coverage": coverage(records),
        "verdicts": [judge(subject, r).to_dict() for r in records],
        "placement": placement(subject, records),
        "leverage": leverage_report(subject, records),
    }


def evidence_payload(subject: Record, records: List[Record]) -> Dict[str, Any]:
    """补证杠杆：用户侧"补哪一条解锁最多" + 语料级"补哪几条记录口径可比对翻倍"。"""
    return {
        "engine": ENGINE_VERSION,
        "rules": RULES_VERSION,
        "subject": subject.record_id,
        "leverage": leverage_report(subject, records),
        "corpus": corpus_leverage(records),
    }


def list_records_payload(records: List[Record]) -> Dict[str, Any]:
    """知识库盘点：一次返回所有参照，无需用户实测值，也无需读取引擎源码。"""
    return {
        "engine": ENGINE_VERSION,
        "rules": RULES_VERSION,
        "count": len(records),
        "records": [
            {
                "record_id": r.record_id,
                "source_id": r.source_id,
                "citation": r.citation,
                "doi": r.doi,
                "locator": r.locator,
                "locator_zh": r.locator_zh,
                "value": r.outcome_value,
                "outcome_type": r.outcome_type,
                "label_raw": r.label_raw,
                "calibration": calibration_level(r),
                "tier": r.tier,
                "substrate": r.substrate,
            }
            for r in records
        ],
    }


# 双侧 95% t 临界值（df 1–30，三位小数）。内嵌以摆脱 scipy 依赖（沙箱确定性）。
_T_CRIT_95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056,
    27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
}


def replicates_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """重复实验统计（Track B3）：用户自报 n 次同口径重复 → 均值 / 样本标准差 / t 置信区间。

    边界（铁律 4 不受影响）：这里只描述**用户自己这组数据**的分散程度，
    不构成对文献值的区间推断，也不是"正常范围"；与文献的可比性判定
    仍必须走 --json 主入口。输出文案不得写成"预期/正常可达"。
    """
    import statistics as _stats
    raw = data.get("values") if isinstance(data, dict) else None
    values: List[float] = []
    for v in raw or []:
        try:
            values.append(float(v))
        except (TypeError, ValueError):
            continue
    if len(values) < 2:
        raise ValueError("重复实验统计至少需要 2 个数值（values 数组）")
    n = len(values)
    mean = _stats.fmean(values)
    if n == 2:
        # n=2 时样本标准差仍可算，但置信区间极宽——照实给并提示
        pass
    sd = _stats.stdev(values) if n >= 2 else None
    sem = sd / (n ** 0.5) if sd is not None else None
    df = n - 1
    t = _T_CRIT_95.get(df, 1.960)  # df>30 用正态近似，注明
    ci_half = t * sem if (sem is not None) else None
    return {
        "replicates": {
            "n": n,
            "values": values,
            "mean": round(mean, 2),
            "sd_sample": round(sd, 3) if sd is not None else None,
            "sem": round(sem, 3) if sem is not None else None,
            "t_crit_95": t,
            "df": df,
            "ci95": [round(mean - ci_half, 2), round(mean + ci_half, 2)]
            if ci_half is not None else None,
            "range": [min(values), max(values)],
        },
        "boundary": [
            "这是对用户自报重复数据的描述统计，不是与文献值的比较结论。",
            "置信区间不得写进『能不能比』的判定；判定仍以 --json 主入口证书为准。",
            "不得把区间表述为『正常范围』或『预期可达』。",
            "n<3 时区间极宽，建议如实告知样本量过小。",
        ],
    }


def judge_pair_payload(records: List[Record], id_a: str, id_b: str) -> Dict[str, Any]:
    """文献—文献两两判定：回答「这两条数值能不能放进同一句话」。

    与用户判定共用同一 judge()，无新增判定逻辑；输出双向证书
    （assay 措辞审计不对称时，两向结论可能不同，对外取较严者）。
    """
    by_id = {r.record_id: r for r in records}
    missing = [i for i in (id_a, id_b) if i not in by_id]
    if missing:
        raise ValueError("未知记录 ID：" + ", ".join(missing) + "（用 --list-records 查全部 ID）")
    fwd = judge(by_id[id_a], by_id[id_b])
    rev = judge(by_id[id_b], by_id[id_a])
    return {
        "engine": ENGINE_VERSION,
        "rules": RULES_VERSION,
        "pair": [id_a, id_b],
        "comparable": fwd.verdict == COMPARABLE and rev.verdict == COMPARABLE,
        "verdict_consistent": fwd.verdict == rev.verdict,
        "forward": fwd.to_dict(),
        "reverse": rev.to_dict(),
        "note": "forward=以第一条为判定主体；两向结论不一致时，对外以较严者为准，不合并。",
    }


def _row_to_payload(raw: Dict[str, Any], index: int) -> Dict[str, Any]:
    """整表审计的行归一：接受完整记录 JSON，或 extract.py 的候选行。"""
    row = dict(raw)
    if "outcome" not in row and (row.get("values_found") or row.get("row_raw")):
        # 结果列约定在表尾（Entry 编号在首列）；先试行尾单元格，退回 values_found 末个
        value = None
        raw_row = row.get("row_raw") or ""
        cells = [c.strip() for c in raw_row.split("|")] if raw_row else []
        for cell in reversed(cells):
            try:
                x = float(cell)
            except ValueError:
                continue
            if 0 < x <= 100:
                value = x
                break
        if value is None:
            for v in reversed(row.get("values_found") or []):
                try:
                    x = float(v)
                except (TypeError, ValueError):
                    continue
                if 0 < x <= 100:
                    value = x
                    break
        row = {
            "record_id": row.get("record_id"),
            "source_id": row.get("source_id"),
            "locator": row.get("locator"),
            "evidence_grade": row.get("evidence_grade") or "imported_unchecked",
            "outcome": {
                "value": value,
                "outcome_type": row.get("outcome_type") or "unknown",
                "label_raw": row.get("row_raw") or row.get("label_raw"),
            },
        }
    if not row.get("record_id"):
        row["record_id"] = f"ROW-{index:02d}"
    return row


def audit_table_payload(rows_raw: List[Dict[str, Any]], records: List[Record]) -> Dict[str, Any]:
    """整表审计：一张文献结果表里，哪些行能同框、卡在哪一维、对库有没有可比值。

    行的 evidence_grade 由操作者声明（extract 候选默认 imported_unchecked），
    引擎不猜：未核验行按 NEEDS_EVIDENCE 处理，这是诚实性的落点。
    """
    rows = [record_from_payload(_row_to_payload(r, i)) for i, r in enumerate(rows_raw, start=1)]
    if len(rows) < 2:
        raise ValueError("整表审计至少需要 2 行")

    within = []
    blocked_by: Dict[str, int] = {}
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            v = judge(rows[i], rows[j])
            within.append({
                "a": rows[i].record_id,
                "b": rows[j].record_id,
                "verdict": v.verdict,
                "blocking_dimension": v.blocking_dimension,
                "non_independent": v.non_independent,
            })
            if v.blocking_dimension:
                blocked_by[v.blocking_dimension] = blocked_by.get(v.blocking_dimension, 0) + 1

    vs_library = []
    for row in rows:
        comparable_refs = []
        checked = 0
        for r in records:
            if r.record_id == row.record_id:
                continue
            checked += 1
            v = judge(row, r)
            if v.verdict == COMPARABLE:
                comparable_refs.append({
                    "ref": r.record_id,
                    "value": r.outcome_value,
                    "outcome_type": r.outcome_type,
                    "citation": r.citation,
                    "locator_zh": r.locator_zh,
                })
        vs_library.append({
            "row": row.record_id,
            "value": row.outcome_value,
            "outcome_type": row.outcome_type,
            "evidence_grade": row.evidence_grade,
            "label_raw": row.label_raw,
            "library_size": checked,
            "comparable_count": len(comparable_refs),
            "comparable_refs": comparable_refs[:3],
        })

    return {
        "engine": ENGINE_VERSION,
        "rules": RULES_VERSION,
        "rows": [
            {
                "row": r.record_id,
                "value": r.outcome_value,
                "outcome_type": r.outcome_type,
                "evidence_grade": r.evidence_grade,
            }
            for r in rows
        ],
        "within_pairs": within,
        "vs_library": vs_library,
        "summary": {
            "row_count": len(rows),
            "within_pair_count": len(within),
            "within_comparable": sum(1 for w in within if w["verdict"] == COMPARABLE),
            "within_blocked": sum(1 for w in within if w["verdict"] == BLOCKED),
            "within_needs_evidence": sum(1 for w in within if w["verdict"] == NEEDS_EVIDENCE),
            "blocked_by": blocked_by,
            "rows_with_library_match": sum(1 for x in vs_library if x["comparable_count"]),
        },
    }


def brief_payload(subject: Record, records: List[Record]) -> Dict[str, Any]:
    """降级路径用：几百字节的证书摘要，可直接贴回会话（本地跑 + 会话编排）。"""
    pl = placement(subject, records)
    return {
        "engine": ENGINE_VERSION,
        "rules": RULES_VERSION,
        "subject": subject.record_id,
        "inputs_source": subject.inputs_source,
        "input_audit": subject.input_audit,
        "placement": {
            "tier": pl.get("tier"),
            "status": pl.get("status"),
            "reference_values": pl.get("reference_values"),
            "delta_vs_min": pl.get("delta_vs_min"),
            "action": pl.get("action"),
        },
        "verdicts": [
            {
                "ref": v.reference_id,
                "v": v.verdict,
                "block": v.blocking_dimension,
                "calib": v.calibration,
                "cit": v.reference_meta.get("citation"),
                "loc": v.reference_meta.get("locator"),
                "loc_zh": v.reference_meta.get("locator_zh"),
            }
            for v in (judge(subject, r) for r in records)
        ],
        "next_evidence": leverage_report(subject, records)["next"],
    }



# run_demo 的三屏/验收函数按模块方式调用，这里补两个命名空间壳。
gates = SimpleNamespace(judge=judge)
placement_mod = SimpleNamespace(
    class_summary=class_summary,
    coverage=coverage,
    placement=placement,
    sensitivity=sensitivity,
    load_records=load_records,
)
leverage_mod = SimpleNamespace(
    leverage_report=leverage_report,
    corpus_leverage=corpus_leverage,
    pair_profile=pair_profile,
    render=render,
    render_corpus=render_corpus,
)


def run_selftest(records: List[Record]) -> bool:
    """T1–T18 验收矩阵；成功时最后一行是 `最终：19 passed, 0 failed`。"""
    return test_matrix(records)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chemcritic_engine.py",
        description="ChemCritic 8 维门槛判定引擎（单文件版）",
    )
    parser.add_argument("--json", action="store_true", help="输出结构化结论 JSON")
    parser.add_argument("--brief", action="store_true", help="输出证书摘要（几百字节，降级路径贴回会话用）")
    parser.add_argument("--input", metavar="PATH", help="用户记录 JSON；给出时默认按 JSON 输出")
    parser.add_argument("--records", metavar="PATH", help="替换参照记录集（默认使用内嵌记录）")
    parser.add_argument("--list-records", action="store_true", help="列出全部参照的引用、定位、口径与档位")
    parser.add_argument("--leverage", action="store_true", help="补证杠杆：补哪一条解锁最多（含语料级）")
    parser.add_argument("--judge-pair", nargs=2, metavar=("ID_A", "ID_B"),
                        help="文献—文献两两判定：这两条数值能不能放进同一句话")
    parser.add_argument("--audit-table", metavar="PATH",
                        help="整表审计：表格行 JSON（extract.py 候选或记录行）逐行判定同框与卡点")
    parser.add_argument("--replicates", metavar="PATH",
                        help="重复实验统计：n 次同口径重复 → 均值/标准差/95% t 置信区间（描述用户数据，非判定）")
    parser.add_argument("--chemsense", nargs="+", metavar="NAME",
                        help="化学感知：底物名 → 规范 SMILES/官能团；≥2 名时两两判同")
    parser.add_argument("--selftest", action="store_true", help="只跑 T1–T23c 验收矩阵")
    parser.add_argument("--version", action="store_true", help="打印引擎与规则版本")
    args = parser.parse_args(argv)

    if args.version:
        print(f"{ENGINE_VERSION} · rules={RULES_VERSION} · contract={CONTRACT_VERSION}")
        return 0

    records = load_records(args.records)

    if args.list_records:
        print(json.dumps(list_records_payload(records), ensure_ascii=False, indent=2))
        return 0

    if args.judge_pair:
        try:
            payload = judge_pair_payload(records, args.judge_pair[0], args.judge_pair[1])
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.audit_table:
        try:
            with open(args.audit_table, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"表格文件读取失败：{exc}", file=sys.stderr)
            return 2
        if isinstance(data, dict) and isinstance(data.get("candidates"), list):
            rows_raw = data["candidates"]      # extract.py --json 输出（单 PDF dict）
        elif isinstance(data, list) and data and isinstance(data[0], dict) \
                and isinstance(data[0].get("candidates"), list):
            rows_raw = [c for item in data for c in item["candidates"]]  # extract 多 PDF 数组
        elif isinstance(data, dict) and isinstance(data.get("rows"), list):
            rows_raw = data["rows"]
        elif isinstance(data, list):
            rows_raw = data
        else:
            print("表格格式不合法：需要行数组，或含 rows/candidates 数组的对象", file=sys.stderr)
            return 2
        try:
            payload = audit_table_payload(rows_raw, records)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.replicates:
        try:
            with open(args.replicates, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"重复实验文件读取失败：{exc}", file=sys.stderr)
            return 2
        try:
            payload = replicates_payload(data)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if args.chemsense:
        fn = globals().get("chemsense_payload")  # bundle：chemsense 段在同命名空间
        if fn is None:
            try:
                from engine.chemsense import chemsense_payload as fn  # 仓库内运行
            except Exception as exc:
                print(f"chemsense 不可用：{exc}", file=sys.stderr)
                return 2
        print(json.dumps(fn(list(args.chemsense)), ensure_ascii=False, indent=2))
        return 0

    subject = USER
    if args.input:
        try:
            subject, refs = load_input(args.input)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"输入记录不合法：{exc}", file=sys.stderr)
            return 2
        if refs:
            records = refs

    if args.selftest:
        return 0 if run_selftest(records) else 1

    if args.leverage:
        print(json.dumps(evidence_payload(subject, records), ensure_ascii=False, indent=2))
        return 0

    if args.brief:
        print(json.dumps(brief_payload(subject, records), ensure_ascii=False, indent=2))
        return 0

    if args.json or args.input:
        print(json.dumps(build_payload(subject, records), ensure_ascii=False, indent=2))
        return 0

    screen_one(records)
    screen_two(records)
    screen_three(records)
    screen_five(records)
    screen_four(records)
    ok = run_selftest(records)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def build() -> str:
    parts: list[str] = [HEADER]
    total = len(SECTIONS)

    for index, (name, title) in enumerate(SECTIONS, start=1):
        source = (ENGINE_DIR / f"{name}.py").read_text(encoding="utf-8")
        doc, body = split_docstring(source)
        body = clean(name, body)

        parts.append("")
        parts.append("")
        parts.append(BANNER)
        parts.append(f"# {index}/{total + 2} · {name}.py —— {title}")
        parts.append(BANNER)
        if doc:
            parts.append(as_comments(doc))
            parts.append("")
        parts.append(body)

    parts.append(RUNNER.replace("__RECORDS__", records_json()))
    return "\n".join(parts) + "\n"


def main() -> int:
    generated = build()
    if "--check" in sys.argv:
        if not OUT_PATH.exists():
            print(f"缺少产物：{OUT_PATH}")
            return 1
        current = OUT_PATH.read_text(encoding="utf-8")
        if current != generated:
            print("产物与 engine/ 不同步，请重新运行 python engine/build_bundle.py")
            return 1
        print("产物与源文件一致")
        return 0

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(generated, encoding="utf-8", newline="\n")
    lines = generated.count("\n")
    print(f"已生成 {OUT_PATH.relative_to(ROOT)}（{lines} 行，{len(generated.encode('utf-8'))} 字节）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
