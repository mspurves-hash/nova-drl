#!/usr/bin/env python3
import importlib.util
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_drl_technician_console_v1_9_2.py"

spec = importlib.util.spec_from_file_location("m", TARGET)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def main():
    assert m.default_report_path("MR-J2S-40A") == Path(
        "/mnt/drl-reports/NOVA_MR-J2S-40A_Repair_Reference.docx"
    )

    sample = """NOVA DRL SEARCH
Actions: :pdf create/open printable PDF   :print send current report to printer
"""
    with patch.object(m, "run_capture", return_value=(0, sample, "")):
        with patch("builtins.print") as p:
            rc = m.search("MR-J2S-40A")
    assert rc == 0
    rendered = "\n".join(" ".join(str(x) for x in call.args) for call in p.call_args_list)
    assert ":docx create/editable Word report" in rendered
    assert ":review review saved edits" in rendered
    assert ":pdf create/open printable PDF" not in rendered

    with patch.object(m.subprocess, "call", return_value=0) as c:
        with patch.object(Path, "exists", return_value=True):
            rc = m.review_docx("MR-J2S-40A")
    assert rc == 0
    args = c.call_args.args[0]
    assert str(args[0]).endswith("nova-drl-docx-review")
    assert str(args[1]).endswith("NOVA_MR-J2S-40A_Repair_Reference.docx")

    print("PASS: NOVA DRL Technician Console v1.9.2 tests")


if __name__ == "__main__":
    main()
