#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""引擎契约回归：把人工判定过的事实固化成断言，改代码后跑一遍就知道有没有改坏。

用法
----
    # 跑当前仓库里的引擎（默认 skills/chemcritic-comparability/scripts/chemcritic_engine.py）
    python tests/engine_contract.py

    # 跑指定的引擎文件（例如从 GitHub 拉下来的沙箱版本）
    python tests/engine_contract.py --engine path/to/chemcritic_engine.py

    # 改代码之前：存基线
    python tests/engine_contract.py --snapshot tests/baseline_before.json

    # 改代码之后：存新快照并与基线对比
    python tests/engine_contract.py --snapshot tests/baseline_after.json --diff tests/baseline_before.json

退出码：0 = 全部通过；1 = 有断言失败或引擎报错。

设计纪律
--------
1. 断言描述「正确行为」，不描述「当前行为」。当前不通过的，是待修的 bug。
2. 每条断言在 cases.json 里有 rule 字段说明依据。改断言必须同时改 rule。
3. ref_set 用精确集合：多一条和少一条都算失败。多出来的通常就是底物污染。
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ENGINE = os.path.join(
    ROOT, "skills", "chemcritic-comparability", "scripts", "chemcritic_engine.py"
)
DEFAULT_CASES = os.path.join(ROOT, "tests", "cases.json")
GITHUB_RAW = (
    "https://raw.githubusercontent.com/LRJ888-Max/Chemcritic_sciencepal-hackathon"
    "/main/skills/chemcritic-comparability/scripts/chemcritic_engine.py"
)


def fetch_sandbox_engine() -> str:
    """从 GitHub main 拉取沙箱实际使用的引擎，返回本地临时路径。

    存在的意义：桌面副本常常落后于远端（本次实测：72KB vs 165KB、8 条 vs 32 条数据），
    在旧副本上测出来的结论与沙箱相反。要判沙箱行为，就必须测沙箱那份代码。
    """
    import urllib.request

    fd, path = tempfile.mkstemp(prefix="sandbox_engine_", suffix=".py")
    os.close(fd)
    with urllib.request.urlopen(GITHUB_RAW, timeout=45) as resp:
        data = resp.read()
    with open(path, "wb") as f:
        f.write(data)
    print(f"已从 GitHub main 拉取引擎：{len(data)} bytes")
    return path


def deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def run_engine(engine: str, payload: dict, workdir: str) -> tuple[dict | None, str]:
    fd, path = tempfile.mkstemp(suffix=".json", dir=workdir)
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        proc = subprocess.run(
            [sys.executable, engine, "--json", "--input", path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            return None, f"引擎退出码 {proc.returncode}：{(proc.stderr or proc.stdout)[:300]}"
        try:
            return json.loads(proc.stdout), ""
        except Exception:
            return None, f"输出不是合法 JSON：{proc.stdout[:300]}"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def observe(obs: dict) -> dict:
    """把引擎输出压成一份可对比、可存快照的观测值。"""
    pl = obs.get("placement") or {}
    vs = obs.get("verdicts") or []
    refs = pl.get("reference_values") or []
    details: list[str] = []
    for v in vs:
        for s in v.get("soft_flags") or []:
            d = s.get("detail")
            if d and d not in details:
                details.append(d)
    return {
        "status": pl.get("status"),
        "ref_values": sorted({round(float(x), 1) for x in refs if x is not None}),
        "reference_count": pl.get("reference_count"),
        "delta_vs_min": pl.get("delta_vs_min"),
        "migration_targets": [m.get("record_id") for m in (pl.get("migration_targets") or [])],
        "verdicts": sorted({v.get("verdict") for v in vs if v.get("verdict")}),
        "blocking": sorted({v.get("blocking_dimension") for v in vs if v.get("blocking_dimension")}),
        "soft_details": details,
        "placement_keys": sorted(pl.keys()),
        "input_audit": (obs.get("input_audit") or {}).get("status"),
    }


def run_list_records(engine: str) -> tuple[dict | None, str]:
    """跑 --list-records，拿引擎自报的完整登记表。"""
    proc = subprocess.run(
        [sys.executable, engine, "--list-records"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return None, f"--list-records 退出码 {proc.returncode}：{(proc.stderr or proc.stdout)[:300]}"
    try:
        return json.loads(proc.stdout), ""
    except Exception:
        return None, f"--list-records 输出不是合法 JSON：{proc.stdout[:300]}"


def check_list_records(exp: dict, catalog: dict) -> list[str]:
    """校验 --list-records 的完整性（C8）。

    依据 v8 行 99/191/192：定位一律用 locator_zh，复述原文一律逐字取 label_raw。
    缺任一字段，agent 只能退回英文 locator 或自行编造，两条都违反铁律 6。
    """
    fails: list[str] = []
    recs = catalog.get("records") or []

    if len(recs) < exp.get("count_min", 0):
        fails.append(f'记录条数  期望 ≥{exp["count_min"]}  实际 {len(recs)}')

    subs = {(r.get("substrate") or {}).get("aryl_halide") for r in recs}
    subs.discard(None)
    if len(subs) < exp.get("substrate_min", 0):
        fails.append(f'底物种类  期望 ≥{exp["substrate_min"]}  实际 {len(subs)}：{sorted(subs)}')

    for field in exp.get("required_fields", []):
        missing = [r.get("record_id") for r in recs if r.get(field) in (None, "", [])]
        if missing:
            fails.append(f'字段 {field} 为空  缺 {len(missing)} 条：{missing[:5]}')

    if exp.get("locator_zh_nonempty"):
        bad = [r.get("record_id") for r in recs
               if not str(r.get("locator_zh") or "").strip()
               or str(r.get("locator_zh")).strip() == str(r.get("locator") or "").strip()]
        if bad:
            fails.append(f"locator_zh 缺失或等于英文 locator  共 {len(bad)} 条：{bad[:5]}")

    return fails


def check(case: dict, seen: dict) -> list[str]:
    exp = case.get("expect") or {}
    fails: list[str] = []

    if "status_in" in exp:
        if seen["status"] not in exp["status_in"]:
            fails.append(f'placement.status  期望 {exp["status_in"]}  实际 {seen["status"]!r}')

    if "ref_set" in exp:
        want = sorted({round(float(x), 1) for x in exp["ref_set"]})
        got = seen["ref_values"]
        if got != want:
            extra = [x for x in got if x not in want]
            missing = [x for x in want if x not in got]
            detail = []
            if extra:
                detail.append(f"多出 {extra}")
            if missing:
                detail.append(f"缺少 {missing}")
            fails.append(
                f"reference_values  期望 {want}  实际 {got}" + (f"（{'；'.join(detail)}）" if detail else "")
            )

    if exp.get("delta_is_null") and seen["delta_vs_min"] is not None:
        fails.append(f"delta_vs_min      期望 null  实际 {seen['delta_vs_min']!r}")

    if "delta_equals" in exp:
        got = seen["delta_vs_min"]
        if got is None or abs(float(got) - float(exp["delta_equals"])) > 1e-9:
            fails.append(f'delta_vs_min      期望 {exp["delta_equals"]}  实际 {got!r}')

    for bad in exp.get("detail_not_contains", []):
        hits = [d for d in seen["soft_details"] if bad in d]
        if hits:
            fails.append(f"soft_flags.detail  不得含 {bad!r}  实际命中 {hits[:2]}")

    for dim in exp.get("blocking_contains", []):
        if dim not in seen["blocking"]:
            fails.append(f"blocking_dimension 期望含 {dim!r}  实际 {seen['blocking']}")

    if "verdicts_all_in" in exp:
        illegal = [v for v in seen["verdicts"] if v not in exp["verdicts_all_in"]]
        if illegal:
            fails.append(f"verdicts          期望全部属于 {exp['verdicts_all_in']}  实际多出 {illegal}")

    for key in exp.get("placement_keys_required", []):
        if key not in seen["placement_keys"]:
            fails.append(
                f"placement.{key} 键缺失  实际键集合 {seen['placement_keys']}"
                f"（v8 把这个字段当一定存在；缺键会让 agent 读到 undefined 后自行补值）"
            )

    return fails


def load_cases(path: str) -> tuple[dict, list[dict]]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return doc.get("base_input", {}), doc.get("cases", [])


def main() -> int:
    ap = argparse.ArgumentParser(description="引擎契约回归")
    ap.add_argument("--engine", default=DEFAULT_ENGINE, help="待测引擎文件（默认仓库内 bundle）")
    ap.add_argument("--cases", default=DEFAULT_CASES, help="用例文件（默认 tests/cases.json）")
    ap.add_argument("--snapshot", help="把本次观测值写入该 JSON 文件")
    ap.add_argument("--diff", help="与指定的旧快照对比，只报告有变化的字段")
    ap.add_argument("--quiet", action="store_true", help="只输出失败项")
    ap.add_argument("--fetch", action="store_true", help="从 GitHub main 拉取引擎再测（即测沙箱那份）")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if args.fetch:
        try:
            args.engine = fetch_sandbox_engine()
        except Exception as e:
            print(f"[FATAL] 拉取远端引擎失败：{e}")
            return 1

    if not os.path.exists(args.engine):
        print(f"[FATAL] 找不到引擎文件：{args.engine}")
        return 1

    base, cases = load_cases(args.cases)
    workdir = tempfile.mkdtemp(prefix="chemcritic_cases_")

    label = os.path.relpath(args.engine, ROOT) if args.engine.startswith(ROOT) else args.engine
    if args.fetch:
        label = "GitHub main（沙箱实际版本）"
    print(f"引擎：{label}")
    print(f"用例：{len(cases)} 条\n")

    snapshot: dict[str, dict] = {}
    passed = failed = errored = 0
    catalog_checked = False

    for case in cases:
        cid = case.get("id", "?")
        name = case.get("name", "")
        exp = case.get("expect") or {}

        # patch 为 null 的用例不走判定，走 --list-records（登记表完整性）
        if case.get("patch") is None and "list_records" in exp:
            if catalog_checked:
                continue
            catalog_checked = True
            catalog, err = run_list_records(args.engine)
            if catalog is None:
                errored += 1
                print(f"  {cid}  {name}  →  ERROR")
                print(f"      {err}\n")
                continue
            fails = check_list_records(exp["list_records"], catalog)
            snapshot[cid] = {
                "count": catalog.get("count"),
                "substrates": sorted({(r.get("substrate") or {}).get("aryl_halide")
                                      for r in (catalog.get("records") or []) if r.get("substrate")}),
            }
            if fails:
                failed += 1
                print(f"  {cid}  {name}  →  FAIL")
                for f in fails:
                    print(f"      {f}")
                if case.get("rule"):
                    print(f"      依据：{case['rule']}")
                print()
            else:
                passed += 1
                if not args.quiet:
                    print(f"  {cid}  {name}  →  pass")
            continue

        payload = deep_merge(base, case.get("patch", {}))
        obs, err = run_engine(args.engine, payload, workdir)

        if obs is None:
            errored += 1
            print(f"  {cid}  {name}  →  ERROR")
            print(f"      {err}\n")
            continue

        seen = observe(obs)
        snapshot[cid] = seen
        fails = check(case, seen)

        if fails:
            failed += 1
            print(f"  {cid}  {name}  →  FAIL")
            for f in fails:
                print(f"      {f}")
            rule = case.get("rule")
            if rule:
                print(f"      依据：{rule}")
            print()
        else:
            passed += 1
            if not args.quiet:
                print(f"  {cid}  {name}  →  pass")

    print()
    print(f"  结果：{passed} passed, {failed} failed, {errored} error（共 {len(cases)} 条）")

    if args.snapshot and snapshot:
        with open(args.snapshot, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=1, sort_keys=True)
        print(f"  快照：{os.path.relpath(args.snapshot, ROOT)}")

    if args.diff and os.path.exists(args.diff):
        with open(args.diff, encoding="utf-8") as f:
            old = json.load(f)
        print("\n  与基线对比：")
        changed = 0
        for cid in sorted(set(old) | set(snapshot)):
            a, b = old.get(cid, {}), snapshot.get(cid, {})
            keys = sorted(set(a) | set(b))
            rows = [k for k in keys if a.get(k) != b.get(k)]
            if not rows:
                continue
            changed += 1
            print(f"    {cid}")
            for k in rows:
                va, vb = a.get(k), b.get(k)
                if isinstance(va, list) and isinstance(vb, list) and (len(va) > 6 or len(vb) > 6):
                    added = [x for x in vb if x not in va]
                    removed = [x for x in va if x not in vb]
                    bits = [f"条数 {len(va)} → {len(vb)}"]
                    if added:
                        bits.append(f"新增 {len(added)} 项：{added[:3]}")
                    if removed:
                        bits.append(f"移除 {len(removed)} 项：{removed[:3]}")
                    print(f"      {k:20} " + "；".join(bits))
                else:
                    print(f"      {k:20} {va!r}  →  {vb!r}")
        if not changed:
            print("    （无变化）")

    return 0 if (failed == 0 and errored == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
