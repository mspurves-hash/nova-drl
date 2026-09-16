#!/usr/bin/env python3
import importlib.util
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / 'nova_drl_docx_report_v1_9_2.py'

spec = importlib.util.spec_from_file_location('m', TARGET)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def sample_report():
    return {
        'meta': {'base_part_number': 'BM23995', 'equipment_family': 'BRD - BM23995 EXEC CAR ASYST'},
        'parts': [{'label':'LM324N','count':92}],
        'failures': [{'label':'REFURBISHMENT','count':3}],
        'actions': [{'label':'REPLACE CONNECTOR','count':3}],
    }


def main():
    report = sample_report()
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        old_dir = m.DEFAULT_REPORT_DIR
        m.DEFAULT_REPORT_DIR = str(td)
        try:
            canonical = td / 'NOVA_BM23995_Repair_Reference.docx'
            out, protected = m.protected_output_path(report)
            assert out == canonical
            assert protected is None

            canonical.write_bytes(b'edited technician docx')
            fixed = datetime(2026, 9, 16, 12, 45, 0, tzinfo=timezone.utc)
            out2, protected2 = m.protected_output_path(report, now=fixed)
            assert protected2 == canonical
            assert out2 == td / 'NOVA_BM23995_Repair_Reference_NEW_2026-09-16_124500.docx'
            assert not out2.exists()

            out2.write_bytes(b'new')
            out3, _ = m.protected_output_path(report, now=fixed)
            assert out3 == td / 'NOVA_BM23995_Repair_Reference_NEW_2026-09-16_124500_2.docx'

            custom = td / 'custom.docx'
            out4, protected4 = m.protected_output_path(report, str(custom))
            assert out4 == custom and protected4 is None

            custom.write_bytes(b'existing')
            try:
                m.protected_output_path(report, str(custom))
                raise AssertionError('existing explicit --out should refuse overwrite')
            except FileExistsError:
                pass

            out5, protected5 = m.protected_output_path(report, str(custom), force=True)
            assert out5 == custom and protected5 == custom

            out6, protected6 = m.protected_output_path(report, force=True)
            assert out6 == canonical and protected6 == canonical
        finally:
            m.DEFAULT_REPORT_DIR = old_dir

    print('PASS: NOVA DOCX generator v1.9.2 overwrite-protection tests')


if __name__ == '__main__':
    main()
