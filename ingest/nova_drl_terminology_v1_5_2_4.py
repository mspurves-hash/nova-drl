#!/usr/bin/env python3
"""
Nova DRL Terminology Layer v1.5.2.4
=================================

Adds non-destructive DRL terminology annotations to human-approved repair
fields produced by Nova Evidence Fusion v1.5.1. v1.5.2.4 extends the frozen
v1.5.2.3 glossary with the human-confirmed shop term Blue Schmoo's.

Core rule:
    Preserve the approved wording exactly.
    Attach normalized meaning separately.

Example:
    Approved wording: "Added Flanges BERS x2 to A1 + A2 upper link"
    Annotation:       BERS -> bearings

Safety:
- Does not modify v1.5.1 output.
- Does not alter source traveler wording.
- Does not rewrite approved human wording.
- Does not infer unknown abbreviations.
- Does not write to Qdrant.
"""

import argparse
import copy
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.5.2.4"


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_") or "unknown"


def locate_approved_fields(source):
    source = Path(source).expanduser().resolve()
    candidates = []
    if source.is_file():
        candidates.append(source)
    else:
        candidates.append(source / "approved_repair_fields.json")

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            data = read_json(candidate)
            if "approved_fields" in data and "repair_identity" in data:
                return candidate, data

    raise ValueError(
        "approved_repair_fields.json was not found at {}".format(source)
    )


def default_glossary_path():
    return (
        Path(__file__).resolve().parents[1]
        / "config"
        / "drl_terminology_v1_5_2_4.json"
    )


def load_glossary(path):
    data = read_json(path)
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValueError("Glossary must contain an 'entries' list.")

    normalized = []
    for index, entry in enumerate(entries, start=1):
        raw = str(entry.get("raw_term") or "").strip()
        meaning = str(entry.get("normalized_meaning") or "").strip()
        if not raw or not meaning:
            raise ValueError(
                "Glossary entry {} requires raw_term and normalized_meaning.".format(index)
            )

        aliases = [raw] + [
            str(x).strip()
            for x in entry.get("aliases", [])
            if str(x).strip()
        ]

        normalized.append({
            **entry,
            "raw_term": raw,
            "normalized_meaning": meaning,
            "aliases": aliases,
            "preserve_raw": bool(entry.get("preserve_raw", True)),
            "status": entry.get("status") or "human_confirmed",
            "scope": entry.get("scope") or "DRL_shop",
        })

    return {
        **data,
        "entries": normalized,
    }


def flexible_term_pattern(term):
    """
    Literal phrase matching with:
    - case-insensitivity
    - straight/curly apostrophe equivalence
    - whitespace flexibility
    - alphanumeric word boundaries

    It does not use fuzzy matching; unknown technician shorthand remains unknown.
    """
    chars = []
    for ch in str(term):
        if ch in {"'", "’", "‘"}:
            chars.append(r"['’‘]?")
        elif ch.isspace():
            chars.append(r"\s+")
        else:
            chars.append(re.escape(ch))

    body = "".join(chars)
    return re.compile(
        r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def find_terminology_matches(value, glossary):
    value = str(value or "")
    matches = []
    seen = set()

    for entry in glossary.get("entries", []):
        best = None
        for alias in entry.get("aliases", []):
            pattern = flexible_term_pattern(alias)
            match = pattern.search(value)
            if not match:
                continue

            candidate = {
                "raw_text_in_value": value[match.start():match.end()],
                "matched_alias": alias,
                "raw_term": entry["raw_term"],
                "normalized_meaning": entry["normalized_meaning"],
                "category": entry.get("category"),
                "scope": entry.get("scope"),
                "status": entry.get("status"),
                "preserve_raw": entry.get("preserve_raw", True),
                "start": match.start(),
                "end": match.end(),
                "notes": entry.get("notes"),
            }
            if best is None or (candidate["end"] - candidate["start"]) > (
                best["end"] - best["start"]
            ):
                best = candidate

        if best:
            key = (
                best["start"],
                best["end"],
                best["normalized_meaning"].lower(),
            )
            if key not in seen:
                seen.add(key)
                matches.append(best)

    matches.sort(key=lambda row: (row["start"], row["end"]))
    return matches


def annotate_approved_fields(source_data, glossary):
    output = copy.deepcopy(source_data)
    approved = output.get("approved_fields", {})
    total_matches = 0
    matched_terms = []

    # Scalar approved fields.
    for field_name, record in approved.items():
        if field_name == "repair_actions":
            continue
        if not isinstance(record, dict):
            continue
        value = record.get("value")
        if not isinstance(value, str):
            continue

        matches = find_terminology_matches(value, glossary)
        record["terminology_annotations"] = matches
        total_matches += len(matches)
        matched_terms.extend(matches)

    # Item-level repair actions.
    actions = approved.get("repair_actions", [])
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, dict):
                continue
            value = action.get("value")
            if not isinstance(value, str):
                action["terminology_annotations"] = []
                continue

            matches = find_terminology_matches(value, glossary)
            action["terminology_annotations"] = matches
            total_matches += len(matches)
            matched_terms.extend(matches)

    distinct = []
    seen = set()
    for row in matched_terms:
        key = (
            row["raw_term"].lower(),
            row["normalized_meaning"].lower(),
        )
        if key not in seen:
            seen.add(key)
            distinct.append({
                "raw_term": row["raw_term"],
                "normalized_meaning": row["normalized_meaning"],
                "category": row.get("category"),
                "scope": row.get("scope"),
            })

    output["terminology_layer"] = {
        "version": VERSION,
        "annotated_at_utc": now_utc(),
        "glossary_version": glossary.get("glossary_version"),
        "match_count": total_matches,
        "distinct_term_count": len(distinct),
        "distinct_terms": distinct,
        "source_values_modified": False,
        "unknown_terms_inferred": False,
        "qdrant_entry_created": False,
    }
    output["fusion_version"] = source_data.get("fusion_version")
    output["qdrant_entry_created"] = False
    output["accepted_as_final_repair_summary"] = bool(
        source_data.get("accepted_as_final_repair_summary", False)
    )
    return output


def terminology_match_rows(enriched):
    rows = []
    approved = enriched.get("approved_fields", {})

    for field_name, record in approved.items():
        if field_name == "repair_actions":
            continue
        if not isinstance(record, dict):
            continue
        for match in record.get("terminology_annotations", []):
            rows.append({
                "field": field_name,
                "item": None,
                "approved_value": record.get("value"),
                **match,
            })

    for action in approved.get("repair_actions", []) or []:
        for match in action.get("terminology_annotations", []):
            rows.append({
                "field": "repair_actions",
                "item": action.get("action_number"),
                "action_id": action.get("action_id"),
                "approved_value": action.get("value"),
                **match,
            })
    return rows


def render_review(enriched, source_path, glossary_path):
    identity = enriched.get("repair_identity", {})
    layer = enriched.get("terminology_layer", {})
    rows = terminology_match_rows(enriched)

    lines = [
        "NOVA DRL TERMINOLOGY LAYER v{}".format(VERSION),
        "=" * 72,
        "Log: {}".format(identity.get("log_number")),
        "Model: {}".format(identity.get("model")),
        "Serial: {}".format(identity.get("serial_number")),
        "Source approved fields: {}".format(source_path),
        "Glossary: {}".format(glossary_path),
        "",
        "TERMINOLOGY MATCHES",
        "-------------------",
    ]

    if not rows:
        lines.append("None")
    else:
        for index, row in enumerate(rows, start=1):
            location = row["field"]
            if row.get("item") is not None:
                location += " action {}".format(row["item"])
            lines += [
                "{}. {}".format(index, location),
                "   Approved wording: {}".format(row.get("approved_value")),
                "   Raw term: {}".format(row.get("raw_term")),
                "   Text matched: {}".format(row.get("raw_text_in_value")),
                "   Normalized meaning: {}".format(
                    row.get("normalized_meaning")
                ),
                "   Category: {}".format(row.get("category")),
                "   Scope: {}".format(row.get("scope")),
                "   Preserve raw wording: {}".format(
                    "YES" if row.get("preserve_raw") else "NO"
                ),
                "",
            ]

    lines += [
        "STATUS",
        "------",
        "Terminology matches: {}".format(layer.get("match_count", 0)),
        "Distinct terms: {}".format(layer.get("distinct_term_count", 0)),
        "Approved wording modified: NO",
        "Unknown abbreviations inferred: NO",
        "Accepted as final repair summary: {}".format(
            "YES"
            if enriched.get("accepted_as_final_repair_summary")
            else "NO"
        ),
        "Qdrant entries created: 0",
    ]
    return "\n".join(lines) + "\n"


def default_output_dir(source_data):
    identity = source_data.get("repair_identity", {})
    folder = "_".join(
        safe_name(x)
        for x in [
            identity.get("equipment_type") or "UNK",
            identity.get("model") or "UNK",
            identity.get("oem") or "UNK",
            "SN",
            identity.get("serial_number") or "UNKNOWN",
            identity.get("customer") or "UNKNOWN",
        ]
    )
    return (
        Path.cwd()
        / "output"
        / "evidence_fusion_v1_5_2_4"
        / folder
        / "events"
        / str(identity.get("log_number") or "unknown")
    )


def main():
    parser = argparse.ArgumentParser(
        description="Nova DRL Terminology Layer v{}".format(VERSION)
    )
    parser.add_argument(
        "source",
        help=(
            "v1.5.1 event directory or approved_repair_fields.json"
        ),
    )
    parser.add_argument(
        "--glossary",
        default=str(default_glossary_path()),
    )
    parser.add_argument("--output-root")
    args = parser.parse_args()

    try:
        source_path, source_data = locate_approved_fields(args.source)
        glossary_path = Path(args.glossary).expanduser().resolve()
        glossary = load_glossary(glossary_path)
        enriched = annotate_approved_fields(source_data, glossary)

        output_dir = (
            Path(args.output_root).expanduser().resolve()
            if args.output_root
            else default_output_dir(source_data)
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        write_json(
            output_dir / "approved_repair_fields_with_terminology.json",
            enriched,
        )
        write_json(
            output_dir / "terminology_matches.json",
            {
                "terminology_version": VERSION,
                "repair_identity": enriched.get("repair_identity"),
                "matches": terminology_match_rows(enriched),
                "source_values_modified": False,
                "qdrant_entry_created": False,
            },
        )
        (output_dir / "terminology_review.txt").write_text(
            render_review(enriched, source_path, glossary_path),
            encoding="utf-8",
        )

    except Exception as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2

    layer = enriched.get("terminology_layer", {})
    print()
    print("Nova DRL Terminology Layer v{}".format(VERSION))
    print("=" * 64)
    print("Log:                       {}".format(
        enriched.get("repair_identity", {}).get("log_number")
    ))
    print("Approved field groups:     {}".format(
        enriched.get("approved_field_count", 0)
    ))
    print("Approved repair actions:   {}".format(
        enriched.get("approved_repair_action_count", 0)
    ))
    print("Terminology matches:       {}".format(
        layer.get("match_count", 0)
    ))
    print("Distinct DRL terms:        {}".format(
        layer.get("distinct_term_count", 0)
    ))
    print("Approved wording modified: NO")
    print("Qdrant entries created:    0")
    print()
    print("Reports: {}".format(output_dir))
    print("NO APPROVED SOURCE VALUES WERE MODIFIED.")
    print("NO QDRANT ENTRY CREATED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
