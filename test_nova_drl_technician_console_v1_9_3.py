#!/usr/bin/env python3
import importlib.util
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
TARGET=HERE/'nova_drl_technician_console_v1_9_3.py'
spec=importlib.util.spec_from_file_location('m',TARGET); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

def main():
    out='''# NOVA DRL Editable DOCX Report | v1.9.2\nExisting technician DOCX protected: /mnt/drl-reports/NOVA_BM23995_Repair_Reference.docx\nNew DOCX created instead: /mnt/drl-reports/NOVA_BM23995_Repair_Reference_NEW_2026-09-16_124500.docx\nDOCX: /mnt/drl-reports/NOVA_BM23995_Repair_Reference_NEW_2026-09-16_124500.docx\n'''
    p=m.docx_path_from_output(out)
    assert p == Path('/mnt/drl-reports/NOVA_BM23995_Repair_Reference_NEW_2026-09-16_124500.docx')

    with tempfile.TemporaryDirectory() as td:
        td=Path(td)
        old=m.REPORT_DIR; m.REPORT_DIR=td
        try:
            a=td/'NOVA_BM23995_Repair_Reference.docx'; a.write_text('a')
            time.sleep(0.02)
            b=td/'NOVA_BM23995_Repair_Reference_NEW_2026-09-16_124500.docx'; b.write_text('b')
            assert m.latest_report_path('BM23995') == b
            with patch.object(m.subprocess,'call',return_value=0) as c:
                rc=m.review_docx('BM23995', b)
            assert rc==0
            assert Path(c.call_args.args[0][1]) == b
        finally:
            m.REPORT_DIR=old

    with patch.object(m,'run_capture',return_value=(0,out,'')):
        rc, created=m.create_docx('BM23995')
    assert rc==0 and created==p
    print('PASS: NOVA DRL Technician Console v1.9.3 DOCX-path tracking tests')

if __name__=='__main__': main()
