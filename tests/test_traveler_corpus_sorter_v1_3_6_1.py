#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "analysis" / "nova_traveler_corpus_sorter_v1_3_6_1.py"
spec = importlib.util.spec_from_file_location("sorter_v1361", MOD_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_mask_hours():
    raw = "Date Shipped: X\nHours in Final Testing: 96+ Final O.K. Initials and date UT 1/2/13\nRepair line"
    masked, suppressed = mod.mask_global_audit_fields(raw)
    check("Hours in Final Testing" not in masked, "hours field name must be absent from working view")
    check("96+" not in masked, "hours value must be absent from working view")
    check("Final O.K. Initials and date UT 1/2/13" in masked, "adjacent Final O.K. text must not be consumed by hours mask")
    check(len(suppressed) == 1, "one hours field should be audited")


def test_sanitation_preserves_event_text():
    raw = """Direct Repair Laboratories - Testing Traveler
Log # 130813004 RMA Number: 28852
Serial # GB8-MT-80010732
[Notes (specific to this customer)]
This customer requires an FA RPT on all BDs and P/Ss (sent electronically)
Turkey fat is cause
Final Unit Test Results and Notes
Passed All Tests ✓
Power-on Tests Only TOP to Bottom Dual
Ttl Time Spent (Hours) WAFER OVERNIGHT x 2
Tested Robot w/ Sugar
Cube test (Homing in Script Log?) over
Next Weekend
Cleaned ✓ Aligned ✓ Adjusted ✓ Latest Firmware ✓
Hours in Final Testing: 48+ Final O.K. Initials and date VT 9/23/13
"""
    view, audit = mod.sanitize_for_prospecting(raw)
    check("Direct Repair Laboratories" not in view, "document title should be removed from working view")
    check("Log # 130813004" not in view, "event identity should be removed from working view")
    check("GB8-MT-80010732" not in view, "serial identity should be removed from working view")
    check("Passed All Tests" not in view, "generic final checkbox should be removed")
    check("Hours in Final Testing" not in view and "48+" not in view, "global hours field must be absent")
    check("Final O.K." not in view, "adjacent Final O.K. admin should be removed by sanitation")
    check("This customer requires an FA RPT" in view, "customer requirement must survive sanitation")
    check("Turkey fat is cause" in view, "event diagnostic must survive sanitation")
    check("TOP to Bottom Dual" in view and "Power-on Tests Only" not in view, "printed test label should be stripped while handwritten trailing text survives")
    check("WAFER OVERNIGHT x 2" in view and "Ttl Time Spent" not in view, "printed time label should be stripped while event-specific test wording survives")
    check("Sugar\nCube test" in view, "line-wrapped Sugar Cube wording must survive")
    check(len(audit) >= 8, "sanitation must leave an audit trail")


def test_support_modes():
    src = "X 2x Blue Schmoo's for A1+A2 UT 1/28\nTested Robot w/ Sugar\nCube test (Homing in Script Log?) over\nNext Weekend\nAdjusted Y-FE from\naround 9000 down to 3000"
    check(mod.find_supported_source_slice(src, "2x Blue Schmoo's for A1+A2 UT 1/28") == ("exact", "2x Blue Schmoo's for A1+A2 UT 1/28"), "exact support failed")
    support = mod.find_supported_source_slice(src, "Adjusted Y-FE from around 9000 down to 3000")
    check(support is not None and support[0] == "whitespace_only", "whitespace-only support failed")
    sugar = mod.find_supported_source_slice(src, "Tested Robot w/ Sugar Cube test (Homing in Script Log?) over Next Weekend.")
    check(sugar is not None and sugar[0] == "whitespace_terminal_punctuation", "single model-added terminal punctuation should be accepted")
    check("Sugar\nCube" in sugar[1], "accepted evidence must return the exact source slice, not rewritten text")
    check(mod.find_supported_source_slice(src, "Blue Schmoo") is None, "apostrophe/word rewrite must be rejected")
    check(mod.find_supported_source_slice(src, "around 9000 down to 3001") is None, "digit change must be rejected")


def test_kind_override():
    kind, why = mod.deterministic_kind_override("robot Fas are put inside packaging with unit..", "repair_or_service")
    check(kind == "customer_requirement" and why, "known customer requirement pattern must override a model repair misclassification")
    kind2, why2 = mod.deterministic_kind_override("Rebuilt A1/A2 motors", "repair_or_service")
    check(kind2 == "repair_or_service" and why2 is None, "ordinary repair text must not be deterministically rewritten")


def _rec(log, rid, sha):
    return {
        "log_number": log,
        "record_id": rid,
        "source_sha256": sha,
        "source_path": f"/{log}.jpg",
        "raw_transcription_path": f"/{log}.txt",
    }


def test_prospector_validation():
    record = _rec("130813004", "r1", "sha1")
    raw = """Direct Repair Laboratories - Testing Traveler
Log # 130813004
This customer requires an FA RPT on all BDs and P/Ss (sent electronically)
X 2x Blue Schmoo's for A1+A2
Tested Robot w/ Sugar
Cube test (Homing in Script Log?) over
Next Weekend
"""
    view, _ = mod.sanitize_for_prospecting(raw)
    parsed = {"candidates": [
        {"kind": "repair_or_service", "raw_quote": "This customer requires an FA RPT on all BDs and P/Ss (sent electronically)"},
        {"kind": "shop_term_or_abbreviation", "raw_quote": "Blue Schmoo"},
        {"kind": "repair_or_service", "raw_quote": "2x Blue Schmoo's for A1+A2"},
        {"kind": "testing_or_process", "raw_quote": "Tested Robot w/ Sugar Cube test (Homing in Script Log?) over Next Weekend."},
    ]}
    accepted, rejected = mod.validate_prospector_output(parsed, record, view, raw, mod.sha256_text(raw))
    check(len(accepted) == 3, "customer requirement, Blue Schmoo evidence, and Sugar Cube evidence should survive")
    check(len(rejected) == 1 and rejected[0]["reason"] == "raw_quote_not_supported_by_prospecting_view", "rewritten Blue Schmoo phrase must be rejected")
    req = next(c for c in accepted if "customer requires" in c["raw_source_text"])
    check(req["kind"] == "customer_requirement" and req["model_kind"] == "repair_or_service", "deterministic kind override must be recorded without changing source text")
    sugar = next(c for c in accepted if "Sugar" in c["raw_source_text"])
    check(sugar["support_mode"] == "whitespace_terminal_punctuation", "Sugar Cube layout/punctuation support mode should be explicit")
    check(sugar["raw_source_text"].endswith("Next Weekend"), "invented terminal punctuation must not enter evidence ledger")


def test_manifest_validation():
    good = {
        "collector_version": "1.3.5.1",
        "inventory_only": False,
        "interrupted": False,
        "records": [{"log_number": "1", "vision_status": "ok", "raw_transcription_path": "/tmp/x"}],
    }
    check(len(mod.validate_collector_manifest(good)) == 1, "complete collector manifest should validate")
    bad = dict(good)
    bad["interrupted"] = True
    try:
        mod.validate_collector_manifest(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("interrupted collector manifest must fail by default")


def test_recurrence_and_kind_compatibility():
    r1 = _rec("1", "r1", "sha1")
    r2 = _rec("2", "r2", "sha2")
    raw1 = "robot Fas are put inside packaging with unit..\nRebuilt A1 motor"
    raw2 = "robot Fas are put inside packaging with unit..\nRebuilt A2 motor"
    v1, _ = mod.sanitize_for_prospecting(raw1)
    v2, _ = mod.sanitize_for_prospecting(raw2)
    p1 = {"candidates": [
        {"kind": "repair_or_service", "raw_quote": "robot Fas are put inside packaging with unit.."},
        {"kind": "repair_or_service", "raw_quote": "Rebuilt A1 motor"},
    ]}
    p2 = {"candidates": [
        {"kind": "repair_or_service", "raw_quote": "robot Fas are put inside packaging with unit.."},
        {"kind": "repair_or_service", "raw_quote": "Rebuilt A2 motor"},
    ]}
    c1, _ = mod.validate_prospector_output(p1, r1, v1, raw1, mod.sha256_text(raw1))
    c2, _ = mod.validate_prospector_output(p2, r2, v2, raw2, mod.sha256_text(raw2))
    allc = c1 + c2
    req_ids = [c["candidate_id"] for c in allc if c["kind"] == "customer_requirement"]
    repair_ids = [c["candidate_id"] for c in allc if c["kind"] == "repair_or_service"]

    wrong_type = {
        "recurring_groups": [{"group_type": "repair_or_service", "label": "packaging", "member_candidate_ids": req_ids}],
        "unique_high_value_candidate_ids": [],
        "human_review_candidate_ids": [],
    }
    accepted, rejected, _, _ = mod.validate_reasoning_output(wrong_type, allc)
    check(not accepted and rejected[0]["reason"] == "group_type_candidate_kind_mismatch", "Python must reject customer requirements grouped as repair work")

    good_group = {
        "recurring_groups": [{"group_type": "repair_or_service", "label": "motor rebuild", "member_candidate_ids": repair_ids}],
        "unique_high_value_candidate_ids": [],
        "human_review_candidate_ids": [],
    }
    accepted2, rejected2, _, _ = mod.validate_reasoning_output(good_group, allc)
    check(len(accepted2) == 1 and not rejected2 and accepted2[0]["distinct_log_count"] == 2, "compatible two-log repair group should pass deterministic recurrence")

    one_log = {
        "recurring_groups": [{"group_type": "repair_or_service", "label": "one", "member_candidate_ids": [repair_ids[0]]}],
        "unique_high_value_candidate_ids": [],
        "human_review_candidate_ids": [],
    }
    accepted3, rejected3, _, _ = mod.validate_reasoning_output(one_log, allc)
    check(not accepted3 and rejected3[0]["reason"].startswith("recurrence_rule_failed"), "one-log group must fail regardless of model label")


def test_high_value_and_ocr_recheck():
    candidates = [
        {"candidate_id": "blue", "log_number": "1", "source_sha256": "s1", "kind": "repair_or_service", "model_kind": "repair_or_service", "raw_source_text": "2x Blue Schmoo's for A1+A2", "source_path": "/1.jpg", "raw_transcription_path": "/1.txt"},
        {"candidate_id": "turkey", "log_number": "2", "source_sha256": "s2", "kind": "diagnostic_or_failure", "model_kind": "diagnostic_or_failure", "raw_source_text": "Turkey fat is cause", "source_path": "/2.jpg", "raw_transcription_path": "/2.txt"},
        {"candidate_id": "sugar", "log_number": "3", "source_sha256": "s3", "kind": "testing_or_process", "model_kind": "testing_or_process", "raw_source_text": "Sugar Cube test (Homing in Script Log?)", "source_path": "/3.jpg", "raw_transcription_path": "/3.txt"},
        {"candidate_id": "part", "log_number": "4", "source_sha256": "s4", "kind": "part_number_or_identifier", "model_kind": "part_number_or_identifier", "raw_source_text": "B R6ZZ", "source_path": "/4.jpg", "raw_transcription_path": "/4.txt"},
        {"candidate_id": "bots", "log_number": "5", "source_sha256": "s5", "kind": "component_or_part", "model_kind": "component_or_part", "raw_source_text": "Flanged Bots X.2", "source_path": "/5.jpg", "raw_transcription_path": "/5.txt"},
        {"candidate_id": "unc", "log_number": "6", "source_sha256": "s6", "kind": "repair_or_service", "model_kind": "repair_or_service", "raw_source_text": "Replaced Z2 because Comm. was [unclear]", "source_path": "/6.jpg", "raw_transcription_path": "/6.txt"},
        {"candidate_id": "ut", "log_number": "7", "source_sha256": "s7", "kind": "shop_term_or_abbreviation", "model_kind": "shop_term_or_abbreviation", "raw_source_text": "UT", "source_path": "/7.jpg", "raw_transcription_path": "/7.txt"},
    ]
    high = mod.deterministic_high_value_ids(candidates, [], [])
    check({"blue", "turkey", "sugar", "part", "bots"}.issubset(set(high)), "high-value backup rules should preserve difficult pilot terms")
    check("ut" not in high, "trivial technician initials should not flood high-value summary")

    rejected = [{"log_number": "8", "reason": "raw_quote_not_supported_by_prospecting_view", "model_item": {"kind": "component_or_part", "raw_quote": "X ADDED Flanged Bots X.2 to A1+A2 upper link"}}]
    queue = mod.build_ocr_recheck_queue(candidates, rejected)
    texts = {q["raw_source_text"] for q in queue}
    check("B R6ZZ" in texts, "part identifier should enter OCR recheck queue")
    check("Flanged Bots X.2" in texts, "mixed X.2 component string should enter OCR recheck queue")
    check("Replaced Z2 because Comm. was [unclear]" in texts, "unclear repair evidence should enter OCR recheck queue")
    check("X ADDED Flanged Bots X.2 to A1+A2 upper link" in texts, "unsupported technical prospector quote should be retained for recheck rather than discarded")


def main():
    test_mask_hours()
    test_sanitation_preserves_event_text()
    test_support_modes()
    test_kind_override()
    test_prospector_validation()
    test_manifest_validation()
    test_recurrence_and_kind_compatibility()
    test_high_value_and_ocr_recheck()
    print("PASS: Nova DRL Traveler Corpus Prospector + Sorter v1.3.6.1 tests")


if __name__ == "__main__":
    main()
