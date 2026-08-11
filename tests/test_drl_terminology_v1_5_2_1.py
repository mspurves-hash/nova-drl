#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_drl_terminology_v1_5_2_1.py"

spec = importlib.util.spec_from_file_location("terms1521", str(TARGET))
terms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(terms)

assert terms.VERSION == "1.5.2.1"

glossary = terms.load_glossary(
    ROOT / "config" / "drl_terminology_v1_5_2_1.json"
)

source = {
    "fusion_version": "1.5.1",
    "repair_identity": {
        "log_number": "130813004",
        "equipment_type": "RBT",
        "oem": "GENMARK",
        "model": "GB8-MT",
        "serial_number": "80010732",
        "customer": "UTI MICRON",
    },
    "approved_fields": {
        "repair_actions": [
            {
                "action_id": "a1",
                "action_number": 1,
                "value": (
                    "Adjusted Y-FE from around 9000 down to around 3000 "
                    "by slipping Y belt a few teeth"
                ),
            },
            {
                "action_id": "a2",
                "action_number": 2,
                "value": "Added Flanges BERS x2 to A1 + A2 upper link",
            },
        ]
    },
    "approved_field_count": 1,
    "approved_repair_action_count": 2,
    "accepted_as_final_repair_summary": False,
    "qdrant_entry_created": False,
}

enriched = terms.annotate_approved_fields(source, glossary)
a1 = enriched["approved_fields"]["repair_actions"][0]
a2 = enriched["approved_fields"]["repair_actions"][1]

assert a1["value"].startswith("Adjusted Y-FE")
assert any(
    row["raw_term"] == "FE"
    and "home-sensor" in row["normalized_meaning"]
    for row in a1["terminology_annotations"]
)
assert any(
    row["raw_term"] == "BERS"
    and row["normalized_meaning"] == "bearings"
    for row in a2["terminology_annotations"]
)
assert enriched["terminology_layer"]["source_values_modified"] is False
assert enriched["qdrant_entry_created"] is False

print("PASS: Nova DRL Terminology Layer v1.5.2.1 tests")
