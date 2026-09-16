#!/usr/bin/env python3
"""
NOVA DRL Editable DOCX Report v1.9.0

Primary technician report generator layered on frozen nova-drl v1.5.16 output.

Design goals:
- DOCX is the editable bench report.
- The frozen nova-drl command remains the sole knowledge source.
- Parts / failures / actions are rendered from the exact nova-drl terminal report.
- A hidden baseline is embedded inside the DOCX for later edit detection.
- Technician notes are intentionally editable and excluded from knowledge review.
- Standard-library only: no python-docx dependency.

Examples:
  python3 nova_drl_docx_report_v1_9_0.py --search "MR-J2S-40A"
  python3 nova_drl_docx_report_v1_9_0.py --search "MR-J2S-40A" --out /tmp/MR-J2S-40A.docx
  python3 nova_drl_docx_report_v1_9_0.py --from-text report.txt --search "MR-J2S-40A" --out sample.docx
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

VERSION = "1.9.0"
DEFAULT_NOVA = "/opt/nova-drl/bin/nova-drl"
DEFAULT_REPORT_DIR = "/opt/nova-drl/reports"
BASELINE_PREFIX = "NOVA_BASELINE_V1_9_0_B64:"

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
CP = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC = "http://purl.org/dc/elements/1.1/"
DCT = "http://purl.org/dc/terms/"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
EP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
XML = "http://www.w3.org/XML/1998/namespace"

for prefix, uri in [("w", W), ("r", R), ("cp", CP), ("dc", DC), ("dcterms", DCT),
                    ("xsi", XSI), ("ep", EP), ("vt", VT)]:
    ET.register_namespace(prefix, uri)


def qn(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def clean_ws(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def safe_filename(value: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return s.strip("._") or "NOVA_REPORT"


def split_report_blocks(text: str) -> List[str]:
    # Search reports always start with a long line followed by NOVA DRL SEARCH.
    starts = [m.start() for m in re.finditer(r"(?m)^={20,}\s*\nNOVA DRL SEARCH\s+\|", text)]
    if not starts:
        return [text]
    out = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        out.append(text[start:end].strip())
    return out


def section_text(block: str, heading: str, next_headings: Sequence[str]) -> str:
    pattern = rf"(?ms)^{re.escape(heading)}\s*\n-+\s*\n(.*?)(?=^(?:{'|'.join(re.escape(x) for x in next_headings)})\s*\n-+\s*\n|^Actions:|\Z)"
    m = re.search(pattern, block)
    return m.group(1).strip() if m else ""


def parse_ranked_rows(section: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for raw in section.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        upper = line.upper().strip()
        if upper.startswith("REFERENCE PN / COMPONENT") or upper.startswith("REPORTED FAILURE") or upper.startswith("RECURRING REPAIR ACTION"):
            continue
        if re.fullmatch(r"[-\s]+", line):
            continue
        m = re.match(r"^(.*?)\s{2,}(\d+)\s*$", line)
        if not m:
            # Some long failure descriptions can collapse spacing; fall back to last integer.
            m = re.match(r"^(.*\S)\s+(\d+)\s*$", line)
        if not m:
            continue
        label = clean_ws(m.group(1))
        if not label:
            continue
        rows.append({"label": label, "count": int(m.group(2))})
    return rows


def parse_report_text(text: str, expected_search: Optional[str] = None) -> Dict[str, Any]:
    blocks = split_report_blocks(text)
    chosen = None
    expected_norm = re.sub(r"[^A-Z0-9]+", "", str(expected_search or "").upper())
    for block in blocks:
        m = re.search(r"(?m)^NOVA DRL SEARCH\s+\|\s*(.*?)\s*\|", block)
        query = clean_ws(m.group(1)) if m else ""
        qnrm = re.sub(r"[^A-Z0-9]+", "", query.upper())
        if not expected_norm or qnrm == expected_norm:
            chosen = block
            break
    if chosen is None:
        raise ValueError(f"No NOVA report block matched search {expected_search!r}")

    m = re.search(r"(?m)^NOVA DRL SEARCH\s+\|\s*(.*?)\s*\|", chosen)
    search = clean_ws(m.group(1)) if m else clean_ws(expected_search)

    equipment = section_text(chosen, "EQUIPMENT / PRODUCT", ["PARTS REPLACED", "REPORTED FAILURE", "RECURRING REPAIR ACTIONS"])
    lines = [x.rstrip() for x in equipment.splitlines() if x.strip()]
    family = ""
    base = search
    indexed_events = None
    indexed_parts = None
    variants = None
    if lines:
        family = re.sub(r"^\s*\d+\.\s*", "", lines[0]).strip()
    for line in lines[1:]:
        b = re.search(r"Base part number:\s*(.*?)\s*(?:\||$)", line, re.I)
        if b:
            base = clean_ws(b.group(1))
        e = re.search(r"Indexed repair events:\s*(\d+)", line, re.I)
        if e:
            indexed_events = int(e.group(1))
        p = re.search(r"Indexed parts:\s*(\d+)", line, re.I)
        if p:
            indexed_parts = int(p.group(1))
        v = re.search(r"Observed suffix/model variants preserved as metadata:\s*(\d+)", line, re.I)
        if v:
            variants = int(v.group(1))

    coverage = ""
    cm = re.search(r"(?m)^Coverage:\s*(.*?)\s*$", chosen)
    if cm:
        coverage = clean_ws(cm.group(1))

    parts_s = section_text(chosen, "PARTS REPLACED", ["REPORTED FAILURE", "RECURRING REPAIR ACTIONS"])
    fail_s = section_text(chosen, "REPORTED FAILURE", ["RECURRING REPAIR ACTIONS"])
    actions_s = section_text(chosen, "RECURRING REPAIR ACTIONS", [])

    report = {
        "schema": "nova_drl_editable_report",
        "version": VERSION,
        "search": search,
        "meta": {
            "equipment_family": family,
            "base_part_number": base,
            "indexed_repair_events": indexed_events,
            "indexed_parts": indexed_parts,
            "model_variants": variants,
            "coverage": coverage,
            "knowledge_source": "Frozen NOVA DRL v1.5.16 technician summary",
        },
        "parts": parse_ranked_rows(parts_s),
        "failures": parse_ranked_rows(fail_s),
        "actions": parse_ranked_rows(actions_s),
    }
    if not family:
        raise ValueError("Could not parse equipment family from nova-drl output")
    if not report["parts"]:
        raise ValueError("Could not parse Parts Replaced rows from nova-drl output")
    return report


def run_nova(search: str, command: str = DEFAULT_NOVA) -> str:
    cmd = [command, "--search", search]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"nova-drl failed with exit code {proc.returncode}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc.stdout


def el(parent: ET.Element, ns: str, tag: str, attrs: Optional[Dict[str, str]] = None) -> ET.Element:
    e = ET.SubElement(parent, qn(ns, tag))
    if attrs:
        for k, v in attrs.items():
            if ":" in k:
                pfx, name = k.split(":", 1)
                uri = {"w": W, "r": R, "xml": XML}.get(pfx)
                e.set(qn(uri, name), str(v))
            else:
                e.set(k, str(v))
    return e


def set_run_format(rpr: ET.Element, bold=False, italic=False, size=20, color=None, hidden=False):
    rfonts = el(rpr, W, "rFonts")
    for a in ("ascii", "hAnsi", "eastAsia"):
        rfonts.set(qn(W, a), "Aptos")
    if bold:
        el(rpr, W, "b")
    if italic:
        el(rpr, W, "i")
    if color:
        el(rpr, W, "color", {"w:val": color})
    el(rpr, W, "sz", {"w:val": str(size)})
    el(rpr, W, "szCs", {"w:val": str(size)})
    if hidden:
        el(rpr, W, "vanish")


def add_paragraph(parent: ET.Element, text: str = "", *, bold=False, italic=False,
                  size=20, color=None, align=None, space_before=0, space_after=80,
                  hidden=False, keep_next=False) -> ET.Element:
    p = el(parent, W, "p")
    ppr = el(p, W, "pPr")
    spacing = el(ppr, W, "spacing")
    spacing.set(qn(W, "before"), str(space_before))
    spacing.set(qn(W, "after"), str(space_after))
    if align:
        el(ppr, W, "jc", {"w:val": align})
    if keep_next:
        el(ppr, W, "keepNext")
    if text:
        r = el(p, W, "r")
        rpr = el(r, W, "rPr")
        set_run_format(rpr, bold=bold, italic=italic, size=size, color=color, hidden=hidden)
        t = el(r, W, "t")
        if text[:1].isspace() or text[-1:].isspace():
            t.set(qn(XML, "space"), "preserve")
        t.text = text
    return p


def add_cell_text(tc: ET.Element, text: str, *, bold=False, size=18, color=None, align=None):
    add_paragraph(tc, str(text), bold=bold, size=size, color=color, align=align, space_after=0)


def add_table(parent: ET.Element, rows: Sequence[Sequence[Any]], widths: Sequence[int], *,
              caption: str, header=True) -> ET.Element:
    tbl = el(parent, W, "tbl")
    tblpr = el(tbl, W, "tblPr")
    el(tblpr, W, "tblW", {"w:w": str(sum(widths)), "w:type": "dxa"})
    el(tblpr, W, "tblLayout", {"w:type": "fixed"})
    el(tblpr, W, "tblCaption", {"w:val": caption})
    borders = el(tblpr, W, "tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el(borders, W, edge, {"w:val": "single", "w:sz": "4", "w:space": "0", "w:color": "B7B7B7"})
    grid = el(tbl, W, "tblGrid")
    for w in widths:
        el(grid, W, "gridCol", {"w:w": str(w)})

    for ri, row in enumerate(rows):
        tr = el(tbl, W, "tr")
        trpr = el(tr, W, "trPr")
        if ri == 0 and header:
            el(trpr, W, "tblHeader")
        for ci, val in enumerate(row):
            tc = el(tr, W, "tc")
            tcpr = el(tc, W, "tcPr")
            el(tcpr, W, "tcW", {"w:w": str(widths[ci]), "w:type": "dxa"})
            mar = el(tcpr, W, "tcMar")
            for side in ("top", "bottom", "left", "right"):
                el(mar, W, side, {"w:w": "80", "w:type": "dxa"})
            if ri == 0 and header:
                el(tcpr, W, "shd", {"w:fill": "E7E6E6"})
                add_cell_text(tc, str(val), bold=True, size=18, color="222222", align="center" if ci else None)
            else:
                add_cell_text(tc, str(val), size=18, align="right" if ci == len(row) - 1 else None)
    return tbl


def add_heading(parent: ET.Element, text: str):
    add_paragraph(parent, text.upper(), bold=True, size=24, color="1F1F1F",
                  space_before=180, space_after=70, keep_next=True)


def styles_xml() -> bytes:
    root = ET.Element(qn(W, "styles"))
    doc_defaults = el(root, W, "docDefaults")
    rpr_def = el(el(doc_defaults, W, "rPrDefault"), W, "rPr")
    set_run_format(rpr_def, size=20)
    ppr_def = el(el(doc_defaults, W, "pPrDefault"), W, "pPr")
    el(ppr_def, W, "spacing", {"w:after": "80", "w:line": "240", "w:lineRule": "auto"})
    style = el(root, W, "style", {"w:type": "paragraph", "w:default": "1", "w:styleId": "Normal"})
    el(style, W, "name", {"w:val": "Normal"})
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def settings_xml() -> bytes:
    root = ET.Element(qn(W, "settings"))
    el(root, W, "hideSpellingErrors")
    el(root, W, "hideGrammaticalErrors")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def document_xml(report: Dict[str, Any], generated_at: str) -> bytes:
    doc = ET.Element(qn(W, "document"))
    body = el(doc, W, "body")

    add_paragraph(body, "DIRECT REPAIR LABORATORIES", bold=True, size=20, color="404040", align="center", space_after=30)
    add_paragraph(body, "NOVA Repair Reference", bold=True, size=34, color="111111", align="center", space_after=30)
    add_paragraph(body, report["meta"]["base_part_number"], bold=True, size=28, color="404040", align="center", space_after=130)

    meta_rows = [
        ["Equipment Family", report["meta"]["equipment_family"]],
        ["Base DRL Part #", report["meta"]["base_part_number"]],
        ["Indexed Repair Events", report["meta"].get("indexed_repair_events") or ""],
        ["Indexed Parts", report["meta"].get("indexed_parts") or ""],
        ["Model Variants Preserved", report["meta"].get("model_variants") if report["meta"].get("model_variants") is not None else ""],
        ["Generated", generated_at],
    ]
    add_table(body, meta_rows, [2300, 7000], caption="NOVA_META", header=False)

    add_paragraph(body,
                  "Parts are ranked from most to least historically replaced. Low-frequency entries are retained as exception history.",
                  italic=True, size=18, color="555555", space_before=120, space_after=90)

    add_heading(body, "Parts Replaced")
    part_rows = [["Reference PN / Component", "Times Replaced"]] + [[x["label"], x["count"]] for x in report["parts"]]
    add_table(body, part_rows, [6900, 2400], caption="NOVA_PARTS", header=True)

    if report.get("failures"):
        add_heading(body, "Reported Failures")
        fail_rows = [["Reported Failure", "Times Seen"]] + [[x["label"], x["count"]] for x in report["failures"]]
        add_table(body, fail_rows, [6900, 2400], caption="NOVA_FAILURES", header=True)

    if report.get("actions"):
        add_heading(body, "Recurring Repair Actions")
        act_rows = [["Recurring Repair Action", "Times Seen"]] + [[x["label"], x["count"]] for x in report["actions"]]
        add_table(body, act_rows, [6900, 2400], caption="NOVA_ACTIONS", header=True)

    add_heading(body, "Technician Notes / Current Repair")
    note_rows = [
        ["DRL Log #", "", "Technician", ""],
        ["Date", "", "Customer / Site", ""],
        ["Current symptom / observations", ""],
        ["Work performed / parts used", ""],
        ["Final test / outcome", ""],
    ]
    # Build notes manually for mixed 4/2 columns.
    tbl = el(body, W, "tbl")
    tblpr = el(tbl, W, "tblPr")
    el(tblpr, W, "tblW", {"w:w": "9300", "w:type": "dxa"})
    el(tblpr, W, "tblCaption", {"w:val": "NOVA_NOTES"})
    borders = el(tblpr, W, "tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el(borders, W, edge, {"w:val": "single", "w:sz": "4", "w:color": "B7B7B7"})
    for ri, row in enumerate(note_rows):
        tr = el(tbl, W, "tr")
        if ri < 2:
            widths = [1500, 3000, 1500, 3300]
            vals = row
        else:
            widths = [2600, 6700]
            vals = [row[0], row[1]]
        for ci, val in enumerate(vals):
            tc = el(tr, W, "tc")
            tcpr = el(tc, W, "tcPr")
            el(tcpr, W, "tcW", {"w:w": str(widths[ci]), "w:type": "dxa"})
            mar = el(tcpr, W, "tcMar")
            for side in ("top", "bottom", "left", "right"):
                el(mar, W, side, {"w:w": "100", "w:type": "dxa"})
            if ci % 2 == 0:
                el(tcpr, W, "shd", {"w:fill": "F2F2F2"})
                add_cell_text(tc, val, bold=True, size=17)
            else:
                # Give Word some editable vertical room.
                p = add_paragraph(tc, val, size=18, space_after=0)
                ppr = p.find(qn(W, "pPr"))
                el(ppr, W, "spacing", {"w:after": "160" if ri >= 2 else "80"})

    add_paragraph(body,
                  "Editable working report. Changes to this DOCX do not modify NOVA knowledge unless explicitly reviewed and exported as a human correction candidate.",
                  italic=True, size=16, color="666666", space_before=140, space_after=60)

    baseline = dict(report)
    baseline["generated_at"] = generated_at
    raw = json.dumps(baseline, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    marker = BASELINE_PREFIX + base64.b64encode(raw).decode("ascii")
    add_paragraph(body, marker, size=2, hidden=True, space_after=0)

    sect = el(body, W, "sectPr")
    el(sect, W, "pgSz", {"w:w": "12240", "w:h": "15840"})
    el(sect, W, "pgMar", {"w:top": "700", "w:right": "700", "w:bottom": "700", "w:left": "700", "w:header": "360", "w:footer": "360", "w:gutter": "0"})
    return ET.tostring(doc, encoding="utf-8", xml_declaration=True)


def content_types_xml() -> bytes:
    # LibreOffice is stricter than Word/python-docx about package-level OPC
    # namespaces. Use the conventional default namespace form here.
    return b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''


def rels_xml() -> bytes:
    return b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''


def document_rels_xml() -> bytes:
    return b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
</Relationships>'''

def core_xml(report: Dict[str, Any], generated_at_iso: str) -> bytes:
    root = ET.Element(qn(CP, "coreProperties"))
    ET.SubElement(root, qn(DC, "title")).text = f"NOVA Repair Reference - {report['meta']['base_part_number']}"
    ET.SubElement(root, qn(DC, "subject")).text = report["meta"]["equipment_family"]
    ET.SubElement(root, qn(DC, "creator")).text = "Direct Repair Laboratories - NOVA DRL"
    ET.SubElement(root, qn(CP, "keywords")).text = "NOVA DRL; repair reference; editable technician report"
    ET.SubElement(root, qn(DC, "description")).text = "Editable NOVA DRL technician repair reference. Human edits do not alter frozen evidence without explicit review."
    created = ET.SubElement(root, qn(DCT, "created")); created.set(qn(XSI, "type"), "dcterms:W3CDTF"); created.text = generated_at_iso
    modified = ET.SubElement(root, qn(DCT, "modified")); modified.set(qn(XSI, "type"), "dcterms:W3CDTF"); modified.text = generated_at_iso
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def app_xml() -> bytes:
    root = ET.Element(qn(EP, "Properties"))
    ET.SubElement(root, qn(EP, "Application")).text = "NOVA DRL"
    ET.SubElement(root, qn(EP, "AppVersion")).text = VERSION
    ET.SubElement(root, qn(EP, "Company")).text = "Direct Repair Laboratories"
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build_docx(report: Dict[str, Any], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    generated_label = now.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    generated_iso = now.isoformat().replace("+00:00", "Z")

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types_xml())
        z.writestr("_rels/.rels", rels_xml())
        z.writestr("word/document.xml", document_xml(report, generated_label))
        z.writestr("word/styles.xml", styles_xml())
        z.writestr("word/settings.xml", settings_xml())
        z.writestr("word/_rels/document.xml.rels", document_rels_xml())
        z.writestr("docProps/core.xml", core_xml(report, generated_iso))
        z.writestr("docProps/app.xml", app_xml())
    return out_path


def default_out(report: Dict[str, Any]) -> Path:
    base = safe_filename(report["meta"]["base_part_number"])
    return Path(DEFAULT_REPORT_DIR) / f"NOVA_{base}_Repair_Reference.docx"


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate editable NOVA DRL technician DOCX report")
    ap.add_argument("--search", required=True, help="DRL part number / family search")
    ap.add_argument("--out", help="Output DOCX path")
    ap.add_argument("--source-command", default=DEFAULT_NOVA, help="Frozen nova-drl launcher path")
    ap.add_argument("--from-text", help="Use saved nova-drl terminal output instead of running nova-drl")
    args = ap.parse_args()

    if args.from_text:
        text = Path(args.from_text).read_text(encoding="utf-8", errors="replace")
    else:
        text = run_nova(args.search, args.source_command)

    report = parse_report_text(text, args.search)
    out = Path(args.out) if args.out else default_out(report)
    build_docx(report, out)

    print("# NOVA DRL Editable DOCX Report | v1.9.0")
    print(f"Search: {report['search']}")
    print(f"Family: {report['meta']['equipment_family']}")
    print(f"Parts rows: {len(report['parts'])}")
    print(f"Failure rows: {len(report['failures'])}")
    print(f"Action rows: {len(report['actions'])}")
    print(f"DOCX: {out}")
    print("Baseline embedded: YES | Technician Notes excluded from knowledge review: YES")
    print("Frozen evidence changed: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
