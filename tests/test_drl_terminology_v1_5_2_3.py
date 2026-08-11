#!/usr/bin/env python3
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_drl_terminology_v1_5_2_3.py"

spec = importlib.util.spec_from_file_location("terms1523", str(TARGET))
terms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(terms)

assert terms.VERSION == "1.5.2.3"

glossary = terms.load_glossary(
    ROOT / "config" / "drl_terminology_v1_5_2_3.json"
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
        "customer_complaint": {
            "value": "FA RPT requested for Y Axis problem",
        },
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
        ],
    },
    "approved_field_count": 2,
    "approved_repair_action_count": 2,
    "qdrant_entry_created": False,
}

enriched = terms.annotate_approved_fields(source, glossary)

assert any(
    row["raw_term"] == "FA RPT"
    and row["normalized_meaning"] == "Failure Analysis Report"
    for row in enriched["approved_fields"]["customer_complaint"][
        "terminology_annotations"
    ]
)
assert any(
    row["raw_term"] == "FE"
    for row in enriched["approved_fields"]["repair_actions"][0][
        "terminology_annotations"
    ]
)
assert any(
    row["raw_term"] == "BERS"
    and row["normalized_meaning"] == "bearings"
    for row in enriched["approved_fields"]["repair_actions"][1][
        "terminology_annotations"
    ]
)
assert enriched["terminology_layer"]["source_values_modified"] is False
assert enriched["qdrant_entry_created"] is False

print("PASS: Nova DRL Terminology Layer v1.5.2.3 tests")
