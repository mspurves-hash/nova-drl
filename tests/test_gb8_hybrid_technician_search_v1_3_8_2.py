#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analysis" / "nova_gb8_hybrid_technician_search_v1_3_8_2.py"
spec = importlib.util.spec_from_file_location("hybrid", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def g(gid, label, serials, logs):
    return {
        "group_id": gid,
        "concept_label": label,
        "lane": "diagnostics",
        "distinct_serial_count": serials,
        "distinct_log_count": logs,
        "raw_variants": [],
    }


def test_rrf_uses_rank_not_raw_score_scale():
    groups = {"a": g("a", "A", 4, 5), "b": g("b", "B", 4, 5)}
    semantic = [
        {"score": 0.6, "payload": {"recurring_group_id": "a"}},
        {"score": 0.59, "payload": {"recurring_group_id": "b"}},
    ]
    # Give B an absurd deterministic raw score. Rank, not 999999, should matter.
    python_rows = [
        {"group_id": "b", "score": 999999.0},
        {"group_id": "a", "score": 0.001},
    ]
    fused = m.fuse_rankings(semantic, python_rows, groups, recurrence_weight=0.0)
    assert abs(fused[0]["rrf_score"] - fused[1]["rrf_score"]) < 1e-9
    assert all(row["hybrid_score"] < 0.1 for row in fused)


def test_consensus_beats_single_engine_top_rank():
    groups = {
        "both": g("both", "Both", 5, 5),
        "qonly": g("qonly", "Q only", 5, 5),
        "ponly": g("ponly", "P only", 5, 5),
    }
    semantic = [
        {"score": 0.9, "payload": {"recurring_group_id": "qonly"}},
        {"score": 0.8, "payload": {"recurring_group_id": "both"}},
    ]
    python_rows = [
        {"group_id": "ponly", "score": 99.0},
        {"group_id": "both", "score": 2.0},
    ]
    fused = m.fuse_rankings(semantic, python_rows, groups, recurrence_weight=0.0)
    assert fused[0]["group_id"] == "both", fused
    assert fused[0]["engine_count"] == 2


def test_recurrence_is_small_tiebreaker():
    groups = {
        "strong": g("strong", "Strong", 40, 70),
        "weak": g("weak", "Weak", 2, 2),
    }
    semantic = [
        {"score": 0.9, "payload": {"recurring_group_id": "weak"}},
        {"score": 0.8, "payload": {"recurring_group_id": "strong"}},
    ]
    python_rows = []
    fused = m.fuse_rankings(semantic, python_rows, groups, recurrence_weight=0.0003)
    # Rank 1 remains ahead despite weaker recurrence; support is a tiebreaker, not relevance override.
    assert fused[0]["group_id"] == "weak", fused
    assert fused[1]["recurrence_bonus"] > fused[0]["recurrence_bonus"]


def test_stale_qdrant_group_is_ignored():
    groups = {"known": g("known", "Known", 2, 2)}
    semantic = [
        {"score": 0.9, "payload": {"recurring_group_id": "stale"}},
        {"score": 0.8, "payload": {"recurring_group_id": "known"}},
    ]
    fused = m.fuse_rankings(semantic, [], groups)
    assert [x["group_id"] for x in fused] == ["known"]


def test_config_load():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "p.json"
        p.write_text(json.dumps({"hybrid_policy": {"semantic_top": 20, "rrf_k": 55}}), encoding="utf-8")
        policy = m.load_policy(p)
        assert policy["semantic_top"] == 20
        assert policy["rrf_k"] == 55
        assert policy["python_top"] == 12


def test_render_contains_engine_provenance():
    row = {
        "group_id": "g1",
        "group": {
            "group_id": "g1", "concept_label": "Y Axis Drift", "distinct_serial_count": 10,
            "distinct_log_count": 14, "v1_3_7_3_service_areas": ["Servo / drift / homing"],
        },
        "semantic_rank": 1, "python_rank": 2, "semantic_score": 0.605,
        "python_score": 40.7, "recurrence_support": 0.5, "hybrid_score": 0.034,
        "examples": [{"log_number": "110718004", "serial_number": "80050608", "raw_source_text": "Y Drift problem"}],
    }
    text = m.render_hybrid({
        "query": "Y axis drifting", "collection": m.DEFAULT_COLLECTION,
        "semantic_top": 12, "python_top": 12, "semantic_python_overlap": 5,
        "top_union_serial_count": 10, "top_union_log_count": 14, "results": [row],
    })
    assert "source=BOTH" in text
    assert "Qrank=1" in text and "Prank=2" in text
    assert "raw Qdrant/Python scores are NOT added" in text


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
    print("PASS: Nova DRL GB8 Hybrid Technician Search v1.3.8.2 tests")
