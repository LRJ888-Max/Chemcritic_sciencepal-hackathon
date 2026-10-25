"""File adapter for the observed SM-004 SciencePal export format.

Imported text is data, never instructions. No network, tool execution or claims
of independent source verification are made by this adapter.
"""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlparse

from .rules import ValidationError


UNKNOWN = {"", "unknown", "not reported", "未知"}


def _object(value, path):
    if not isinstance(value, dict):
        raise ValidationError(path, "expected an object in SciencePal export")
    return value


def _text(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(path, "required non-empty text in SciencePal export")
    return value


def _identifier(value, path):
    value = _text(value, path)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value):
        raise ValidationError(path, "expected a simple identifier")
    return value


def _numeric(value, path, unit=None):
    if value is None or (isinstance(value, str) and value.strip().lower() in UNKNOWN):
        return None
    if isinstance(value, bool):
        raise ValidationError(path, "boolean is not a measurement")
    if unit is not None:
        if not isinstance(value, str):
            raise ValidationError(path, "quantity must explicitly include its unit")
        match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*" + unit + r"\s*", value, flags=re.I)
        if not match:
            raise ValidationError(path, "unsupported quantity/unit; original text must be reviewed")
        number = float(match.group(1))
    else:
        if not isinstance(value, (str, int, float)):
            raise ValidationError(path, "expected a numeric value")
        try:
            number = float(value)
        except ValueError as exc:
            raise ValidationError(path, "expected a numeric value without a unit") from exc
    if not math.isfinite(number):
        raise ValidationError(path, "measurement must be finite")
    return number


def import_sciencepal(path):
    """Return one normalized precedent and retain the entire raw record."""
    return import_sciencepal_bytes(Path(path).read_bytes())


def import_sciencepal_bytes(raw_bytes):
    """Import uploaded bytes without executing text or writing temporary files."""
    raw = _object(json.loads(raw_bytes.decode("utf-8-sig")), "sciencepal")
    source_id = _identifier(raw.get("task_id"), "task_id")
    paper = _object(raw.get("target_paper"), "target_paper")
    location = _object(raw.get("table_location"), "table_location")
    table = _text(location.get("table_number"), "table_location.table_number")
    entry = location.get("entry")
    if type(entry) is not int or entry < 1:
        raise ValidationError("table_location.entry", "expected a positive integer")
    if not re.fullmatch(r"Table\s+1", table, flags=re.I) or entry != 7:
        raise ValidationError("table_location", "adapter currently supports Table 1 entry 7 only")
    page = location.get("pdf_page")
    if type(page) is not int or page < 1:
        raise ValidationError("table_location.pdf_page", "expected a positive PDF page number")
    url = _text(paper.get("source_url"), "target_paper.source_url")
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ValidationError("target_paper.source_url", "expected an HTTP(S) source URL")
    extraction = _object(raw.get("entry_7_extraction"), "entry_7_extraction")
    result = _object(extraction.get("result"), "entry_7_extraction.result")
    metric = _text(result.get("metric"), "entry_7_extraction.result.metric")
    _text(result.get("source"), "entry_7_extraction.result.source")
    value = _numeric(result.get("value"), "entry_7_extraction.result.value")
    if result.get("unit") != "%":
        raise ValidationError("entry_7_extraction.result.unit", "expected percent unit %")
    if value is not None and not 0 <= value <= 100:
        raise ValidationError("entry_7_extraction.result.value", "expected a percentage in [0, 100]")

    # Map explicit leading labels only: a GC measurement is not necessarily a GC yield.
    yield_type = "unknown"
    for pattern, label in ((r"^GC\s+yield\b", "gc"), (r"^isolated\s+yield\b", "isolated"),
                           (r"^(?:1H\s+)?NMR\s+yield\b", "nmr"), (r"^LC\s+yield\b", "lc"),
                           (r"^conversion\b", "conversion")):
        if re.search(pattern, metric.strip(), flags=re.I):
            yield_type = label
            break
    warnings = ["导入为SciencePal文件报告；未独立核验原文或工具执行日志。"]
    if yield_type == "unknown":
        warnings.append("结果口径无法明确映射，保留unknown；禁止文献数值比较。")
    if re.search(r"yield", metric, flags=re.I) and re.search(r"conversion", metric, flags=re.I):
        warnings.append("原始结果定义同时含yield与conversion措辞，须由A核验原文定义。")
    field_evidence = {}

    def field(section, name):
        record = _object(extraction.get(section, {}), f"entry_7_extraction.{section}")
        value = record.get(name)
        if value is None or (isinstance(value, str) and value.strip().lower() in UNKNOWN):
            return None
        _text(value, f"entry_7_extraction.{section}.{name}")
        source = _text(record.get("source"), f"entry_7_extraction.{section}.source")
        field_evidence[f"{section}.{name}"] = source
        return value

    substrate = field("substrate", "name")
    partner = field("coupling_partner", "name")
    base = field("base", "name")
    conditions = {
        "catalyst": field("catalyst", "name"),
        # Accept this observed export annotation only; arbitrary trailing text,
        # ranges and changes of loading basis must still be reviewed.
        "catalyst_loading_mol_pct": _numeric(field("catalyst", "loading"), "catalyst.loading", r"mol\s*%(?:\s*\(as reported\))?"),
        "base_status": "present" if base else "unknown", "base_name": base,
        "base_amount_mmol": _numeric(field("base", "amount"), "base.amount", "mmol"),
        "solvent": field("solvent", "name"),
        "solvent_volume_ml": _numeric(field("solvent", "volume"), "solvent.volume", "mL"),
        "temperature_C": _numeric(field("conditions", "temperature"), "conditions.temperature", r"(?:°\s*C|C)"),
        "time_h": _numeric(field("conditions", "time"), "conditions.time", "h"),
        "atmosphere": field("conditions", "atmosphere"),
    }
    for key, number in conditions.items():
        if isinstance(number, (int, float)) and key != "temperature_C" and number < 0:
            raise ValidationError(f"conditions.{key}", "cannot be negative")
    if conditions["temperature_C"] is not None and conditions["temperature_C"] < -273.15:
        raise ValidationError("conditions.temperature_C", "below absolute zero")
    original_unit = "entry_7_extraction.result"
    field_evidence["result"] = result["source"]
    return [{
        "record_id": f"{source_id}-T1-E7", "source_id": source_id,
        "title": paper.get("title"), "doi": paper.get("doi"), "url": url,
        "locator": f"PDF p.{page}, {table} entry {entry}; exact footnotes in field_evidence",
        "provenance": "sciencepal_file_report_unverified",
        "verification": {"original_source_verified_locally": False, "tool_log_verified": False},
        "reaction": {"substrates": [substrate, partner], "target_reaction_raw": raw.get("target_reaction")},
        "conditions": conditions,
        "outcome": {"value_pct": value, "yield_type": yield_type, "original_metric": metric,
                    "assay": field("quantitative_method", "method"),
                    "internal_standard": field("quantitative_method", "internal_standard"),
                    "basis": field("quantitative_method", "basis"), "raw_field": original_unit},
        "field_evidence": field_evidence,
        "import_metadata": {"adapter_version": "sciencepal.sm004.v0.2", "mode": "sciencepal_file_import",
                            "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(), "warnings": warnings},
        "raw_record": deepcopy(raw),
    }]
