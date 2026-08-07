#!/usr/bin/env python3
"""
Nova DRL Surveyor v1.1
======================

Read-only survey tool for the Direct Repair Labs Traveler Database.

v1.1 focus
----------
The first Nova DRL pilot now concentrates on the organized Traveler Database
under "000 folder for tech scans". The Operations Check List is intentionally
deferred to a later phase.

New in v1.1
-----------
- Adds DOMAIN DISCOVERY mode for finding repair folders by Type/OEM/Model.
- Supports model-family matching: --model GB8 also finds GB8-MT folders.
- Discovery reads folder names only; it does not traverse every repair folder.
- Default file hashing is OFF for faster NAS surveys. Use --hash when wanted.
- Keeps direct single-repair-folder survey mode from v1.0.
- Writes TXT, JSON, and CSV reports.
- Never moves, renames, edits, or deletes source files.

Safety
------
This program is read-only against the source archive. It only writes reports
to the chosen local output directory.
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

VERSION = "1.1.0"

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
DEFAULT_TECHNICIANS = ["ERICH", "MATT"]
DEFAULT_SITES = {"MTV": "Micron Technology Virginia"}

PRIMARY_TRAVELER_RE = re.compile(
    r"^(?P<log_number>\d+)\s+Line\s+Card\s+Original"
    r"(?:\s*\(\d+\))?\.(?P<ext>jpg|jpeg|png|pdf|tif|tiff)$",
    re.IGNORECASE,
)
LEADING_LOG_RE = re.compile(r"^(?P<log_number>\d{6,})\b", re.IGNORECASE)


def clean_spaces(value):
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_upper(value):
    return clean_spaces(value).upper()


def sha256_file(path, chunk_size=1024 * 1024):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def load_reference_config(config_dir):
    config_dir = Path(config_dir)
    refs = {
        "oems": list(DEFAULT_OEMS),
        "technicians": list(DEFAULT_TECHNICIANS),
        "sites": dict(DEFAULT_SITES),
    }

    oems_path = config_dir / "oems.json"
    techs_path = config_dir / "technicians.json"
    sites_path = config_dir / "site_codes.json"

    try:
        if oems_path.exists():
            data = json.loads(oems_path.read_text(encoding="utf-8"))
            refs["oems"] = [normalize_upper(x) for x in data.get("oems", [])]
    except Exception as exc:
        print("WARNING: Could not read {}: {}".format(oems_path, exc), file=sys.stderr)

    try:
        if techs_path.exists():
            data = json.loads(techs_path.read_text(encoding="utf-8"))
            refs["technicians"] = [
                normalize_upper(x) for x in data.get("technicians", [])
            ]
    except Exception as exc:
        print("WARNING: Could not read {}: {}".format(techs_path, exc), file=sys.stderr)

    try:
        if sites_path.exists():
            data = json.loads(sites_path.read_text(encoding="utf-8"))
            refs["sites"] = {
                normalize_upper(k): clean_spaces(v)
                for k, v in data.get("sites", {}).items()
            }
    except Exception as exc:
        print("WARNING: Could not read {}: {}".format(sites_path, exc), file=sys.stderr)

    return refs


def parse_repair_folder_name(folder_name, refs):
    """
    Initial DRL folder convention:

      RBT - GB8-MT GENMARK SN 80050608 UTI MICRON MTV ERICH

    Customer can contain a variable number of words, so known technician and
    site references are parsed from the right instead of fixed positions.
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

    oem_idx = None
    for idx, token in enumerate(upper_tokens):
        if token in refs["oems"]:
            oem_idx = idx
            parsed["oem"] = token
            break

    if oem_idx is None:
        parsed["parse_notes"].append("Known OEM not found.")
        return parsed

    if oem_idx > 0:
        parsed["model"] = " ".join(tokens[:oem_idx])

    sn_idx = None
    for idx in range(oem_idx + 1, len(tokens)):
        if upper_tokens[idx] == "SN":
            sn_idx = idx
            break

    if sn_idx is None or sn_idx + 1 >= len(tokens):
        parsed["parse_notes"].append("SN marker or serial number not found.")
        return parsed

    parsed["serial_number"] = tokens[sn_idx + 1]
    tail = tokens[sn_idx + 2:]

    if tail and normalize_upper(tail[-1]) in refs["technicians"]:
        parsed["technician"] = normalize_upper(tail[-1])
        tail = tail[:-1]
    else:
        parsed["parse_notes"].append("Technician not confidently identified.")

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

    strong = [
        parsed["equipment_type"], parsed["model"], parsed["oem"],
        parsed["serial_number"], parsed["customer"]
    ]
    if all(strong) and parsed["site_code"] and parsed["technician"]:
        parsed["parse_confidence"] = "high"
    elif all(strong):
        parsed["parse_confidence"] = "medium"

    return parsed


def model_matches(parsed_model, requested_model):
    """
    Family matching:
      requested GB8 matches GB8, GB8-MT, GB8S, etc.
    Exact requested variants still work naturally.
    """
    if not requested_model:
        return True
    if not parsed_model:
        return False

    parsed = normalize_upper(parsed_model)
    requested = normalize_upper(requested_model)

    if parsed == requested:
        return True

    # Family-style prefix match only at a reasonable model boundary.
    if parsed.startswith(requested):
        remainder = parsed[len(requested):]
        if not remainder or remainder[0] in "-_/ " or remainder[0].isalpha():
            return True

    return False


def folder_matches_filters(meta, equipment_type=None, oem=None, model=None):
    if equipment_type and normalize_upper(meta.get("equipment_type") or "") != normalize_upper(equipment_type):
        return False
    if oem and normalize_upper(meta.get("oem") or "") != normalize_upper(oem):
        return False
    if model and not model_matches(meta.get("model"), model):
        return False
    return True


def discover_repair_folders(root, refs, equipment_type=None, oem=None, model=None, limit=None):
    """
    Survey immediate child directories only.

    This is intentional for the organized tech-scans root. It is fast and
    avoids recursively opening every repair folder during discovery.
    """
    root = Path(root).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("Traveler root does not exist or is not a directory: {}".format(root))

    matches = []
    total_dirs = 0
    parsed_dirs = 0

    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.is_symlink():
            continue

        total_dirs += 1
        meta = parse_repair_folder_name(child.name, refs)
        if meta.get("equipment_type") and meta.get("model") and meta.get("oem"):
            parsed_dirs += 1

        if folder_matches_filters(meta, equipment_type, oem, model):
            matches.append({
                "folder_name": child.name,
                "full_path": str(child),
                **meta,
            })
            if limit and len(matches) >= limit:
                break

    return {
        "surveyor_version": VERSION,
        "mode": "domain_discovery",
        "surveyed_at_utc": datetime.now(timezone.utc).isoformat(),
        "traveler_root": str(root),
        "filters": {
            "equipment_type": equipment_type,
            "oem": oem,
            "model": model,
            "limit": limit,
        },
        "summary": {
            "immediate_child_directories_seen": total_dirs,
            "parseable_repair_folders_seen": parsed_dirs,
            "matching_repair_folders": len(matches),
        },
        "repair_folders": matches,
    }


def extract_log_number(filename):
    m = PRIMARY_TRAVELER_RE.match(filename)
    if m:
        return m.group("log_number")
    m = LEADING_LOG_RE.match(filename)
    if m:
        return m.group("log_number")
    return None


def classify_evidence(path, source_root):
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

    if "schematic" in lower_path:
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

    if ext in SPREADSHEET_EXTENSIONS:
        result.update({
            "role": "structured_document",
            "confidence": "low",
            "reason": "Spreadsheet recognized but role is uncertain.",
        })
        return result

    if ext in DOCUMENT_EXTENSIONS:
        result.update({
            "role": "document",
            "confidence": "low",
            "reason": "Document type recognized but role is uncertain.",
        })
        return result

    if ext in PARAMETER_EXTENSIONS:
        result.update({
            "role": "technical_file",
            "confidence": "low",
            "reason": "Technical/configuration extension recognized; role uncertain.",
        })
        return result

    return result


def survey_repair_folder(source, refs, hash_files=False):
    source = Path(source).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise ValueError("Repair folder does not exist or is not a directory: {}".format(source))

    folder_metadata = parse_repair_folder_name(source.name, refs)

    files = []
    errors = []
    role_counts = Counter()
    ext_counts = Counter()
    hash_map = defaultdict(list)

    for path in sorted(source.rglob("*")):
        if path.is_dir() or path.is_symlink():
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
            errors.append({"relative_path": str(rel), "error": str(exc)})

    duplicate_groups = []
    if hash_files:
        for digest, paths in hash_map.items():
            if digest and len(paths) > 1:
                duplicate_groups.append({
                    "sha256": digest,
                    "paths": paths,
                    "count": len(paths),
                })

    current_travelers = [f for f in files if f["role"] == "current_traveler"]
    primary_logs = sorted(set(
        f["log_number"] for f in current_travelers if f["log_number"]
    ))

    return {
        "surveyor_version": VERSION,
        "mode": "repair_folder",
        "surveyed_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_folder": str(source),
        "source_folder_name": source.name,
        "hashing_enabled": bool(hash_files),
        "folder_metadata": folder_metadata,
        "summary": {
            "file_count": len(files),
            "error_count": len(errors),
            "primary_traveler_count": len(current_travelers),
            "primary_log_numbers": primary_logs,
            "role_counts": dict(sorted(role_counts.items())),
            "extension_counts": dict(sorted(ext_counts.items())),
            "duplicate_group_count": len(duplicate_groups),
        },
        "files": files,
        "duplicate_groups": duplicate_groups,
        "errors": errors,
    }


def safe_output_name(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "survey"


def write_discovery_reports(report, output_dir):
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "nova_domain_discovery.json"
    csv_path = output_dir / "nova_domain_discovery.csv"
    txt_path = output_dir / "nova_domain_discovery.txt"

    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    fields = [
        "folder_name", "full_path", "equipment_type", "model", "oem",
        "serial_number", "customer", "site_code", "site_name", "technician",
        "parse_confidence"
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["repair_folders"])

    s = report["summary"]
    filters = report["filters"]
    lines = [
        "NOVA DRL SURVEYOR v{} - TRAVELER DOMAIN DISCOVERY".format(VERSION),
        "=" * 72,
        "",
        "Traveler root: {}".format(report["traveler_root"]),
        "Type filter:    {}".format(filters.get("equipment_type") or "Any"),
        "OEM filter:     {}".format(filters.get("oem") or "Any"),
        "Model filter:   {}".format(filters.get("model") or "Any"),
        "",
        "Directories seen:       {}".format(s["immediate_child_directories_seen"]),
        "Parseable repair dirs:  {}".format(s["parseable_repair_folders_seen"]),
        "Matching repair dirs:   {}".format(s["matching_repair_folders"]),
        "",
        "MATCHES",
    ]

    for idx, item in enumerate(report["repair_folders"], 1):
        lines.append(
            "{:>4}. {} | {} | SN {} | {} | {} | {}".format(
                idx,
                item.get("model") or "?",
                item.get("oem") or "?",
                item.get("serial_number") or "?",
                item.get("customer") or "?",
                item.get("site_code") or "?",
                item.get("technician") or "?",
            )
        )
        lines.append("      {}".format(item["full_path"]))

    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return txt_path, json_path, csv_path


def write_repair_reports(report, output_dir):
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "nova_survey.json"
    csv_path = output_dir / "nova_survey_files.csv"
    txt_path = output_dir / "nova_survey_summary.txt"

    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    fields = [
        "relative_path", "filename", "extension", "size_bytes",
        "modified_time", "sha256", "role", "classification_confidence",
        "classification_reason", "log_number"
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["files"])

    meta = report["folder_metadata"]
    summary = report["summary"]

    lines = [
        "NOVA DRL SURVEYOR v{} - REPAIR FOLDER".format(VERSION),
        "=" * 60,
        "",
        "SOURCE",
        "  {}".format(report["source_folder"]),
        "",
        "PARSED FOLDER",
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
        "  Hashing enabled:      {}".format("YES" if report["hashing_enabled"] else "NO"),
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
    lines.extend(["", "FILES NEEDING HUMAN REVIEW", "  {}".format(len(uncertain))])
    for item in uncertain[:50]:
        lines.append(
            "  - [{} / {}] {}".format(
                item["role"], item["classification_confidence"], item["relative_path"]
            )
        )
    if len(uncertain) > 50:
        lines.append("  ... {} more".format(len(uncertain) - 50))

    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return txt_path, json_path, csv_path


def main():
    parser = argparse.ArgumentParser(
        description="Read-only Nova DRL Traveler Database Surveyor."
    )
    parser.add_argument(
        "source",
        help="Traveler root for --discover, or one repair folder for normal survey."
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Discover matching immediate child repair folders without opening their contents."
    )
    parser.add_argument("--type", dest="equipment_type", help="Equipment type filter, e.g. RBT.")
    parser.add_argument("--oem", help="OEM filter, e.g. GENMARK.")
    parser.add_argument("--model", help="Model/family filter, e.g. GB8.")
    parser.add_argument("--limit", type=int, help="Limit number of discovered matches.")
    parser.add_argument(
        "--hash",
        action="store_true",
        help="Calculate SHA-256 hashes during a repair-folder survey. Off by default for NAS speed."
    )
    parser.add_argument("--output", help="Local output directory.")
    parser.add_argument("--config", help="Config directory containing JSON references.")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()

    if args.config:
        config_dir = Path(args.config).expanduser().resolve()
    else:
        config_dir = Path(__file__).resolve().parent.parent / "config"

    refs = load_reference_config(config_dir)

    try:
        if args.discover:
            report = discover_repair_folders(
                source,
                refs,
                equipment_type=args.equipment_type,
                oem=args.oem,
                model=args.model,
                limit=args.limit,
            )

            if args.output:
                output_dir = Path(args.output).expanduser().resolve()
            else:
                label = "{}_{}_{}".format(
                    args.equipment_type or "ALL",
                    args.oem or "ALL",
                    args.model or "ALL",
                )
                output_dir = Path.cwd() / "output" / (
                    "discovery_" + safe_output_name(label)
                )

            txt_path, json_path, csv_path = write_discovery_reports(report, output_dir)

            print()
            print("Nova DRL Surveyor v{} - DOMAIN DISCOVERY".format(VERSION))
            print("=" * 58)
            print("Traveler root: {}".format(report["traveler_root"]))
            print("Type:          {}".format(args.equipment_type or "Any"))
            print("OEM:           {}".format(args.oem or "Any"))
            print("Model:         {}".format(args.model or "Any"))
            print("Matches:       {}".format(report["summary"]["matching_repair_folders"]))
            print()
            for item in report["repair_folders"][:20]:
                print("  {}".format(item["folder_name"]))
            if len(report["repair_folders"]) > 20:
                print("  ... {} more in report".format(len(report["repair_folders"]) - 20))
            print()
            print("Reports:")
            print("  {}".format(txt_path))
            print("  {}".format(json_path))
            print("  {}".format(csv_path))
            print()
            print("READ-ONLY COMPLETE: No DRL source files were changed.")
            return 0

        report = survey_repair_folder(source, refs, hash_files=args.hash)

        if args.output:
            output_dir = Path(args.output).expanduser().resolve()
        else:
            output_dir = Path.cwd() / "output" / safe_output_name(source.name)

        txt_path, json_path, csv_path = write_repair_reports(report, output_dir)
        meta = report["folder_metadata"]
        summary = report["summary"]

        print()
        print("Nova DRL Surveyor v{} - REPAIR FOLDER".format(VERSION))
        print("=" * 54)
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
        print("Log #:       {}".format(
            ", ".join(summary["primary_log_numbers"]) or "Not identified"
        ))
        print("Hashing:     {}".format("ON" if report["hashing_enabled"] else "OFF"))
        print()
        print("Reports:")
        print("  {}".format(txt_path))
        print("  {}".format(json_path))
        print("  {}".format(csv_path))
        print()
        print("READ-ONLY COMPLETE: No DRL source files were changed.")
        return 0

    except Exception as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
