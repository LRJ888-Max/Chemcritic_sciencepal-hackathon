"""Load either a SciencePal export or a normalized local team library."""

import json

from .comparison import pair_state
from .rules import ValidationError, validate
from .sciencepal import import_sciencepal_bytes


def load_sources(text, experiment):
    if not isinstance(text, str):
        raise ValidationError("sciencepal_text", "expected original JSON text")
    if not text.strip():
        return [], {"mode": "provided_records", "input_record_count": 0, "record_count": 0, "live_retrieval_performed": False}
    parsed = json.loads(text.lstrip("\ufeff"))
    if isinstance(parsed, dict) and parsed.get("schema_version") == "chemcritic.precedents.v0.1":
        records = parsed.get("records")
        validate(experiment, records)
        matched = [r for r in records if pair_state(experiment["reaction"]["substrates"], r.get("reaction", {}).get("substrates")) == "same"]
        return matched, {"mode": "local_library_exact_pair", "input_record_count": len(records),
                         "record_count": len(matched), "no_match": not matched, "live_retrieval_performed": False}
    records = import_sciencepal_bytes(text.encode("utf-8"))
    return records, {"mode": "sciencepal_file_import", "input_record_count": len(records),
                     "record_count": len(records), "live_retrieval_performed": False}
