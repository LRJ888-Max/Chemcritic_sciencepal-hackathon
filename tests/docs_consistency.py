#!/usr/bin/env python3
"""文档里写死的数字 vs 引擎实际输出 —— 一致性检查。

为什么需要（2026-10-31）：
`SKILL.md` 是 `bootstrap.sh` 拷进沙箱、平台 agent 会读的文件。它曾写着
「自检必须看到 26 passed」（实际 29）、「内置参照 32 条」（实际 140）、
「引擎 1.1.0」（实际 1.7.2）、「corpus 496 个两两比较」（实际 9730）。
agent 读到这些会判错：以为引擎坏了、以为库里只有 32 条。

这与 `ENGINE_VERSION` 写在两处是同一类问题——**同一事实有第二个来源**。
修法不是"记得去改"，是让 CI 在数字不一致时直接红。

检查方式：文档里出现的数字，必须等于引擎此刻的实际输出。
语料增长时文档跟着改即可；不改就红——这正是目的。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "skills" / "chemcritic-comparability" / "scripts" / "chemcritic_engine.py"
SKILL = ROOT / "skills" / "chemcritic-comparability" / "SKILL.md"
PROMPT = ROOT / "docs" / "integration" / "2026-10-28-Agent提示词v8-参照格式版.md"
VERSION = ROOT / "engine" / "version.py"


def run_engine(*args: str) -> str:
    out = subprocess.run(
        [sys.executable, str(BUNDLE), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if out.returncode != 0:
        raise SystemExit(f"引擎调用失败：{' '.join(args)}\n{out.stderr[-800:]}")
    return out.stdout


def actual() -> dict[str, object]:
    """从引擎实测取值（不读任何源码常量）。"""
    selftest = run_engine("--selftest")
    m = re.search(r"最终：(\d+) passed, (\d+) failed", selftest)
    if not m:
        raise SystemExit("--selftest 输出里找不到「最终：N passed, M failed」")
    passed, failed = int(m.group(1)), int(m.group(2))

    records = json.loads(run_engine("--list-records"))
    ver_text = run_engine("--version").strip()  # 纯文本：chemcritic-engine/1.7.2 · rules=… · contract=…
    vm2 = re.search(r"chemcritic-engine/([0-9]+\.[0-9]+\.[0-9]+)", ver_text)

    # 版本号以 version.py 为准，同时要求引擎自报一致（这条 C11 也管，这里顺带守文档）
    ver_src = VERSION.read_text(encoding="utf-8")
    vm = re.search(r'ENGINE_VERSION\s*=\s*"chemcritic-engine/([0-9.]+)"', ver_src)
    if not vm:
        raise SystemExit("version.py 里找不到 ENGINE_VERSION")

    return {
        "selftest_passed": passed,
        "selftest_failed": failed,
        "record_count": int(records["count"]),
        "version": vm.group(1),
        "version_reported": vm2.group(1) if vm2 else "(未取到)",
    }


def check(label: str, doc_value: int | str, real_value: int | str, where: str, problems: list[str]) -> None:
    if str(doc_value) != str(real_value):
        problems.append(f"{where}：文档写 {doc_value}，实际 {real_value}（{label}）")


def main() -> int:
    act = actual()
    problems: list[str] = []
    checked = 0

    # ── SKILL.md ───────────────────────────────────────────────
    skill = SKILL.read_text(encoding="utf-8")

    for m in re.finditer(r"(\d+) passed, 0 failed", skill):
        checked += 1
        check("自检通过条数", m.group(1), act["selftest_passed"], f"SKILL.md:--selftest 条数", problems)

    for m in re.finditer(r"当前 (\d+) 项", skill):
        checked += 1
        check("自检项数", m.group(1), act["selftest_passed"], "SKILL.md:自检项数", problems)

    for m in re.finditer(r"共 \*\*(\d+) 条\*\*", skill):
        checked += 1
        check("记录总数", m.group(1), act["record_count"], "SKILL.md:内置参照条数", problems)

    for m in re.finditer(r"chemcritic-engine/([0-9]+\.[0-9]+\.[0-9]+)", skill):
        checked += 1
        check("引擎版本", m.group(1), act["version"], "SKILL.md:引擎版本", problems)

    # ── 提示词（只查会被 agent 当事实引用的规模数字）────────────
    prompt = PROMPT.read_text(encoding="utf-8")
    for m in re.finditer(r"参照集 (\d+) 条", prompt):
        checked += 1
        check("记录总数", m.group(1), act["record_count"], "提示词:参照集条数", problems)

    # 引擎自报版本必须与源码一致（防止 bundle 忘了重新生成）
    check("引擎自报版本", act["version_reported"], act["version"], "引擎 --version 自报", problems)

    print(f"  文档数字一致性：检查 {checked} 处")
    print(f"  实测：自检 {act['selftest_passed']} passed / {act['selftest_failed']} failed · "
          f"记录 {act['record_count']} 条 · 引擎 {act['version']}")
    if problems:
        print()
        for p in problems:
            print(f"  [FAIL] {p}")
        print(f"\n  结果：{len(problems)} 处文档数字与引擎实际不符")
        return 1
    print("  [PASS] 文档里写死的数字与引擎实际输出全部一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
