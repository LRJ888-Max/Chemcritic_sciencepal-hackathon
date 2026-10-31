"""把一条人工核验过的记录追加进 data/records.json，并重建单文件引擎。

与 scripts/merge_corpus_records.py 的分工：
- merge_corpus_records.py：吃 promote_corpus.py 的表格抽取结果（自动抽取，可能抽错）
- 本脚本：吃**人工已核**的完整记录 JSON（人在原文里核对过表号/页码），
  复用同一个 check() 门槛，通过才写，写完自动重建并自检。

用法：
    python scripts/append_verified_record.py data/incoming/P40.json --dry
    python scripts/append_verified_record.py data/incoming/P40.json data/incoming/P41.json --write

为什么不用手改 records.json：
每一条都会真的参与 verdict。一条口径对不上、底物名带残渣的记录不会"只是脏数据"，
它会变成一次错误的 COMPARABLE。所以这里沿用 merge_corpus_records.check() 同一套门槛。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT / "data" / "records.json"

sys.path.insert(0, str(ROOT / "engine"))
sys.path.insert(0, str(ROOT / "scripts"))
from merge_corpus_records import check  # noqa: E402  同一套入库门槛


def rebuild() -> int:
    py = sys.executable
    for step in (
        [str(ROOT / "engine" / "build_bundle.py")],
        [str(ROOT / "engine" / "build_bundle.py"), "--check"],
        [str(ROOT / "skills" / "chemcritic-comparability" / "scripts" / "chemcritic_engine.py"), "--selftest"],
    ):
        proc = subprocess.run([py] + step, cwd=str(ROOT))
        if proc.returncode != 0:
            print(f"\n❌ 失败：{step[0].split(chr(92))[-1]} {' '.join(step[1:])}")
            return proc.returncode
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="人工核验过的记录 JSON（单个对象或对象数组）")
    ap.add_argument("--write", action="store_true", help="真的写入；不加则为 dry-run")
    ap.add_argument("--no-rebuild", action="store_true", help="只写数据，不重建引擎")
    args = ap.parse_args()

    data = json.loads(RECORDS.read_text(encoding="utf-8"))
    rows = data["records"]
    seen = {r["record_id"] for r in rows}

    accepted, rejected = [], []
    for f in args.files:
        payload = json.loads(Path(f).read_text(encoding="utf-8"))
        items = payload if isinstance(payload, list) else [payload]
        for rec in items:
            rid = rec.get("record_id", "?")
            if rid in seen:
                rejected.append((rid, "record_id 已存在（改 ID 或先确认是不是重复入库）"))
                continue
            ok, why = check(rec, seen)
            if ok:
                seen.add(rid)
                accepted.append(rec)
            else:
                rejected.append((rid, why))

    print(f"现有 {len(rows)} 条 | 接受 {len(accepted)} 条 | 拒绝 {len(rejected)} 条")
    for rid, why in rejected:
        print(f"  ✗ {rid}: {why}")
    for rec in accepted:
        o = rec.get("outcome") or {}
        sub = {k: v for k, v in (rec.get("substrate") or {}).items() if k != "product" and v}
        pair = " + ".join(f"{v}" for v in sub.values()) or "-"
        print(f"  ✓ {rec['record_id']}: {pair} → "
              f"{o.get('value')}% {o.get('outcome_type')}  {rec['doi']}")

    if not args.write:
        print("\n（dry-run，未写入。确认无误后加 --write）")
        return 0
    if rejected:
        print("\n❌ 有记录被拒，整批不写入（宁可报错，不静默补值）。")
        return 2

    rows.extend(accepted)
    RECORDS.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写入 data/records.json，总条数 {len(rows)}")

    if args.no_rebuild:
        print("⚠️  跳过了重建 —— Agent 现在还读不到新记录，记得跑 rebuild。")
        return 0
    print("\n重建引擎 ...")
    return rebuild()


if __name__ == "__main__":
    raise SystemExit(main())
