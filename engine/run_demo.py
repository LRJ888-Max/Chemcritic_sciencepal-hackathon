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
import input_audit  # noqa: E402
import leverage as leverage_mod  # noqa: E402
import placement as placement_mod  # noqa: E402
from schemas import BLOCKED, COMPARABLE, FAIL, NEEDS_EVIDENCE, PASS, Record  # noqa: E402

USER = Record(
    record_id="USER-001",
    outcome_type="isolated",
    outcome_value=35,
    label_raw="分离收率（柱层析称重）",
    label_raw_provenance="verbatim",
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
    "T8": ("SM-006-e1", BLOCKED, "outcome_type"),
    "T9": ("SM-004.7", NEEDS_EVIDENCE, "outcome_type"),  # 参照物改为 SM-006 时见 T9b
    "T10": ("SM-004.7", NEEDS_EVIDENCE, "outcome_type"),
    "T11": ("SM-002", NEEDS_EVIDENCE, "outcome_type"),
    "T12": ("SM-002", NEEDS_EVIDENCE, "assay"),  # 用户只说 35%，未声明口径 → 维度 4 第一层：措辞不支撑 isolated
    "T13": ("SM-006-e1", COMPARABLE, None),
    "T14": ("SM-001b", NEEDS_EVIDENCE, "assay"),
    "T15": ("SM-001b", NEEDS_EVIDENCE, "assay"),
    "T16": ("SM-001b", COMPARABLE, None),
    "T17": ("SM-001b", COMPARABLE, None),
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


def screen_five(records):
    _hr("屏 5 · 补证杠杆：补哪一条，解锁最多")
    report = leverage_mod.leverage_report(USER, records)
    for line in leverage_mod.render(report):
        print(line)
    corpus = leverage_mod.corpus_leverage(records)
    print("\n  —— 语料级（补哪几条记录的口径，能新增最多可比对）")
    for line in leverage_mod.render_corpus(corpus):
        print(line)
    return report


def test_matrix(records):
    _hr("规则表测试矩阵（验收标准）")
    by_id = {r.record_id: r for r in records}
    passed = failed = 0
    for case, (ref_id, exp_verdict, exp_dim) in EXPECTED.items():
        subject = USER
        if case == "T9":
            subject = by_id["SM-006-e1"]
            ref_id = "SM-004.7"
        elif case == "T10":
            subject = by_id["SM-004.1"]
            ref_id = "SM-004.7"
        elif case == "T11":
            subject = Record(record_id="USER-011", outcome_type="unknown", outcome_value=35,
                             substrate=USER.substrate, conditions=USER.conditions, assay=USER.assay, tier="A")
        elif case == "T12":
            subject = Record(record_id="USER-012", outcome_type="isolated", outcome_value=35, label_raw="35%（用户只说 35%，未说明口径）",
                             substrate=USER.substrate, conditions=USER.conditions,
                             assay={"method": "reported as yield"}, tier="A")
        elif case == "T13":
            subject = Record(record_id="USER-013", outcome_type="gc", outcome_value=66, label_raw="GC 66%",
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
            subject = Record(record_id="USER-016", outcome_type="conversion", outcome_value=95, label_raw="转化率 95%",
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

    # T17 · C 方案：无内标不阻断比较，但必须标 uncalibrated，且不进档内定位结论
    ref_uncal = replacement(by_id["SM-001b"], record_id="REF-017", outcome_type="gc",
                            label_raw="GC yield",
                            assay={"method": "1H-NMR", "internal_standard": None})
    subj_uncal = Record(record_id="USER-017", outcome_type="gc", outcome_value=95, label_raw="GC 95%",
                        substrate=USER.substrate, conditions=USER.conditions,
                        assay={"method": "1H-NMR", "internal_standard": None}, tier="A")
    v17 = gates.judge(subj_uncal, ref_uncal)
    pl17 = placement_mod.placement(subj_uncal, [ref_uncal])
    ok17 = (v17.verdict == COMPARABLE and v17.calibration == "uncalibrated"
            and pl17["status"] == "NO_CALIBRATED_REFERENCE")
    passed += ok17
    failed += not ok17
    print(f"  [{'PASS' if ok17 else 'FAIL'}] T17  user(gc,无内标) vs ref(无内标) → "
          f"{v17.verdict}/{v17.calibration}, placement={pl17['status']}  "
          f"expect=COMPARABLE/uncalibrated, NO_CALIBRATED_REFERENCE")
    print(f"\n  C 方案验收：{passed} passed, {failed} failed（含 T17）")

    # T18 · 同篇不同条目必须标 non_independent（B 指出该字段一直缺）
    v18 = gates.judge(by_id["SM-001a"], by_id["SM-001b"])
    ok18 = v18.non_independent is True
    passed += ok18
    failed += not ok18
    print(f"  [{'PASS' if ok18 else 'FAIL'}] T18  SM-001a vs SM-001b → "
          f"non_independent={v18.non_independent}  expect=True")

    print(f"\n  最终：{passed} passed, {failed} failed（含 T17/T18）")

    # T19 · 迁移目标必须同底物（2026-10-28：此前会把溴苯的 P20 当迁移目标）
    pl = placement_mod.placement(USER, records)
    mt_ids = [t["record_id"] for t in pl.get("migration_targets", [])]
    by_mt = {r.record_id: r for r in records}
    ok19 = (
        bool(mt_ids)
        and all((by_mt[i].substrate or {}).get("aryl_halide") == "4-bromoanisole" for i in mt_ids)
        and "P20" not in mt_ids
        and pl.get("delta_vs_min") == -5
    )
    passed += ok19
    failed += not ok19
    print(f"  [{'PASS' if ok19 else 'FAIL'}] T19  迁移目标同底物 → {mt_ids}  expect 仅 4-溴苯甲醚，delta=-5")

    # T20 · 输入来源审计：记忆不得进入判定；unknown 必须对应 null；当前对话原话可通过
    valid_raw = {
        "outcome": {"value": 35, "outcome_type": "isolated", "label_raw": "分离收率"},
        "conditions": {"base": None},
        "inputs_source": {"outcome.outcome_type": "user_stated", "conditions.base": "unknown"},
    }
    memory_raw = {
        "conditions": {"base": "Na2CO3"},
        "inputs_source": {"conditions.base": "memory"},
    }
    unknown_bad_raw = {
        "conditions": {"base": "Na2CO3"},
        "inputs_source": {"conditions.base": "unknown"},
    }
    a_valid = input_audit.audit_input_payload(valid_raw)
    a_memory = input_audit.audit_input_payload(memory_raw)
    a_unknown = input_audit.audit_input_payload(unknown_bad_raw)
    ok20 = (a_valid["status"] == "pass" and a_memory["status"] == "rejected"
            and a_unknown["status"] == "rejected")
    passed += ok20
    failed += not ok20
    print(f"  [{'PASS' if ok20 else 'FAIL'}] T20  输入来源审计 → valid={a_valid['status']}, "
          f"memory={a_memory['status']}, unknown非空={a_unknown['status']}")

    # T21 · 全部参照都有中英定位；无实测值的提前返回也带引用与原文措辞
    no_value = replacement(USER, outcome_value=None)
    v21 = gates.judge(no_value, by_id["SM-004.1"])
    meta21 = v21.reference_meta
    ok21 = (
        all(r.citation and r.locator and r.locator_zh for r in records)
        and meta21.get("locator_zh")
        and "Yield (%)" in (meta21.get("label_raw") or "")
        and "total conversion" in (meta21.get("label_raw") or "")
    )
    passed += ok21
    failed += not ok21
    print(f"  [{'PASS' if ok21 else 'FAIL'}] T21  参照元数据完整 → 中英定位齐全，"
          f"无实测值证书仍带 label_raw/locator_zh")

    # T22 · 即使用户 JSON 错标 isolated，"收了35%"也不得产生档内差值
    ambiguous = Record(
        record_id="USER-022", outcome_type="isolated", outcome_value=35,
        label_raw="收了35%", substrate=USER.substrate, conditions=USER.conditions,
        assay=USER.assay, tier="A",
    )
    v22 = gates.judge(ambiguous, by_id["SM-001b"])
    pl22 = placement_mod.placement(ambiguous, records)
    ok22 = (v22.verdict == NEEDS_EVIDENCE and v22.blocking_dimension == "assay"
            and pl22["status"] == "UNDEFINED" and pl22.get("delta_vs_min") is None)
    passed += ok22
    failed += not ok22
    print(f"  [{'PASS' if ok22 else 'FAIL'}] T22  模糊口径双重拦截 → "
          f"verdict={v22.verdict}/{v22.blocking_dimension}, placement={pl22['status']}")

    # T23 · 补证杠杆：FAIL 的对绝不能被算成"可解锁"；曲线单调；口径未声明时只指向一条
    rep = leverage_mod.leverage_report(USER, records)
    rep2 = leverage_mod.leverage_report(USER, records)
    permanent_fail = all(
        row["verdict"] == BLOCKED
        for row in leverage_mod.pair_profile(USER, records)
        if row["permanent"]
    )
    curve = [c["pairs"] for c in rep["curve"]]
    plan_fields = {step["field"] for step in rep["plan"]}
    ok23 = (
        rep["status"] == "OK"
        and rep["fixable"] + rep["permanent"] == rep["blocked"]
        and rep["blocked"] + rep["comparable"] == len(records)
        and curve == sorted(curve)
        and (not curve or curve[-1] == rep["fixable"])
        and permanent_fail
        and not (plan_fields & {"substrate:subject", "product:subject"})  # 用户底物/产物已记录，不该建议补
        and rep["plan"] == rep2["plan"]  # 确定性：同输入同计划
    )
    passed += ok23
    failed += not ok23
    print(f"  [{'PASS' if ok23 else 'FAIL'}] T23  补证杠杆 → blocked={rep['blocked']}, "
          f"fixable={rep['fixable']}, permanent={rep['permanent']}, "
          f"curve={curve}, plan={[s['field'] for s in rep['plan']]}")

    # T23b · 口径未声明时，计划只应指向"你的口径声明"，不得让用户先去补文献
    unclear = Record(record_id="USER-023", outcome_type="isolated", outcome_value=35,
                     label_raw="", substrate=USER.substrate, conditions=USER.conditions,
                     assay={"method": "reported as yield"}, tier="A")
    rep_unclear = leverage_mod.leverage_report(unclear, records)
    ok23b = (
        rep_unclear["status"] == "OK"
        and len(rep_unclear["plan"]) == 1
        and rep_unclear["plan"][0]["field"] == "caliber:subject"
    )
    passed += ok23b
    failed += not ok23b
    print(f"  [{'PASS' if ok23b else 'FAIL'}] T23b 口径未声明 → plan="
          f"{[s['field'] for s in rep_unclear['plan']]} expect 只有 caliber:subject")

    # T23c · 无实测值：不得给出补证排序，应直接要求先给数
    novalue = Record(record_id="USER-024", outcome_type="isolated", outcome_value=None,
                     label_raw="分离收率", substrate=USER.substrate, conditions=USER.conditions,
                     assay=USER.assay, tier="A")
    rep_novalue = leverage_mod.leverage_report(novalue, records)
    ok23c = rep_novalue["status"] == "NO_SUBJECT_VALUE" and not rep_novalue["plan"]
    passed += ok23c
    failed += not ok23c
    print(f"  [{'PASS' if ok23c else 'FAIL'}] T23c 无实测值 → status="
          f"{rep_novalue['status']}, plan={len(rep_novalue['plan'])} expect 空")

    # T24 · 外部壳字段兼容：yield_basis → 口径，user_verbatim → 原文措辞
    ext_raw = {
        "record_id": "USER-025",
        "outcome": {"value": 35, "yield_basis": "分离", "user_verbatim": "柱层析后称重得 35%"},
        "substrate": {"aryl_halide": "4-bromoanisole", "boronic_acid": "phenylboronic acid",
                      "product": "4-methoxybiphenyl"},
        "conditions": {"catalyst": "Pd(PPh3)4", "base": "Na2CO3", "solvent": "THF", "temp_c": 60},
        "assay": {"method": "isolated (weighed)", "quantification_basis": "isolated product"},
        "catalytic_profile": USER.catalytic_profile,
    }
    ext_rec = Record.from_dict(ext_raw)
    v24 = gates.judge(ext_rec, by_id["SM-001b"])
    ok24 = (
        ext_rec.outcome_type == "isolated"
        and ext_rec.label_raw == "柱层析后称重得 35%"
        and v24.verdict == COMPARABLE and v24.blocking_dimension is None
    )
    passed += ok24
    failed += not ok24
    print(f"  [{'PASS' if ok24 else 'FAIL'}] T24  外部壳字段 → 口径={ext_rec.outcome_type}, "
          f"措辞={'有' if ext_rec.label_raw else '无'}, verdict={v24.verdict}")

    # T25 · 名称别名归一：中文名按 name_aliases 映射后才参与底物/产物比对
    alias_raw = {
        "record_id": "USER-026",
        "name_aliases": {"4-溴苯甲醚": "4-bromoanisole", "苯硼酸": "phenylboronic acid",
                         "4-甲氧基联苯": "4-methoxybiphenyl"},
        "outcome": {"value": 35, "outcome_type": "isolated", "label_raw": "分离收率"},
        "substrate": {"aryl_halide": "4-溴苯甲醚", "boronic_acid": "苯硼酸", "product": "4-甲氧基联苯"},
        "conditions": {"catalyst": "Pd(PPh3)4", "base": "Na2CO3", "solvent": "THF", "temp_c": 60},
        "assay": {"method": "isolated (weighed)", "quantification_basis": "isolated product"},
        "catalytic_profile": USER.catalytic_profile,
    }
    alias_rec = Record.from_dict(alias_raw)
    v25 = gates.judge(alias_rec, by_id["SM-001b"])
    ok25 = (
        alias_rec.substrate.get("aryl_halide") == "4-bromoanisole"
        and v25.gates[1].status == PASS and v25.gates[2].status == PASS
    )
    passed += ok25
    failed += not ok25
    print(f"  [{'PASS' if ok25 else 'FAIL'}] T25  别名归一 → 底物={alias_rec.substrate.get('aryl_halide')}, "
          f"gate1={v25.gates[1].status}, gate2={v25.gates[2].status}")

    print(f"\n  最终：{passed} passed, {failed} failed（含 T17–T25）")
    return failed == 0


def main() -> int:
    records = placement_mod.load_records()
    if "--json" in sys.argv:
        payload = {
            "subject": USER.record_id,
            "inputs_source": USER.inputs_source,
            "input_audit": USER.input_audit,
            "classes": placement_mod.class_summary(records),
            "coverage": placement_mod.coverage(records),
            "verdicts": [gates.judge(USER, r).to_dict() for r in records],
            "placement": placement_mod.placement(USER, records),
            "leverage": leverage_mod.leverage_report(USER, records),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    screen_one(records)
    screen_two(records)
    screen_three(records)
    screen_five(records)
    tier_ok = screen_four(records)
    ok = test_matrix(records)
    return 0 if (ok and tier_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())

