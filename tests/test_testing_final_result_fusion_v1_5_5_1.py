#!/usr/bin/env python3
import importlib.util
import json
import tempfile
import time
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TARGET = (
    ROOT
    / "ingest"
    / "nova_testing_final_result_fusion_v1_5_5_1.py"
)

spec = importlib.util.spec_from_file_location("tfr1551", str(TARGET))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

assert mod.VERSION == "1.5.5.1"
rules = mod.load_rules(
    ROOT
    / "config"
    / "testing_final_result_rules_v1_5_5_1.json"
)

source_data = {
    "fusion_version": "1.5.4",
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
            "value": "Y Axis needs to be fixed"
        },
        "repair_actions": [
            {
                "value": (
                    "Adjusted Y-FE from around 9000 down to around "
                    "3000 by slipping Y belt a few teeth"
                )
            },
            {
                "value": (
                    "Added Flanges BERS x2 to A1 + A2 upper link"
                )
            },
        ],
        "parts_replaced": [
            {"part": "flanged bearings", "quantity": 2}
        ],
    },
    "approved_field_count": 4,
    "approved_repair_action_count": 2,
    "approved_parts_replaced_count": 1,
    "qdrant_entry_created": False,
}

analyses = [
    {
        "analysis_id": "checklist-page2",
        "vision_status": "ok",
        "source": {
            "source_kind": "supporting_document_page",
            "document_role": "robot_checklist",
            "source_document": "130813004 Robot Checklist.PDF",
            "source_path": "/mnt/drl/130813004 Robot Checklist.PDF",
            "page_number": 2,
            "image_path": "/tmp/checklist2.png",
        },
        "parsed_analysis": {
            "testing_items": [
                # v1.5.5 false positive: enum echo + generic mark.
                {
                    "step_label": "Remove shipping bracket",
                    "event_mark": "handwritten mark",
                    "mark_type": (
                        "checkmark|x_mark|initials|handwritten_value|"
                        "circle|pass_fail_mark|other"
                    ),
                    "result": "completed",
                    "confidence": "high",
                },
                # v1.5.5 false positive: result became printed paragraph.
                {
                    "step_label": "Check overall alignment of A1,2",
                    "event_mark": "X",
                    "mark_type": "checkmark",
                    "result": (
                        "good indication whether the belt has jumped "
                        "teeth and other arm issues"
                    ),
                    "confidence": "high",
                },
                # Valid supporting-doc test.
                {
                    "step_label": (
                        "Try to replicate the customer problem with "
                        "a specific axis"
                    ),
                    "event_mark": "X",
                    "mark_type": "checkmark",
                    "result": "completed",
                    "confidence": "high",
                },
            ],
            "final_result_items": [],
            "other_event_observations": [],
            "printed_template_only_labels": [],
            "uncertain_marks": [],
        },
    },
    {
        "analysis_id": "report-page2",
        "vision_status": "ok",
        "source": {
            "source_kind": "supporting_document_page",
            "document_role": "robot_test_report",
            "source_document": "130813004 Robot Test Report.PDF",
            "source_path": "/mnt/drl/130813004 Robot Test Report.PDF",
            "page_number": 2,
            "image_path": "/tmp/report2.png",
        },
        "parsed_analysis": {
            "testing_items": [
                {
                    "step_label": "Check belt tension on all axes.",
                    "event_mark": "X",
                    "mark_type": "x_mark",
                    "result": "completed",
                    "confidence": "high",
                },
                # v1.5.5 false mark: long printed instruction.
                {
                    "step_label": (
                        "Check the vacuum on both ports for proper readings"
                    ),
                    "event_mark": (
                        "Pass/Fail [4E2 Command: Out1,0 vacuum open "
                        "sensor reading for A1]"
                    ),
                    "mark_type": "pass_fail_mark",
                    "result": "pass",
                    "confidence": "high",
                },
            ],
            "final_result_items": [],
            "other_event_observations": [],
            "printed_template_only_labels": [],
            "uncertain_marks": [],
        },
    },
    {
        "analysis_id": "report-page1",
        "vision_status": "ok",
        "source": {
            "source_kind": "supporting_document_page",
            "document_role": "robot_test_report",
            "source_document": "130813004 Robot Test Report.PDF",
            "source_path": "/mnt/drl/130813004 Robot Test Report.PDF",
            "page_number": 1,
            "image_path": "/tmp/report1.png",
        },
        "parsed_analysis": {
            "testing_items": [],
            "final_result_items": [
                # v1.5.5 false final: customer complaint.
                {
                    "value": "Y Axis needs to be fixed",
                    "basis_label": "Customer Problem/Symptom Description",
                    "event_mark": "handwritten_value",
                    "result": "other",
                    "confidence": "high",
                },
                # v1.5.5 false final: no explicit final context.
                {
                    "value": "Scanner check",
                    "basis_label": "",
                    "event_mark": (
                        "Try a slow test scan by issuing the SCFD command."
                    ),
                    "result": "other",
                    "confidence": "high",
                },
            ],
            "other_event_observations": [],
            "printed_template_only_labels": [],
            "uncertain_marks": [],
        },
    },
    {
        "analysis_id": "final-test",
        "vision_status": "ok",
        "source": {
            "source_kind": "traveler_event_crop",
            "document_role": "traveler",
            "source_document": "final_test.png",
            "source_path": "/derived/final_test.png",
            "page_number": None,
            "image_path": "/derived/final_test.png",
        },
        "parsed_analysis": {
            "testing_items": [
                # Must NOT become testing; should be recovered as final.
                {
                    "step_label": "Passed All Tests",
                    "event_mark": "Checked box",
                    "mark_type": "checkmark",
                    "result": "pass",
                    "confidence": "high",
                },
                # Must route away from testing.
                {
                    "step_label": "Ttl Time Spent (Hours)",
                    "event_mark": "4 hours handwritten",
                    "mark_type": "handwritten_value",
                    "result": "recorded_value",
                    "recorded_value": 4.0,
                    "confidence": "high",
                },
                {
                    "step_label": "Warranty Sticker Applied",
                    "event_mark": "Checked box",
                    "mark_type": "checkmark",
                    "result": "completed",
                    "confidence": "high",
                },
            ],
            "final_result_items": [
                {
                    "value": "No Trouble Found",
                    "basis_label": "Final Unit Test Results and Notes",
                    "event_mark": "Checked box",
                    "result": "other",
                    "confidence": "high",
                },
                {
                    "value": "Passed All Tests",
                    "basis_label": "Final Unit Test Results and Notes",
                    "event_mark": "Checked box",
                    "result": "pass",
                    "confidence": "high",
                },
            ],
            "other_event_observations": [],
            "printed_template_only_labels": [],
            "uncertain_marks": [],
        },
    },
    {
        "analysis_id": "shipping-final",
        "vision_status": "ok",
        "source": {
            "source_kind": "traveler_event_crop",
            "document_role": "traveler",
            "source_document": "shipping_final_ok.png",
            "source_path": "/derived/shipping_final_ok.png",
            "page_number": None,
            "image_path": "/derived/shipping_final_ok.png",
        },
        "parsed_analysis": {
            "testing_items": [
                # v1.5.5 invalid test enum; traveler source must not be test.
                {
                    "step_label": "Final O.K.",
                    "event_mark": "G",
                    "mark_type": "initials",
                    "result": "completed|recorded_value",
                    "confidence": "high",
                }
            ],
            "final_result_items": [
                {
                    "value": "48+ hours in Final Testing: FINAL OK.",
                    "basis_label": "hours in final testing",
                    "event_mark": "48+ hours / FINAL OK",
                    "result": "final_ok",
                    "confidence": "high",
                }
            ],
            "other_event_observations": [],
            "printed_template_only_labels": [],
            "uncertain_marks": [],
        },
    },
]

review = mod.build_review(analyses, rules, [], source_data)

# Only the two legitimate supporting-document tests survive.
assert review["testing"]["candidate_count"] == 2
assert {
    row["step_label"]
    for row in review["testing"]["candidates"]
} == {
    "Try to replicate the customer problem with a specific axis",
    "Check belt tension on all axes.",
}

# Noise is rejected/routed instead of becoming review work.
reasons = {
    row["reason"]
    for row in review["hardening"]["testing_rejections"]
}
assert "invalid_mark_type_enum" in reasons
assert "invalid_testing_result_enum" in reasons
assert ("event_mark_too_long" in reasons or "event_mark_looks_like_printed_instruction" in reasons)

routed_labels = {
    row["label"]
    for row in review["hardening"]["routed_observations"]
}
assert "Ttl Time Spent (Hours)" in routed_labels
assert "Warranty Sticker Applied" in routed_labels
assert "Passed All Tests" in routed_labels
assert "Final O.K." in routed_labels

# Customer complaint and scanner instruction cannot survive as final results.
final_values = {
    row["value"] for row in review["final_result"]["candidates"]
}
assert "Y Axis needs to be fixed" not in final_values
assert "Scanner check" not in final_values

# Genuine final-disposition candidates remain.
assert "Passed All Tests" in final_values
assert "48+ hours in Final Testing: FINAL OK." in final_values
assert "Final O.K." in final_values
assert "No Trouble Found" in final_values

# No Trouble Found is explicitly conflicted by the approved repair history,
# and the mutually-exclusive same-source dispositions are also flagged.
ntf = next(
    row
    for row in review["final_result"]["candidates"]
    if row["value"] == "No Trouble Found"
)
assert "conflicts_with_approved_repair_actions" in ntf["conflict_flags"]
assert "conflicts_with_approved_parts_replaced" in ntf["conflict_flags"]
assert (
    "mutually_exclusive_final_options_detected_same_source"
    in ntf["conflict_flags"]
)
assert ntf["approval_requires_conflict_acknowledgement"] is True

passed = next(
    row
    for row in review["final_result"]["candidates"]
    if row["value"] == "Passed All Tests"
)
assert (
    "mutually_exclusive_final_options_detected_same_source"
    in passed["conflict_flags"]
)

with tempfile.TemporaryDirectory() as tmp:
    out = Path(tmp)

    # Conflict candidate cannot be approved accidentally.
    try:
        mod.record_decision(
            out,
            "approve-final",
            "Matt Purves",
            review["testing"]["candidates"],
            review["final_result"]["candidates"],
            final_number=ntf["final_number"],
        )
        raise AssertionError(
            "Conflict candidate was approved without acknowledgement."
        )
    except ValueError:
        pass

    decision = mod.record_decision(
        out,
        "approve-final",
        "Matt Purves",
        review["testing"]["candidates"],
        review["final_result"]["candidates"],
        final_number=ntf["final_number"],
        acknowledge_conflict=True,
        note="Synthetic test only.",
    )
    assert decision["conflict_acknowledged"] is True

# Cache regression: second identical run must reuse the cache.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    image_path = root / "page.png"
    Image.new("RGB", (20, 20), "white").save(image_path)

    source = {
        "source_kind": "supporting_document_page",
        "document_role": "robot_checklist",
        "source_document": "test.pdf",
        "source_path": "/mnt/drl/test.pdf",
        "page_number": 1,
        "image_path": str(image_path),
        "template_ocr_text": "",
    }

    first = mod.analyze_sources(
        [source],
        root / "output",
        "minicpm-v:latest",
        10,
        2200,
        refresh=False,
        no_vision=True,
    )
    assert first[0]["cache_status"] == "created"

    second = mod.analyze_sources(
        [source],
        root / "output",
        "minicpm-v:latest",
        10,
        2200,
        refresh=False,
        no_vision=True,
    )
    assert second[0]["cache_status"] == "reused"

    # Change the image so the signature invalidates.
    time.sleep(0.01)
    Image.new("RGB", (21, 20), "white").save(image_path)

    third = mod.analyze_sources(
        [source],
        root / "output",
        "minicpm-v:latest",
        10,
        2200,
        refresh=False,
        no_vision=True,
    )
    assert third[0]["cache_status"] == "invalidated"

print("PASS: Nova DRL Testing / Final Result Fusion v1.5.5.1 tests")
