#!/usr/bin/env python3
import importlib.util
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_evidence_fusion_v1_5_1.py"

spec = importlib.util.spec_from_file_location("fusion151", str(TARGET))
fusion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fusion)

assert fusion.VERSION == "1.5.1"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    old_cwd = Path.cwd()
    os.chdir(root)
    try:
        collector = root / "collector"
        event_dir = collector / "events" / "130813004"
        event_dir.mkdir(parents=True)

        traveler_dir = root / "traveler" / "130813004"
        traveler_dir.mkdir(parents=True)

        traveler_regions = traveler_dir / "traveler_regions.json"
        traveler_regions.write_text(json.dumps({
            "source_path": "/nas/130813004 Line Card Warranty.JPG",
            "relative_path": "130813004 Line Card Warranty.JPG",
            "regions": {
                "special_notes": {
                    "selected_text": "Notes: FA: Y Axis needs to be fixed. ["
                },
                "repairs_replacements": {
                    "crop_path": "/local/repairs_replacements.png",
                    "selected_psm": 11,
                    "selected_score": 444,
                    "selected_text": "Replaced Y bearing\nCleaned Y lead screw"
                }
            }
        }), encoding="utf-8")

        repair_entries = traveler_dir / "repair_entries_v1_3_4_2.json"
        repair_entries.write_text(json.dumps({
            "reader_version": "1.3.4.2",
            "log_number": "130813004",
            "source_path": "/nas/130813004 Line Card Warranty.JPG",
            "relative_path": "130813004 Line Card Warranty.JPG",
            "status": "ok",
            "model": "minicpm-v:latest",
            "detect_only": False,
            "vision_processing_stopped": False,
            "accepted_as_facts": 0,
            "entries": [
                {
                    "entry_index": 1,
                    "blank_rejected": False,
                    "literal_fields": {
                        "description": "Replaced Y axis motor bearing",
                        "initials": "EF",
                        "date": "9/24/13"
                    },
                    "eligible_for_evidence_comparison": True,
                    "review_reasons": [
                        "vision_transcription_requires_human_review"
                    ],
                    "initials_validation": {"status": "valid"},
                    "date_validation": {"status": "plausible"},
                    "glossary_matches": [],
                    "crop_paths": {
                        "full_row": "/local/entry_01_full.png"
                    },
                    "tesseract": {
                        "full_row": {
                            "selected_text": "Replaced Y axis motor bearing"
                        }
                    },
                    "vision": {
                        "response": (
                            "Replaced Y axis motor bearing | EF | 9/24/13"
                        )
                    }
                },
                {
                    "entry_index": 2,
                    "blank_rejected": False,
                    "literal_fields": {
                        "description": "Cleaned Y axis lead screw",
                        "initials": "EF",
                        "date": "9/24/13"
                    },
                    "eligible_for_evidence_comparison": False,
                    "review_reasons": [
                        "vision_transcription_requires_human_review",
                        "date_requires_review"
                    ],
                    "initials_validation": {"status": "valid"},
                    "date_validation": {"status": "incomplete"},
                    "glossary_matches": [],
                    "crop_paths": {
                        "full_row": "/local/entry_02_full.png"
                    },
                    "tesseract": {
                        "full_row": {"selected_text": "Cleaned Y axis lead screw"}
                    },
                    "vision": {
                        "response": "Cleaned Y axis lead screw | EF | 9/24"
                    }
                }
            ]
        }), encoding="utf-8")

        notes_text = root / "notes.txt"
        notes_text.write_text(
            "Replaced Y axis motor bearing\nOther unrelated note\n",
            encoding="utf-8"
        )

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
                },
                {
                    "role": "internal_checklist_notes",
                    "relative_path": "130813004 Internal Checklist Notes.docx",
                    "source_path": "/nas/130813004 Internal Checklist Notes.docx",
                    "authority": "technician_working_notes",
                    "extraction": {
                        "status": "ok",
                        "method": "docx_xml",
                        "text_path": str(notes_text)
                    }
                }
            ],
            "derived_traveler_artifacts": [
                {"role": "region_ocr", "path": str(traveler_regions)},
                {
                    "role": "repair_entry_extraction",
                    "path": str(repair_entries)
                }
            ],
            "cross_document_complaint_comparison": {
                "status": "strong_cross_document_agreement",
                "raw_candidates": [
                    {
                        "raw_value": "Y Axis needs to be fixed",
                        "source_document": "130813004 Robot Checklist.PDF",
                        "document_role": "robot_checklist",
                        "page_number": 1,
                        "source_method": "header_vision_minicpm-v:latest"
                    },
                    {
                        "raw_value": "Y Axis Needs to be Fixed",
                        "source_document": "130813004 Robot Test Report.PDF",
                        "document_role": "robot_test_report",
                        "page_number": 1,
                        "source_method": "header_vision_minicpm-v:latest"
                    }
                ]
            }
        }

        bundle = {
            "collector_version": "1.4.3.2",
            "scope": "repair_event",
            "serial_metadata": {
                "original_folder_name": (
                    "RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH"
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
        bundle_path.write_text(json.dumps(bundle), encoding="utf-8")

        prior_dir = (
            root / "output" / "evidence_fusion_v1_5"
            / "RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH"
            / "events" / "130813004"
        )
        prior_dir.mkdir(parents=True)
        prior_decision = {
            "decision_id": "prior-complaint-1",
            "field": "customer_complaint",
            "decision": "approved",
            "reviewer": "Matt Purves",
            "reviewed_at_utc": "2026-08-11T19:06:21+00:00",
            "value": "Y Axis needs to be fixed",
            "edited_from_canonical": False,
            "canonical_candidate_at_review": "Y Axis needs to be fixed",
            "note": "Verified.",
            "fusion_version": "1.5.0",
            "qdrant_entry_created": False
        }
        (prior_dir / "human_review_decisions.json").write_text(
            json.dumps([prior_decision]), encoding="utf-8"
        )

        found_path, found_bundle = fusion.base.locate_bundle(
            collector, "130813004"
        )
        prior = fusion.load_decisions_from_dir(prior_dir)
        review = fusion.build_review(
            found_path,
            found_bundle,
            collector,
            "130813004",
            prior_decisions=prior,
            local_decisions=[],
        )

        assert review["fusion_version"] == "1.5.1"
        assert (
            review["fields"]["customer_complaint"]["human_review"]["status"]
            == "approved"
        )

        actions = review["fields"]["repair_actions"]
        assert actions["candidate_status"] == "structured_candidates_available"
        assert actions["candidate_count"] == 2
        assert actions["items"][0]["canonical_candidate"] == (
            "Replaced Y axis motor bearing"
        )
        assert actions["items"][0]["confidence"] == "high"
        assert actions["items"][0]["independent_source_count"] == 2
        assert actions["items"][1]["confidence"] == "low"
        assert actions["approved_action_count"] == 0

        output_dir = (
            root / "output" / "evidence_fusion_v1_5_1"
            / "RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH"
            / "events" / "130813004"
        )

        decision = fusion.record_action_decision(
            review,
            output_dir,
            "approve",
            "Matt Purves",
            action_number=1,
            note="Verified against traveler and technician notes.",
        )
        local = fusion.load_decisions_from_dir(output_dir)
        review = fusion.build_review(
            found_path,
            found_bundle,
            collector,
            "130813004",
            prior_decisions=prior,
            local_decisions=local,
        )
        fusion.write_outputs(review, output_dir, prior_decisions=prior)

        assert decision["decision"] == "approved"
        assert review["fields"]["repair_actions"]["approved_action_count"] == 1
        assert (
            review["fields"]["repair_actions"]["items"][0]
            ["qdrant"]["eligible_for_future_ingestion"]
            is True
        )
        assert review["qdrant_entry_created"] is False

        approved = json.loads(
            (output_dir / "approved_repair_fields.json").read_text()
        )
        assert approved["approved_fields"]["customer_complaint"]["value"] == (
            "Y Axis needs to be fixed"
        )
        assert len(approved["approved_fields"]["repair_actions"]) == 1
        assert approved["qdrant_entry_created"] is False

        # Safety test: raw repairs-region OCR alone must not create action facts.
        event_no_structured = dict(event)
        event_no_structured["derived_traveler_artifacts"] = [
            {"role": "region_ocr", "path": str(traveler_regions)}
        ]
        no_actions = fusion.build_repair_actions_field(event_no_structured)
        assert no_actions["candidate_count"] == 0
        assert no_actions["candidate_status"] == (
            "structured_traveler_extraction_required"
        )
        assert (
            no_actions["fallback_region_evidence"]
            ["converted_to_action_candidates"]
            is False
        )
    finally:
        os.chdir(old_cwd)

print("PASS: Nova Evidence Fusion v1.5.1 tests")
