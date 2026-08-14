#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analysis" / "nova_traveler_knowledge_distiller_v1_3_7_2.py"

spec = importlib.util.spec_from_file_location("distiller", SCRIPT)
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
    assert mod.is_clear_template_noise(group("x", "repairs", "Repaired Replaced Inits. Date (m/d/yy)", ["S1", "S2"], ["L1", "L2"], ["Repaired Replaced Inits. Date (m/d/yy)", "Repaired Replaced Inits. Date (m/d/yy)"]))[0]
    assert not mod.is_clear_template_noise(group("y", "repairs", "Repaired VAC Leaks", ["S1", "S2"], ["L1", "L2"], ["Repaired VAC leak at A1", "Repaired VAC leak at A2"]))[0]

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp = root / "input"
        out = root / "out"
        inp.mkdir()
        payload = {
            "reasoner_version": "1.3.7.1",
            "minimum_distinct_logs": 2,
            "minimum_distinct_source_hashes": 2,
            "recurring_group_count": 4,
            "groups": [
                group("g1", "repairs", "A1+A2 Belts", ["S1", "S2", "S3"], ["L1", "L2", "L3"], ["Replaced lower A1+A2 belts", "New upper belts", "Retensioned A1+A2 belts"]),
                group("g2", "repairs", "Z Lead Screws Maintenance", ["S1", "S4"], ["L4", "L5"], ["Cleaned and regreased Z lead screws", "Relubed all 3 Z lead screws"]),
                group("g3", "components", "A1/A2 VAC Solenoids", ["S2", "S5"], ["L6", "L7"], ["A1/A2 vac solenoids / 43um filters", "Replaced vac solenoid"]),
                group("g4", "repairs", "Repaired Replaced Detailed description of repairs/replacements", ["S6", "S7"], ["L8", "L9"], ["Repaired Replaced Detailed description of repairs/replacements (including any costs for new parts)", "Repaired Replaced Detailed description of repairs/replacements"]),
            ],
            "automatic_fact_acceptance": False,
            "accepted_fact_count": 0,
            "qdrant_entries_created": 0,
        }
        (inp / "recurring_patterns_v1_3_7_1.json").write_text(json.dumps(payload), encoding="utf-8")
        cp = subprocess.run([sys.executable, str(SCRIPT), "--input-root", str(inp), "--output-root", str(out)], capture_output=True, text=True)
        assert cp.returncode == 0, cp.stderr
        manifest = json.loads((out / "knowledge_distiller_manifest_v1_3_7_2.json").read_text())
        assert manifest["source_recurring_group_count"] == 4
        assert manifest["main_view_group_count"] == 3
        assert manifest["suppressed_template_noise_count"] == 1
        assert manifest["new_llm_calls"] == 0
        assert manifest["accepted_fact_count"] == 0
        assert manifest["qdrant_entries_created"] == 0
        report = (out / "gb8_provisional_knowledge_report_v1_3_7_2.txt").read_text()
        assert "A1+A2 Belts" in report
        assert "Z Lead Screws Maintenance" in report
        assert "A1/A2 VAC Solenoids" in report
        assert "Repaired Replaced Detailed description" not in report
        stocking = json.loads((out / "stocking_attention_v1_3_7_2.json").read_text())["items"]
        names = {x["item"] for x in stocking}
        assert "Belts" in names
        assert "Vacuum solenoids" in names
        assert "Vacuum filters" in names

    print("PASS: Nova DRL Provisional Knowledge Distiller v1.3.7.2 tests")


if __name__ == "__main__":
    main()
