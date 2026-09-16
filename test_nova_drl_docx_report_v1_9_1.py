#!/usr/bin/env python3
import importlib.util
from pathlib import Path

HERE=Path(__file__).resolve().parent
TARGET=HERE / "nova_drl_docx_report_v1_9_1.py"
spec=importlib.util.spec_from_file_location("gen", TARGET)
gen=importlib.util.module_from_spec(spec); spec.loader.exec_module(gen)

report={"meta":{"base_part_number":"MR-J2S-40A"}}
out=gen.default_out(report)
assert str(out)=="/mnt/drl-reports/NOVA_MR-J2S-40A_Repair_Reference.docx", out
assert gen.BASELINE_PREFIX=="NOVA_BASELINE_V1_9_0_B64:"
assert gen.VERSION=="1.9.1"
print("PASS: NOVA DOCX generator v1.9.1 defaults to /mnt/drl-reports and preserves baseline compatibility")
