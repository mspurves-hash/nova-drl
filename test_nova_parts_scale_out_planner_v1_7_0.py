#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_parts_scale_out_planner_v1_7_0.py"


def event(eid, family, replaced=0, refs=0, processed=True):
    row = {
        "version": "1.6.0",
        "repair_event_id": eid,
        "equipment_family": family,
        "primary_source_record_ids": [f"src_{eid}"],
    }
    if processed:
        row["facts"] = {
            "parts_replaced": [
                {
                    "text": f"{family} part {i}",
                    "evidence_quote": f"replaced {family} part {i}",
                }
                for i in range(replaced)
            ],
            "part_references": [
                {
                    "reference": f"{family}-PN-{i}",
                    "eligible_component_reference": True,
                }
                for i in range(refs)
            ],
        }
    return row


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        events_path = td / "repair_events_lossless_v1_6_0.jsonl"
        out = td / "out"

        rows = []
        # A=5 parts-bearing events, B=3, C=1, D=1 => 10 total.
        for i in range(5):
            rows.append(event(f"a{i}", "FAMILY_A", replaced=2, refs=3))
        for i in range(3):
            rows.append(event(f"b{i}", "FAMILY_B", replaced=1, refs=1))
        rows.append(event("c0", "FAMILY_C", replaced=1, refs=1))
        rows.append(event("d0", "FAMILY_D", replaced=1, refs=1))

        # Processed but no replacement: must not affect Parts ranking.
        rows.append(event("n0", "NO_PARTS", replaced=0, refs=8))

        # Frozen event not yet processed: must count in frozen rows only.
        rows.append(event("u0", "UNPROCESSED", processed=False))

        events_path.write_text(
            "".join(json.dumps(r) + "\n" for r in rows),
            encoding="utf-8",
        )

        run = subprocess.run(
            [
                sys.executable,
                str(TARGET),
                "--events", str(events_path),
                "--output-root", str(out),
                "--coverage", "0.80",
                "--benchmark-family", "FAMILY_A",
            ],
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, run.stdout + "\n" + run.stderr

        fam = [
            json.loads(x)
            for x in (out / "family_volume_v1_7_0.jsonl").read_text().splitlines()
            if x.strip()
        ]
        targets = [
            json.loads(x)
            for x in (out / "scale_out_targets_80_20_v1_7_0.jsonl").read_text().splitlines()
            if x.strip()
        ]
        sample = [
            json.loads(x)
            for x in (out / "validation_sample_v1_7_0.jsonl").read_text().splitlines()
            if x.strip()
        ]
        manifest = json.loads(
            (out / "parts_scale_out_manifest_v1_7_0.json").read_text()
        )

        assert [x["equipment_family"] for x in fam] == [
            "FAMILY_A", "FAMILY_B", "FAMILY_C", "FAMILY_D"
        ]
        assert fam[0]["parts_repair_events"] == 5
        assert fam[1]["parts_repair_events"] == 3

        # 5 + 3 = exactly 80% of 10 parts-bearing events.
        assert [x["equipment_family"] for x in targets] == ["FAMILY_A", "FAMILY_B"]
        assert abs(targets[-1]["cumulative_event_share"] - 0.8) < 1e-9

        # FAMILY_A benchmark is excluded from validation sample.
        assert all(x["equipment_family"] != "FAMILY_A" for x in sample)

        assert manifest["counts"]["frozen_event_rows"] == 12
        assert manifest["counts"]["processed_event_rows"] == 11
        assert manifest["counts"]["parts_bearing_events"] == 10
        assert manifest["policy"]["canonicalization"] is False
        assert manifest["policy"]["qdrant_entries"] == 0

        # Plan-only does not create output files.
        plan_out = td / "plan"
        run2 = subprocess.run(
            [
                sys.executable,
                str(TARGET),
                "--events", str(events_path),
                "--output-root", str(plan_out),
                "--plan-only",
            ],
            capture_output=True,
            text=True,
        )
        assert run2.returncode == 0
        assert not plan_out.exists()
        assert "PLAN ONLY" in run2.stdout

    print("PASS: Nova DRL Parts Scale-Out Planner v1.7.0 tests")


if __name__ == "__main__":
    main()
