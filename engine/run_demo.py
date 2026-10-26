"""演示入口（骨架）：跑通"35% 分离收率 → 分堆 → 逐条判定 → 档内定位"三屏。

用法：
    python engine/run_demo.py          # 打印三屏 + 跑规则表测试矩阵
    python engine/run_demo.py --json   # 只输出结构化结论

设计原则：
- 结论全部由 gates.py 生成；本文件只做展示与断言，不含任何判断逻辑。
- 测试矩阵是"规则表可交付给 C"的验收标准（对应 docs/rules/8维门槛规则表-待签字.md §4）。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:  # 作为子包被 app/chemcritic/run_demo 调用时
    from dataclasses import replace as replacement
except ImportError:  # pragma: no cover
    replacement = None

import gates  # noqa: E402
import placement as placement_mod  # noqa: E402
from schemas import BLOCKED, COMPARABLE, NEEDS_EVIDENCE, Record  # noqa: E402

USER = Record(
    record_id="USER-001",
    outcome_type="isolated",
    outcome_value=35,
    tier="A",
    substrate={
        "aryl_halide": "4-bromoanisole",
        "boronic_acid": "phenylboronic acid",
        "product": "4-methoxybiphenyl",
    },
    conditions={
        "catalyst": "Pd(PPh3)4",
        "catalyst_mol_pct": None,
        "ligand": None,
        "ligand_mol_pct": None,
        "base": None,
        "base_eq": None,
        "solvent": "THF",
        "temp_c": 60,
        "time_h": 12,
        "atmosphere": None,
        "scale_mmol": None,
        "equivalents": {"aryl_halide": 1.0, "boronic_acid": None},
        "concentration_m": None,
    },
    assay={"method": "isolated (weighed)", "internal_standard": None, "quantification_basis": "isolated product"},
    purification=None,
    verification="user_report",
    catalytic_profile={
        "catalyst_form": "molecular_complex",
        "ligand_family": "phosphine",
        "support": "none",
        "medium": "organic",
        "activation_mode": "thermal",
    },
)

# 规则表 §4 的验收矩阵：T编号 -> (对照记录, 期望结论, 期望阻断维度)
EXPECTED = {
    "T1": ("SM-001b", COMPARABLE, None),
    "T2": ("SM-001a", BLOCKED, "outcome_type"),
    "T3": ("SM-002", COMPARABLE, None),
    "T4": ("SM-003", BLOCKED, "outcome_type"),
    "T5": ("SM-004.1", NEEDS_EVIDENCE, "outcome_type"),
    "T6": ("SM-004.7", NEEDS_EVIDENCE, "outcome_type"),
    "T7": ("SM-005", COMPARABLE, None),
    "T8": ("SM-006", BLOCKED, "outcome_type"),
    "T9": ("SM-004.7", NEEDS_EVIDENCE, "outcome_type"),  # 参照物改为 SM-006 时见 T9b
    "T10": ("SM-004.7", NEEDS_EVIDENCE, "outcome_type"),
    "T11": ("SM-002", NEEDS_EVIDENCE, "outcome_type"),
    "T12": ("SM-002", COMPARABLE, None),
    "T13": ("SM-006", NEEDS_EVIDENCE, "assay"),
    "T14": ("SM-001b", NEEDS_EVIDENCE, "assay"),
    "T15": ("SM-001b", NEEDS_EVIDENCE, "assay"),
    "T16": ("SM-001b", NEEDS_EVIDENCE, "assay"),
}


def _hr(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def screen_one(records):
    _hr("屏 1 · 分堆：8 个报告值属于哪些等价类")
    for row in placement_mod.class_summary(records):
        mark = "[OK] 可比  " if row["comparable"] else "[??] 口径未决"
        print(f"  {row['outcome_type']:<10} {mark} n={row['count']}  values={row['values']}  {row['records']}")
    cov = placement_mod.coverage(records)
    print(f"\n  可比类覆盖率：{cov['largest_class']}/{cov['total_values']} = {cov['coverage']:.1%}")
    print(f"  （分母含口径未决的 {cov['unresolved']} 条——它们一样是被报告的值，但进不了任何等价类）")


def screen_two(records, verbose=True):
    _hr("屏 2 · 逐条判定：哪些数字有资格与你同框")
    verdicts = [gates.judge(USER, r) for r in records]
    order = {COMPARABLE: 0, BLOCKED: 1, NEEDS_EVIDENCE: 2}
    for v in sorted(verdicts, key=lambda x: (order[x.verdict], x.reference_id)):
        if not verbose and v.verdict != COMPARABLE:
            continue
        ref = next(r for r in records if r.record_id == v.reference_id)
        tag = {COMPARABLE: "[OK] 可比", BLOCKED: "[XX] 禁止同框", NEEDS_EVIDENCE: "[!!] 需补证"}[v.verdict]
        print(f"  {tag:<10} {v.reference_id:<10} {str(ref.outcome_value) + '%':<7} {ref.outcome_type:<9}", end="")
        if v.verdict != COMPARABLE:
            print(f"  卡在：{v.blocking_dimension}")
            print(f"             解锁：{v.unlock_condition}")
        else:
            flags = ", ".join(f"{f['dimension']}:{f['level']}" for f in v.soft_flags) or "无"
            print(f"  差异标注：{flags}")
    return verdicts


def screen_three(records):
    _hr("屏 3 · 档内定位")
    result = placement_mod.placement(USER, records)
    if result["status"] != "PLACED":
        print(f"  {result['status']}：{result.get('reason')}")
        print(f"  下一步：{result.get('action')}")
        return result
    print(f"  你的值：{result['observed']}%  （口径 {result['outcome_type']}，档 {result['tier']}）")
    print(f"  同档可比记录：{result['comparables']}")
    print(f"  档内参照值：{result['reference_values']}")
    print(f"  与档内最低值差 {result['delta_vs_min']:+.1f} 个百分点；与最高值差 {result['delta_vs_max']:+.1f}")
    print()
    print("  结论（示例文案，需 A 签字后使用）：")
    print("    「你的 35% 不属于异常。档 A（经典膦配体体系）在此底物上的文献分离收率是 40%；")
    print("      100% 来自档 B（无配体水相），95% 来自档 C（生物法 Pd 纳米颗粒）——换条件调不到，需要换体系。」")

    meta = USER.tier_meta
    if meta.get("borderline"):
        sens = placement_mod.sensitivity(USER, records, meta["borderline"])
        print("\n  档归属敏感（%s）：" % "/".join(meta["borderline"]))
        for t, res in sens["per_tier"].items():
            print(f"    按档 {t} → 参照 {res.get('reference_values')}  status={res.get('status')}")
        print(f"    结论稳健性：{'一致' if sens['consistent'] else '不一致，需补录可观察事实'}")
    return result


def screen_four(records):
    _hr("屏 4 · 档位判定审计（字段 → 规则 → 结果，全程可复现）")
    print("  档位由 engine/tier.py 从受控词表字段计算，不读人工标注；标注仅用于交叉校验。\n")
    ok = 0
    for r in records:
        meta = r.tier_meta or {}
        rules = ", ".join(f"{x['rule_id']}" for x in meta.get("rules", [])) or "-"
        borderline = "/".join(meta.get("borderline", [])) or "-"
        annotated = meta.get("annotated") or "-"
        computed = meta.get("tier") or "-"
        same = "OK" if annotated == computed else "MISMATCH"
        ok += annotated == computed
        print(f"  {r.record_id:<10} tier={computed:<3} (标注 {annotated:<3}) {same:<8} "
              f"rules={rules:<8} borderline={borderline:<6} conf={meta.get('confidence', '-'):<7} "
              f"form={r.catalytic_profile.get('catalyst_form', '-'):<17} ligand={r.catalytic_profile.get('ligand_family', '-')}")
    print(f"\n  计算值与标注一致：{ok}/{len(records)}")
    return ok == len(records)


def test_matrix(records):
    _hr("规则表测试矩阵（验收标准）")
    by_id = {r.record_id: r for r in records}
    passed = failed = 0
    for case, (ref_id, exp_verdict, exp_dim) in EXPECTED.items():
        subject = USER
        if case == "T9":
            subject = by_id["SM-006"]
            ref_id = "SM-004.7"
        elif case == "T10":
            subject = by_id["SM-004.1"]
            ref_id = "SM-004.7"
        elif case == "T11":
            subject = Record(record_id="USER-011", outcome_type="unknown", outcome_value=35,
                             substrate=USER.substrate, conditions=USER.conditions, assay=USER.assay, tier="A")
        elif case == "T12":
            subject = Record(record_id="USER-012", outcome_type="isolated", outcome_value=35,
                             substrate=USER.substrate, conditions=USER.conditions,
                             assay={"method": "reported as yield"}, tier="A")
        elif case == "T13":
            subject = Record(record_id="USER-013", outcome_type="gc", outcome_value=66,
                             substrate=USER.substrate, conditions=USER.conditions,
                             assay={"method": "GC", "internal_standard": None}, tier="A")
        elif case == "T14":
            # 文献侧：口径标记写 isolated，但原文措辞只写 "Yield (%)" → 标记与原文矛盾
            subject = USER
        elif case == "T15":
            # 文献侧：同口径同措辞，但证据等级未达标（导入未核验）→ 不支撑数值比较
            subject = USER
        elif case == "T16":
            # conversion 口径缺内标与基准 → 转化率无法复算，必须补证
            subject = Record(record_id="USER-016", outcome_type="conversion", outcome_value=95,
                             substrate=USER.substrate, conditions=USER.conditions,
                             assay={"method": "conversion"}, tier="A")

        ref_obj = by_id[ref_id]
        if case == "T14":
            ref_obj = replacement(by_id["SM-001b"], record_id="REF-014", label_raw="Yield (%)")
        elif case == "T15":
            ref_obj = replacement(by_id["SM-001b"], record_id="REF-015",
                                  evidence_grade="imported_unchecked")
        elif case == "T16":
            ref_obj = replacement(by_id["SM-001b"], record_id="REF-016", outcome_type="conversion",
                                  label_raw="conversion (%)", assay={"method": "GC"})

        v = gates.judge(subject, ref_obj)
        ok = v.verdict == exp_verdict and (exp_dim is None or v.blocking_dimension == exp_dim)
        passed += ok
        failed += not ok
        flag = "PASS" if ok else "FAIL"
        detail = f"{v.verdict:<14} {(v.blocking_dimension or '-'):<20}"
        print(f"  [{flag}] {case:<4} {subject.record_id:<10} vs {ref_id:<10} → {detail} expect={exp_verdict}/{exp_dim or '-'}")
    print(f"\n  结果：{passed} passed, {failed} failed")
    return failed == 0


def main() -> int:
    records = placement_mod.load_records()
    if "--json" in sys.argv:
        payload = {
            "subject": USER.record_id,
            "classes": placement_mod.class_summary(records),
            "coverage": placement_mod.coverage(records),
            "verdicts": [gates.judge(USER, r).to_dict() for r in records],
            "placement": placement_mod.placement(USER, records),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    screen_one(records)
    screen_two(records)
    screen_three(records)
    tier_ok = screen_four(records)
    ok = test_matrix(records)
    return 0 if (ok and tier_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
