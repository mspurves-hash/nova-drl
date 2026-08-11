#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_terminology_review_queue_v1_5_2_2.py"

spec = importlib.util.spec_from_file_location("queue1522", str(TARGET))
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)

assert queue.VERSION == "1.5.2.2"

glossary = queue.load_glossary(
    ROOT / "config" / "drl_terminology_v1_5_2_2.json"
)
rules = queue.load_rules(
    ROOT / "config" / "terminology_queue_rules_v1_5_2_2.json"
)
metadata = queue.load_metadata(
    ROOT / "config" / "drl_metadata_identifiers_v1_5_2_2.json"
)
metadata_terms, _ = queue.metadata_identifier_set(metadata)

# Human-confirmed site code is metadata, not terminology.
assert "MTV" in metadata_terms

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "derived"
    root.mkdir()

    # Nine events on one serial repeat an OCR/template-style unknown TMP.
    serial_folder = root / "RBT - GB8-MT GENMARK SN 80010732 UTI MICRON"
    for index in range(1, 10):
        log = "2301{:02d}{:03d}".format(index, index)
        event = serial_folder / log
        event.mkdir(parents=True)
        (event / "traveler_regions.json").write_text(
            json.dumps({
                "source_path": str(
                    serial_folder / "{} Line Card Original.jpg".format(log)
                ),
                "regions": {
                    "special_notes": {
                        "selected_text": (
                            "This customer requires TMP for UNIT INSIDE. "
                            "FA RPT goes with MTV."
                        )
                    }
                },
            }),
            encoding="utf-8",
        )

    # Twelve events across six serials use unknown XYZ in consequential,
    # structured repair actions. This should be HIGH and ask_now.
    for index in range(12):
        serial = "90010{}".format(index % 6)
        log = "2402{:02d}{:03d}".format((index % 9) + 1, index + 1)
        folder = root / (
            "RBT - GB8-MT GENMARK SN {} UTI MICRON".format(serial)
        )
        event = folder / log
        event.mkdir(parents=True, exist_ok=True)
        (event / "repair_entries_v1_3_4_4.json").write_text(
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

    rows, _ = queue.discover_evidence([root])
    unresolved, known, suppressed = queue.build_occurrences(
        rows, glossary, rules, metadata_terms
    )

    unresolved_terms = {row["term"] for row in unresolved}

    # Known terminology does not re-enter the queue.
    assert "FA" not in unresolved_terms
    assert "RPT" not in unresolved_terms
    assert "FE" not in unresolved_terms
    assert any(row["term"] == "FA RPT" for row in known)

    # Common OCR words and site code are suppressed.
    suppressed_terms = {row["term"] for row in suppressed}
    for term in ["UNIT", "INSIDE", "MTV"]:
        assert term in suppressed_terms
        assert term not in unresolved_terms

    # Pure-alpha word longer than 5 is suppressed before queueing.
    assert "INSIDE" not in unresolved_terms

    queue_rows, _, _, suppressions, summary = queue.build_queue(
        [root], glossary, rules, metadata_terms, []
    )
    by_term = {row["term"]: row for row in queue_rows}

    tmp_term = by_term["TMP"]
    xyz = by_term["XYZ"]

    assert tmp_term["unique_repair_events"] == 9
    assert tmp_term["unique_serial_numbers"] == 1
    assert tmp_term["template_repetition"]["template_like"] is True
    assert tmp_term["template_priority_penalty"] > 0
    assert tmp_term["priority"] != "HIGH"

    assert xyz["unique_repair_events"] == 12
    assert xyz["unique_serial_numbers"] == 6
    assert xyz["priority"] == "HIGH"
    assert xyz["intervention_recommendation"] == "ask_now"
    assert xyz["priority_score"] > tmp_term["priority_score"]

    # Suppression reporting is active.
    assert summary["suppression"]["suppressed_occurrence_count"] > 0
    assert (
        summary["suppression"]["counts_by_reason"]["common_english_word"]
        > 0
    )
    assert (
        summary["suppression"]["counts_by_reason"][
            "known_metadata_identifier"
        ] > 0
    )

    # Define / effective glossary behavior remains intact.
    out = Path(tmp) / "queue"
    out.mkdir()
    decision = queue.record_decision(
        out,
        term="XYZ",
        decision="define",
        reviewer="Matt Purves",
        meaning="synthetic test definition",
        scope="OEM=GENMARK",
        category="test_term",
        note="Synthetic validation.",
    )
    assert decision["decision"] == "defined"

    effective = queue.effective_glossary(
        glossary, queue.load_decisions(out)
    )
    defined = [
        row for row in effective["entries"]
        if row["raw_term"] == "XYZ"
    ]
    assert len(defined) == 1
    assert defined[0]["normalized_meaning"] == (
        "synthetic test definition"
    )
    assert effective["source_modified"] is False
    assert effective["qdrant_entry_created"] is False

print("PASS: Nova Terminology Review Queue v1.5.2.2 tests")
