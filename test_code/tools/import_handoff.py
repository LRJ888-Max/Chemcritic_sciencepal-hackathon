"""Read the team's XLSX without changing it; build a traceable JSON library.

All scientific normalization below is explicitly transcribed from the supplied
six rows. Unknown values are not inferred from chemistry or other papers.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def read_rows(path):
    with ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(item.itertext()) for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheet = next(s for s in workbook.findall("s:sheets/s:sheet", NS) if s.attrib["name"] == "案例表 v1")
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        target = next(item.attrib["Target"] for item in relationships if item.attrib["Id"] == sheet.attrib[f"{{{R}}}id"])
        target = target.lstrip("/") if target.startswith("/") else "xl/" + target
        rows = {}
        for row in ET.fromstring(archive.read(target)).findall("s:sheetData/s:row", NS):
            cells = {}
            for cell in row.findall("s:c", NS):
                value = cell.findtext("s:v", "", NS)
                if cell.attrib.get("t") == "s":
                    value = shared[int(value)]
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(t.text or "" for t in cell.findall(".//s:t", NS))
                cells[cell.attrib["r"]] = value
            rows[int(row.attrib["r"])] = cells
        return rows


def build_library(path):
    rows = read_rows(path)
    # row, source, DOI, catalyst, loading, base, eq, solvent, volume, T, hours, atmosphere, scale, partner eq
    definitions = [
        (5,"SM-001","10.1080/00397918108063618","Pd(PPh3)4",3,"Na2CO3",2,"benzene",None,None,6,"N2",1,1.1),
        (6,"SM-002","10.1039/C0OB00524J","PdCl2",0.5,"K2CO3",2,"EtOH/H2O (1:1)",4,25,0.5,"air",0.5,1.5),
        (7,"SM-003","10.1039/C5DT02611C","Pd(OAc)2",1,"NaOtBu",3,"THF",1,60,1,"N2",1,1.5),
        (8,"SM-004","10.3390/molecules23102435","Pd-bpydc-Ce (2)",0.5,"NaOtBu",1,"MeOH",1,60,4,"air",0.5,1.2),
        (9,"SM-005","10.1021/acsomega.8b00039","biogenic Pd NPs",0.2,"K2CO3",3,"H2O",2,25,2,"air",0.5,1.2),
        (10,"SM-004","10.3390/molecules23102435","Pd-bpydc-La (1)",0.5,"NaOtBu",1,"MeOH",1,30,8,None,0.5,1.2),
    ]
    outcomes = {5:[("GC",66,"gc"),("isolated",40,"isolated")],6:[("isolated",100,"isolated")],
                7:[("NMR",95,"nmr")],8:[("T1-E7",91.9,"unknown")],9:[("isolated",95,"isolated")],10:[("T1-E1",35,"unknown")]}
    records = []
    for row, source, doi, catalyst, loading, base, eq, solvent, volume, temperature, hours, atmosphere, scale, partner in definitions:
        cells = rows[row]
        expected_id = "SM-004-E1" if row == 10 else source
        if cells.get(f"A{row}") != expected_id:
            raise ValueError(f"Workbook layout changed at A{row}; review mapping before importing")
        for suffix, value, yield_type in outcomes[row]:
            pending = row in (8, 10)
            record = {
                "record_id": f"{source}-{suffix}", "source_id": source, "doi": doi,
                "title": cells[f"B{row}"], "url": "https://doi.org/" + doi, "locator": cells[f"D{row}"],
                "source_links": re.findall(r"https?://[^\s；]+", cells[f"P{row}"]),
                "provenance": "team_workbook_report_unverified_locally",
                "evidence_level_claimed": cells[f"U{row}"],
                "verification": {"original_source_verified_locally": False, "tool_log_verified": False},
                "reaction": {"substrates": ["4-bromoanisole", "phenylboronic acid"], "target_product": "4-methoxybiphenyl"},
                "conditions": {"catalyst": catalyst, "catalyst_loading_mol_pct": loading,
                    "base_status": "present", "base_name": base, "base_equivalents": eq,
                    "solvent": solvent, "solvent_volume_ml": volume, "temperature_C": temperature,
                    "time_h": hours, "atmosphere": atmosphere, "scale_mmol": scale,
                    "partner_equivalents": partner, "ligand": "SPO 5h" if row == 7 else None},
                "outcome": {"value_pct": value, "yield_type": yield_type,
                    "reported_technique": "gc" if pending else yield_type,
                    "interpretation_pending": pending, "original_metric": cells[f"L{row}"],
                    "assay": cells[f"M{row}"], "internal_standard": "hexadecane" if pending else None,
                    "replicates": 2 if row == 7 else None},
                "limitations": [cells[f"R{row}"], cells[f"S{row}"], cells[f"T{row}"]],
                "field_evidence": {key: {"cell": f"案例表 v1!{col}{row}", "text": cells.get(f"{col}{row}", "")}
                    for key,col in {"reaction":"C","locator":"D","catalyst":"E","ligand":"F","base":"G",
                                    "solvent":"H","conditions":"I","scale":"J","value":"K","metric":"L","assay":"M"}.items()},
                "raw_workbook_row": cells,
                "import_metadata": {"mode": "team_library", "workbook_row": row,
                    "warnings": ["团队工作簿报告的A级证据，未在本工程独立核验原文。"] +
                        (["A标GC total conversion，B用gc标签；口径待确认，当前规范类型为unknown。"] if pending else [])},
            }
            records.append(record)
    return {"schema_version": "chemcritic.precedents.v0.1", "metadata": {
        "source_file": Path(path).name, "source_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "record_count": len(records), "independent_publication_count": len({r["doi"] for r in records}),
        "retrieval_mode": "local_library_exact_pair", "numeric_comparison_enabled": False,
        "note": "Local team-curated records, not a live SciencePal search or independent chemistry validation."}, "records": records}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    library = build_library(args.workbook)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(library, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(library["metadata"], ensure_ascii=True))
