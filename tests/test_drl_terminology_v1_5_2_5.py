#!/usr/bin/env python3
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_drl_terminology_v1_5_2_5.py"
spec = importlib.util.spec_from_file_location("terms1524", str(TARGET))
terms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(terms)
assert terms.VERSION == "1.5.2.5"

glossary = terms.load_glossary(ROOT / "config" / "drl_terminology_v1_5_2_5.json")
source = {
    "fusion_version": "1.5.1",
    "repair_identity": {"log_number": "130130006", "equipment_type": "RBT", "oem": "GENMARK", "model": "GB8-MT", "serial_number": "80010732", "customer": "UTI MICRON"},
    "approved_fields": {
        "customer_complaint": {"value": "FA RPT requested"},
        "repair_actions": [
            {"action_id": "a1", "action_number": 1, "value": "Added Flanges BERS x2 to A1 + A2 upper link"},
            {"action_id": "a2", "action_number": 2, "value": "Installed 2 Blue Schmoo's for A1 + A2"},
        ],
    },
    "approved_field_count": 2,
    "approved_repair_action_count": 2,
    "qdrant_entry_created": False,
}
enriched = terms.annotate_approved_fields(source, glossary)
assert any(x["raw_term"] == "FA RPT" for x in enriched["approved_fields"]["customer_complaint"]["terminology_annotations"])
assert any(x["raw_term"] == "BERS" for x in enriched["approved_fields"]["repair_actions"][0]["terminology_annotations"])
blue = enriched["approved_fields"]["repair_actions"][1]["terminology_annotations"]
assert len(blue) == 1
assert blue[0]["raw_term"] == "Blue Schmoo's"
assert blue[0]["normalized_meaning"] == "special shim"
assert blue[0]["preserve_raw"] is True
assert enriched["approved_fields"]["repair_actions"][1]["value"] == "Installed 2 Blue Schmoo's for A1 + A2"
assert enriched["terminology_layer"]["source_values_modified"] is False
assert enriched["qdrant_entry_created"] is False
print("PASS: Nova DRL Terminology Layer v1.5.2.5 tests")
