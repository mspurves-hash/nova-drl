#!/usr/bin/env python3
import importlib.util
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_parts_replaced_fusion_v1_5_3_1.py"
spec = importlib.util.spec_from_file_location("parts1531", str(TARGET))
parts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parts)
assert parts.VERSION == "1.5.3.1"
part_terms = parts.load_part_terms(ROOT / "config" / "drl_part_terms_v1_5_3_1.json")

# Regression: original pilot behavior must remain conservative.
pilot = {
    "fusion_version": "1.5.2.4",
    "repair_identity": {"log_number": "130813004", "equipment_type": "RBT", "oem": "GENMARK", "model": "GB8-MT", "serial_number": "80010732", "customer": "UTI MICRON"},
    "approved_fields": {"repair_actions": [
        {"action_id": "p1", "action_number": 1, "value": "Adjusted Y-FE from around 9000 down to around 3000 by slipping Y belt a few teeth", "decision_id": "d1", "reviewer": "Matt Purves", "terminology_annotations": []},
        {"action_id": "p2", "action_number": 2, "value": "Added Flanges BERS x2 to A1 + A2 upper link", "decision_id": "d2", "reviewer": "Matt Purves", "terminology_annotations": [{"raw_text_in_value":"BERS","raw_term":"BERS","normalized_meaning":"bearings","status":"human_confirmed","preserve_raw":True,"start":14,"end":18}]},
    ]},
    "approved_field_count": 1,
    "approved_repair_action_count": 2,
}
field = parts.extract_parts(pilot, part_terms)
assert field["candidate_count"] == 1
assert field["candidates"][0]["canonical_part"] == "bearings"
assert field["candidates"][0]["quantity"] == 2
belt_obs = [x for x in field["component_observations"] if x["canonical_part"] == "belts"]
assert len(belt_obs) == 1 and belt_obs[0]["classification"] == "referenced_or_serviced_component"

# Second event hardening.
source = {
    "fusion_version": "1.5.2.4",
    "repair_identity": {"log_number": "130130006", "equipment_type": "RBT", "oem": "GENMARK", "model": "GB8-MT", "serial_number": "80010732", "customer": "UTI MICRON"},
    "approved_fields": {"repair_actions": [
        {"action_id":"a1","action_number":1,"value":"Replaced R8ZZ bearings on R, T, and Z axes (3 total)","decision_id":"d1","reviewer":"Matt Purves","terminology_annotations":[]},
        {"action_id":"a2","action_number":2,"value":"Resurfaced commutators on R, T, and Z motors (3 total)","decision_id":"d2","reviewer":"Matt Purves","terminology_annotations":[]},
        {"action_id":"a3","action_number":3,"value":"Vacuumed brush dust from A1, A2, and Y motors (not much)","decision_id":"d3","reviewer":"Matt Purves","terminology_annotations":[]},
        {"action_id":"a4","action_number":4,"value":"Replaced belts: 4 for A1 + A2, 2 for R + T, and 3 for Z (9 total)","decision_id":"d4","reviewer":"Matt Purves","terminology_annotations":[]},
        {"action_id":"a5","action_number":5,"value":"Installed 2 Blue Schmoo's for A1 + A2","decision_id":"d5","reviewer":"Matt Purves","terminology_annotations":[{"raw_text_in_value":"Blue Schmoo's","raw_term":"Blue Schmoo's","normalized_meaning":"special shim","status":"human_confirmed","preserve_raw":True,"start":12,"end":25}]},
    ]},
    "approved_field_count": 1,
    "approved_repair_action_count": 5,
}
field2 = parts.extract_parts(source, part_terms)
assert field2["candidate_count"] == 3
by_part = {x["canonical_part"]: x for x in field2["candidates"]}

bearings = by_part["bearings"]
assert bearings["quantity"] == 3
assert bearings["quantity_source"] == "explicit_total"
assert bearings["identified_part_number"] == "R8ZZ"

belts = by_part["belts"]
assert belts["quantity"] == 9
assert belts["quantity_source"] == "explicit_total"
assert belts["quantity_breakdown_sum"] == 9
assert belts["quantity_breakdown_verified"] is True
assert [(x["quantity"], x["context"]) for x in belts["quantity_breakdown"]] == [(4,"A1 + A2"),(2,"R + T"),(3,"Z")]

shims = by_part["special shims"]
assert shims["quantity"] == 2
assert shims["terminology"]["raw_term"] == "Blue Schmoo's"
assert shims["terminology"]["normalized_meaning"] == "special shim"
assert shims["installation_context"] == "for A1 + A2"

comm = [x for x in field2["component_observations"] if x["canonical_part"] == "commutators"]
assert len(comm) == 1
assert comm[0]["classification"] == "referenced_or_serviced_component"
assert any(x.lower().startswith("resurfac") for x in comm[0]["service_signals"])
assert not any(x["canonical_part"] == "commutators" for x in field2["candidates"])
assert not any(x["canonical_part"] == "motors" for x in field2["candidates"])
motor_obs = [x for x in field2["component_observations"] if x["canonical_part"] == "motors"]
assert len(motor_obs) == 2
assert all(x["classification"] == "referenced_or_serviced_component" for x in motor_obs)
assert field2["raw_ocr_used_as_part_source"] is False
assert field2["qdrant_entry_created"] is False

print("PASS: Nova DRL Parts Replaced Fusion v1.5.3.1 tests")
