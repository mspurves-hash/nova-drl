#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "analysis" / "nova_traveler_corpus_sorter_v1_3_6_0.py"
spec = importlib.util.spec_from_file_location("sorter_v1360", MOD_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_mask():
    raw = "Date Shipped: X\nHours in Final Testing: 96+ Final O.K. Initials and date UT 1/2/13\nRepair line"
    masked, suppressed = mod.mask_global_audit_fields(raw)
    check("96+" not in masked, "hours value must be suppressed from sort view")
    check("Final O.K. Initials and date UT 1/2/13" in masked, "adjacent non-hours text should remain")
    check(len(suppressed) == 1, "suppression audit should record one field")
    masked2, _ = mod.mask_global_audit_fields("Hours in Final Testing: Final O.K. UT 1/2/13")
    check("Final O.K. UT 1/2/13" in masked2, "blank hours field must not consume adjacent Final O.K. text")


def test_support():
    src = "X 2x Blue Schmoo's for A1+A2 UT 1/28\nTurkey fat is cause"
    check(mod.find_supported_source_slice(src, "Turkey fat is cause") == ("exact", "Turkey fat is cause"), "exact quote support failed")
    support = mod.find_supported_source_slice("Adjusted Y-FE from\naround 9000 down to 3000", "Adjusted Y-FE from around 9000 down to 3000")
    check(support is not None and support[0] == "whitespace_only", "whitespace-only support failed")
    check(mod.find_supported_source_slice(src, "Blue Schmoo") is None, "character rewrite must be rejected")
    check(mod.candidate_id("1", "sha", "same", "repair_or_service") != mod.candidate_id("1", "sha", "same", "component_or_part"), "same source phrase in different kinds needs distinct candidate IDs")


def test_manifest_validation():
    good = {
        "collector_version": "1.3.5.1",
        "inventory_only": False,
        "interrupted": False,
        "records": [{"log_number": "1", "vision_status": "ok", "raw_transcription_path": "/tmp/x"}],
    }
    check(len(mod.validate_collector_manifest(good)) == 1, "complete manifest should validate")
    bad = dict(good)
    bad["interrupted"] = True
    try:
        mod.validate_collector_manifest(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("interrupted manifest must fail by default")


def test_prospector_validation_and_recurrence():
    rec1 = {"log_number": "130130006", "record_id": "r1", "source_sha256": "sha1", "source_path": "/a", "raw_transcription_path": "/ra"}
    rec2 = {"log_number": "130813004", "record_id": "r2", "source_sha256": "sha2", "source_path": "/b", "raw_transcription_path": "/rb"}
    src1 = "X 2x Blue Schmoo's for A1+A2\nSucked A1 A2 + Y"
    src2 = "Adjusted Y-FE from around 9000 down to around 3000\nSugar Cube test"
    parsed1 = {"candidates": [
        {"kind": "shop_term_or_abbreviation", "raw_quote": "Blue Schmoo"},
        {"kind": "shop_term_or_abbreviation", "raw_quote": "2x Blue Schmoo's for A1+A2"},
        {"kind": "repair_or_service", "raw_quote": "Sucked A1 A2 + Y"},
    ]}
    c1, r1 = mod.validate_prospector_output(parsed1, rec1, src1, mod.sha256_text(src1))
    check(len(c1) == 2, "two evidence-backed candidates expected")
    check(len(r1) == 1 and r1[0]["reason"] == "raw_quote_not_supported_by_transcription", "rewritten shop term should be rejected")

    parsed2 = {"candidates": [
        {"kind": "repair_or_service", "raw_quote": "Adjusted Y-FE from around 9000 down to around 3000"},
        {"kind": "testing_or_process", "raw_quote": "Sugar Cube test"},
    ]}
    c2, _ = mod.validate_prospector_output(parsed2, rec2, src2, mod.sha256_text(src2))
    allc = c1 + c2

    # A one-log group must be rejected even if the model calls it recurring.
    one_id = c2[0]["candidate_id"]
    parsed_reason = {
        "recurring_groups": [{"group_type": "repair_or_service", "label": "Y adjustment", "member_candidate_ids": [one_id]}],
        "unique_high_value_candidate_ids": [],
        "human_review_candidate_ids": [],
    }
    accepted, rejected, _, _ = mod.validate_reasoning_output(parsed_reason, allc)
    check(len(accepted) == 0 and len(rejected) == 1, "one-log recurring group must be rejected")

    # Build a synthetic two-log group using one evidence-backed candidate from each log.
    parsed_reason2 = {
        "recurring_groups": [{
            "group_type": "repair_or_service",
            "label": "provisional service family",
            "member_candidate_ids": [c1[-1]["candidate_id"], c2[0]["candidate_id"]],
        }],
        "unique_high_value_candidate_ids": [c2[1]["candidate_id"]],
        "human_review_candidate_ids": [],
    }
    accepted2, rejected2, unique2, _ = mod.validate_reasoning_output(parsed_reason2, allc)
    check(len(accepted2) == 1 and not rejected2, "two-log/two-source-hash group should pass deterministic recurrence")
    check(accepted2[0]["distinct_log_count"] == 2, "Python must own distinct-log count")
    check(unique2 == [c2[1]["candidate_id"]], "valid unique candidate ID should survive")


def main():
    test_mask()
    test_support()
    test_manifest_validation()
    test_prospector_validation_and_recurrence()
    print("PASS: Nova DRL Traveler Corpus Prospector + Sorter v1.3.6.0 tests")


if __name__ == "__main__":
    main()
