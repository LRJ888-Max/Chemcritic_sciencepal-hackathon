"""重算 data/records.json 里语料晋升记录的**展示型字段**（label_raw / citation / catalyst）。

背景（2026-10-31 收尾）：
1. 旧 `_caliber` 用固定 ±30 字符窗口截取原文，`citation` 用 `[:120]` 硬截，
   于是 22 条记录的 `label_raw` 是 `, catalyst, base and solvent.bIsolated yield.`
   这种以标点开头、脚注上标粘连的残片；60 条 citation 在单词中间被切断；
   另有 12 条 citation 里带着 PMC 内嵌 `<svg>` 标记。
2. `catalyst` 取的是**第 1 列**：那张表第 1 列是 Solvent/Olefin/R1/底物编号时，
   26 条记录的催化剂名变成 "DMF"、"Styrene"、"NS"、"Catalyst"（表头自己）。
   这是化学错话，不是排版问题。

本脚本只改这些字段，判定输入（substrate / outcome / base / solvent / 档位）不动，并且：

* 重算出来的口径类型必须与当前记录一致才写（不一致 → 跳过并报警，交人工看）；
* 重算档位若与当前不同 → 跳过并报警（不允许"顺手改档位"）；
* 只处理 `CORP-` 开头的语料晋升记录，手工核验的 P*/SM* 不碰；
* 默认 dry-run，`--write` 才落盘，改前改后逐条打印。

用法：
    python scripts/refix_corpus_text.py           # 只看差异
    python scripts/refix_corpus_text.py --write
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "engine"))
from quote_text import drop_trailing_marker, excerpt  # noqa: E402
import tier as tier_mod  # noqa: E402
from merge_corpus_records import _profile, _resolve_catalyst  # noqa: E402

TRIAGE = ROOT / "data" / "corpus" / "triage_2026-10-31.json"
PROMOTE = ROOT / "data" / "incoming" / "corpus_promote_2026-10-31.json"
RECORDS = ROOT / "data" / "records.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    triage = json.loads(TRIAGE.read_text(encoding="utf-8"))
    un = {u["unit_id"]: u["material"] for u in triage["units"]}
    cap = {k: (v.get("caption") or "") for k, v in un.items()}
    fn = {u["unit_id"]: (u["material"].get("footnote") or "") for u in triage["units"]}
    promote = {t["unit_id"]: t for t in json.loads(PROMOTE.read_text(encoding="utf-8"))["tables"]}
    doc = json.loads(RECORDS.read_text(encoding="utf-8"))

    changed = 0
    skipped = []
    for r in doc["records"]:
        rid = r["record_id"]
        if not rid.startswith("CORP-"):
            continue
        unit = r.get("source_id")
        p = promote.get(unit)
        if not p:
            skipped.append((rid, f"来源表 {unit} 不在晋升候选里"))
            continue
        want_type = (p.get("caliber") or {}).get("outcome_type")
        got_type = (r.get("outcome") or {}).get("outcome_type")
        if want_type != got_type:
            skipped.append((rid, f"口径不一致：记录 {got_type!r} vs 重算 {want_type!r}"))
            continue

        new_label = f'{p["caliber"]["quote"]} | 表头/题注原文：{excerpt(drop_trailing_marker(cap.get(unit), fn.get(unit)), 120)}'
        new_cit = (p.get("source") or {}).get("citation") or r.get("citation")
        old_label = (r.get("outcome") or {}).get("label_raw")
        old_cit = r.get("citation")

        # 催化剂名：按表头重新解析（旧实现取第 1 列，可能取到溶剂/烯烃/编号）
        header = (un.get(unit) or {}).get("header") or []
        loc = (r.get("locator") or "").split("entry")[-1].strip()
        entry = next((e for e in (p.get("entries") or [])
                      if str(e.get("locator") or "").split("entry")[-1].strip() == loc), None)
        cond = r.get("conditions") or {}
        old_cat = cond.get("catalyst")
        new_cat = _resolve_catalyst((entry or {}).get("catalyst_raw") or (p.get("parsed") or {}).get("catalyst"), header)
        new_prof = None
        if (new_cat or None) != (old_cat or None):
            ctx = f"{(un.get(unit) or {}).get('caption','')} {(un.get(unit) or {}).get('table_context','')}"
            new_prof = _profile(new_cat, cond.get("base"), cond.get("solvent"),
                                (p.get("parsed") or {}).get("reaction_class_data") or p.get("reaction_class") or "", ctx)
            t_old = tier_mod.tier_of(r.get("catalytic_profile") or {}).get("tier")
            t_new = tier_mod.tier_of(new_prof).get("tier")
            if t_old != t_new:
                # 不允许"顺手改档位"：档位变了就是判定结果变了，得有人签字
                skipped.append((rid, f"改催化剂名会改变档位 {t_old} → {t_new}"))
                new_cat, new_prof = old_cat, None

        if new_label == old_label and new_cit == old_cit and new_prof is None:
            continue

        changed += 1
        print(f"{rid}")
        if old_label != new_label:
            print(f"   label_raw 旧 {old_label!r}")
            print(f"   label_raw 新 {new_label!r}")
        if old_cit != new_cit:
            print(f"   citation  旧 {old_cit!r}")
            print(f"   citation  新 {new_cit!r}")
        if new_prof is not None:
            print(f"   catalyst  旧 {old_cat!r} → 新 {new_cat!r}（profile 重算，档位不变）")
        if args.write:
            r["outcome"]["label_raw"] = new_label
            r["citation"] = new_cit
            if new_prof is not None:
                r["conditions"]["catalyst"] = new_cat
                r["catalytic_profile"] = new_prof

    print(f"\n{'已写入' if args.write else '将改动'} {changed} 条；跳过 {len(skipped)} 条")
    for rid, why in skipped[:10]:
        print(f"  跳过 {rid}: {why}")

    if args.write and changed:
        # 保持原文件缩进（indent=1），否则整份重排、diff 里看不出到底改了什么
        RECORDS.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"→ {RECORDS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
