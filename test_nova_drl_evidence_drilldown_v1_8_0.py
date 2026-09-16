#!/usr/bin/env python3
import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_drl_evidence_drilldown_v1_8_0.py"

spec = importlib.util.spec_from_file_location("d", TARGET)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def row(label, ids):
    return {
        "primary_value": label,
        "title": label,
        "payload": {"event_ids": ids, "repairs": len(ids)},
    }


def main():
    collections = {
        "part": [row("7800", ["e1", "e2"]), row("BEARING", ["b1", "b2", "b3"])],
        "action": [row("REPLACE BEARING", ["b1", "b2"])],
        "failure": [row("LOW VOLTAGE", ["e1", "e3"])],
    }

    m = d.exact_item_matches(collections, "7800", "auto")
    assert len(m) == 1 and m[0][0] == "part"

    m = d.exact_item_matches(collections, "replace bearing", "auto")
    assert len(m) == 1 and m[0][0] == "action"

    hints = d.fuzzy_item_hints(collections, "bearing", "auto")
    assert any(x[1] == "BEARING" for x in hints)
    assert any(x[1] == "REPLACE BEARING" for x in hints)

    with tempfile.TemporaryDirectory() as td0:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE repair_events("
            "repair_event_id TEXT, log_number TEXT, equipment_family TEXT,"
            "reported_problem_text TEXT, repair_history_text TEXT,"
            "test_outcome_text TEXT, source_paths_json TEXT)"
        )
        conn.execute(
            "CREATE TABLE replacement_mentions("
            "repair_event_id TEXT, manufacturer_pn TEXT, quantity INTEGER,"
            "text TEXT, evidence_quote TEXT, procurement_only_excluded INTEGER)"
        )
        conn.execute(
            "INSERT INTO repair_events VALUES(?,?,?,?,?,?,?)",
            (
                "e1", "240101001", "SVO DRV - TEST",
                "Low voltage", "Replaced 7800 regulator",
                "Passed All Tests",
                json.dumps(["/mnt/drl/test/240101001 Line Card Original.jpg"]),
            ),
        )
        conn.execute(
            "INSERT INTO replacement_mentions VALUES(?,?,?,?,?,0)",
            ("e1", "7800", 1, "7800 regulator", "Replaced 7800 regulator"),
        )

        events = d.fetch_events(conn, ["e1"])
        assert events["e1"]["log_number"] == "240101001"

        paths = d.parse_source_paths(events["e1"])
        assert paths == ["/mnt/drl/test/240101001 Line Card Original.jpg"]

        repl = d.fetch_replacement_mentions(conn, "e1")
        assert len(repl) == 1
        assert d.compact(repl[0]["manufacturer_pn"]) == "7800"

        class Gate:
            @staticmethod
            def component_matches_text(label, text):
                return d.compact(label) in d.compact(text)

        ev = d.relevant_part_evidence(conn, "e1", "7800", Gate)
        assert len(ev) == 1

        conn.close()

    print("PASS: Nova DRL Evidence Drill-Down v1.8.0 tests")


if __name__ == "__main__":
    main()
