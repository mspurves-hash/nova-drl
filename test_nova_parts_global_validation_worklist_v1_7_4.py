#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_parts_global_validation_worklist_v1_7_4.py"

def row(label, repairs, families, variants=None, n=1):
    return {
        "global_identity_id": f"gp_{n}",
        "identity_key": label.replace(" ","").upper(),
        "display_label": label,
        "observed_variants": variants or [label],
        "repair_event_count": repairs,
        "family_count": families,
        "families": [f"F{i}" for i in range(families)],
        "mention_count": repairs,
        "recorded_pieces": repairs,
    }

def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        q = td/"queue.jsonl"
        out = td/"out"
        rows = [
            row("LM324N", 100, 20, n=1),       # Tier A
            row("MPSA56", 25, 1, n=2),         # Tier B
            row("ABC123", 12, 1, n=3),         # Tier C
            row("N/A", 37, 31, n=4),           # safe exclude
            row("100uF cap", 28, 4, n=5),      # safe exclude spec
            row("B00NQX0AVC", 34, 12, n=6),    # retail reference
        ]
        q.write_text("".join(json.dumps(x)+"\n" for x in rows))

        run = subprocess.run(
            [
                sys.executable, str(TARGET),
                "--queue", str(q),
                "--output-root", str(out),
            ],
            capture_output=True, text=True,
        )
        assert run.returncode == 0, run.stdout + "\n" + run.stderr

        work = [json.loads(x) for x in
                (out/"manufacturer_validation_worklist_v1_7_4.jsonl").read_text().splitlines()
                if x.strip()]
        exc = [json.loads(x) for x in
               (out/"non_manufacturer_recurring_identities_v1_7_4.jsonl").read_text().splitlines()
               if x.strip()]

        assert [x["display_label"] for x in work] == ["LM324N","MPSA56","ABC123"]
        assert [x["priority_tier"] for x in work] == ["A","B","C"]

        classes = {x["display_label"]:x["classification"] for x in exc}
        assert classes["N/A"] == "not_a_part_identity"
        assert classes["100uF cap"] == "component_spec"
        assert classes["B00NQX0AVC"] == "retail_reference"

        manifest = json.loads(
            (out/"manufacturer_validation_manifest_v1_7_4.json").read_text()
        )
        assert manifest["counts"]["tier_a"] == 1
        assert manifest["counts"]["tier_b"] == 1
        assert manifest["counts"]["tier_c"] == 1
        assert manifest["policy"]["fuzzy_merges"] == 0

        plan = td/"plan"
        run2 = subprocess.run(
            [
                sys.executable, str(TARGET),
                "--queue", str(q),
                "--output-root", str(plan),
                "--plan-only",
            ],
            capture_output=True, text=True,
        )
        assert run2.returncode == 0
        assert not plan.exists()
        assert "PLAN ONLY" in run2.stdout

    print("PASS: Nova DRL Global Manufacturer-PN Validation Worklist v1.7.4 tests")

if __name__ == "__main__":
    main()
