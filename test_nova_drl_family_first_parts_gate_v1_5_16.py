#!/usr/bin/env python3
import importlib.util
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_drl_family_first_parts_gate_v1_5_16.py"

spec = importlib.util.spec_from_file_location("m", TARGET)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def comp(label, events):
    return {
        "item_type": "product_part",
        "primary_value": label,
        "title": label,
        "payload": {
            "pn": label,
            "reference_pn": label,
            "repairs": len(events),
            "explicit_repairs": len(events),
            "event_ids": events,
            "reference_kind": "component",
        },
    }


def pn(label, events):
    return {
        "item_type": "product_part",
        "primary_value": label,
        "title": label,
        "payload": {
            "pn": label,
            "reference_pn": label,
            "repairs": len(events),
            "event_ids": events,
            "reference_kind": "pn",
        },
    }


def main():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE replacement_mentions("
        "repair_event_id TEXT,text TEXT,evidence_quote TEXT,"
        "procurement_only_excluded INTEGER)"
    )

    # MOTOR has four upstream events, but only two structured replacement mentions.
    conn.executemany(
        "INSERT INTO replacement_mentions VALUES(?,?,?,0)",
        [
            ("m2", "Motor", "Motor replaced with 400W motor"),
            ("m3", '10" motor, caps', 'Repaired: 10" motor, caps'),
            # m1/m4 intentionally absent: narrative/test recovery only.
            ("b1", "Bearing", "Replaced bearing"),
            ("b2", "Bearings", "Replaced bearings"),
            ("b3", "Top bearing", "Changed top bearing"),
            ("b4", "Bearing", "Bearing replaced"),
            ("belt1", "Belt", "Replaced belt"),
            ("belt2", "Belts", "Changed belts"),
            ("belt3", "Z belt", "Replaced Z belt"),
            ("r1", "Relay", "Replaced relay"),
            ("r2", "Relay", "Replaced relay"),
        ],
    )

    original_rows = [
        comp("MOTOR", ["m1", "m2", "m3", "m4"]),
        comp("BEARING", ["b1", "b2", "b3", "b4"]),
        comp("BELT", ["belt1", "belt2", "belt3"]),
        comp("RELAY", ["r1", "r2"]),
        pn("LM324N", ["p1", "p2"]),
    ]

    def original(conn, families, base_part_number=None):
        return original_rows

    gate = m.make_structured_component_parts_gate(original)
    out = gate(conn, ["FAMILY"], "X")
    by = {r["primary_value"]: r for r in out}

    # False MOTOR row removed: only two structured component-support events.
    assert "MOTOR" not in by

    # Strong recurring generic components remain.
    assert by["BEARING"]["payload"]["repairs"] == 4
    assert by["BELT"]["payload"]["repairs"] == 3

    # Two-event generic component is preserved upstream but omitted from normal 80/20 view.
    assert "RELAY" not in by

    # Explicit PN keeps existing recurrence behavior.
    assert by["LM324N"]["payload"]["repairs"] == 2

    print("PASS: Nova DRL v1.5.16 structured-component Parts gate tests")


if __name__ == "__main__":
    main()
