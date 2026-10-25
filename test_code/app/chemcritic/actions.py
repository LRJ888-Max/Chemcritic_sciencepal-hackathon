"""Prioritize evidence collection, not reaction optimization or diagnosis."""


def next_checks(experiment, evidence_state, goal_comparison, unknowns, sources):
    checks = []
    gaps = set(unknowns)
    results = experiment.get("results", {})

    def add(rule_id, title, fields, why, materials, resolution, limits, related_label=None):
        related = []
        for source in sources:
            if related_label and any(item["label"] == related_label and item["state"] == "unknown" and item["precedent"] is not None
                                     for item in source.get("field_comparisons", [])):
                related.append({"record_id": source["record_id"], "source_id": source["source_id"],
                                "url": source["url"], "locator": source["locator"]})
        checks.append({"priority": len(checks) + 1, "rule_id": rule_id, "title": title,
                       "triggered_fields": fields, "why": why, "requested_materials": materials,
                       "expected_resolution": resolution, "limits": limits,
                       "basis_type": "input_evidence_gap", "related_sources": related[:2]})

    if evidence_state in {"missing", "contradictory"}:
        add("A01_PRODUCT_EVIDENCE", "先核查目标产物的结构证据", ["results.characterization"],
            "当前结构证据缺失或与目标矛盾，尚不能确认所报结果对应目标产物。",
            ["核对原始表征记录、样品编号与目标产物", "记录支持与不支持目标结构的观察，由领域人员审核"],
            "补充后可重新判断结构证据状态，再决定是否恢复目标比较。",
            "系统未自动解析谱图；有表征记录不等于产物纯度或收率已确认。")

    if "results.yield_pct" in gaps:
        add("A02_OUTCOME_VALUE", "明确本次实际报告的结果", ["results.yield_pct"],
            "尚未提供可描述的结果数值。", ["从原始记录核对结果数值及计算过程"],
            "记录数值后才能检查目标差距。", "不替用户估计或补造收率。")
    if "results.yield_type" in gaps:
        add("A03_OUTCOME_TYPE", "先确认百分比的测量口径", ["results.yield_type"],
            "现有百分比的含义未明确，无法与用户目标或先例对齐。",
            ["核对是分离收率、GC/LC/NMR收率还是转化率", "记录计算基准、实际使用的定量方法及内标/校准说明（如适用）"],
            "明确口径后重新评估目标比较；文献数值比较仍保持关闭。",
            "不能凭使用了GC或NMR就推定测到的是收率，也不要求所有方法都使用内标。")
    elif goal_comparison["reason"] == "assay_mismatch":
        add("A04_GOAL_TYPE", "核对用户目标与结果是否同口径", ["results.yield_type", "user_goal.yield_type"],
            "用户目标与实测结果属于不同的测量口径。", ["确认原先设定目标的口径及当前结果定义"],
            "目标与结果对齐后再判断达标；不换算不同口径的百分比。", "不能为了得出达标结论而改写原始目标。")

    if evidence_state == "user_report_only" and any(not item.get("raw_spectrum_provided", False) for item in results.get("characterization", [])):
        add("A05_REPORTED_EVIDENCE", "核对用户报告对应的原始表征", ["results.characterization"],
            "当前仅记录用户报告结构匹配，尚无独立审核依据。",
            ["核对原谱图或表征记录与样品编号", "保留审核人和审核记录；未审核时继续标为用户报告"],
            "可区分口头报告和已留存的审核证据。", "本地程序不自动解析谱图，也不会因此自行确认纯度。")

    if not sources:
        add("A06_FIND_PRECEDENT", "补充可追溯的文献先例", [],
            "本次评估没有可用先例，无法提供逐字段文献参照。",
            ["通过SciencePal或人工检索完整底物对", "保留原文/SI链接、表格条目及结果定义，再导入复盘"],
            "获取先例后可展示条件差异；没有先例不等于不存在相关文献。",
            "不能补造来源或把库内未命中解释为反应没有先例。")

    base_fields = [key for key in ("conditions.base_status", "conditions.base_name", "conditions.base_equivalents") if key in gaps]
    if base_fields:
        add("A07_BASE_RECORD", "查清碱的原始使用记录", base_fields,
            "碱的使用信息尚不完整，相关条件无法与先例对齐。",
            ["核查称量、配液或操作记录", "记录是否加入、具体名称和当量；查不到就保留未知"],
            "补齐后可把相关条件从未知改为相同或不同。",
            "文献中加了碱不能证明本次未加碱，也不能证明碱导致结果差异。", "碱名称")

    loading_fields = [key for key in ("conditions.catalyst_loading_mol_pct", "conditions.catalyst_loading_basis") if key in gaps]
    if loading_fields:
        add("A08_LOADING_BASIS", "核对催化剂载量与计算基准", loading_fields,
            "载量或其计算基准未明确，尚不能完整解释这个mol%。",
            ["核对实际投料记录", "记录载量相对哪个组分计算；已知数值不必重新填写"],
            "补齐载量定义后才能完整描述本次反应条件。", "载量差异不自动意味着催化剂不足或过量。", "载量（mol%）")

    condition_fields = [key for key in ("conditions.scale_mmol", "conditions.solvent_volume_ml", "conditions.partner_equivalents", "conditions.atmosphere") if key in gaps]
    if condition_fields:
        add("A09_CONTEXT_RECORD", "补全规模、用量与气氛记录", condition_fields,
            "仍有条件字段缺失，后续复盘与复现缺少上下文。", ["从原操作记录核对规模、溶剂体积、投料比与气氛"],
            "能够更完整地记录条件并显示与文献的差异。", "补齐记录不会自动确定失败原因。")

    if goal_comparison["reason"] == "goal_not_set":
        add("A10_OPTIONAL_GOAL", "如需判断达标，再设定自己的目标", ["user_goal.minimum_yield_pct"],
            "未设用户目标时只报告结果与证据。", ["可选择填写目标值及结果口径，也可以继续不设目标"],
            "用户明确目标后才判断是否达到预期。", "不使用文献收率替用户设定目标。")
    # The complete followup list is retained separately; this panel stays small.
    return checks[:3]
