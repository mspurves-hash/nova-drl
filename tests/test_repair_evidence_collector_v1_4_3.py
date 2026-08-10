#!/usr/bin/env python3
import importlib.util
from pathlib import Path
p=Path(__file__).parents[1]/"ingest"/"nova_repair_evidence_collector_v1_4_3.py"
spec=importlib.util.spec_from_file_location("x",p)
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
assert m.VERSION=="1.4.3"
assert m.classify_page("Acceptance test report")=="DRL_ACCEPTANCE_TEST_REPORT"
assert m.classify_page("Checklist for internal use at DRL")=="DRL_INTERNAL_CHECKLIST"
assert m.extract_event_fields("Serial Number: 80010732 RMA Number: 28852 Log Number: 130813004")["serial"]=="80010732"
print("PASS: Nova Repair Evidence Collector v1.4.3 tests")
