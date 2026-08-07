#!/usr/bin/env python3
"""
Nova DRL Surveyor v1.0
======================

Read-only survey tool for Direct Repair Labs repair folders.

Purpose
-------
- Observe a copied DRL repair folder without changing it.
- Parse useful metadata from the folder name.
- Identify the primary traveler and other evidence.
- Inventory all files recursively.
- Calculate SHA-256 hashes for duplicate detection.
- Produce JSON, CSV, and human-readable reports.

Safety
------
This program never moves, renames, edits, or deletes source files.
It only reads source files and writes reports to an output directory.

Initial pilot
-------------
Type: RBT
OEM: Genmark
Model: GB8 / GB8-MT
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


VERSION = "1.0.0"

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp"
}
VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".mpg", ".mpeg", ".m4v"
}
DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".txt", ".rtf", ".md"
}
SPREADSHEET_EXTENSIONS = {
    ".xls", ".xlsx", ".xlsm", ".csv", ".tsv"
}
PARAMETER_EXTENSIONS = {
    ".par", ".prm", ".cfg", ".conf", ".ini", ".json", ".xml", ".dat", ".bin"
}

DEFAULT_OEMS = [
    "GENMARK", "BROOKS", "ASYST", "PRI", "RORZE", "YASKAWA",
    "KAWASAKI", "NIKON", "TAZMO", "HINE"
]

DEFAULT_TECHNICIANS = [
    "ERICH", "MATT"
]

DEFAULT_SITES = {
    "MTV": "Micron Technology Virginia"
}

PRIMARY_TRAVELER_RE = re.compile(
    r"^(?P<log_number>\d+)\s+Line\s+Card\s+Original(?:\s*\(\d+\))?\.(?P<ext>jpg|jpeg|png|pdf|tif|tiff)$",
    re.IGNORECASE,
)

LEADING_LOG_RE = re.compile(r"^(?P<log_number>\d{6,})\b", re.IGNORECASE)


def sha256_file(path, chunk_size=1024 * 1024):
    """Return SHA-256 for a file without loading the whole file into memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def clean_spaces(value):
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_upper(value):
    return clean_spaces(value).upper()


def load_reference_config(config_dir):
    """
    Load optional JSON reference files.

    JSON is used here deliberately so Surveyor v1 has no third-party dependency.
    If files are missing, safe built-in defaults are used.
    """
    config_dir = Path(config_dir)
    result = {
        "oems": list(DEFAULT_OEMS),
        "technicians": list(DEFAULT_TECHNICIANS),
        "sites": dict(DEFAULT_SITES),
    }

    paths = {
        "oems": config_dir / "oems.json",
        "technicians": config_dir / "technicians.json",
        "sites": config_dir / "site_codes.json",
    }

    try:
        if paths["oems"].exists():
            data = json.loads(paths["oems"].read_text(encoding="utf-8"))
            result["oems"] = [normalize_upper(x) for x in data.get("oems", [])]
    except Exception as exc:
        print("WARNING: Could not read {}: {}".format(paths["oems"], exc), file=sys.stderr)

    try:
        if paths["technicians"].exists():
            data = json.loads(paths["technicians"].read_text(encoding="utf-8"))
            result["technicians"] = [
                normalize_upper(x) for x in data.get("technicians", [])
            ]
    except Exception as exc:
        print("WARNING: Could not read {}: {}".format(paths["technicians"], exc), file=sys.stderr)

    try:
        if paths["sites"].exists():
            data = json.loads(paths["sites"].read_text(encoding="utf-8"))
            result["sites"] = {
                normalize_upper(k): clean_spaces(v)
                for k, v in data.get("sites", {}).items()
            }
    except Exception as exc:
        print("WARNING: Could not read {}: {}".format(paths["sites"], exc), file=sys.stderr)

    return result


def parse_repair_folder_name(folder_name, refs):
    """
    Parse DRL repair folder names such as:

      RBT - GB8-MT GENMARK SN 80050608 UTI MICRON MTV ERICH

    Known reference lists are used from the right side to avoid assuming
    a fixed number of words in customer names.
    """
    raw = clean_spaces(folder_name)
    parsed = {
        "original_folder_name": raw,
        "equipment_type": None,
        "model": None,
        "oem": None,
        "serial_number": None,
        "customer": None,
        "site_code": None,
        "site_name": None,
        "technician": None,
        "parse_confidence": "low",
        "parse_notes": [],
    }

    if " - " not in raw:
        parsed["parse_notes"].append("Missing expected ' - ' separator.")
        return parsed

    equipment_type, remainder = raw.split(" - ", 1)
    parsed["equipment_type"] = normalize_upper(equipment_type)

    tokens = remainder.split()
    upper_tokens = [normalize_upper(t) for t in tokens]

    # Find OEM by known value.
    oem_idx = None
    oem_value = None
    for idx, token in enumerate(upper_tokens):
        if token in refs["oems"]:
            oem_idx = idx
            oem_value = token
            break

    if oem_idx is None:
        parsed["parse_notes"].append("Known OEM not found in folder name.")
        return parsed

    if oem_idx > 0:
        parsed["model"] = " ".join(tokens[:oem_idx])
    parsed["oem"] = oem_value

    # Find SN marker after OEM.
    sn_idx = None
    for idx in range(oem_idx + 1, len(upper_tokens)):
        if upper_tokens[idx] == "SN":
            sn_idx = idx
            break

    if sn_idx is None or sn_idx + 1 >= len(tokens):
        parsed["parse_notes"].append("SN marker or serial number not found.")
        return parsed

    parsed["serial_number"] = tokens[sn_idx + 1]
    tail = tokens[sn_idx + 2:]

    # Recognize technician from final token if possible.
    if tail and normalize_upper(tail[-1]) in refs["technicians"]:
        parsed["technician"] = normalize_upper(tail[-1])
        tail = tail[:-1]
    else:
        parsed["parse_notes"].append("Technician not confidently identified.")

    # Recognize site code from final remaining token.
    if tail and normalize_upper(tail[-1]) in refs["sites"]:
        site_code = normalize_upper(tail[-1])
        parsed["site_code"] = site_code
        parsed["site_name"] = refs["sites"][site_code]
        tail = tail[:-1]
    else:
        parsed["parse_notes"].append("Site code not confidently identified.")

    if tail:
        parsed["customer"] = " ".join(tail)
    else:
        parsed["parse_notes"].append("Customer could not be parsed.")

    strong_fields = [
        parsed["equipment_type"], parsed["model"], parsed["oem"],
        parsed["serial_number"], parsed["customer"]
    ]
    if all(strong_fields) and parsed["site_code"] and parsed["technician"]:
        parsed["parse_confidence"] = "high"
    elif all(strong_fields):
        parsed["parse_confidence"] = "medium"

    return parsed


def extract_log_number(filename):
    m = PRIMARY_TRAVELER_RE.match(filename)
    if m:
        return m.group("log_number")

    m = LEADING_LOG_RE.match(filename)
    if m:
        return m.group("log_number")

    return None


def classify_evidence(path, source_root):
    """
    Conservative filename/path classification.
    Unknown is preferred over an invented classification.
    """
    rel = path.relative_to(source_root)
    name = path.name
    lower_name = name.lower()
    lower_path = " / ".join(p.lower() for p in rel.parts)
    ext = path.suffix.lower()

    result = {
        "role": "unknown",
        "confidence": "low",
        "reason": "No confirmed rule matched.",
        "log_number": extract_log_number(name),
    }

    m = PRIMARY_TRAVELER_RE.match(name)
    if m:
        result.update({
            "role": "current_traveler",
            "confidence": "confirmed",
            "reason": "Matches DRL '[log] Line Card Original' naming rule.",
            "log_number": m.group("log_number"),
        })
        return result

    previous_words = ("previous", "prior", "old traveler", "previous traveler")
    traveler_words = ("traveler", "line card")

    if any(w in lower_path for w in previous_words) and any(w in lower_path for w in traveler_words):
        result.update({
            "role": "previous_traveler",
            "confidence": "high",
            "reason": "Path/name contains previous-repair and traveler indicators.",
        })
        return result

    if ext in IMAGE_EXTENSIONS:
        if any(w in lower_path for w in previous_words):
            result.update({
                "role": "previous_photo",
                "confidence": "high",
                "reason": "Image file located in a previous/prior context.",
            })
        else:
            result.update({
                "role": "current_photo",
                "confidence": "medium",
                "reason": "Image file; no previous/prior indicator found.",
            })
        return result

    if ext in VIDEO_EXTENSIONS:
        result.update({
            "role": "movie",
            "confidence": "high",
            "reason": "Recognized video extension.",
        })
        return result

    if "reverse engineer" in lower_path or "reverse-engineer" in lower_path or "reverse engineering" in lower_path:
        result.update({
            "role": "reverse_engineering_sheet",
            "confidence": "high",
            "reason": "Path/name contains reverse-engineering wording.",
        })
        return result

    if "schematic" in lower_path or "schematics" in lower_path:
        result.update({
            "role": "schematic",
            "confidence": "high",
            "reason": "Path/name contains schematic wording.",
        })
        return result

    if "parameter" in lower_path or "uploadparam" in lower_path or "upload param" in lower_path:
        result.update({
            "role": "parameter_file",
            "confidence": "high",
            "reason": "Path/name contains parameter wording.",
        })
        return result

    if "manual" in lower_path:
        result.update({
            "role": "manual",
            "confidence": "high",
            "reason": "Path/name contains manual wording.",
        })
        return result

    if "test procedure" in lower_path or "testing" in lower_path or "test procedure" in lower_path:
        result.update({
            "role": "test_procedure",
            "confidence": "medium",
            "reason": "Path/name contains testing/procedure wording.",
        })
        return result

    if "engineering" in lower_path or "engineer note" in lower_path or "engineering note" in lower_path:
        result.update({
            "role": "engineering_note",
            "confidence": "medium",
            "reason": "Path/name contains engineering wording.",
        })
        return result

    if ext in SPREADSHEET_EXTENSIONS:
        result.update({
            "role": "structured_document",
            "confidence": "low",
            "reason": "Spreadsheet recognized but role is not yet certain.",
        })
        return result

    if ext in DOCUMENT_EXTENSIONS:
        result.update({
            "role": "document",
            "confidence": "low",
            "reason": "Readable document type recognized but role is not yet certain.",
        })
        return result

    if ext in PARAMETER_EXTENSIONS:
        result.update({
            "role": "technical_file",
            "confidence": "low",
            "reason": "Technical/configuration extension recognized; content role uncertain.",
        })
        return result

    return result


def survey_folder(source, refs, hash_files=True):
    source = Path(source).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise ValueError("Source folder does not exist or is not a directory: {}".format(source))

    folder_metadata = parse_repair_folder_name(source.name, refs)

    files = []
    errors = []
    role_counts = Counter()
    ext_counts = Counter()
    hash_map = defaultdict(list)

    for path in sorted(source.rglob("*")):
        if path.is_dir():
            continue
        if path.is_symlink():
            continue

        rel = path.relative_to(source)
        try:
            stat = path.stat()
            classification = classify_evidence(path, source)
            digest = None
            if hash_files:
                digest = sha256_file(path)
                hash_map[digest].append(str(rel))

            record = {
                "relative_path": str(rel),
                "filename": path.name,
                "extension": path.suffix.lower(),
                "size_bytes": stat.st_size,
                "modified_time": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
                "sha256": digest,
                "role": classification["role"],
                "classification_confidence": classification["confidence"],
                "classification_reason": classification["reason"],
                "log_number": classification["log_number"],
            }
            files.append(record)
            role_counts[record["role"]] += 1
            ext_counts[record["extension"] or "(none)"] += 1

        except Exception as exc:
            errors.append({
                "relative_path": str(rel),
                "error": str(exc),
            })

    duplicate_groups = []
    if hash_files:
        for digest, paths in hash_map.items():
            if digest and len(paths) > 1:
                duplicate_groups.append({
                    "sha256": digest,
                    "paths": paths,
                    "count": len(paths),
                })

    primary_travelers = [f for f in files if f["role"] == "current_traveler"]
    primary_logs = sorted(set(
        f["log_number"] for f in primary_travelers if f["log_number"]
    ))

    return {
        "surveyor_version": VERSION,
        "surveyed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_folder": str(source),
        "source_folder_name": source.name,
        "folder_metadata": folder_metadata,
        "summary": {
            "file_count": len(files),
            "error_count": len(errors),
            "primary_traveler_count": len(primary_travelers),
            "primary_log_numbers": primary_logs,
            "role_counts": dict(sorted(role_counts.items())),
            "extension_counts": dict(sorted(ext_counts.items())),
            "duplicate_group_count": len(duplicate_groups),
        },
        "files": files,
        "duplicate_groups": duplicate_groups,
        "errors": errors,
    }


def write_reports(report, output_dir):
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "nova_survey.json"
    csv_path = output_dir / "nova_survey_files.csv"
    txt_path = output_dir / "nova_survey_summary.txt"

    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    fieldnames = [
        "relative_path", "filename", "extension", "size_bytes",
        "modified_time", "sha256", "role", "classification_confidence",
        "classification_reason", "log_number"
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(report["files"])

    meta = report["folder_metadata"]
    summary = report["summary"]

    lines = [
        "NOVA DRL SURVEYOR v{}".format(VERSION),
        "=" * 60,
        "",
        "SOURCE",
        "  {}".format(report["source_folder"]),
        "",
        "PARSED REPAIR FOLDER",
        "  Type:        {}".format(meta.get("equipment_type") or "Unknown"),
        "  OEM:         {}".format(meta.get("oem") or "Unknown"),
        "  Model:       {}".format(meta.get("model") or "Unknown"),
        "  Serial:      {}".format(meta.get("serial_number") or "Unknown"),
        "  Customer:    {}".format(meta.get("customer") or "Unknown"),
        "  Site:        {}{}".format(
            meta.get("site_code") or "Unknown",
            " - " + meta.get("site_name") if meta.get("site_name") else ""
        ),
        "  Technician:  {}".format(meta.get("technician") or "Unknown"),
        "  Confidence:  {}".format(meta.get("parse_confidence") or "Unknown"),
        "",
        "SURVEY SUMMARY",
        "  Files:                {}".format(summary["file_count"]),
        "  Primary travelers:    {}".format(summary["primary_traveler_count"]),
        "  Primary log numbers:  {}".format(
            ", ".join(summary["primary_log_numbers"]) or "None identified"
        ),
        "  Duplicate groups:     {}".format(summary["duplicate_group_count"]),
        "  Errors:               {}".format(summary["error_count"]),
        "",
        "EVIDENCE COUNTS",
    ]

    for role, count in summary["role_counts"].items():
        lines.append("  {:28} {}".format(role + ":", count))

    if meta.get("parse_notes"):
        lines.extend(["", "FOLDER PARSE NOTES"])
        for note in meta["parse_notes"]:
            lines.append("  - {}".format(note))

    uncertain = [
        f for f in report["files"]
        if f["classification_confidence"] in ("low", "medium")
    ]
    lines.extend([
        "",
        "FILES NEEDING HUMAN REVIEW",
        "  {}".format(len(uncertain)),
    ])
    for item in uncertain[:50]:
        lines.append(
            "  - [{} / {}] {}".format(
                item["role"],
                item["classification_confidence"],
                item["relative_path"],
            )
        )
    if len(uncertain) > 50:
        lines.append("  ... {} more".format(len(uncertain) - 50))

    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return json_path, csv_path, txt_path


def main():
    parser = argparse.ArgumentParser(
        description="Read-only DRL repair-folder survey and classification tool."
    )
    parser.add_argument(
        "source",
        help="Copied DRL repair folder to survey."
    )
    parser.add_argument(
        "--output",
        help="Output directory. Default: ./output/<source-folder-name>"
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Config directory containing oems.json, technicians.json, and site_codes.json."
    )
    parser.add_argument(
        "--no-hash",
        action="store_true",
        help="Skip SHA-256 calculation for a faster first pass."
    )
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()

    if args.config:
        config_dir = Path(args.config).expanduser().resolve()
    else:
        # Script expected at /opt/nova-drl/ingest/nova_surveyor_v1.py
        config_dir = Path(__file__).resolve().parent.parent / "config"

    refs = load_reference_config(config_dir)

    if args.output:
        output_dir = Path(args.output).expanduser().resolve()
    else:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", source.name).strip("_")
        output_dir = Path.cwd() / "output" / (safe_name or "survey")

    try:
        report = survey_folder(source, refs, hash_files=not args.no_hash)
        json_path, csv_path, txt_path = write_reports(report, output_dir)
    except Exception as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2

    meta = report["folder_metadata"]
    summary = report["summary"]

    print()
    print("Nova DRL Surveyor v{}".format(VERSION))
    print("=" * 48)
    print("Source:      {}".format(report["source_folder"]))
    print("Type:        {}".format(meta.get("equipment_type") or "Unknown"))
    print("OEM:         {}".format(meta.get("oem") or "Unknown"))
    print("Model:       {}".format(meta.get("model") or "Unknown"))
    print("Serial:      {}".format(meta.get("serial_number") or "Unknown"))
    print("Customer:    {}".format(meta.get("customer") or "Unknown"))
    print("Site:        {}".format(meta.get("site_name") or meta.get("site_code") or "Unknown"))
    print("Technician:  {}".format(meta.get("technician") or "Unknown"))
    print("Files:       {}".format(summary["file_count"]))
    print("Travelers:   {}".format(summary["primary_traveler_count"]))
    print("Log #:       {}".format(", ".join(summary["primary_log_numbers"]) or "Not identified"))
    print()
    print("Reports:")
    print("  {}".format(txt_path))
    print("  {}".format(json_path))
    print("  {}".format(csv_path))
    print()
    print("READ-ONLY COMPLETE: No source files were changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
