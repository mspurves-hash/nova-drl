#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_terminology_review_queue_v1_5_2_3.py"

spec = importlib.util.spec_from_file_location("queue1523", str(TARGET))
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)

assert queue.VERSION == "1.5.2.3"

glossary = queue.load_glossary(
    ROOT / "config" / "drl_terminology_v1_5_2_3.json"
)
rules = queue.load_rules(
    ROOT / "config" / "terminology_queue_rules_v1_5_2_3.json"
)
metadata = queue.load_metadata(
    ROOT / "config" / "drl_metadata_identifiers_v1_5_2_3.json"
)
metadata_terms, metadata_details = queue.metadata_identifier_set(metadata)

for initials in ["EF", "VT", "SF", "MP", "NP", "RB", "AM", "MB", "BP"]:
    assert initials in metadata_terms

assert metadata_details["EF"]["name"] == "Erich Franke"
assert metadata_details["VT"]["name"] == "Victor Thomas"
assert metadata_details["MP"]["name"] == "Matt Purves"
assert "MTV" in metadata_terms

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "derived"
    root.mkdir()

    # Pilot event with superseded machine reading.
    serial = root / "RBT - GB8-MT GENMARK SN 80010732 UTI MICRON"
    event = serial / "130813004"
    event.mkdir(parents=True)

    (event / "repair_entries_v1_3_4_4.json").write_text(
        json.dumps({
            "reader_version": "1.3.4.4.3",
            "log_number": "130813004",
            "source_path": str(
                serial / "130813004 Line Card Warranty.JPG"
            ),
            "status": "ok",
            "detect_only": False,
            "entries": [
                {
                    "literal_fields": {
                        "description": (
                            "X ADDED Flanged BCS X2 to A1+A3 WALK LINK"
                        ),
                        "notes": (
                            "FE suspected in intermittent homing problem"
                        ),
                    }
                }
            ],
        }),
        encoding="utf-8",
    )

    (event / "traveler_regions.json").write_text(
        json.dumps({
            "source_path": str(
                serial / "130813004 Line Card Warranty.JPG"
            ),
            "regions": {
                "special_notes": {
                    "selected_text": (
                        "EF VT SF MP NP RB AM MB BP reviewed FA RPT. "
                        "SHIP WOOD CRATE AIR."
                    )
                },
                "repairs_replacements": {
                    "selected_text": (
                        "ADDED BCS WALK LINK and strange YY ES RS AX NN TT"
                    )
                },
            },
        }),
        encoding="utf-8",
    )

    # Human-approved action in a separate derived evidence root.
    approved_dir = root / "approved" / "130813004"
    approved_dir.mkdir(parents=True)
    (approved_dir / "approved_repair_fields_with_terminology.json").write_text(
        json.dumps({
            "fusion_version": "1.5.2",
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
                "customer_complaint": {
                    "value": "Y Axis needs to be fixed"
                },
                "repair_actions": [
                    {
                        "action_id": "a2",
                        "action_number": 2,
                        "value": (
                            "Added Flanges BERS x2 to A1 + A2 upper link"
                        ),
                    }
                ],
            },
            "approved_field_count": 2,
            "approved_repair_action_count": 1,
            "qdrant_entry_created": False,
        }),
        encoding="utf-8",
    )

    # High-value unknown XYZ across many serials should still survive.
    for index in range(12):
        serial_num = "90010{}".format(index % 6)
        log = "2402{:02d}{:03d}".format((index % 9) + 1, index + 1)
        folder = root / (
            "RBT - GB8-MT GENMARK SN {} UTI MICRON".format(serial_num)
        )
        ev = folder / log
        ev.mkdir(parents=True, exist_ok=True)
        (ev / "repair_entries_v1_3_4_4.json").write_text(
            json.dumps({
                "reader_version": "1.3.4.4.3",
                "log_number": log,
                "source_path": str(
                    folder / "{} Line Card Original.jpg".format(log)
                ),
                "status": "ok",
                "detect_only": False,
                "entries": [
                    {
                        "literal_fields": {
                            "description": (
                                "Adjusted XYZ after FE home check"
                            ),
                            "notes": None,
                        }
                    }
                ],
            }),
            encoding="utf-8",
        )

    # Low-support 2-char OCR fragments should be gated.
    noise_folder = root / "RBT - GB8-MT GENMARK SN 81111111 UTI MICRON"
    noise_event = noise_folder / "220101001"
    noise_event.mkdir(parents=True)
    (noise_event / "traveler_regions.json").write_text(
        json.dumps({
            "source_path": str(
                noise_folder / "220101001 Line Card Original.jpg"
            ),
            "regions": {
                "special_notes": {
                    "selected_text": "YY ES RS AX NN TT appeared in OCR."
                }
            },
        }),
        encoding="utf-8",
    )

    rows, stats = queue.discover_evidence([root])
    filtered, shadowed = queue.apply_authority_shadowing(rows, rules)

    # Lower-authority machine repair action and raw repairs region are shadowed.
    shadow_fields = {row["field"] for row in shadowed}
    assert "structured_repair_action" in shadow_fields
    assert "repairs_region_ocr" in shadow_fields

    # Diagnostic note is preserved.
    assert any(
        row["field"] == "diagnostic_note"
        and "FE suspected" in row["text"]
        for row in filtered
    )

    # Human-approved BERS action is preserved.
    assert any(
        row["field"] == "approved_repair_action"
        and "BERS" in row["text"]
        for row in filtered
    )

    (
        queue_rows,
        occurrences,
        known_usage,
        candidate_suppressions,
        shadowed_evidence,
        low_support,
        summary,
    ) = queue.build_queue(
        [root], glossary, rules, metadata_terms, []
    )

    unresolved_terms = {row["term"] for row in queue_rows}

    # Superseded machine vocabulary must not enter the queue.
    assert "BCS" not in unresolved_terms
    assert "WALK" not in unresolved_terms

    # Technician initials and site code are metadata, not terminology.
    for term in ["EF", "VT", "SF", "MP", "NP", "RB", "AM", "MB", "BP", "MTV"]:
        assert term not in unresolved_terms

    # Common words stay out.
    for term in ["SHIP", "WOOD", "CRATE", "AIR", "ADDED", "LINK"]:
        assert term not in unresolved_terms

    # Short one-event OCR fragments are gated.
    for term in ["YY", "ES", "RS", "AX", "NN", "TT"]:
        assert term not in unresolved_terms
    assert any(
        row["reason"].startswith("low_support_2_char_ocr_fragment")
        for row in low_support
    )

    # Known terms are still recognized.
    known_terms = {row["term"] for row in known_usage}
    assert "FE" in known_terms
    assert "FA RPT" in known_terms
    assert "BERS" in known_terms

    # High-value cross-serial unknown remains and asks now.
    by_term = {row["term"]: row for row in queue_rows}
    assert "XYZ" in by_term
    assert by_term["XYZ"]["priority"] == "HIGH"
    assert by_term["XYZ"]["intervention_recommendation"] == "ask_now"

    assert summary["authority_shadowing"]["shadowed_evidence_row_count"] >= 2
    assert summary["low_support_suppression_count"] >= 1
    assert summary["qdrant_entry_created"] is False

print("PASS: Nova Terminology Review Queue v1.5.2.3 tests")
