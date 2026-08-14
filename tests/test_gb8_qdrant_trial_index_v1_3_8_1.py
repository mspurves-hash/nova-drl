#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analysis" / "nova_gb8_qdrant_trial_index_v1_3_8_1.py"
spec = importlib.util.spec_from_file_location("qtrial", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def sample_group(gid: str = "rg_test001"):
    return {
        "group_id": gid,
        "lane": "diagnostics",
        "concept_label": "Y Axis Drift",
        "concept_key": "y_axis_drift",
        "distinct_serial_count": 2,
        "distinct_log_count": 3,
        "candidate_count": 3,
        "serial_numbers": ["80000001", "80000002"],
        "logs": ["240101001", "240101002", "240101003"],
        "member_candidate_ids": ["c1", "c2", "c3"],
        "v1_3_7_3_service_areas": ["Servo / drift / homing"],
        "raw_variants": [
            {
                "log_number": "240101001",
                "serial_number": "80000001",
                "candidate_id": "c1",
                "source_sha256": "a" * 64,
                "source_path": "/readonly/a.jpg",
                "raw_source_text": "Y axis position intermittently drifts off position.",
            },
            {
                "log_number": "240101002",
                "serial_number": "80000002",
                "candidate_id": "c2",
                "source_sha256": "b" * 64,
                "source_path": "/readonly/b.jpg",
                "raw_source_text": "Changed Y encoder; drift still present.",
            },
            {
                "log_number": "240101003",
                "serial_number": "80000002",
                "candidate_id": "c3",
                "source_sha256": "c" * 64,
                "source_path": "/readonly/c.jpg",
                "raw_source_text": "Checked Y home flag for binding.",
            },
        ],
    }


def test_embedding_text_and_payload():
    g = sample_group()
    text = m.build_embedding_text(g, evidence_limit=3, max_chars=6000)
    assert "Concept: Y Axis Drift" in text
    assert "3 distinct repair logs" in text
    assert "Changed Y encoder" in text
    payload = m.build_point_payload(g, text, evidence_limit=3)
    assert payload["recurring_group_id"] == "rg_test001"
    assert payload["approved"] is False
    assert payload["knowledge_status"] == "provisional"
    assert payload["qdrant_role"] == "disposable_semantic_search_index"
    assert payload["serial_numbers"] == ["80000001", "80000002"]
    assert payload["candidate_ids"] == ["c1", "c2", "c3"]
    assert len(payload["source_hashes"]) == 3


def test_deterministic_uuid():
    a = m.deterministic_point_id("rg_test001")
    b = m.deterministic_point_id("rg_test001")
    c = m.deterministic_point_id("rg_test002")
    assert a == b
    assert a != c
    assert len(a) == 36


def test_guarded_collection_delete_names():
    m.assert_trial_collection_name("nova_drl_gb8_trial_v1_3_8_1")
    try:
        m.assert_trial_collection_name("production_knowledge")
    except ValueError:
        pass
    else:
        raise AssertionError("unguarded collection name was not rejected")


def test_prepare_records_unique():
    rows = m.prepare_records([sample_group("rg_a"), sample_group("rg_b")], 3, 6000)
    assert len(rows) == 2
    assert rows[0]["point_id"] != rows[1]["point_id"]
    try:
        m.prepare_records([sample_group("rg_a"), sample_group("rg_a")], 3, 6000)
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate group ID was not rejected")


def test_synthetic_baseline_load_and_plan():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        g1 = sample_group("rg_a")
        g2 = sample_group("rg_b")
        tech = {
            "signal_cleaner_version": "1.3.7.3",
            "source_recurring_group_count": 3,
            "technician_group_count": 2,
            "groups": [g1, g2],
            "accepted_fact_count": 0,
            "qdrant_entries_created": 0,
        }
        ref = {
            "signal_cleaner_version": "1.3.7.3",
            "reference_group_count": 1,
            "groups": [{"group_id": "rg_ref", "lane": "terminology", "concept_label": "FA"}],
        }
        manifest = {
            "signal_cleaner_version": "1.3.7.3",
            "source_recurring_group_count": 3,
            "technician_group_count": 2,
            "reference_group_count": 1,
        }
        (root / "technician_patterns_v1_3_7_3.json").write_text(json.dumps(tech), encoding="utf-8")
        (root / "reference_patterns_v1_3_7_3.json").write_text(json.dumps(ref), encoding="utf-8")
        (root / "technician_signal_manifest_v1_3_7_3.json").write_text(json.dumps(manifest), encoding="utf-8")
        baseline = m.load_frozen_baseline(root, strict_counts=False)
        assert baseline["counts"] == {"source": 3, "technician": 2, "reference": 1}
        records = m.prepare_records(baseline["technician_groups"], 3, 6000)
        plan = m.make_plan(baseline, records, m.DEFAULT_COLLECTION, m.DEFAULT_EMBED_MODEL, 24)
        assert plan["planned_points"] == 2
        assert plan["generative_reasoning_calls"] == 0
        assert plan["accepted_fact_count"] == 0


def test_search_rendering():
    payload = m.build_point_payload(sample_group(), m.build_embedding_text(sample_group()), 3)
    result = {
        "query": "Y axis drifting",
        "collection": m.DEFAULT_COLLECTION,
        "results": [{"score": 0.81234, "payload": payload}],
    }
    text = m.render_semantic(result)
    assert "Y Axis Drift" in text
    assert "0.8123" in text
    assert "PROVISIONAL" in text


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
    print("PASS: Nova DRL GB8 Qdrant Trial Index v1.3.8.1 tests")
