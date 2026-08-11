#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_testing_final_result_fusion_v1_5_5.py"

spec = importlib.util.spec_from_file_location("tfr155", str(TARGET))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

assert mod.VERSION == "1.5.5"
rules = mod.load_rules(
    ROOT / "config" / "testing_final_result_rules_v1_5_5.json"
)

source_data = {
    "fusion_version": "1.5.4",
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
            {"value": "Adjusted Y-FE from around 9000 down to around 3000 by slipping Y belt a few teeth"},
            {"value": "Added Flanges BERS x2 to A1 + A2 upper link"},
        ],
        "parts_replaced": [
            {"part": "flanged bearings", "quantity": 2}
        ],
        "diagnostic_hypotheses": [
            {
                "value": "High Y-FE may have caused the intermittent homing problem.",
                "confirmed_root_cause": False,
            }
        ],
    },
    "approved_field_count": 4,
    "approved_repair_action_count": 2,
    "approved_parts_replaced_count": 1,
    "approved_diagnostic_hypothesis_count": 1,
    "qdrant_entry_created": False,
}

# Synthetic vision analyses. Printed template content without a mark must not
# create a testing item or final result.
analyses = [
    {
        "analysis_id": "a1",
        "vision_status": "ok",
        "source": {
            "source_kind": "supporting_document_page",
            "document_role": "robot_checklist",
            "document_family": "DRL_INTERNAL_CHECKLIST",
            "source_document": "130813004 Robot Checklist.PDF",
            "source_path": "/mnt/drl/130813004 Robot Checklist.PDF",
            "page_number": 3,
            "image_path": "/tmp/page3.png",
        },
        "parsed_analysis": {
            "page_has_event_specific_testing_evidence": True,
            "testing_items": [
                {
                    "step_label": "Y Axis Home",
                    "event_mark": "VT",
                    "mark_type": "initials",
                    "result": "completed",
                    "recorded_value": None,
                    "technician_initials": "VT",
                    "date": None,
                    "confidence": "high",
                }
            ],
            "final_result_items": [],
            "printed_template_only_labels": [
                "Visual inspection",
                "Acceptance Test Report",
            ],
            "uncertain_marks": [],
        },
    },
    {
        "analysis_id": "a2",
        "vision_status": "ok",
        "source": {
            "source_kind": "supporting_document_page",
            "document_role": "robot_test_report",
            "document_family": "DRL_ACCEPTANCE_TEST_REPORT",
            "source_document": "130813004 Robot Test Report.PDF",
            "source_path": "/mnt/drl/130813004 Robot Test Report.PDF",
            "page_number": 3,
            "image_path": "/tmp/report3.png",
        },
        "parsed_analysis": {
            "page_has_event_specific_testing_evidence": True,
            "testing_items": [
                {
                    "step_label": "Robot homing",
                    "event_mark": "PASS",
                    "mark_type": "pass_fail_mark",
                    "result": "pass",
                    "recorded_value": None,
                    "technician_initials": None,
                    "date": None,
                    "confidence": "high",
                }
            ],
            "final_result_items": [
                {
                    "value": "PASS",
                    "basis_label": "Final Result",
                    "event_mark": "PASS",
                    "result": "pass",
                    "confidence": "high",
                }
            ],
            "printed_template_only_labels": [],
            "uncertain_marks": ["small unreadable mark beside Z axis test"],
        },
    },
    {
        "analysis_id": "a3",
        "vision_status": "ok",
        "source": {
            "source_kind": "supporting_document_page",
            "document_role": "robot_test_report",
            "document_family": "DRL_ACCEPTANCE_TEST_REPORT",
            "source_document": "130813004 Robot Test Report.PDF",
            "source_path": "/mnt/drl/130813004 Robot Test Report.PDF",
            "page_number": 1,
            "image_path": "/tmp/report1.png",
        },
        "parsed_analysis": {
            "page_has_event_specific_testing_evidence": False,
            "testing_items": [],
            "final_result_items": [],
            "printed_template_only_labels": [
                "Acceptance Test Report"
            ],
            "uncertain_marks": [],
        },
    },
]

review = mod.build_review(analyses, rules, [])
assert review["testing"]["candidate_count"] == 2
assert review["final_result"]["candidate_count"] == 1
assert review["testing"]["approved_count"] == 0
assert review["final_result"]["approved_count"] == 0
assert review["final_result"]["status"] == "candidate_pending_human_review"
assert len(review["printed_template_only_observations"]) == 3

# Static "Acceptance Test Report" never created a final candidate by itself.
assert all(
    row["value"] != "Acceptance Test Report"
    for row in review["final_result"]["candidates"]
)

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp)

    d1 = mod.record_decision(
        out,
        "approve-test",
        "Matt Purves",
        review["testing"]["candidates"],
        review["final_result"]["candidates"],
        test_number=1,
        value="Y-axis homing test completed.",
        note="Verified against event-specific initials on the checklist.",
    )
    assert d1["decision"] == "approved"
    assert d1["field"] == "testing_performed"

    d2 = mod.record_decision(
        out,
        "approve-final",
        "Matt Purves",
        review["testing"]["candidates"],
        review["final_result"]["candidates"],
        final_number=1,
        value="Robot passed final acceptance testing.",
        note="Verified against explicit marked final PASS result.",
    )
    assert d2["decision"] == "approved"
    assert d2["field"] == "final_result"

    review2 = mod.build_review(
        analyses, rules, mod.load_decisions(out)
    )
    assert review2["testing"]["approved_count"] == 1
    assert review2["final_result"]["approved_count"] == 1
    assert review2["final_result"]["status"] == "human_approved"

    output = mod.build_output(
        Path("/tmp/approved.json"),
        source_data,
        Path("/tmp/repair_evidence_bundle.json"),
        review2,
        analyses,
        [],
        [],
        None,
    )

    assert output["approved_field_count"] == 6
    assert output["approved_testing_item_count"] == 1
    assert output["approved_final_result_count"] == 1
    assert output["approved_fields"]["testing_performed"][0]["value"] == (
        "Y-axis homing test completed."
    )
    assert output["approved_fields"]["final_result"][0]["value"] == (
        "Robot passed final acceptance testing."
    )
    assert output["accepted_as_final_repair_summary"] is False
    assert output["qdrant_entry_created"] is False

print("PASS: Nova DRL Testing / Final Result Fusion v1.5.5 tests")
