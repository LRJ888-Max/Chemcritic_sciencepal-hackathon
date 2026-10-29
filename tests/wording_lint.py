#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""措辞层检查（v8 版）：把「我觉得它编了」变成「命中第 N 条规则」。

用途
----
平台 Agent 的输出是模型生成的，无法用断言判，但**违规特征是可以枚举的**。
把输出粘进一个 txt，跑这个脚本，得到命中清单。同一份输出不同人跑，结论一致。

v8 相对 v5 的结构变化（本脚本已跟随）
------------------------------------
- 「五、下一轮实验」从第二层移入**第一层**（v8 行 252）。所以「出现下一轮实验代码块」
  不再是违规——反过来，缺这一节才是违规。
- 新增五节骨架检查（一/二/三/四/五，标题文字与顺序固定）。
- 新增一批禁用措辞：预期效果、尾部状态说明、过程自述、成败判断。

用法
----
    python tests/wording_lint.py draft.txt --case R1
    python tests/wording_lint.py draft.txt --case R2 --ref 40 50      # 引擎给出的合法参照值

--case 决定「用户到底给了哪些值」。用户没给的数字出现在输出里，就是编造。
--ref 是引擎本次返回的参照值，出现在输出里属于合法（不算编造）。

退出码：0 = 无硬性违规；1 = 有违规。
"""

from __future__ import annotations

import argparse
import re
import sys

# ── 每个用例：用户输入里真实出现过的数值 / 单位 ──────────────────────────
CASES = {
    "R1": {
        "user_input": "4-溴苯甲醚 1 mmol + 苯硼酸，Pd(PPh3)4，THF，60度，12小时，35% 分离收率。",
        "allowed_numbers": {1.0, 35.0, 60.0, 12.0},
        "banned_reagents": ["Na2CO3", "K2CO3", "N2", "氩", "无水"],
        "must_mention": ["可以并排比较"],
        "structure": "answer",
        "note": "用户给了 1 mmol 与 35% isolated；没给苯硼酸当量、没给 mol%、没说无水、没说碱。",
    },
    "R2": {
        "user_input": "3-溴噻吩 + 苯硼酸，Pd(PPh3)4，THF，60度，12小时，40% 分离收率。",
        "allowed_numbers": {40.0, 60.0, 12.0},
        "banned_reagents": ["Na2CO3", "K2CO3", "N2", "氩", "无水", "1.2"],
        "must_mention": ["不能和任何文献值"],
        "structure": "answer",
        "note": "库外底物；不得出现任何参照值或差值，尤其不得出现「差 0 个百分点」。",
    },
    "R4": {
        "user_input": "4-溴苯甲醚 + 苯硼酸，Pd(PPh3)4，THF，60度，12小时，收了35%。",
        "allowed_numbers": {35.0, 60.0, 12.0},
        "banned_reagents": ["Na2CO3", "K2CO3", "N2", "氩", "无水", "1.2"],
        "must_mention": ["定量方式"],
        "structure": "clarify",
        "note": "口径未定；必须追问，不得自行定为分离收率。",
    },
}

# ── 硬性违规：命中任一即不合格 ─────────────────────────────────────────
BANNED = [
    ("W1", r"(如|例如)\s*[:：]?\s*K[₂2]?CO[₃3]", "禁令：不得替用户指定碱（「如 K₂CO₃ 2 当量」）"),
    ("W2", r"(如|例如)\s*[:：]?\s*Pd\(PPh3\)4\s*\d", "不得在示例里替用户指定载量"),
    ("W3", r"PPh[₃3]\)?\s*[₄4]\s*[0-9.]+\s*mol", "载量若用户未给，不得写成用户给出或用具体值"),
    ("W4", r"1\.2\s*(mmol|当量|eq)", "当量比若用户未给，不得按常规过量补成 1.2"),
    ("W5", r"无水\s*THF|THF\s*无水|无水体系", "用户只说 THF，不得补成「无水」"),
    ("W6", r"正常范围|正常区间|文献范围", "铁律 5：不得输出正常范围 / 区间"),
    ("W7", r"(\d+)\s*[–\-~～]\s*(\d+)\s*%", "铁律 4：不得输出区间（如 40–100%）"),
    ("W8", r"\bLet me\b|\bWait\s*—|\bI must\b|</?think|Then\s+\"|and STOP|The research state", "思维链 / 自我指令泄漏进输出"),
    ("W9", r"\bL[12]\b|\bD[1-7]\b", "提示词禁术语：8 维、硬门槛、L1/L2、D3 一律不出现"),
    ("W10", r"SM-0\d\d|P\d\d\b", "提示词禁记录编号：改成作者 + 年份 + 期刊卷页 + 表号"),
    ("W12", r"预期提升|预计提升|可提升至|通常可达|普遍\s*9\d\s*%", "铁律 12：禁止预期效果类表述"),
    ("W14", r"^\s*(让我|我想|我来|经过分析|我的判断是|推理如下|考虑到)", "v8 行 70：禁止思考式开场"),
    ("W15", r"我调用了引擎|引擎返回|JSON 里|bootstrap|git clone|\d+\s*passed|ENGINE_READY",
     "v8 行 72-73：禁止引擎与工具的过程描述"),
    ("W16", r"偏低|偏高|异常|失败|未达标|可解释|与预期相符|表现不错",
     "铁律 5：不得判成败"),
    ("W17", r"^\s*[^。\n]*原文未注明定量依据[^。\n]*$", "v8 行 230：口径未决时必须复述两处原文，只说「未注明」不合格"),
    ("W18", r"相差\s*0\s*个百分点|差\s*0\s*个百分点|差值为\s*0", "跨底物算差值的硬证据：库外底物不得出现 0 差值"),
    ("W20", r"气氛不同[（(].{0,12}(None|您未记录)|不同[：:]\s*None\s*vs", 
     "v8 行 240：缺失不是差异——「气氛未记录」不得写成「气氛不同」"),
]

# ── 结构性要求 ─────────────────────────────────────────────────────────
STRUCT_COMMON = [
    ("S3", r"展开", "结尾必须有「回复『展开』…」的停止线"),
]

# 尾部状态说明：v8 行 200 规定的开头句本身就是「已完成对您…的判定」，
# 所以这条禁令只能作用于结尾，不能全文匹配（否则每条合规输出都会误报）。
TAIL_BANNED = [
    ("W13", r"分析已完成|已保存至记忆|本轮交付|可供后续迭代|^\s*已完成[。\s]*$|^\s*已交付",
     "v8 行 74：禁止尾部状态说明"),
]

# v8 五节骨架（行 205/214/235/244/252），标题文字与顺序固定
V8_SECTIONS = [
    ("S5", r"判定结果[:：]", "第一层必须以「## 判定结果：<底物对> Suzuki 偶联」开头"),
    ("S6", r"一、本次用到的条件", "缺第一节「本次用到的条件」"),
    ("S7", r"二、能不能与文献值并排比较", "缺第二节「能不能与文献值并排比较」"),
    ("S8", r"三、为什么", "缺第三节「为什么」"),
    ("S9", r"四、下一步该做什么", "缺第四节「下一步该做什么」"),
    ("S10", r"五、下一轮实验", "缺第五节「下一轮实验」（v8 已把它移入第一层）"),
]

STRUCT_CLARIFY = [
    ("S4", r"定量方式|哪种定量|收率口径", "追问型输出必须问清收率口径"),
]


def lint(text: str, case_id: str | None, ref_values: set[float]) -> int:
    lines = text.splitlines()
    hard = 0

    print(f"字数：{len(text)}　行数：{len(lines)}")
    if case_id:
        c = CASES[case_id]
        print(f"用例：{case_id}　用户原话：{c['user_input']}")
        print(f"　　{c['note']}")
    if ref_values:
        print(f"引擎参照值（合法，不计编造）：{sorted(ref_values)}")
    print()

    print("── 硬性违规 ──")
    for rid, pat, msg in BANNED:
        hits = []
        for i, ln in enumerate(lines, 1):
            if re.search(pat, ln, flags=re.IGNORECASE):
                hits.append((i, ln.strip()[:90]))
        if hits:
            hard += 1
            print(f"  {rid}  ✗  {msg}")
            for i, ln in hits[:3]:
                print(f"        L{i}: {ln}")
        else:
            print(f"  {rid}  ✓")

    # 尾部单独检查：v8 规定的开头句就含「已完成」，不能全文匹配
    tail_start = max(0, len(lines) - 5)
    for rid, pat, msg in TAIL_BANNED:
        hits = [(i, ln.strip()[:90]) for i, ln in enumerate(lines, 1)
                if i > tail_start and re.search(pat, ln, flags=re.IGNORECASE)]
        if hits:
            hard += 1
            print(f"  {rid}  ✗  {msg}")
            for i, ln in hits[:3]:
                print(f"        L{i}: {ln}")
        else:
            print(f"  {rid}  ✓")

    print()
    print("── 编造数值（数字 + 单位，且用户没给、引擎也没给）──")
    if not case_id:
        print("  （未指定 --case，跳过）")
    else:
        allowed = set(CASES[case_id]["allowed_numbers"]) | ref_values
        found = {}
        for i, ln in enumerate(lines, 1):
            for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(mmol|mol\s*%|当量|eq)(?![a-zA-Z])", ln):
                val = float(m.group(1))
                if val in allowed:
                    continue
                found.setdefault(m.group(0).strip(), []).append(i)
        if found:
            hard += 1
            print("  X1  ✗ 以下数值用户未提供、引擎也未给出，疑似编造：")
            for k, ls in found.items():
                print(f"        {k}　出现在 L{','.join(map(str, ls[:4]))}")
        else:
            print("  X1  ✓ 未发现编造数值")

    print()
    print("── 编造试剂（用户没提过的试剂/条件词）──")
    if not case_id:
        print("  （未指定 --case，跳过）")
    else:
        bad = CASES[case_id]["banned_reagents"]
        found = {}
        for i, ln in enumerate(lines, 1):
            # 只在「声称用户给出」的行里抓：那是唯一会产生假溯源的位置。
            # 标了「未提供 / 待确认」的行里出现试剂名属于合规（那是在陈述缺什么）。
            if "用户给出" not in ln:
                continue
            for w in bad:
                if w in ln:
                    found.setdefault(w, []).append(i)
        if found:
            hard += 1
            print("  X2  ✗ 以下试剂/条件用户从未提及，却被标注为「用户给出」：")
            for k, ls in found.items():
                print(f"        {k}　出现在 L{','.join(map(str, ls[:4]))}")
        else:
            print("  X2  ✓ 未发现假溯源")

    print()
    print("── 结构 ──")
    kind = CASES[case_id]["structure"] if case_id else "answer"
    rules = STRUCT_COMMON + (V8_SECTIONS if kind == "answer" else STRUCT_CLARIFY)
    for sid, pat, msg in rules:
        if re.search(pat, text):
            print(f"  {sid}  ✓")
        else:
            print(f"  {sid}  ✗  {msg}")
            hard += 1

    print()
    print("── 必含项 ──")
    if case_id:
        for phrase in CASES[case_id]["must_mention"]:
            if phrase in text:
                print(f"  M  ✓ 「{phrase}」")
            else:
                print(f"  M  ✗ 缺少「{phrase}」")
                hard += 1
    else:
        print("  （未指定 --case，跳过）")

    print()
    print(f"  硬性问题合计：{hard}")
    return 0 if hard == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="平台输出的措辞层检查（v8）")
    ap.add_argument("path", nargs="?", help="粘好的输出文本；省略则从 stdin 读")
    ap.add_argument("--case", choices=sorted(CASES), help="对应用例（决定用户给过哪些值）")
    ap.add_argument("--ref", nargs="*", type=float, default=[], help="引擎本次返回的参照值")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if args.path:
        with open(args.path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    return lint(text, args.case, set(args.ref))


if __name__ == "__main__":
    sys.exit(main())
