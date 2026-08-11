#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QUEUE = ROOT / "ingest" / "nova_terminology_review_queue_v1_5_2_1.py"

spec = importlib.util.spec_from_file_location("queue1521", str(QUEUE))
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)

assert queue.VERSION == "1.5.2.1"

glossary = queue.load_glossary(
    ROOT / "config" / "drl_terminology_v1_5_2_1.json"
)
rules = queue.load_rules(
    ROOT / "config" / "terminology_queue_rules_v1_5_2_1.json"
)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "derived"
    root.mkdir()

    # Four unique repair events use unknown XYZ in consequential repair actions.
    # Event 1 repeats XYZ twice; this must not become two unique events.
    for index in range(1, 5):
        log = "23010{}00{}".format(index, index)
        folder = root / "RBT - GB8-MT GENMARK SN 80010{} UTI MICRON".format(index)
        event = folder / log
        event.mkdir(parents=True)

        description = (
            "Adjusted XYZ and checked XYZ FE"
            if index == 1
            else "Adjusted XYZ after FE home check"
        )
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
                            "description": description,
                            "notes": None,
                        }
                    }
                ],
            }),
            encoding="utf-8",
        )

    # Low-value unknown QZ appears once in OCR-only Special Notes.
    qfolder = root / "RBT - GB8-MT GENMARK SN 89999999 UTI MICRON"
    qevent = qfolder / "230201001"
    qevent.mkdir(parents=True)
    (qevent / "traveler_regions.json").write_text(
        json.dumps({
            "source_path": str(
                qfolder / "230201001 Line Card Original.jpg"
            ),
            "regions": {
                "special_notes": {
                    "selected_text": "QZ noted once in receiving comments."
                }
            },
        }),
        encoding="utf-8",
    )

    rows, stats = queue.discover_evidence([root])
    unresolved_occ, known_occ = queue.build_occurrences(
        rows, glossary, rules
    )

    unresolved_terms = {row["term"] for row in unresolved_occ}
    assert "XYZ" in unresolved_terms
    assert "QZ" in unresolved_terms

    # FE is known and must not enter unresolved queue.
    assert "FE" not in unresolved_terms
    assert any(row["term"] == "FE" for row in known_occ)

    queue_rows, occurrences, known_usage, summary = queue.build_queue(
        [root], glossary, rules, []
    )
    by_term = {row["term"]: row for row in queue_rows}

    xyz = by_term["XYZ"]
    qz = by_term["QZ"]

    assert xyz["unique_repair_events"] == 4
    assert xyz["raw_occurrences"] >= 5
    assert xyz["priority_score"] > qz["priority_score"]
    assert xyz["priority"] in {"MEDIUM", "HIGH"}
    assert xyz["intervention_recommendation"] in {"queue", "ask_now"}
    assert xyz["scope_suggestion"]["suggested_scope"] in {
        "OEM=GENMARK",
        "OEM=GENMARK;model=GB8-MT",
    }

    # Human definition creates a derived effective glossary.
    out = Path(tmp) / "queue"
    out.mkdir()
    decision = queue.record_decision(
        out,
        term="XYZ",
        decision="define",
        reviewer="Matt Purves",
        meaning="example test term",
        scope="OEM=GENMARK",
        category="test_term",
        note="Synthetic validation definition.",
    )
    assert decision["decision"] == "defined"

    decisions = queue.load_decisions(out)
    effective = queue.effective_glossary(glossary, decisions)
    defined = [
        entry for entry in effective["entries"]
        if entry["raw_term"] == "XYZ"
    ]
    assert len(defined) == 1
    assert defined[0]["normalized_meaning"] == "example test term"
    assert effective["source_modified"] is False
    assert effective["qdrant_entry_created"] is False

print("PASS: Nova Terminology Review Queue v1.5.2.1 tests")
