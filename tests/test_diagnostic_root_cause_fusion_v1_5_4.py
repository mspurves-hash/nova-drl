#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_diagnostic_root_cause_fusion_v1_5_4.py"

spec = importlib.util.spec_from_file_location("diag154", str(TARGET))
diag = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diag)

assert diag.VERSION == "1.5.4"
glossary = diag.load_glossary(ROOT / "config" / "drl_terminology_v1_5_2_3.json")

source = {
    "fusion_version": "1.5.3",
    "repair_identity": {
        "log_number": "130813004",
        "repair_date": "2013-08-13",
        "equipment_type": "RBT",
        "oem": "GENMARK",
        "model": "GB8-MT",
        "serial_number": "80010732",
        "customer": "UTI MICRON",
    },
    "approved_fields": {
        "customer_complaint": {"value": "Y Axis needs to be fixed"},
        "repair_actions": [
            {
                "action_id": "a1",
                "action_number": 1,
                "value": (
                    "Adjusted Y-FE from around 9000 down to around 3000 "
                    "by slipping Y belt a few teeth"
                ),
                "reviewer": "Matt Purves",
                "decision_id": "d1",
                "primary_source": {
                    "authority": "primary_repair_anchor",
                    "source_path": "/mnt/drl/example/130813004 Line Card Warranty.JPG",
                    "source_document": "130813004 Line Card Warranty.JPG",
                    "location": "repairs_replacements entry 1",
                    "crop_paths": {"full_row": "/derived/entry_01_full.png"},
                    "vision_raw": json.dumps({
                        "description": "Adjusted y-FE from 900 to 300",
                        "notes": (
                            "We Suspect this 'High' FE # WAS Causing to "
                            "intermittent running problem, but we don't "
                            "Have 'concrete proof of this.'"
                        ),
                    }),
                },
            },
            {
                "action_id": "a2",
                "action_number": 2,
                "value": "Added Flanges BERS x2 to A1 + A2 upper link",
                "reviewer": "Matt Purves",
                "decision_id": "d2",
                "primary_source": {
                    "authority": "primary_repair_anchor",
                    "source_path": "/mnt/drl/example/130813004 Line Card Warranty.JPG",
                    "source_document": "130813004 Line Card Warranty.JPG",
                    "location": "repairs_replacements entry 2",
                    "crop_paths": {"full_row": "/derived/entry_02_full.png"},
                    "vision_raw": json.dumps({
                        "description": "X ADDED Flanged BCS X2",
                        "notes": None,
                    }),
                },
            },
        ],
        "parts_replaced": [{"part": "bearings", "quantity": 2}],
    },
    "approved_field_count": 3,
    "approved_repair_action_count": 2,
    "approved_parts_replaced_count": 1,
    "qdrant_entry_created": False,
}

review = diag.build_review(source, glossary, [])
assert review["candidate_count"] == 1
assert review["diagnostic_hypothesis_count"] == 1
assert review["root_cause_candidate_count"] == 0
assert review["root_cause_status"] == "not_established"

candidate = review["candidates"][0]
assert candidate["candidate_type"] == "diagnostic_hypothesis"
assert any(x["cue"] == "suspect" for x in candidate["uncertainty_cues"])
assert any(x["cue"] == "no_concrete_proof" for x in candidate["uncertainty_cues"])
assert any(
    x["raw_term"] == "FE" and "home-sensor" in x["normalized_meaning"]
    for x in candidate["terminology_annotations"]
)

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp)
    decision = diag.record_decision(
        review["candidates"],
        out,
        "approve-hypothesis",
        "Matt Purves",
        candidate_number=1,
        value="High Y-FE may have caused the intermittent homing problem.",
        note="Verified visually; diagnostic hypothesis only.",
    )
    review2 = diag.build_review(source, glossary, diag.load_decisions(out))
    output = diag.build_output(Path("/tmp/source.json"), source, review2)

    assert decision["decision"] == "approved_hypothesis"
    assert decision["edited_from_candidate"] is True
    assert review2["approved_hypothesis_count"] == 1
    assert review2["confirmed_root_cause_count"] == 0
    assert review2["root_cause_status"] == "not_established"
    assert output["approved_diagnostic_hypothesis_count"] == 1
    assert output["confirmed_root_cause_count"] == 0
    assert output["approved_fields"]["diagnostic_hypotheses"][0]["confirmed_root_cause"] is False
    assert output["qdrant_entry_created"] is False

    try:
        diag.record_decision(
            review["candidates"],
            out,
            "confirm-root-cause",
            "Matt Purves",
            candidate_number=1,
        )
        raise AssertionError("Hypothesis was incorrectly confirmable as root cause.")
    except ValueError:
        pass

root_source = json.loads(json.dumps(source))
root_source["approved_fields"]["repair_actions"][0]["primary_source"]["vision_raw"] = json.dumps({
    "description": "Replaced Y encoder",
    "notes": "Root cause: failed Y encoder caused homing failure.",
})
root_review = diag.build_review(root_source, glossary, [])
assert root_review["root_cause_candidate_count"] == 1
assert root_review["confirmed_root_cause_count"] == 0
assert root_review["root_cause_status"] == "candidate_pending_human_confirmation"

print("PASS: Nova DRL Diagnostic / Root Cause Fusion v1.5.4 tests")
