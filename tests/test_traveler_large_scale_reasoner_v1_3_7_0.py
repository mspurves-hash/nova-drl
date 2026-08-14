#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analysis" / "nova_traveler_large_scale_reasoner_v1_3_7_0.py"
spec = importlib.util.spec_from_file_location("v1370", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(mod)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def cand(cid, log, kind, raw, sha=None, source=None):
    return {
        "candidate_id": cid,
        "log_number": log,
        "record_id": "rec_" + cid,
        "source_sha256": sha or ("sha_" + cid),
        "kind": kind,
        "raw_source_text": raw,
        "source_path": source or f"/mnt/drl/RBT - GB8-MT GENMARK SN 80010{int(log[-3:]):03d} MICRON/{log} Line Card Original.jpg",
        "raw_transcription_path": f"/out/{log}.txt",
        "status": "evidence_backed_candidate_not_approved",
    }


def test_metadata_filter_and_lanes():
    c1 = cand("a", "130000001", "part_number_or_identifier", "DRL part # RBT-GB8-MT (GENMARK)")
    c2 = cand("b", "130000002", "repair_or_service", "Rebuilt A1/A2 motors")
    c3 = cand("c", "130000003", "unclear_ocr", "Comm. was [unclear]")
    eligible, excluded = mod.prepare_reasoning_candidates([c1, c2, c3])
    check(len(eligible) == 1 and eligible[0]["reasoning_lane"] == "repairs", "repair should remain eligible")
    check(len(excluded) == 2, "metadata and unclear OCR should be preserved outside 32B working set")
    reasons = {x["reasoning_exclusion_reason"] for x in excluded}
    check(any("identity" in r or "model_identity" in r for r in reasons), "model identity exclusion missing")
    check(any("unclear_ocr" in r for r in reasons), "unclear OCR exclusion missing")


def test_serial_parse():
    p1 = "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80010732 UTI MICRON/130130006 Line Card Original.jpg"
    p2 = "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN  GB8-MT-80110451 PTB SALES/250429004 Line Card Original.jpg"
    check(mod.extract_serial_from_source_path(p1) == "80010732", "plain GB8 serial parse failed")
    check(mod.extract_serial_from_source_path(p2) == "80110451", "prefixed GB8 serial parse failed")


def test_dynamic_batch_packing():
    rows = []
    for i in range(20):
        c = cand(f"c{i}", f"130000{i:03d}", "repair_or_service", "Rebuilt A1/A2 motors " + ("x" * 80))
        c["reasoning_lane"] = "repairs"
        rows.append(c)
    batches = mod.pack_candidates(rows, max_chars=1000)
    check(len(batches) > 1, "small max_chars should create multiple batches")
    flattened = [x["candidate_id"] for b in batches for x in b]
    check(sorted(flattened) == sorted(x["candidate_id"] for x in rows), "batch packing must preserve every candidate exactly once")
    check(all(len({x["reasoning_lane"] for x in b}) == 1 for b in batches), "batches may not mix semantic lanes")


def test_stage1_validation_and_fallback():
    rows = [
        cand("a", "130000001", "repair_or_service", "Rebuilt A1 motor"),
        cand("b", "130000002", "repair_or_service", "Rebuilt A2 motor"),
        cand("c", "130000003", "repair_or_service", "Replaced Y belt"),
    ]
    for r in rows:
        r["reasoning_lane"] = "repairs"
    parsed = {
        "clusters": [
            {"concept_label": "A motor rebuild", "concept_key": "a_motor_rebuild", "member_candidate_ids": ["a", "b"]},
            {"concept_label": "bad", "concept_key": "bad", "member_candidate_ids": ["b", "DOES_NOT_EXIST"]},
        ]
    }
    clusters, rejected = mod.validate_stage1_clusters(parsed, rows)
    ids_by_cluster = [set(x["member_candidate_ids"]) for x in clusters]
    check(any(x == {"a", "b"} for x in ids_by_cluster), "valid model cluster should survive")
    check(any(x == {"c"} for x in ids_by_cluster), "unassigned candidate must fall back to singleton")
    all_ids = [cid for x in clusters for cid in x["member_candidate_ids"]]
    check(sorted(all_ids) == ["a", "b", "c"], "every candidate must appear exactly once after validation")
    check(rejected and rejected[0]["reason"] == "unknown_or_duplicate_candidate_ids", "bad IDs should be audited")


def test_exact_key_consolidation():
    clusters = [
        {"cluster_id": "x1", "lane": "repairs", "concept_label": "A1/A2 motor rebuild", "concept_key": "a1_a2_motor_rebuild", "member_candidate_ids": ["a"]},
        {"cluster_id": "x2", "lane": "repairs", "concept_label": "A1 A2 motor rebuild", "concept_key": "a1_a2_motor_rebuild", "member_candidate_ids": ["b"]},
        {"cluster_id": "x3", "lane": "repairs", "concept_label": "Repair", "concept_key": "repair", "member_candidate_ids": ["c"]},
        {"cluster_id": "x4", "lane": "repairs", "concept_label": "Repair", "concept_key": "repair", "member_candidate_ids": ["d"]},
    ]
    out = mod.consolidate_exact_model_keys(clusters)
    check(any(set(x["member_candidate_ids"]) == {"a", "b"} for x in out), "matching specific provisional keys should consolidate")
    check(sum(1 for x in out if set(x["member_candidate_ids"]) in ({"c"}, {"d"})) == 2, "generic keys must not auto-consolidate")


def test_merge_union_and_recurrence_counts():
    candidates = [
        cand("a", "130000001", "repair_or_service", "Rebuilt A1 motor", sha="sha1", source="/mnt/drl/RBT - GB8-MT GENMARK SN 80010001 X/130000001 Line Card Original.jpg"),
        cand("b", "130000002", "repair_or_service", "Rebuilt A2 motor", sha="sha2", source="/mnt/drl/RBT - GB8-MT GENMARK SN 80010002 X/130000002 Line Card Original.jpg"),
        cand("c", "130000003", "repair_or_service", "Replaced Y belt", sha="sha3", source="/mnt/drl/RBT - GB8-MT GENMARK SN 80010003 X/130000003 Line Card Original.jpg"),
    ]
    for c in candidates:
        c["serial_number"] = mod.extract_serial_from_source_path(c["source_path"])
        c["unit_folder"] = mod.unit_folder_from_source_path(c["source_path"])
        c["reasoning_lane"] = "repairs"
    by = {c["candidate_id"]: c for c in candidates}
    clusters = [
        {"cluster_id": "x1", "lane": "repairs", "concept_label": "A motor rebuild", "concept_key": "a_motor_rebuild", "member_candidate_ids": ["a"]},
        {"cluster_id": "x2", "lane": "repairs", "concept_label": "A motor rebuild", "concept_key": "a_motor_rebuild_2", "member_candidate_ids": ["b"]},
        {"cluster_id": "x3", "lane": "repairs", "concept_label": "Y belt", "concept_key": "y_belt", "member_candidate_ids": ["c"]},
    ]
    proposals = [{"concept_label": "A motor rebuild", "concept_key": "a_motor_rebuild", "member_cluster_ids": ["x1", "x2"]}]
    merged, n = mod.apply_merge_proposals(clusters, proposals)
    check(n == 1 and len(merged) == 2, "merge proposal should create one union component")
    rec = mod.recurrence_groups(merged, by)
    check(len(rec) == 1, "only the 2-event merged repair should be recurrent")
    g = rec[0]
    check(g["distinct_log_count"] == 2 and g["distinct_source_hash_count"] == 2, "Python recurrence counts must use logs and source hashes")
    check(g["distinct_serial_count"] == 2, "distinct serial count should be computed independently")
    check(len(g["raw_variants"]) == 2, "full evidence provenance must remain attached")


def test_merge_validation():
    batch = [
        {"cluster_id": "x1", "lane": "repairs", "concept_label": "one", "concept_key": "one", "member_candidate_ids": ["a"]},
        {"cluster_id": "x2", "lane": "repairs", "concept_label": "two", "concept_key": "two", "member_candidate_ids": ["b"]},
    ]
    good = {"merge_groups": [{"concept_label": "same", "concept_key": "same", "member_cluster_ids": ["x1", "x2"]}]}
    accepted, rejected = mod.validate_merge_output(good, batch)
    check(len(accepted) == 1 and not rejected, "valid merge IDs should pass")
    bad = {"merge_groups": [{"concept_label": "bad", "concept_key": "bad", "member_cluster_ids": ["x1", "nope"]}]}
    accepted2, rejected2 = mod.validate_merge_output(bad, batch)
    check(not accepted2 and rejected2[0]["reason"] == "unknown_cluster_ids", "unknown cluster IDs must be rejected")


def test_manifest_guardrails():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        manifest = {
            "sorter_version": "1.3.6.1",
            "status": "prospect_only_complete",
            "reasoning_performed": False,
            "candidate_count": 1,
            "global_policy": {"accepted_fact_count": 0, "qdrant_entries_created": 0},
        }
        (root / "sort_manifest_v1_3_6_1.json").write_text(json.dumps(manifest), encoding="utf-8")
        (root / "candidate_ledger_v1_3_6_1.jsonl").write_text(json.dumps(cand("a", "130000001", "repair_or_service", "Rebuilt motor")) + "\n", encoding="utf-8")
        got = mod.validate_input_manifest(root)
        rows = mod.load_candidates(root, got)
        check(len(rows) == 1, "valid prospect-only input should load")
        manifest["global_policy"]["qdrant_entries_created"] = 1
        (root / "sort_manifest_v1_3_6_1.json").write_text(json.dumps(manifest), encoding="utf-8")
        try:
            mod.validate_input_manifest(root)
        except ValueError:
            pass
        else:
            raise AssertionError("input with Qdrant writes must be rejected")


def main():
    test_metadata_filter_and_lanes()
    test_serial_parse()
    test_dynamic_batch_packing()
    test_stage1_validation_and_fallback()
    test_exact_key_consolidation()
    test_merge_union_and_recurrence_counts()
    test_merge_validation()
    test_manifest_guardrails()
    print("PASS: Nova DRL Large-Scale Batched Corpus Reasoner v1.3.7.0 tests")


if __name__ == "__main__":
    main()
