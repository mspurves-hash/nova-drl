#!/usr/bin/env python3
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_parts_full_corpus_gate_v1_7_3.py"

def ev(eid, fam):
    return {"repair_event_id": eid, "equipment_family": fam}

def part(eid, fam, pn=None, text=None, qty=1):
    return {
        "repair_event_id": eid,
        "equipment_family": fam,
        "part_number": pn,
        "quantity": qty,
        "text": text or pn or "component",
        "evidence_quote": text or pn or "component",
    }

def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        ep = td/"repair_events_v1_5_2.jsonl"
        pp = td/"replacement_mentions_v1_5_2.jsonl"
        db = td/"knowledge.sqlite"
        out = td/"out"

        events = [
            ev("a1","BOARD_A"), ev("a2","BOARD_A"), ev("a3","BOARD_A"),
            ev("a4","BOARD_A"), ev("b1","MOTOR_B"), ev("b2","MOTOR_B"),
        ]
        parts = [
            # Safe exact-format collapse.
            part("a1","BOARD_A","D45H11"),
            part("a2","BOARD_A","D45-H11"),
            # Distinct suffix must remain distinct.
            part("a3","BOARD_A","2N2222"),
            part("a4","BOARD_A","2N2222A"),
            # Supplier wrapper can attach because body independently exists.
            part("a3","BOARD_A","LM324N"),
            part("a4","BOARD_A","511-LM324N"),
            # Spec, not manufacturer PN.
            part("a1","BOARD_A","47uF"),
            part("a2","BOARD_A","47 uF"),
            # Mechanical description recurrence.
            part("b1","MOTOR_B",None,"Bearing"),
            part("b2","MOTOR_B",None,"Bearing"),
        ]
        ep.write_text("".join(json.dumps(x)+"\n" for x in events))
        pp.write_text("".join(json.dumps(x)+"\n" for x in parts))

        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE repair_events(x)")
        conn.execute("CREATE TABLE product_families(x)")
        conn.execute("CREATE TABLE product_parts(x)")
        conn.executemany("INSERT INTO repair_events VALUES(?)", [(1,),(2,)])
        conn.execute("INSERT INTO product_families VALUES(1)")
        conn.executemany("INSERT INTO product_parts VALUES(?)", [(1,),(2,),(3,)])
        conn.commit(); conn.close()

        run = subprocess.run(
            [
                sys.executable, str(TARGET),
                "--events", str(ep),
                "--parts", str(pp),
                "--knowledge-db", str(db),
                "--output-root", str(out),
            ],
            capture_output=True, text=True,
        )
        assert run.returncode == 0, run.stdout + "\n" + run.stderr

        gids = [json.loads(x) for x in
                (out/"global_recurring_pn_identities_v1_7_3.jsonl").read_text().splitlines()
                if x.strip()]
        by_key = {x["identity_key"]: x for x in gids}

        # D45H11 and D45-H11 collapse only because alphanumeric sequence is exact.
        assert by_key["D45H11"]["repair_event_count"] == 2
        assert set(by_key["D45H11"]["observed_variants"]) == {"D45H11","D45-H11"}

        # Suffix variants remain distinct identities.
        assert "2N2222" in by_key and "2N2222A" in by_key

        # 47uF classified as component spec and punctuation/space format collapsed.
        assert by_key["47UF"]["identity_class"] == "component_spec"
        assert by_key["47UF"]["repair_event_count"] == 2

        suppliers = [json.loads(x) for x in
                     (out/"supplier_aliases_v1_7_3.jsonl").read_text().splitlines()
                     if x.strip()]
        assert len(suppliers) == 1
        assert suppliers[0]["status"] == "resolved_to_independently_observed_body"
        assert suppliers[0]["supplier"] == "Mouser"

        fam = [json.loads(x) for x in
               (out/"family_recurring_parts_v1_7_3.jsonl").read_text().splitlines()
               if x.strip()]
        labels = {(x["family"], x["display_label"]) for x in fam}
        assert ("MOTOR_B","Bearing") in labels

        # Python sqlite3 snapshot works even without sqlite3 CLI.
        manifest = json.loads((out/"full_corpus_parts_gate_manifest_v1_7_3.json").read_text())
        snap = manifest["inputs"]["knowledge_db"]
        assert snap["readable"] is True
        assert snap["product_parts_rows"] == 3

        # Plan-only writes nothing.
        plan = td/"plan"
        run2 = subprocess.run(
            [
                sys.executable, str(TARGET),
                "--events", str(ep),
                "--parts", str(pp),
                "--knowledge-db", str(db),
                "--output-root", str(plan),
                "--plan-only",
            ],
            capture_output=True, text=True,
        )
        assert run2.returncode == 0
        assert not plan.exists()
        assert "PLAN ONLY" in run2.stdout

    print("PASS: Nova DRL Full-Corpus Recurring Parts Gate v1.7.3 tests")

if __name__ == "__main__":
    main()
