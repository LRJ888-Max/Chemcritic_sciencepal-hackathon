"""Field-by-field descriptions, never yield rankings or causal conclusions."""

import re


ALIASES = {
    "甲醇": "meoh", "methanol": "meoh", "四氢呋喃": "thf", "tetrahydrofuran": "thf",
    "水": "h2o", "water": "h2o", "空气": "air", "氮气": "n2", "nitrogen": "n2",
    "4-溴苯甲醚": "4-bromoanisole", "苯硼酸": "phenylboronicacid",
    "4-甲氧基联苯": "4-methoxybiphenyl",
    "pd-bpydc-ce(2)": "pd-bpydc-ce", "pd-bpydc-la(1)": "pd-bpydc-la",
}


def normalize(value):
    if value is None or (isinstance(value, str) and value.strip().lower() in {"", "unknown", "未知"}):
        return None
    if isinstance(value, (int, float, bool)):
        return value
    normalized = re.sub(r"\s+", "", str(value).translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")).lower())
    return ALIASES.get(normalized, normalized)


def compare_value(label, actual, precedent):
    left, right = normalize(actual), normalize(precedent)
    return {"label": label, "actual": actual, "precedent": precedent,
            "state": "unknown" if left is None or right is None else "same" if left == right else "different"}


def pair_state(actual, precedent):
    if not isinstance(actual, list) or not isinstance(precedent, list) or len(actual) != 2 or len(precedent) != 2:
        return "unknown"
    left, right = [normalize(x) for x in actual], [normalize(x) for x in precedent]
    if None in left or None in right:
        return "unknown"
    return "same" if sorted(left) == sorted(right) else "different"


def compare_fields(experiment, source):
    actual = experiment.get("conditions", {})
    other = source.get("conditions", {})
    substrate = {"label": "底物对", "actual": experiment["reaction"]["substrates"],
                 "precedent": source.get("reaction", {}).get("substrates"),
                 "state": pair_state(experiment["reaction"]["substrates"], source.get("reaction", {}).get("substrates"))}
    fields = [substrate, compare_value("目标产物", experiment["reaction"]["target_product"], source.get("reaction", {}).get("target_product"))]
    for key, label in [("catalyst","催化剂名称"),("catalyst_loading_mol_pct","载量（mol%）"),
                       ("base_status","是否加碱"),("base_name","碱名称"),("base_equivalents","碱当量"),
                       ("solvent","溶剂"),("temperature_C","温度（°C）"),("time_h","时间（h）"),
                       ("atmosphere","气氛"),("ligand","另加配体")]:
        fields.append(compare_value(label, actual.get(key), other.get(key)))
    fields.append(compare_value("结果口径", experiment.get("results", {}).get("yield_type", "unknown"), source["outcome"].get("yield_type", "unknown")))
    return fields


def publication_key(source):
    doi = normalize(source.get("doi"))
    if doi:
        doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    # source_id alone is not proof of a distinct publication.
    return doi
