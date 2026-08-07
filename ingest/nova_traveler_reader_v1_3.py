#!/usr/bin/env python3
"""
Nova DRL Traveler Reader v1.3
=============================

Read-only traveler-content extraction for the Direct Repair Labs Traveler Database.

Purpose
-------
Surveyor v1.2.1 established:
    Model -> Serial Number -> Repair Event -> Evidence

Traveler Reader v1.3 begins reading the actual traveler evidence attached to
those repair events.

Design principles
-----------------
- Never modify DRL source files.
- Preserve exact source path, log number, and raw extracted text.
- Separate extraction from interpretation.
- Never silently invent missing fields.
- Keep low-confidence / unavailable fields explicitly empty.
- Local-only by default.

Supported first-pass extraction
-------------------------------
- PDF travelers: pdftotext if installed; pypdf fallback if installed.
- JPG/JPEG/PNG/TIF/TIFF travelers: tesseract CLI if installed.
- Optional local Ollama structuring of extracted text into DRL repair fields.

Important
---------
OCR on handwritten or poor-quality scans can be incomplete. v1.3 therefore
stores the raw extraction and an explicit extraction status before any AI
interpretation.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.3.0"

LOG_RE = re.compile(r"^(?P<log>\d{9})\b")
TRAVELER_RE = re.compile(
    r"^(?P<log>\d{9})\s+Line\s+Card\s+(?P<kind>Original|Warranty)\b.*"
    r"\.(?P<ext>jpg|jpeg|png|pdf|tif|tiff)$",
    re.IGNORECASE,
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
PDF_EXTS = {".pdf"}

STRUCTURED_FIELDS = [
    "customer_complaint",
    "incoming_condition",
    "technician_findings",
    "diagnosis",
    "root_cause",
    "repair_actions",
    "parts_replaced",
    "testing_performed",
    "final_result",
    "technician_notes",
]

def now_utc():
    return datetime.now(timezone.utc).isoformat()

def run_command(args, timeout=120):
    try:
        p = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        return p.returncode, p.stdout, p.stderr
    except Exception as exc:
        return 999, "", str(exc)

def find_travelers(serial_folder):
    serial_folder = Path(serial_folder).expanduser().resolve()
    rows = []
    if not serial_folder.exists() or not serial_folder.is_dir():
        raise ValueError("Serial folder does not exist: {}".format(serial_folder))

    for path in sorted(serial_folder.rglob("*")):
        if path.is_dir() or path.is_symlink():
            continue
        m = TRAVELER_RE.match(path.name)
        if not m:
            continue
        rows.append({
            "path": path,
            "relative_path": str(path.relative_to(serial_folder)),
            "log_number": m.group("log"),
            "traveler_kind": m.group("kind").lower(),
            "warranty": m.group("kind").lower() == "warranty",
            "extension": "." + m.group("ext").lower(),
        })
    return rows

def extract_pdf_text(path):
    # Preferred: pdftotext CLI
    if shutil.which("pdftotext"):
        code, out, err = run_command(["pdftotext", "-layout", str(path), "-"])
        if code == 0 and out.strip():
            return {
                "status": "ok",
                "method": "pdftotext",
                "text": out,
                "warning": None,
            }

    # Fallback: pypdf
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                parts.append("")
        text = "\n".join(parts)
        if text.strip():
            return {
                "status": "ok",
                "method": "pypdf",
                "text": text,
                "warning": None,
            }
    except Exception as exc:
        fallback_error = str(exc)
    else:
        fallback_error = "No extractable PDF text."

    return {
        "status": "unavailable",
        "method": None,
        "text": "",
        "warning": "PDF text extraction unavailable or document may be scanned. {}".format(fallback_error),
    }

def extract_image_text(path):
    if not shutil.which("tesseract"):
        return {
            "status": "dependency_missing",
            "method": None,
            "text": "",
            "warning": "tesseract is not installed. Traveler image preserved but not OCR'd.",
        }

    # Tesseract stdout mode. PSM 6 is a reasonable first pass for a structured form.
    code, out, err = run_command(
        ["tesseract", str(path), "stdout", "--psm", "6"],
        timeout=180,
    )
    if code == 0:
        return {
            "status": "ok" if out.strip() else "empty",
            "method": "tesseract_psm6",
            "text": out,
            "warning": None if out.strip() else "OCR returned no text.",
        }

    return {
        "status": "error",
        "method": "tesseract_psm6",
        "text": "",
        "warning": err.strip() or "Tesseract failed.",
    }

def extract_traveler_text(path):
    ext = path.suffix.lower()
    if ext in PDF_EXTS:
        return extract_pdf_text(path)
    if ext in IMAGE_EXTS:
        return extract_image_text(path)
    return {
        "status": "unsupported",
        "method": None,
        "text": "",
        "warning": "Unsupported traveler extension: {}".format(ext),
    }

def blank_structured():
    return {
        field: None for field in STRUCTURED_FIELDS
    }

def ollama_available():
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False

def structure_with_ollama(raw_text, log_number, model):
    if not raw_text.strip():
        return {
            "status": "skipped",
            "model": model,
            "fields": blank_structured(),
            "warning": "No extracted text available.",
        }

    if not ollama_available():
        return {
            "status": "unavailable",
            "model": model,
            "fields": blank_structured(),
            "warning": "Local Ollama API not reachable.",
        }

    prompt = """You are extracting facts from a Direct Repair Labs repair traveler.

Return ONLY valid JSON. Do not invent information. If a field is not explicitly
supported by the traveler text, use null. Preserve part numbers and error codes
exactly when possible.

Required JSON keys:
customer_complaint
incoming_condition
technician_findings
diagnosis
root_cause
repair_actions
parts_replaced
testing_performed
final_result
technician_notes

For list-like fields, return arrays of strings. For prose fields, return a short
string or null.

Log number: {log}

TRAVELER TEXT:
{text}
""".format(log=log_number, text=raw_text[:30000])

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=240) as r:
            body = json.loads(r.read().decode("utf-8"))
        response_text = body.get("response", "")
        parsed = json.loads(response_text)

        fields = blank_structured()
        for key in STRUCTURED_FIELDS:
            fields[key] = parsed.get(key)

        return {
            "status": "ok",
            "model": model,
            "fields": fields,
            "warning": None,
        }
    except Exception as exc:
        return {
            "status": "error",
            "model": model,
            "fields": blank_structured(),
            "warning": str(exc),
        }

def read_serial_travelers(serial_folder, output_dir, use_ollama=False, model="qwen2.5:32b"):
    serial_folder = Path(serial_folder).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    travelers = find_travelers(serial_folder)
    records = []

    for item in travelers:
        path = item["path"]
        extraction = extract_traveler_text(path)

        structured = {
            "status": "not_requested",
            "model": None,
            "fields": blank_structured(),
            "warning": None,
        }
        if use_ollama:
            structured = structure_with_ollama(
                extraction["text"],
                item["log_number"],
                model
            )

        record = {
            "reader_version": VERSION,
            "processed_at_utc": now_utc(),
            "source_serial_folder": str(serial_folder),
            "source_path": str(path),
            "relative_path": item["relative_path"],
            "log_number": item["log_number"],
            "traveler_kind": item["traveler_kind"],
            "warranty": item["warranty"],
            "extraction": extraction,
            "structured": structured,
        }
        records.append(record)

        per_log = output_dir / item["log_number"]
        per_log.mkdir(parents=True, exist_ok=True)

        (per_log / "traveler_raw.txt").write_text(
            extraction["text"],
            encoding="utf-8"
        )
        (per_log / "traveler_reader.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    summary = {
        "reader_version": VERSION,
        "processed_at_utc": now_utc(),
        "source_serial_folder": str(serial_folder),
        "traveler_count": len(records),
        "successful_extractions": sum(
            1 for r in records if r["extraction"]["status"] == "ok"
        ),
        "dependency_missing": sum(
            1 for r in records if r["extraction"]["status"] == "dependency_missing"
        ),
        "ollama_requested": use_ollama,
        "ollama_successes": sum(
            1 for r in records if r["structured"]["status"] == "ok"
        ),
        "travelers": [
            {
                "log_number": r["log_number"],
                "traveler_kind": r["traveler_kind"],
                "warranty": r["warranty"],
                "relative_path": r["relative_path"],
                "extraction_status": r["extraction"]["status"],
                "extraction_method": r["extraction"]["method"],
                "structured_status": r["structured"]["status"],
            }
            for r in records
        ]
    }

    (output_dir / "traveler_reader_summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8"
    )

    lines = [
        "NOVA DRL TRAVELER READER v{}".format(VERSION),
        "=" * 72,
        "Source: {}".format(serial_folder),
        "Travelers found: {}".format(summary["traveler_count"]),
        "Successful text extractions: {}".format(summary["successful_extractions"]),
        "OCR dependency missing: {}".format(summary["dependency_missing"]),
        "Ollama requested: {}".format("YES" if use_ollama else "NO"),
        "Ollama structured: {}".format(summary["ollama_successes"]),
        "",
        "TRAVELERS",
    ]

    for row in summary["travelers"]:
        flags = []
        if row["warranty"]:
            flags.append("WARRANTY")
        if row["extraction_status"] != "ok":
            flags.append(row["extraction_status"].upper())
        if use_ollama and row["structured_status"] != "ok":
            flags.append("STRUCTURE:" + row["structured_status"].upper())
        tag = " [{}]".format(", ".join(flags)) if flags else ""
        lines.append(
            "{}  {}{}  extraction={}  structure={}".format(
                row["log_number"],
                row["traveler_kind"],
                tag,
                row["extraction_method"] or row["extraction_status"],
                row["structured_status"],
            )
        )

    (output_dir / "traveler_reader_summary.txt").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8"
    )

    return summary

def main():
    ap = argparse.ArgumentParser(
        description="Read-only Nova DRL Traveler Reader v1.3"
    )
    ap.add_argument("serial_folder")
    ap.add_argument(
        "--output",
        help="Local output directory. Default: ./output/traveler_reader/<serial-folder>"
    )
    ap.add_argument(
        "--ollama",
        action="store_true",
        help="After extraction, use local Ollama to structure traveler facts."
    )
    ap.add_argument(
        "--model",
        default="qwen2.5:32b",
        help="Ollama model name. Default: qwen2.5:32b"
    )
    args = ap.parse_args()

    source = Path(args.serial_folder).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        print("ERROR: Serial folder not found:", source, file=sys.stderr)
        return 2

    if args.output:
        out = Path(args.output).expanduser().resolve()
    else:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", source.name).strip("_")
        out = Path.cwd() / "output" / "traveler_reader" / safe

    try:
        summary = read_serial_travelers(
            source,
            out,
            use_ollama=args.ollama,
            model=args.model,
        )
    except Exception as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 2

    print()
    print("Nova DRL Traveler Reader v{}".format(VERSION))
    print("=" * 52)
    print("Travelers found:     {}".format(summary["traveler_count"]))
    print("Text extracted:      {}".format(summary["successful_extractions"]))
    print("OCR dependency miss: {}".format(summary["dependency_missing"]))
    print("Ollama requested:    {}".format("YES" if args.ollama else "NO"))
    print("Ollama structured:   {}".format(summary["ollama_successes"]))
    print()
    for row in summary["travelers"]:
        print(
            "{}  {:8}  extraction={:18} structure={}".format(
                row["log_number"],
                row["traveler_kind"],
                row["extraction_method"] or row["extraction_status"],
                row["structured_status"],
            )
        )
    print()
    print("Reports:", out)
    print("READ-ONLY COMPLETE: No DRL source files were changed.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
