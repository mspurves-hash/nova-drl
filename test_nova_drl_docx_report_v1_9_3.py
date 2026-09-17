#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import tempfile

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_drl_docx_report_v1_9_3.py"

spec = importlib.util.spec_from_file_location("m", TARGET)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def main():
    # A valid exact family with zero known replacement Parts is still a valid report.
    text = """
======================================================================================
NOVA DRL SEARCH  |  9800106841  |  100.4 ms
======================================================================================
Coverage: test

EQUIPMENT / PRODUCT
-------------------
1. CNTL - 9800106841 GENMARK
   Base part number: 9800106841 | Indexed repair events: 2 | Indexed parts: 0

REPAIR HISTORY
--------------
1. DRL log 210401004 | CNTL - 9800106841 GENMARK
"""
    report = m.parse_report_text(text, "9800106841")
    assert report["meta"]["equipment_family"] == "CNTL - 9800106841 GENMARK"
    assert report["parts"] == []

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "zero_parts.docx"
        m.build_docx(report, out)
        assert out.exists() and out.stat().st_size > 0

    # Unresolved/broad output remains invalid for DOCX instead of being mis-bound.
    bad = """
NOVA DRL SEARCH  |  CNTL - Genmark  |  10 ms
Coverage: test
TRACKING / PROJECT
------------------
1. some tracking result
"""
    try:
        m.parse_report_text(bad, "CNTL - Genmark")
    except ValueError:
        pass
    else:
        raise AssertionError("Broad unresolved query must not parse as a DOCX product report")

    print("PASS: DOCX v1.9.3 zero-parts + unresolved-search safety tests")

if __name__ == "__main__":
    main()
