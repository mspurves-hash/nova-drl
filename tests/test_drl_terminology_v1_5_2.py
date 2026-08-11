#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_drl_terminology_v1_5_2.py"

spec = importlib.util.spec_from_file_location("terms152", str(TARGET))
terms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(terms)

assert terms.VERSION == "1.5.2"

glossary = terms.load_glossary(
    ROOT / "config" / "drl_terminology_v1_5_2.json"
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
            "value": "Y Axis needs to be fixed",
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
            {
                "action_id": "a3",
                "action_number": 3,
                "value": "Machined Comm's and cleaned motor",
            },
        ],
    },
    "approved_field_count": 2,
    "approved_repair_action_count": 3,
    "accepted_as_final_repair_summary": False,
    "qdrant_entry_created": False,
}

enriched = terms.annotate_approved_fields(source, glossary)

# Original human-approved wording is unchanged.
assert (
    enriched["approved_fields"]["repair_actions"][1]["value"]
    == "Added Flanges BERS x2 to A1 + A2 upper link"
)

# BERS is annotated, not replaced.
bers = enriched["approved_fields"]["repair_actions"][1]["terminology_annotations"]
assert len(bers) == 1
assert bers[0]["raw_term"] == "BERS"
assert bers[0]["normalized_meaning"] == "bearings"
assert bers[0]["preserve_raw"] is True

# Comm's is annotated.
comms = enriched["approved_fields"]["repair_actions"][2]["terminology_annotations"]
assert len(comms) == 1
assert comms[0]["normalized_meaning"] == "commutators"

# FE is intentionally unresolved and must not be guessed.
action1 = enriched["approved_fields"]["repair_actions"][0]["terminology_annotations"]
assert action1 == []

assert enriched["terminology_layer"]["source_values_modified"] is False
assert enriched["terminology_layer"]["unknown_terms_inferred"] is False
assert enriched["qdrant_entry_created"] is False

# Straight/curly apostrophe aliases both match.
assert terms.find_terminology_matches("Machined Comm’s", glossary)[0][
    "normalized_meaning"
] == "commutators"

print("PASS: Nova DRL Terminology Layer v1.5.2 tests")
