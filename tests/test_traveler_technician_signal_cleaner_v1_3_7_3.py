#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analysis" / "nova_traveler_technician_signal_cleaner_v1_3_7_3.py"

spec = importlib.util.spec_from_file_location("cleaner", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def group(gid, lane, label, serials, logs, raws):
    return {
        "group_id": gid,
        "lane": lane,
        "concept_label": label,
        "concept_key": label.lower().replace(" ", "_"),
        "candidate_count": len(raws),
        "distinct_log_count": len(logs),
        "logs": logs,
        "distinct_source_hash_count": len(logs),
        "distinct_serial_count": len(serials),
        "serial_numbers": serials,
        "raw_variants": [
            {
                "candidate_id": f"c_{gid}_{i}",
                "log_number": logs[i % len(logs)],
                "serial_number": serials[i % len(serials)],
                "raw_source_text": raw,
                "source_sha256": f"hash_{gid}_{i}",
                "source_path": f"/source/{gid}/{i}.jpg",
            }
            for i, raw in enumerate(raws)
        ],
        "status": "provisional_recurrence_python_counted_not_approved",
    }


def main():
    motor = group("m", "repairs", "Motor Replacements and Refurbishments", ["S1", "S2"], ["L1", "L2"], ["Replaced motor", "Rebuilt Y motor"])
    assert set(mod.matched_service_areas(motor)) == {"Motors & harmonics", "Refurbishment / rebuild"}
    assert "Sensors / scanner / connectors" not in mod.matched_service_areas(motor)
    assert "Belts & tension" not in mod.matched_service_areas(motor)

    shipping = group("ship", "customer_requirements", "Shipping New Style Crate", ["S1", "S2"], ["L1", "L2"], ["Use KEAL case", "Do not use wood crate"])
    assert mod.route_group(shipping)[0] == "reference"

    fa = group("fa", "terminology", "FA RPT", ["S1", "S2"], ["L1", "L2"], ["FA", "FA RPT"])
    assert mod.route_group(fa)[0] == "reference"

    identity = group("id", "components", "GB8-MT (GENMARK)", ["S1", "S2"], ["L1", "L2"], ["RBT - GB8-MT (GENMARK)", "RBT - GB8-MT (GENMARK)"])
    assert mod.route_group(identity) == ("reference", "equipment_identity_not_service_signal")

    belts = group("b", "repairs", "A1+A2 Belts", ["S1", "S2", "S3"], ["L1", "L2", "L3"], ["Replaced A1+A2 belts", "New lower belts", "Retensioned upper belts"])
    assert "Belts & tension" in mod.matched_service_areas(belts)
    assert "A1/A2 arms & geometry" in mod.matched_service_areas(belts)

    mixed = group("mix", "repairs", "Arm Refresh", ["S1", "S2", "S3"], ["L1", "L2", "L3"], ["Rebuilt arms with new bearings", "Replaced bearings in arm", "Checked vacuum"])
    matched, logs, serials, basis = mod.stocking_match(mixed, ("bearing", "bearings"))
    assert matched and basis == "repeated_explicit_action_evidence"
    assert logs == {"L1", "L2"}
    assert serials == {"S1", "S2"}

    one_stray = group("stray", "repairs", "Arm Refresh", ["S1", "S2"], ["L1", "L2"], ["Rebuilt arms", "Looked at encoder once"])
    assert not mod.stocking_match(one_stray, ("encoder", "encoders"))[0]

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "v1372"
        out = root / "out"
        inp.mkdir()
        kept = [belts, motor, shipping, fa, identity, mixed]
        suppressed = [group("tpl", "repairs", "Repaired Replaced Detailed description of repairs/replacements", ["S9", "S10"], ["L9", "L10"], ["Repaired Replaced Detailed description of repairs/replacements", "Repaired Replaced Detailed description of repairs/replacements"])]
        (inp / "distilled_recurring_patterns_v1_3_7_2.json").write_text(json.dumps({
            "distiller_version": "1.3.7.2",
            "source_reasoner_version": "1.3.7.1",
            "source_recurring_group_count": len(kept) + len(suppressed),
            "main_view_group_count": len(kept),
            "suppressed_template_noise_count": len(suppressed),
            "groups": kept,
            "accepted_fact_count": 0,
            "qdrant_entries_created": 0,
        }), encoding="utf-8")
        (inp / "suppressed_template_noise_v1_3_7_2.json").write_text(json.dumps({
            "distiller_version": "1.3.7.2",
            "suppressed_count": len(suppressed),
            "groups": suppressed,
        }), encoding="utf-8")
        (inp / "knowledge_distiller_manifest_v1_3_7_2.json").write_text(json.dumps({
            "distiller_version": "1.3.7.2",
            "source_recurring_group_count": len(kept) + len(suppressed),
            "new_llm_calls": 0,
            "accepted_fact_count": 0,
            "qdrant_entries_created": 0,
        }), encoding="utf-8")

        cp = subprocess.run([sys.executable, str(SCRIPT), "--input-root", str(inp), "--output-root", str(out)], capture_output=True, text=True)
        assert cp.returncode == 0, cp.stderr
        manifest = json.loads((out / "technician_signal_manifest_v1_3_7_3.json").read_text())
        assert manifest["source_recurring_group_count"] == 7
        assert manifest["technician_group_count"] == 3  # belts, motor, mixed
        assert manifest["reference_group_count"] == 4  # shipping, FA, identity, template
        assert manifest["new_llm_calls"] == 0
        assert manifest["accepted_fact_count"] == 0
        assert manifest["qdrant_entries_created"] == 0

        report = (out / "gb8_technician_signal_report_v1_3_7_3.txt").read_text()
        assert "A1+A2 Belts" in report
        assert "Motor Replacements and Refurbishments" in report
        assert "Shipping New Style Crate" not in report
        assert "FA RPT" not in report
        assert "GB8-MT (GENMARK)" not in report
        assert "Repaired Replaced Detailed description" not in report

        ref = (out / "gb8_reference_patterns_v1_3_7_3.txt").read_text()
        assert "Shipping New Style Crate" in ref
        assert "FA RPT" in ref
        assert "GB8-MT (GENMARK)" in ref

        service = json.loads((out / "service_area_rollup_v1_3_7_3.json").read_text())["areas"]
        by_area = {x["service_area"]: x for x in service}
        motor_labels = {x["concept_label"] for x in by_area["Motors & harmonics"]["top_patterns"]}
        assert "Motor Replacements and Refurbishments" in motor_labels
        sensor_labels = {x["concept_label"] for x in by_area.get("Sensors / scanner / connectors", {}).get("top_patterns", [])}
        assert "Motor Replacements and Refurbishments" not in sensor_labels

        stocking = json.loads((out / "stocking_attention_v1_3_7_3.json").read_text())["items"]
        names = {x["item"] for x in stocking}
        assert "Belts" in names
        assert "Bearings" in names

    print("PASS: Nova DRL Technician Signal Cleaner v1.3.7.3 tests")


if __name__ == "__main__":
    main()
