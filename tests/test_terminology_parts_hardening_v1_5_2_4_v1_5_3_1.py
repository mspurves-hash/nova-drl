#!/usr/bin/env python3
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

terms = load("terms1524", ROOT / "ingest" / "nova_drl_terminology_v1_5_2_4.py")
parts = load("parts1531", ROOT / "ingest" / "nova_parts_replaced_fusion_v1_5_3_1.py")
glossary = terms.load_glossary(ROOT / "config" / "drl_terminology_v1_5_2_4.json")
part_terms = parts.load_part_terms(ROOT / "config" / "drl_part_terms_v1_5_3_1.json")

source = {
    "fusion_version":"1.5.1",
    "repair_identity":{"log_number":"130130006","equipment_type":"RBT","oem":"GENMARK","model":"GB8-MT","serial_number":"80010732","customer":"UTI MICRON"},
    "approved_fields":{"repair_actions":[
        {"action_id":"a1","action_number":1,"value":"Replaced R8ZZ bearings on R, T, and Z axes (3 total)","decision_id":"d1","reviewer":"Matt Purves"},
        {"action_id":"a2","action_number":2,"value":"Resurfaced commutators on R, T, and Z motors (3 total)","decision_id":"d2","reviewer":"Matt Purves"},
        {"action_id":"a3","action_number":3,"value":"Vacuumed brush dust from A1, A2, and Y motors (not much)","decision_id":"d3","reviewer":"Matt Purves"},
        {"action_id":"a4","action_number":4,"value":"Replaced belts: 4 for A1 + A2, 2 for R + T, and 3 for Z (9 total)","decision_id":"d4","reviewer":"Matt Purves"},
        {"action_id":"a5","action_number":5,"value":"Installed 2 Blue Schmoo's for A1 + A2","decision_id":"d5","reviewer":"Matt Purves"},
    ]},
    "approved_field_count":1,
    "approved_repair_action_count":5,
    "accepted_as_final_repair_summary":False,
    "qdrant_entry_created":False,
}

enriched = terms.annotate_approved_fields(source, glossary)
assert enriched["terminology_layer"]["match_count"] == 1
assert enriched["approved_fields"]["repair_actions"][4]["value"] == "Installed 2 Blue Schmoo's for A1 + A2"

field = parts.extract_parts(enriched, part_terms)
assert field["candidate_count"] == 3
assert field["raw_ocr_used_as_part_source"] is False
assert field["qdrant_entry_created"] is False
assert enriched["qdrant_entry_created"] is False
print("PASS: coordinated Nova DRL v1.5.2.4 + v1.5.3.1 integration test")
