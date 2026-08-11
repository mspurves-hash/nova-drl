#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_evidence_fusion_v1_5.py"

spec = importlib.util.spec_from_file_location("fusion", str(TARGET))
fusion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fusion)

assert fusion.VERSION == "1.5.0"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    collector = root / "collector"
    event_dir = collector / "events" / "130813004"
    event_dir.mkdir(parents=True)

    traveler_dir = root / "traveler" / "130813004"
    traveler_dir.mkdir(parents=True)
    traveler_json = traveler_dir / "traveler_regions.json"
    traveler_json.write_text(json.dumps({
        "source_path": "/nas/130813004 Line Card Warranty.JPG",
        "relative_path": "130813004 Line Card Warranty.JPG",
        "regions": {
            "special_notes": {
                "selected_text": (
                    "Attention! Notes: FA: Y Axis needs to be fixed. ["
                )
            }
        }
    }), encoding="utf-8")

    event = {
        "log_number": "130813004",
        "repair_date": "2013-08-13",
        "repair_date_display": "8/13/2013",
        "daily_sequence": "004",
        "warranty": True,
        "evidence_files": [
            {
                "role": "traveler",
                "relative_path": "130813004 Line Card Warranty.JPG",
                "source_path": "/nas/130813004 Line Card Warranty.JPG",
                "authority": "primary_repair_anchor"
            },
            {
                "role": "robot_checklist",
                "relative_path": "130813004 Robot Checklist.PDF",
                "source_path": "/nas/130813004 Robot Checklist.PDF",
                "authority": "procedure_completion_evidence"
            },
            {
                "role": "robot_test_report",
                "relative_path": "130813004 Robot Test Report.PDF",
                "source_path": "/nas/130813004 Robot Test Report.PDF",
                "authority": "final_test_evidence"
            }
        ],
        "derived_traveler_artifacts": [
            {
                "role": "region_ocr",
                "path": str(traveler_json)
            }
        ],
        "cross_document_complaint_comparison": {
            "status": "strong_cross_document_agreement",
            "raw_candidates": [
                {
                    "raw_value": "Y Axis needs to be fixed",
                    "source_document": (
                        "130813004 Robot Checklist.PDF"
                    ),
                    "document_role": "robot_checklist",
                    "page_number": 1,
                    "source_method": (
                        "header_vision_minicpm-v:latest"
                    )
                },
                {
                    "raw_value": "Y Axis Needs to be Fixed",
                    "source_document": (
                        "130813004 Robot Test Report.PDF"
                    ),
                    "document_role": "robot_test_report",
                    "page_number": 1,
                    "source_method": (
                        "header_vision_minicpm-v:latest"
                    )
                }
            ]
        }
    }

    bundle = {
        "collector_version": "1.4.3.2",
        "scope": "repair_event",
        "serial_metadata": {
            "original_folder_name": (
                "RBT - GB8-MT GENMARK SN 80010732 "
                "UTI MICRON ERICH"
            ),
            "equipment_type": "RBT",
            "oem": "GENMARK",
            "model": "GB8-MT",
            "serial_number": "80010732",
            "customer": "UTI MICRON"
        },
        "repair_event": event
    }

    bundle_path = event_dir / "repair_evidence_bundle.json"
    bundle_path.write_text(
        json.dumps(bundle),
        encoding="utf-8",
    )

    found_path, found_bundle = fusion.locate_bundle(
        collector,
        "130813004",
    )
    review = fusion.build_review(found_path, found_bundle, [])
    complaint = review["fields"]["customer_complaint"]

    assert complaint["canonical_candidate"] == (
        "Y Axis needs to be fixed"
    )
    assert complaint["independent_source_count"] == 3
    assert complaint["confidence"] == "high"
    assert complaint["human_review"]["status"] == "pending"
    assert complaint["accepted_as_human_reviewed_fact"] is False
    assert (
        complaint["qdrant"]["eligible_for_future_ingestion"]
        is False
    )

    output = root / "review"
    decision = fusion.record_decision(
        review,
        output,
        field="customer_complaint",
        decision="approve",
        reviewer="Matt Purves",
        note="Verified against three source documents.",
    )
    fusion.write_outputs(review, output)

    assert decision["decision"] == "approved"
    assert (
        review["fields"]["customer_complaint"]
        ["accepted_as_human_reviewed_fact"]
        is True
    )
    assert (
        review["fields"]["customer_complaint"]
        ["qdrant"]["eligible_for_future_ingestion"]
        is True
    )
    assert review["qdrant_entry_created"] is False

    approved = json.loads(
        (output / "approved_repair_fields.json").read_text()
    )
    assert (
        approved["approved_fields"]["customer_complaint"]["value"]
        == "Y Axis needs to be fixed"
    )
    assert approved["qdrant_entry_created"] is False

conflict = [
    fusion.make_candidate(
        "customer_complaint",
        "Y Axis needs to be fixed",
        "robot_checklist",
        "procedure_completion_evidence",
        "/a",
        "a.pdf",
        "test",
    ),
    fusion.make_candidate(
        "customer_complaint",
        "X Axis needs to be fixed",
        "robot_test_report",
        "final_test_evidence",
        "/b",
        "b.pdf",
        "test",
    ),
]
canonical, reason = fusion.choose_canonical(conflict)
assert canonical is None
assert reason == "meaningful_word_sequences_differ"

print("PASS: Nova Evidence Fusion v1.5 tests")
