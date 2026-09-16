#!/usr/bin/env python3
"""
NOVA DRL DOCX Edit Review v1.9.0

Compares editable report knowledge sections against the hidden baseline embedded
by nova_drl_docx_report_v1_9_0.py.

Rules:
- Technician Notes and formatting are ignored.
- Parts / failures / actions / core family metadata are reviewed.
- No edit is ever promoted automatically.
- Optional export writes an unreviewed human-correction-candidate JSON only.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

VERSION = "1.9.0"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
BASELINE_PREFIX = "NOVA_BASELINE_V1_9_0_B64:"


def qn(tag: str) -> str:
    return f"{{{W}}}{tag}"


def text_of(node: ET.Element) -> str:
    return "".join((t.text or "") for t in node.iter(qn("t"))).strip()


def parse_int(value: str) -> Optional[int]:
    s = re.sub(r"[^0-9-]+", "", str(value or ""))
    try:
        return int(s)
    except Exception:
        return None


def load_document_xml(path: Path) -> ET.Element:
    with zipfile.ZipFile(path, "r") as z:
        data = z.read("word/document.xml")
    return ET.fromstring(data)


def baseline_from_doc(root: ET.Element) -> Dict[str, Any]:
    for p in root.iter(qn("p")):
        txt = text_of(p)
        if txt.startswith(BASELINE_PREFIX):
            raw = base64.b64decode(txt[len(BASELINE_PREFIX):].encode("ascii"))
            return json.loads(raw.decode("utf-8"))
    raise RuntimeError("NOVA hidden baseline not found. This DOCX may not be a v1.9.0 NOVA report.")


def table_caption(tbl: ET.Element) -> str:
    cap = tbl.find("./w:tblPr/w:tblCaption", {"w": W})
    return cap.get(qn("val"), "") if cap is not None else ""


def table_rows(tbl: ET.Element) -> List[List[str]]:
    out = []
    for tr in tbl.findall("./w:tr", {"w": W}):
        row = []
        for tc in tr.findall("./w:tc", {"w": W}):
            row.append(text_of(tc))
        out.append(row)
    return out


def extract_current(root: ET.Element) -> Dict[str, Any]:
    tables = {table_caption(tbl): table_rows(tbl) for tbl in root.iter(qn("tbl")) if table_caption(tbl)}

    meta = {}
    for row in tables.get("NOVA_META", []):
        if len(row) >= 2:
            meta[row[0]] = row[1]

    def ranked(caption: str) -> List[Dict[str, Any]]:
        rows = tables.get(caption, [])
        if rows:
            rows = rows[1:]  # header
        out = []
        for row in rows:
            if len(row) < 2 or not row[0].strip():
                continue
            count = parse_int(row[-1])
            out.append({"label": row[0].strip(), "count": count})
        return out

    return {
        "meta": {
            "equipment_family": meta.get("Equipment Family", ""),
            "base_part_number": meta.get("Base DRL Part #", ""),
            "indexed_repair_events": parse_int(meta.get("Indexed Repair Events", "")),
            "indexed_parts": parse_int(meta.get("Indexed Parts", "")),
            "model_variants": parse_int(meta.get("Model Variants Preserved", "")),
        },
        "parts": ranked("NOVA_PARTS"),
        "failures": ranked("NOVA_FAILURES"),
        "actions": ranked("NOVA_ACTIONS"),
    }


def norm_label(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def diff_rows(section: str, before: Sequence[Dict[str, Any]], after: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    diffs: List[Dict[str, Any]] = []
    bmap = {norm_label(x.get("label")): x for x in before if norm_label(x.get("label"))}
    amap = {norm_label(x.get("label")): x for x in after if norm_label(x.get("label"))}

    for key in sorted(set(bmap) & set(amap)):
        b, a = bmap[key], amap[key]
        if b.get("count") != a.get("count"):
            diffs.append({
                "section": section,
                "kind": "count_changed",
                "label": b.get("label"),
                "before": b.get("count"),
                "after": a.get("count"),
            })
    for key in sorted(set(bmap) - set(amap)):
        b = bmap[key]
        diffs.append({"section": section, "kind": "removed", "label": b.get("label"), "before": b.get("count")})
    for key in sorted(set(amap) - set(bmap)):
        a = amap[key]
        diffs.append({"section": section, "kind": "added", "label": a.get("label"), "after": a.get("count")})
    return diffs


def compute_diff(baseline: Dict[str, Any], current: Dict[str, Any]) -> List[Dict[str, Any]]:
    diffs: List[Dict[str, Any]] = []
    bmeta = baseline.get("meta") or {}
    cmeta = current.get("meta") or {}
    for field, label in [
        ("equipment_family", "Equipment Family"),
        ("base_part_number", "Base DRL Part #"),
        ("indexed_repair_events", "Indexed Repair Events"),
        ("indexed_parts", "Indexed Parts"),
        ("model_variants", "Model Variants Preserved"),
    ]:
        if bmeta.get(field) != cmeta.get(field):
            diffs.append({"section": "META", "kind": "changed", "label": label,
                          "before": bmeta.get(field), "after": cmeta.get(field)})
    diffs.extend(diff_rows("PARTS", baseline.get("parts") or [], current.get("parts") or []))
    diffs.extend(diff_rows("FAILURES", baseline.get("failures") or [], current.get("failures") or []))
    diffs.extend(diff_rows("ACTIONS", baseline.get("actions") or [], current.get("actions") or []))
    return diffs


def print_screen(path: Path, baseline: Dict[str, Any], diffs: Sequence[Dict[str, Any]]) -> None:
    print("=" * 88)
    print("NOVA DOCX EDIT REVIEW  |  v1.9.0")
    print("=" * 88)
    print(f"Report: {path}")
    print(f"DRL Part #: {(baseline.get('meta') or {}).get('base_part_number', '')}")
    print(f"Family: {(baseline.get('meta') or {}).get('equipment_family', '')}")
    print()
    if not diffs:
        print("NO KNOWLEDGE-AREA EDITS DETECTED")
        print("Formatting and Technician Notes are intentionally ignored.")
        print("Frozen NOVA evidence remains unchanged.")
        return

    print(f"KNOWLEDGE-AREA EDITS DETECTED: {len(diffs)}")
    print("Technician Notes / Current Repair are not included in this count.")
    print()
    current_section = None
    for d in diffs:
        if d["section"] != current_section:
            current_section = d["section"]
            print(current_section)
            print("-" * len(current_section))
        kind = d["kind"]
        if kind in {"changed", "count_changed"}:
            print(f"~ {d['label']}: {d.get('before')}  ->  {d.get('after')}")
        elif kind == "removed":
            print(f"- {d['label']}  ({d.get('before')})")
        elif kind == "added":
            print(f"+ {d['label']}  ({d.get('after')})")
    print()
    print("Nothing above has been promoted into NOVA knowledge.")


def export_candidate(path: Path, report_path: Path, baseline: Dict[str, Any], current: Dict[str, Any], diffs: Sequence[Dict[str, Any]]) -> Path:
    obj = {
        "schema": "nova_drl_human_correction_candidate",
        "version": VERSION,
        "status": "unreviewed_candidate_only",
        "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source_docx": str(report_path),
        "base_part_number": (baseline.get("meta") or {}).get("base_part_number"),
        "equipment_family": (baseline.get("meta") or {}).get("equipment_family"),
        "diffs": list(diffs),
        "baseline": baseline,
        "edited_report": current,
        "promotion_policy": "NO automatic promotion; requires separate explicit human approval",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Review edits in a NOVA v1.9.0 editable DOCX report")
    ap.add_argument("docx", help="Edited NOVA DOCX report")
    ap.add_argument("--export-candidate", help="Write unreviewed correction candidate JSON")
    ap.add_argument("--non-interactive", action="store_true", help="Do not prompt")
    args = ap.parse_args()

    path = Path(args.docx)
    root = load_document_xml(path)
    baseline = baseline_from_doc(root)
    current = extract_current(root)
    diffs = compute_diff(baseline, current)
    print_screen(path, baseline, diffs)

    if not diffs:
        return 0

    if args.export_candidate:
        out = export_candidate(Path(args.export_candidate), path, baseline, current, diffs)
        print(f"\nCorrection candidate exported: {out}")
        print("Accepted facts changed: NO")
        return 0

    if args.non_interactive or not sys.stdin.isatty():
        return 3

    print("\nReview choice:")
    print("  1) Keep edits in this report only (default; NOVA unchanged)")
    print("  2) Export an unreviewed human correction candidate JSON")
    print("  3) Exit")
    choice = input("Select [1]: ").strip() or "1"
    if choice == "2":
        out = path.with_suffix(".nova_correction_candidate.json")
        export_candidate(out, path, baseline, current, diffs)
        print(f"Candidate exported: {out}")
        print("Accepted facts changed: NO")
    elif choice == "3":
        return 3
    else:
        print("Report edits kept as report-only. NOVA knowledge unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
