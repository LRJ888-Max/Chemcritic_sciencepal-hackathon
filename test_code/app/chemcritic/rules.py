"""Small, conservative subset of the agreed ChemCritic rules.

S1/S2/S3, literature scoring and causal diagnosis deliberately remain unset.
"""

from decimal import Decimal
import math
from urllib.parse import urlparse
from .comparison import compare_fields, publication_key
from .actions import next_checks


YIELD_TYPES = {"isolated", "gc", "nmr", "lc", "conversion", "unknown"}
BASE_STATES = {"present", "absent", "unknown"}


class ValidationError(ValueError):
    """Invalid input, with a machine-readable field path."""

    def __init__(self, field, message):
        self.field = field
        super().__init__(f"{field}: {message}")


def _object(value, path):
    if not isinstance(value, dict):
        raise ValidationError(path, "must be an object")
    return value


def _number(value, path, maximum=None, positive=False):
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(path, "must be a numeric value or null")
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValidationError(path, "must be finite and non-negative (positive for scale/time)")
    if maximum is not None and value > maximum:
        raise ValidationError(path, f"must be <= {maximum}")


def _text(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(path, "must be a non-empty string")


def validate(experiment, precedents):
    _object(experiment, "experiment")
    _text(experiment.get("experiment_id"), "experiment_id")
    if type(experiment.get("simulated")) is not bool:
        raise ValidationError("simulated", "must explicitly be true or false")
    reaction = _object(experiment.get("reaction"), "reaction")
    _text(reaction.get("target_product"), "reaction.target_product")
    substrates = reaction.get("substrates")
    if not isinstance(substrates, list) or len(substrates) != 2:
        raise ValidationError("reaction.substrates", "must contain two substrate names")
    for index, name in enumerate(substrates):
        _text(name, f"reaction.substrates[{index}]")
    conditions = _object(experiment.get("conditions", {}), "conditions")
    state = conditions.get("base_status", "unknown")
    if not isinstance(state, str) or state not in BASE_STATES:
        raise ValidationError("conditions.base_status", "expected present, absent or unknown")
    for name in ("base_name", "catalyst", "solvent", "atmosphere"):
        if conditions.get(name) is not None:
            _text(conditions[name], f"conditions.{name}")
    if state == "absent" and conditions.get("base_name") not in (None, "unknown"):
        raise ValidationError("conditions.base_name", "conflicts with base_status=absent")
    for name in ("catalyst_loading_mol_pct", "time_h", "scale_mmol", "solvent_volume_ml", "partner_equivalents", "base_equivalents"):
        _number(conditions.get(name), f"conditions.{name}", positive=name in {"scale_mmol", "solvent_volume_ml", "time_h"})
    temperature = conditions.get("temperature_C")
    if temperature is not None and (isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not math.isfinite(temperature) or temperature < -273.15):
        raise ValidationError("conditions.temperature_C", "must be a finite temperature >= -273.15")
    results = _object(experiment.get("results", {}), "results")
    _number(results.get("yield_pct"), "results.yield_pct", maximum=100)
    if not isinstance(results.get("yield_type", "unknown"), str) or results.get("yield_type", "unknown") not in YIELD_TYPES:
        raise ValidationError("results.yield_type", "unsupported outcome type")
    evidence = results.get("characterization", [])
    if not isinstance(evidence, list):
        raise ValidationError("results.characterization", "must be a list")
    for index, item in enumerate(evidence):
        _object(item, f"results.characterization[{index}]")
        _text(item.get("method"), f"results.characterization[{index}].method")
        if not isinstance(item.get("provenance"), str) or item.get("provenance") not in {"user_report", "human_review"}:
            raise ValidationError(f"results.characterization[{index}].provenance", "expected user_report or human_review")
        if type(item.get("supports_target")) is not bool:
            raise ValidationError(f"results.characterization[{index}].supports_target", "must be boolean")
        if item["provenance"] == "human_review":
            _text(item.get("review_reference"), f"results.characterization[{index}].review_reference")
    attempts = results.get("repeat_attempts")
    if attempts is not None and (type(attempts) is not int or attempts < 1):
        raise ValidationError("results.repeat_attempts", "must be a positive integer")
    goal = experiment.get("user_goal")
    if goal is not None:
        _object(goal, "user_goal")
        _number(goal.get("minimum_yield_pct"), "user_goal.minimum_yield_pct", maximum=100)
        if not isinstance(goal.get("yield_type", "unknown"), str) or goal.get("yield_type", "unknown") not in YIELD_TYPES:
            raise ValidationError("user_goal.yield_type", "unsupported outcome type")
    if not isinstance(precedents, list):
        raise ValidationError("precedents", "must be a list")
    ids = set()
    for index, source in enumerate(precedents):
        prefix = f"precedents[{index}]"
        _object(source, prefix)
        for key in ("record_id", "source_id", "locator", "url", "provenance"):
            _text(source.get(key), f"{prefix}.{key}")
        if source["record_id"] in ids:
            raise ValidationError(f"{prefix}.record_id", "duplicate record ID")
        ids.add(source["record_id"])
        url = urlparse(source["url"])
        if url.scheme not in {"http", "https"} or not url.netloc:
            raise ValidationError(f"{prefix}.url", "must be an HTTP(S) source URL")
        outcome = _object(source.get("outcome"), f"{prefix}.outcome")
        _number(outcome.get("value_pct"), f"{prefix}.outcome.value_pct", maximum=100)
        if not isinstance(outcome.get("yield_type", "unknown"), str) or outcome.get("yield_type", "unknown") not in YIELD_TYPES:
            raise ValidationError(f"{prefix}.outcome.yield_type", "unsupported outcome type")
        for key in ("conditions", "reaction", "import_metadata"):
            if key in source:
                _object(source[key], f"{prefix}.{key}")
        if "source_links" in source:
            if not isinstance(source["source_links"], list):
                raise ValidationError(f"{prefix}.source_links", "must be a list")
            for link in source["source_links"]:
                _text(link, f"{prefix}.source_links")
                parsed_link = urlparse(link)
                if parsed_link.scheme not in {"http", "https"} or not parsed_link.netloc:
                    raise ValidationError(f"{prefix}.source_links", "must contain HTTP(S) URLs")


def review(experiment, precedents=None):
    """Return facts and restrictions without modifying the supplied records."""
    precedents = [] if precedents is None else precedents
    validate(experiment, precedents)
    conditions = experiment.get("conditions", {})
    results = experiment.get("results", {})
    goal = experiment.get("user_goal") or {}
    value, target = results.get("yield_pct"), goal.get("minimum_yield_pct")
    actual_type = results.get("yield_type", "unknown")
    target_type = goal.get("yield_type", "unknown")
    unknowns, followups, blocked = [], [], {"causal_attribution"}

    def missing(field, question):
        unknowns.append(field)
        followups.append({"field": field, "question": question})

    if value is None:
        missing("results.yield_pct", "本次测得的结果百分比是多少？")
    if actual_type == "unknown":
        missing("results.yield_type", "这个百分比是分离、GC/LC、NMR收率还是转化率？")
    if target is None:
        missing("user_goal.minimum_yield_pct", "是否有明确用户目标？可以不设定；未设定时不判断达标。")
    elif target_type == "unknown":
        missing("user_goal.yield_type", "目标百分比对应哪种收率或转化率口径？")

    comparison = {"state": "unavailable", "reason": None, "gap_pp": None}
    if target is None:
        comparison["reason"] = "goal_not_set"
    elif value is None:
        comparison["reason"] = "outcome_missing"
    elif "unknown" in (actual_type, target_type):
        comparison["reason"] = "assay_unknown"
    elif actual_type != target_type:
        comparison["reason"] = "assay_mismatch"
    else:
        gap = Decimal(str(target)) - Decimal(str(value))
        comparison.update(state="below_target" if gap > 0 else "met", reason="goal_below" if gap > 0 else "goal_met", gap_pp=float(gap))
    if comparison["state"] == "unavailable":
        blocked.add("target_comparison")

    base_state = conditions.get("base_status", "unknown")
    if base_state == "unknown":
        missing("conditions.base_status", "请查记录确认是否加碱；查不到时继续保留未知。")
    elif base_state == "present" and conditions.get("base_name") in (None, "unknown"):
        missing("conditions.base_name", "已确认加碱，使用哪一种？")
    if base_state == "present" and conditions.get("base_equivalents") is None:
        missing("conditions.base_equivalents", "所用碱相对底物的当量是多少？")
    fields = {
        "catalyst": "使用了哪种催化剂？",
        "catalyst_loading_mol_pct": "催化剂载量是多少mol%，相对哪个组分？",
        "solvent": "溶剂是什么？", "temperature_C": "反应温度是多少摄氏度？",
        "time_h": "反应时间是多少小时？", "scale_mmol": "反应规模是多少mmol？",
        "solvent_volume_ml": "溶剂总体积是多少mL？",
        "partner_equivalents": "偶联伙伴相对芳基卤化物的当量是多少？",
        "atmosphere": "使用什么反应气氛？",
    }
    for name, question in fields.items():
        if conditions.get(name) in (None, "unknown"):
            missing(f"conditions.{name}", question)
    if conditions.get("catalyst_loading_mol_pct") is not None and conditions.get("catalyst_loading_basis") in (None, "unknown"):
        missing("conditions.catalyst_loading_basis", "已知催化剂载量；这个mol%相对哪一种投料计算？")
    if value is not None and results.get("yield_basis") in (None, "unknown"):
        missing("results.yield_basis", "该结果百分比以哪一种投料为计算基准？")

    evidence = results.get("characterization", [])
    if any(not item["supports_target"] for item in evidence):
        evidence_state = "contradictory"
    elif not evidence:
        evidence_state = "missing"
        missing("results.characterization", "有哪些支持目标产物结构的表征记录？")
    elif any(item["provenance"] == "human_review" for item in evidence):
        evidence_state = "human_review_recorded"
    else:
        evidence_state = "user_report_only"
    attempts = results.get("repeat_attempts")
    if attempts is None:
        missing("results.repeat_attempts", "同条件重复了几次，每次结果分别是多少？")
    # Counts alone cannot establish repeatability; the MVP does not estimate it.
    blocked.update({"repeatability", "precedent_numeric"})
    # Only implement the unambiguous negative evidence branches from B v0.2.
    # Do not promote an unreviewed user report to product confirmation.
    status, reason_code, triggered_fields = None, None, []
    pending = ["用户报告的结构证据是否足以进入S1，仍待A/B明确。"]
    if evidence_state in {"missing", "contradictory"}:
        status, reason_code = "S3", "evidence_gap"
        triggered_fields = ["results.characterization"]
        comparison = {"state": "unavailable", "reason": "evidence_gap", "gap_pp": None}
        blocked.add("target_comparison")
        pending = []
    elif value is None and actual_type == "unknown":
        status, reason_code = "S3", "input_gap"
        triggered_fields = ["results.yield_pct", "results.yield_type"]
        pending = []
    if status is None and comparison["state"] == "unavailable" and comparison["reason"] != "goal_not_set":
        pending.append("有目标但结果数值或口径不全时的S1/S2/S3映射仍待确认。")
    source_comparisons = []
    for source in precedents:
        source_type = source["outcome"].get("yield_type", "unknown")
        if "unknown" in (actual_type, source_type):
            reason = "assay_unknown"
        elif actual_type != source_type:
            reason = "assay_mismatch"
        else:
            reason = "mvp_numeric_disabled"
        source_comparisons.append({
            "source_id": source["source_id"], "record_id": source["record_id"],
            "locator": source["locator"], "url": source["url"],
            "provenance": source["provenance"],
            "numeric_comparison_allowed": False, "reason": reason,
            "outcome_type": source_type,
            "verification": source.get("verification"),
            "import_metadata": source.get("import_metadata"),
            "paper_id": publication_key(source),
            "reported_outcome": source["outcome"],
            "field_comparisons": compare_fields(experiment, source),
            "evidence_level_claimed": source.get("evidence_level_claimed"),
            "field_evidence": source.get("field_evidence", {}),
            "limitations": source.get("limitations", []),
            "source_links": source.get("source_links", []),
        })
    imported = any(source.get("import_metadata", {}).get("mode") == "sciencepal_file_import" for source in precedents)
    papers = {publication_key(source) for source in precedents if publication_key(source)}
    return {
        "schema_version": "chemcritic.review.v0.2", "rule_version": "core-0.2.1-partial",
        "experiment_id": experiment["experiment_id"], "simulated": experiment["simulated"],
        "status": status, "reason_code": reason_code, "triggered_fields": triggered_fields,
        "status_policy": "role_B_v0.2_partial_pending_A_confirmation", "pending_decisions": pending,
        "numeric_comparison_policy": {"enabled_in_mvp": False, "reason": "role_B_v0.2_explicit_disable"},
        "goal_comparison": comparison,
        "product_evidence": {"state": evidence_state, "independently_verified_by_system": False},
        "base": {"state": base_state, "name": conditions.get("base_name")},
        "unknowns": unknowns, "blocked_dimensions": sorted(blocked),
        "followups": followups, "precedent_comparisons": source_comparisons,
        "next_checks": next_checks(experiment, evidence_state, comparison, unknowns, source_comparisons),
        "retrieval": {"mode": "sciencepal_file_import" if imported else "provided_records", "record_count": len(precedents), "independent_publication_count": len(papers), "live_retrieval_performed": False},
        "warnings": (["模拟案例，非团队真实实验。"] if experiment["simulated"] else [])
        + (["没有提供先例；未判断是否存在相关文献。"] if not precedents else [])
        + ["不推断失败真因；未启用SciencePal实时连接、排序评分或LLM解释。"],
    }
