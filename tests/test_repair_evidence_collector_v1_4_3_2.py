#!/usr/bin/env python3
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_repair_evidence_collector_v1_4_3_2.py"
BASE_1431 = ROOT / "ingest" / "nova_repair_evidence_collector_v1_4_3_1.py"
BASE_142 = ROOT / "ingest" / "nova_repair_evidence_collector_v1_4_2.py"

assert BASE_1431.exists(), "v1.4.3.1 prior collector is required"
assert BASE_142.exists(), "v1.4.2 base collector is required"

spec = importlib.util.spec_from_file_location("collector", str(TARGET))
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

assert collector.VERSION == "1.4.3.2"

# Document-level family inheritance must not ask every continuation page to
# rediscover its document family from OCR text.
family = collector.infer_document_family(
    Path("130813004 Robot Checklist.PDF"),
    "robot_checklist",
    "garbled OCR",
)
assert family["document_family"] == "DRL_INTERNAL_CHECKLIST"
assert family["source"] == "document_role"
page = collector.classify_inherited_page(
    "random continuation page text",
    7,
    family["document_family"],
)
assert page["document_family"] == "DRL_INTERNAL_CHECKLIST"
assert page["page_type"] == "checklist_continuation"

family = collector.infer_document_family(
    Path("130813004 Robot Test Report.PDF"),
    "robot_test_report",
    "garbled OCR",
)
assert family["document_family"] == "DRL_ACCEPTANCE_TEST_REPORT"
page = collector.classify_inherited_page(
    "Completion signatures",
    3,
    family["document_family"],
)
assert page["document_family"] == "DRL_ACCEPTANCE_TEST_REPORT"
assert page["page_type"] == "test_report_completion"

expected = {
    "serial_number": "80010732",
    "log_number": "130813004",
    "source": "test",
}

# Labels must never be returned as field values.
label = collector.validate_field_candidate(
    "serial_number", "Number", expected, "test"
)
assert label["rejected"] is True
assert label["rejection_reason"] == "field_label_not_value"

# Folder/filename anchors validate but do not become document facts.
serial = collector.validate_field_candidate(
    "serial_number", "80010732", expected, "test"
)
assert serial["anchor_status"] == "exact_match"
assert serial["eligible_for_evidence_comparison"] is True
assert serial["accepted_as_repair_fact"] is False

wrong_serial = collector.validate_field_candidate(
    "serial_number", "80010733", expected, "test"
)
assert wrong_serial["anchor_status"] == "mismatch"
assert wrong_serial["eligible_for_evidence_comparison"] is False

log = collector.validate_field_candidate(
    "log_number", "130813004", expected, "test"
)
assert log["anchor_status"] == "exact_match"

# Known form profiles are explicit and limited to verified page-one regions.
assert collector.KNOWN_FORM_HEADER_PROFILES["DRL_INTERNAL_CHECKLIST"]["page_number"] == 1
assert collector.KNOWN_FORM_HEADER_PROFILES["DRL_ACCEPTANCE_TEST_REPORT"]["page_number"] == 1

# Vision responses may be wrapped in Markdown but must parse as JSON.
parsed = collector.parse_json_object(
    '```json\n{"serial_number":"80010732","customer_complaint":"Y Axis needs to be fixed"}\n```'
)
assert parsed["serial_number"] == "80010732"

# Cross-document comparison preserves raw candidates and never creates a
# canonical fact automatically.
comparison = collector.compare_complaint_records([
    {
        "raw_value": "Y Axis needs to be fixed",
        "source_document": "Robot Checklist.PDF",
        "page_number": 1,
    },
    {
        "raw_value": "Y Ayis weeds 2 be xe",
        "source_document": "Robot Test Report.PDF",
        "page_number": 1,
    },
])
assert comparison["source_count"] == 2
assert comparison["status"] in {
    "possible_cross_document_agreement",
    "strong_cross_document_agreement",
}
assert comparison["canonical_complaint"] is None
assert comparison["accepted_as_repair_fact"] is False

# Template OCR and event-annotation quality are independent gates.
candidates = [
    collector.validate_field_candidate(
        "customer_complaint",
        "Y Axis needs to be fixed",
        expected,
        "header_vision_minicpm-v:latest",
    ),
    serial,
    log,
]
assert collector.annotation_quality(candidates, "ok") == "good"

print("PASS: Nova Repair Evidence Collector v1.4.3.2 tests")
