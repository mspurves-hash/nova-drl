#!/usr/bin/env python3
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_repair_evidence_collector_v1_4_3_1.py"
BASE = ROOT / "ingest" / "nova_repair_evidence_collector_v1_4_2.py"

assert BASE.exists(), "v1.4.2 base collector is required"

spec = importlib.util.spec_from_file_location("collector", str(TARGET))
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

assert collector.VERSION == "1.4.3.1"

checklist = """
RBT-GB8-MT (GENMARK)
Checklist for internal use at DRL
Serial Number: 80010732
RMA Number: 28852
Log Number: 130813004
Customer FA (summary): Y Axis needs to be fixed
Initial checkout
"""
test_report = """
Acceptance test report
REPORT GENMARK ROBOT TEST
Document DRL148710
Production Information
Serial number: 80010732
RMA #: 28852
Traveler #: 130813004
Customer Problem/Symptom Description: Y Axis needs to be fixed
"""

assert collector.classify_scanned_page(checklist)["document_family"] == "DRL_INTERNAL_CHECKLIST"
assert collector.classify_scanned_page(test_report)["document_family"] == "DRL_ACCEPTANCE_TEST_REPORT"

fields = collector.extract_event_field_candidates(
    test_report,
    collector.classify_scanned_page(test_report),
)
values = {x["field"]: x["raw_value"] for x in fields}
assert values["serial_number"] == "80010732"
assert values["rma_number"] == "28852"
assert values["log_number"] == "130813004"
assert "Y Axis needs to be fixed" in values["customer_complaint"]

good = collector.semantic_ocr_metrics(checklist)
bad = collector.semantic_ocr_metrics(
    "CEST PSAP Le eeeeCoegnvaDe Ese oCeDDe Si SuScaRstbususEtdccoceuee"
)
assert good["semantic_score"] > bad["semantic_score"]
assert good["quality"] in ("good", "usable")
assert bad["quality"] == "low"

print("PASS: Nova Repair Evidence Collector v1.4.3.1 tests")
