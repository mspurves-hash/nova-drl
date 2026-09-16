#!/usr/bin/env python3
import importlib.util
import json
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

HERE = Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def remove_table_captions(root, rev):
    ns = {"w": rev.W}
    for tbl in root.iter(rev.qn("tbl")):
        tblpr = tbl.find("./w:tblPr", ns)
        if tblpr is None:
            continue
        cap = tblpr.find("./w:tblCaption", ns)
        if cap is not None:
            tblpr.remove(cap)


def find_table_by_kind(root, rev, kind):
    for tbl in root.iter(rev.qn("tbl")):
        rows = rev.table_rows(tbl)
        if rev.classify_table(tbl, rows) == kind:
            return tbl
    return None


def main():
    gen = load("gen", HERE / "nova_drl_docx_report_v1_9_0.py")
    rev = load("rev", HERE / "nova_drl_docx_review_v1_9_1.py")

    report = {
        "meta": {
            "equipment_family": "SVO DRV - MR-J2S-40A MITSUBISHI",
            "base_part_number": "MR-J2S-40A",
            "indexed_repair_events": 226,
            "indexed_parts": 10,
            "model_variants": 5,
        },
        "parts": [
            {"label": "7800", "count": 88},
            {"label": "47uF", "count": 33},
            {"label": "33uF", "count": 29},
        ],
        "failures": [
            {"label": "LOW VOLTAGE", "count": 11},
            {"label": "E9 alarm", "count": 8},
        ],
        "actions": [
            {"label": "REPLACE CHIP", "count": 6},
            {"label": "REPLACE CAPACITOR", "count": 4},
        ],
    }

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        original = td / "report.docx"
        gen.build_docx(report, original)

        # Baseline sanity.
        root = rev.load_document_xml(original)
        baseline = rev.baseline_from_doc(root)
        current = rev.extract_current(root)
        assert rev.compute_diff(baseline, current) == []

        # Simulate Word re-save by dropping every w:tblCaption.
        with zipfile.ZipFile(original, "r") as zin:
            members = {n: zin.read(n) for n in zin.namelist()}
        root2 = ET.fromstring(members["word/document.xml"])
        remove_table_captions(root2, rev)
        members["word/document.xml"] = ET.tostring(root2, encoding="utf-8", xml_declaration=True)

        word_saved = td / "word_saved.docx"
        with zipfile.ZipFile(word_saved, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for n, data in members.items():
                zout.writestr(n, data)

        root3 = rev.load_document_xml(word_saved)
        b3 = rev.baseline_from_doc(root3)
        c3 = rev.extract_current(root3)
        assert rev.compute_diff(b3, c3) == [], (b3, c3, rev.compute_diff(b3, c3))

        # Now change a real knowledge cell after captions are gone.
        parts_tbl = find_table_by_kind(root3, rev, "NOVA_PARTS")
        assert parts_tbl is not None
        changed = False
        for t in parts_tbl.iter(rev.qn("t")):
            if (t.text or "").strip() == "7800":
                t.text = "HCPL-7800"
                changed = True
                break
        assert changed

        # Change Technician Notes too; this must remain ignored.
        notes_tbl = find_table_by_kind(root3, rev, "NOVA_NOTES")
        assert notes_tbl is not None
        note_cells = list(notes_tbl.iter(rev.qn("t")))
        # Put text into the first blank/short editable note run we can find.
        for t in note_cells:
            if not (t.text or "").strip():
                t.text = "Technician edit test"
                break

        members["word/document.xml"] = ET.tostring(root3, encoding="utf-8", xml_declaration=True)
        edited = td / "edited.docx"
        with zipfile.ZipFile(edited, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for n, data in members.items():
                zout.writestr(n, data)

        r4 = rev.load_document_xml(edited)
        b4 = rev.baseline_from_doc(r4)
        c4 = rev.extract_current(r4)
        diffs = rev.compute_diff(b4, c4)

        assert len(diffs) == 2, diffs
        assert any(d["section"] == "PARTS" and d["kind"] == "removed" and d["label"] == "7800" for d in diffs)
        assert any(d["section"] == "PARTS" and d["kind"] == "added" and d["label"] == "HCPL-7800" for d in diffs)
        assert not any(d["section"] == "META" for d in diffs)
        assert not any(d["section"] == "FAILURES" for d in diffs)
        assert not any(d["section"] == "ACTIONS" for d in diffs)

    print("PASS: NOVA DOCX Review v1.9.1 survives Word-style table-caption removal")


if __name__ == "__main__":
    main()
