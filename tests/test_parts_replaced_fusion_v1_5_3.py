#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_parts_replaced_fusion_v1_5_3.py"

spec = importlib.util.spec_from_file_location("parts153", str(TARGET))
parts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parts)

assert parts.VERSION == "1.5.3"

part_terms = parts.load_part_terms(
    ROOT / "config" / "drl_part_terms_v1_5_3.json"
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
                "decision_id": "d1",
                "reviewer": "Matt Purves",
                "reviewed_at_utc": "2026-08-11T20:53:03+00:00",
                "terminology_annotations": [],
            },
            {
                "action_id": "a2",
                "action_number": 2,
                "value": "Added Flanges BERS x2 to A1 + A2 upper link",
                "decision_id": "d2",
                "reviewer": "Matt Purves",
                "reviewed_at_utc": "2026-08-11T20:51:35+00:00",
                "terminology_annotations": [
                    {
                        "raw_text_in_value": "BERS",
                        "raw_term": "BERS",
                        "normalized_meaning": "bearings",
                        "status": "human_confirmed",
                        "preserve_raw": True,
                        "start": 14,
                        "end": 18,
                    }
                ],
            },
            {
                "action_id": "a3",
                "action_number": 3,
                "value": "Machined Comm's and cleaned motor",
                "decision_id": "d3",
                "reviewer": "Matt Purves",
                "reviewed_at_utc": "2026-08-11T21:00:00+00:00",
                "terminology_annotations": [
                    {
                        "raw_text_in_value": "Comm's",
                        "raw_term": "Comm's",
                        "normalized_meaning": "commutators",
                        "status": "human_confirmed",
                        "preserve_raw": True,
                        "start": 9,
                        "end": 15,
                    }
                ],
            },
        ],
    },
    "approved_field_count": 2,
    "approved_repair_action_count": 3,
    "accepted_as_final_repair_summary": False,
    "qdrant_entry_created": False,
}

field = parts.extract_parts(source, part_terms)

# Only BERS/bearings is an explicit installed/replaced candidate.
assert field["candidate_count"] == 1
candidate = field["candidates"][0]
assert candidate["canonical_part"] == "bearings"
assert candidate["raw_mention"] == "BERS"
assert candidate["quantity"] == 2
assert candidate["classification"] == "installed_or_replaced_candidate"
assert "Added" in candidate["install_signals"]
assert candidate["terminology"]["normalized_meaning"] == "bearings"

# Y belt is referenced/adjusted, not replaced.
belt_obs = [
    row for row in field["component_observations"]
    if row["canonical_part"] == "belts"
]
assert len(belt_obs) == 1
assert belt_obs[0]["classification"] == "referenced_or_serviced_component"
assert belt_obs[0]["accepted_as_replaced_part"] is False

# Machined commutators and cleaned motor remain service observations.
comm_obs = [
    row for row in field["component_observations"]
    if row["canonical_part"] == "commutators"
]
assert len(comm_obs) == 1
assert comm_obs[0]["classification"] == "referenced_or_serviced_component"

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp)
    output = parts.build_output(
        source,
        Path("/tmp/source.json"),
        part_terms,
        [],
    )
    assert output["approved_parts_replaced_count"] == 0
    assert "parts_replaced" not in output["approved_fields"]

    decision = parts.record_part_decision(
        output["parts_replaced_review"],
        out,
        decision="approve",
        reviewer="Matt Purves",
        part_number=1,
        note="Verified against approved repair action and DRL glossary.",
    )
    decisions = parts.load_decisions(out)
    output2 = parts.build_output(
        source,
        Path("/tmp/source.json"),
        part_terms,
        decisions,
    )
    assert decision["decision"] == "approved"
    assert output2["approved_parts_replaced_count"] == 1
    assert output2["approved_field_count"] == 3
    approved_part = output2["approved_fields"]["parts_replaced"][0]
    assert approved_part["part"] == "bearings"
    assert approved_part["quantity"] == 2
    assert approved_part["raw_mention"] == "BERS"
    assert output2["qdrant_entry_created"] is False

print("PASS: Nova DRL Parts Replaced Fusion v1.5.3 tests")
