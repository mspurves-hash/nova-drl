#!/usr/bin/env python3
"""
Nova DRL Terminology Review Queue v1.5.2.2
==========================================

Noise-filtered, frequency-weighted terminology discovery for DERIVED Nova DRL
repair evidence.

v1.5.2.2 improvements:
- suppress common English words even when OCR capitalizes them;
- suppress known metadata identifiers such as site codes;
- read existing DRL site-code / technician / OEM configs when present;
- limit pure-alpha unknown candidates to acronym-like lengths;
- detect single-serial OCR/template repetition and reduce its priority;
- increase the value of serial diversity;
- preserve the v1.5.2.1 Define / Defer / Ignore workflow;
- no source-file or Qdrant modifications.
"""

import argparse
import collections
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.5.2.3"

ACRONYM_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{1,9})(?![A-Za-z0-9])"
)

FOLDER_RE = re.compile(
    r"(?P<equipment_type>[A-Z0-9]+)\s*-\s*"
    r"(?P<model>\S+)\s+"
    r"(?P<oem>\S+)\s+SN\s+"
    r"(?P<serial>\S+)",
    re.IGNORECASE,
)


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


def stable_id(*parts):
    joined = "\n".join(str(x or "") for x in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def default_glossary_path():
    return (
        Path(__file__).resolve().parents[1]
        / "config"
        / "drl_terminology_v1_5_2_3.json"
    )


def default_rules_path():
    return (
        Path(__file__).resolve().parents[1]
        / "config"
        / "terminology_queue_rules_v1_5_2_3.json"
    )


def default_metadata_path():
    return (
        Path(__file__).resolve().parents[1]
        / "config"
        / "drl_metadata_identifiers_v1_5_2_3.json"
    )


def load_json_or_empty(path):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return read_json(path)
    except Exception:
        return {}


def load_glossary(path):
    data = read_json(path)
    if not isinstance(data.get("entries", []), list):
        raise ValueError("Glossary must contain an entries list.")
    return data


def load_rules(path):
    return read_json(path)


def load_metadata(path):
    return load_json_or_empty(path)


def project_config_dir():
    return Path(__file__).resolve().parents[1] / "config"


def metadata_codes_from_project_config():
    """
    Extract only code-like identifiers from known project metadata configs.
    This intentionally avoids treating arbitrary uppercase text values as
    identifiers.
    """
    root = project_config_dir()
    output = set()
    sources = {}

    def add(value, source):
        value = str(value or "").strip().upper()
        if re.fullmatch(r"[A-Z0-9_-]{2,12}", value):
            output.add(value)
            sources.setdefault(value, set()).add(source)

    def visit(value, source, key_hint=None):
        if isinstance(value, dict):
            for key, child in value.items():
                # Mapping keys are frequently site/technician/OEM codes.
                add(key, source)
                visit(child, source, key_hint=str(key).lower())
        elif isinstance(value, list):
            for child in value:
                visit(child, source, key_hint=key_hint)
        elif isinstance(value, str):
            if key_hint in {
                "code", "site_code", "initials", "tech_initials",
                "technician_initials", "abbr", "abbreviation", "oem"
            }:
                add(value, source)

    for filename in ["site_codes.json", "technicians.json", "oems.json"]:
        path = root / filename
        data = load_json_or_empty(path)
        if data:
            visit(data, filename)

    return output, {
        code: sorted(srcs)
        for code, srcs in sorted(sources.items())
    }


def metadata_identifier_set(metadata):
    terms = set()
    meanings = {}

    for row in metadata.get("site_codes", []) or []:
        code = str(row.get("code") or "").strip().upper()
        if code:
            terms.add(code)
            meanings[code] = {
                "type": "site_code",
                "meaning": row.get("meaning"),
                "status": row.get("status"),
            }

    for value in metadata.get("fixed_identifiers", []) or []:
        term = str(value or "").strip().upper()
        if term:
            terms.add(term)
            meanings.setdefault(term, {"type": "fixed_identifier"})

    for value in metadata.get("technician_initials", []) or []:
        if isinstance(value, dict):
            term = str(value.get("initials") or "").strip().upper()
            if term:
                terms.add(term)
                meanings[term] = {
                    "type": "technician_initials",
                    "name": value.get("name"),
                    "status": value.get("status"),
                }
        else:
            term = str(value or "").strip().upper()
            if term:
                terms.add(term)
                meanings[term] = {"type": "technician_initials"}

    project_terms, project_sources = metadata_codes_from_project_config()
    terms.update(project_terms)
    for term in project_terms:
        meanings.setdefault(
            term,
            {
                "type": "project_config_identifier",
                "sources": project_sources.get(term, []),
            },
        )
    return terms, meanings


def flexible_term_pattern(term):
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


def known_term_match(text, glossary):
    matches = []
    seen = set()

    # Longest terms first so "FA RPT" is recognized as a compound phrase.
    entries = sorted(
        glossary.get("entries", []) or [],
        key=lambda entry: len(str(entry.get("raw_term") or "")),
        reverse=True,
    )

    for entry in entries:
        aliases = [entry.get("raw_term")] + list(entry.get("aliases", []) or [])
        for alias in aliases:
            alias = str(alias or "").strip()
            if not alias:
                continue
            for match in flexible_term_pattern(alias).finditer(str(text or "")):
                key = (
                    match.start(),
                    match.end(),
                    str(entry.get("raw_term")).casefold(),
                )
                if key in seen:
                    continue
                seen.add(key)
                matches.append({
                    "raw_text": str(text)[match.start():match.end()],
                    "raw_term": entry.get("raw_term"),
                    "normalized_meaning": entry.get("normalized_meaning"),
                    "category": entry.get("category"),
                    "scope": entry.get("scope"),
                    "status": entry.get("status"),
                    "start": match.start(),
                    "end": match.end(),
                })
    return matches


def dynamic_ignore_terms(meta):
    terms = set()
    for key in ["model", "oem", "equipment_type", "serial_number"]:
        value = str(meta.get(key) or "")
        for token in re.findall(r"[A-Z][A-Z0-9]{1,9}", value.upper()):
            terms.add(token)
    return terms


def candidate_terms(text, rules, meta, metadata_terms):
    config = rules.get("candidate_detection", {})
    ignored = {str(x).upper() for x in config.get("ignore_terms", [])}
    ignored.update(dynamic_ignore_terms(meta))
    common = {
        str(x).upper()
        for x in config.get("common_word_suppression", [])
    }
    ignore_patterns = [
        re.compile(pattern)
        for pattern in config.get("ignore_patterns", [])
    ]

    minimum = int(config.get("minimum_token_length", 2))
    max_alpha = int(config.get("maximum_alpha_length", 5))
    max_mixed = int(config.get("maximum_mixed_length", 10))

    candidates = []
    suppressed = []

    for match in ACRONYM_RE.finditer(str(text or "")):
        term = match.group(1)
        upper = term.upper()

        def suppress(reason):
            suppressed.append({
                "term": term,
                "reason": reason,
                "start": match.start(1),
                "end": match.end(1),
            })

        if len(term) < minimum:
            suppress("too_short")
            continue

        if term.isalpha() and len(term) > max_alpha:
            suppress("alpha_token_too_long_for_acronym")
            continue

        if not term.isalpha() and len(term) > max_mixed:
            suppress("mixed_token_too_long")
            continue

        if upper in common:
            suppress("common_english_word")
            continue

        if upper in metadata_terms:
            suppress("known_metadata_identifier")
            continue

        if upper in ignored:
            suppress("fixed_ignore_term")
            continue

        if any(pattern.fullmatch(term) for pattern in ignore_patterns):
            suppress("ignore_pattern")
            continue

        candidates.append({
            "term": term,
            "start": match.start(1),
            "end": match.end(1),
        })

    return candidates, suppressed


def parse_meta_from_source_path(source_path):
    source_path = str(source_path or "")
    parent = Path(source_path).parent.name
    match = FOLDER_RE.search(parent)
    if not match:
        return {}
    return {
        "equipment_type": match.group("equipment_type").upper(),
        "model": match.group("model"),
        "oem": match.group("oem").upper(),
        "serial_number": match.group("serial"),
    }


def decode_log_date(log_number):
    match = re.fullmatch(r"(\d{2})(\d{2})(\d{2})(\d{3})", str(log_number or ""))
    if not match:
        return None
    yy, mm, dd, _ = [int(x) for x in match.groups()]
    year = 2000 + yy if yy <= 69 else 1900 + yy
    if not 1 <= mm <= 12 or not 1 <= dd <= 31:
        return None
    return "{:04d}-{:02d}-{:02d}".format(year, mm, dd)


def event_meta(log_number, source_path=None, supplied=None):
    meta = dict(supplied or {})
    parsed = parse_meta_from_source_path(source_path)
    for key, value in parsed.items():
        meta.setdefault(key, value)
    meta["log_number"] = str(log_number or meta.get("log_number") or "")
    meta["repair_date"] = (
        meta.get("repair_date")
        or decode_log_date(meta.get("log_number"))
    )
    return meta


def compact_snippet(text, start, end, radius=90):
    raw = str(text or "")
    left = max(0, start - radius)
    right = min(len(raw), end + radius)
    return re.sub(r"\s+", " ", raw[left:right]).strip()


def context_signature(text, start, end, term, token_radius=7):
    raw = str(text or "")
    tokens = list(re.finditer(r"[A-Za-z0-9']+", raw))
    if not tokens:
        return ""

    indexes = [
        index
        for index, token in enumerate(tokens)
        if not (token.end() <= start or token.start() >= end)
    ]
    if not indexes:
        return ""

    first = max(0, indexes[0] - token_radius)
    last = min(len(tokens) - 1, indexes[-1] + token_radius)
    words = []
    for token in tokens[first:last + 1]:
        value = token.group(0).upper()
        if token.start() < end and token.end() > start:
            value = "<TERM>"
        elif value.isdigit():
            value = "<N>"
        words.append(value)
    return " ".join(words)


def occurrence_record(term, text, start, end, field, source_type, source_path, meta):
    event_key = "{}::{}".format(
        meta.get("serial_number") or "UNKNOWN_SERIAL",
        meta.get("log_number") or "UNKNOWN_LOG",
    )
    return {
        "occurrence_id": stable_id(
            term, event_key, field, source_type, source_path, start, end
        ),
        "term": term,
        "event_key": event_key,
        "log_number": meta.get("log_number"),
        "repair_date": meta.get("repair_date"),
        "equipment_type": meta.get("equipment_type"),
        "oem": meta.get("oem"),
        "model": meta.get("model"),
        "serial_number": meta.get("serial_number"),
        "field": field,
        "source_type": source_type,
        "source_path": str(source_path),
        "text_snippet": compact_snippet(text, start, end),
        "context_signature": context_signature(text, start, end, term),
    }


def extract_from_repair_entries(path, data):
    if data.get("detect_only") or data.get("status") != "ok":
        return []
    meta = event_meta(
        data.get("log_number"),
        source_path=data.get("source_path"),
    )
    rows = []
    for entry in data.get("entries", []) or []:
        fields = entry.get("literal_fields", {}) or {}
        if fields.get("description"):
            rows.append({
                "text": fields["description"],
                "field": "structured_repair_action",
                "source_type": "structured_traveler_reader",
                "source_path": path,
                "meta": meta,
            })
        if fields.get("notes"):
            rows.append({
                "text": fields["notes"],
                "field": "diagnostic_note",
                "source_type": "structured_traveler_reader",
                "source_path": path,
                "meta": meta,
            })
    return rows


def extract_from_traveler_regions(path, data):
    source_path = data.get("source_path")
    filename = Path(str(source_path or "")).name
    log_match = re.match(r"(\d{9})", filename)
    log_number = log_match.group(1) if log_match else None
    meta = event_meta(log_number, source_path=source_path)
    regions = data.get("regions", {}) or {}
    rows = []

    special = regions.get("special_notes", {}) or {}
    if special.get("selected_text"):
        rows.append({
            "text": special["selected_text"],
            "field": "special_notes_ocr",
            "source_type": "traveler_region_ocr",
            "source_path": path,
            "meta": meta,
        })

    repairs = regions.get("repairs_replacements", {}) or {}
    if repairs.get("selected_text"):
        rows.append({
            "text": repairs["selected_text"],
            "field": "repairs_region_ocr",
            "source_type": "traveler_region_ocr",
            "source_path": path,
            "meta": meta,
        })
    return rows


def extract_from_approved_fields(path, data):
    identity = data.get("repair_identity", {}) or {}
    meta = event_meta(
        identity.get("log_number"),
        supplied={
            "repair_date": identity.get("repair_date"),
            "equipment_type": identity.get("equipment_type"),
            "oem": identity.get("oem"),
            "model": identity.get("model"),
            "serial_number": identity.get("serial_number"),
        },
    )
    approved = data.get("approved_fields", {}) or {}
    rows = []

    complaint = approved.get("customer_complaint")
    if isinstance(complaint, dict) and complaint.get("value"):
        rows.append({
            "text": complaint["value"],
            "field": "approved_customer_complaint",
            "source_type": "human_approved_field",
            "source_path": path,
            "meta": meta,
        })

    actions = approved.get("repair_actions", []) or []
    if isinstance(actions, list):
        for action in actions:
            if isinstance(action, dict) and action.get("value"):
                rows.append({
                    "text": action["value"],
                    "field": "approved_repair_action",
                    "source_type": "human_approved_field",
                    "source_path": path,
                    "meta": meta,
                })
    return rows



def evidence_event_key(row):
    meta = row.get("meta", {}) or {}
    return "{}::{}".format(
        meta.get("serial_number") or "UNKNOWN_SERIAL",
        meta.get("log_number") or "UNKNOWN_LOG",
    )


def apply_authority_shadowing(evidence_rows, rules):
    """
    For terminology discovery only, human-approved repair-action wording
    shadows lower-authority machine/OCR repair-action wording from the same
    repair event.

    Diagnostic notes and Special Notes remain independent evidence because
    they can contain information not represented by the approved action.
    """
    config = rules.get("authority_shadowing", {})
    if not config.get("enabled", False):
        return list(evidence_rows), []

    shadow_fields = set(
        config.get(
            "human_approved_repair_action_shadows",
            ["structured_repair_action", "repairs_region_ocr"],
        )
    )

    approved_events = {
        evidence_event_key(row)
        for row in evidence_rows
        if row.get("field") == "approved_repair_action"
    }

    kept = []
    shadowed = []
    for row in evidence_rows:
        event_key = evidence_event_key(row)
        if (
            event_key in approved_events
            and row.get("field") in shadow_fields
        ):
            shadowed.append({
                "event_key": event_key,
                "log_number": (row.get("meta") or {}).get("log_number"),
                "serial_number": (row.get("meta") or {}).get("serial_number"),
                "field": row.get("field"),
                "source_type": row.get("source_type"),
                "source_path": str(row.get("source_path")),
                "text": str(row.get("text") or ""),
                "shadowed_by": "human_approved_repair_action",
                "reason": (
                    "human-approved repair wording has higher authority for "
                    "terminology discovery"
                ),
            })
        else:
            kept.append(row)

    return kept, shadowed


def discover_evidence(scan_roots):
    seen_files = set()
    rows = []
    stats = collections.Counter()

    for root_value in scan_roots:
        root = Path(root_value).expanduser().resolve()
        if not root.exists():
            continue

        files = [root] if root.is_file() else root.rglob("*.json")

        for path in files:
            resolved = str(path.resolve())
            if resolved in seen_files:
                continue

            name = path.name.lower()
            relevant = (
                name == "traveler_regions.json"
                or name == "repair_entries_v1_3_4_4.json"
                or name in {
                    "approved_repair_fields.json",
                    "approved_repair_fields_with_terminology.json",
                    "approved_repair_fields_with_parts.json",
                }
            )
            if not relevant:
                continue

            seen_files.add(resolved)
            try:
                data = read_json(path)
            except Exception:
                stats["json_read_errors"] += 1
                continue

            stats["evidence_files"] += 1
            if name == "traveler_regions.json":
                rows.extend(extract_from_traveler_regions(path, data))
                stats["traveler_region_files"] += 1
            elif name == "repair_entries_v1_3_4_4.json":
                rows.extend(extract_from_repair_entries(path, data))
                stats["repair_entry_files"] += 1
            else:
                rows.extend(extract_from_approved_fields(path, data))
                stats["approved_field_files"] += 1

    stats["evidence_rows"] = len(rows)
    return rows, dict(stats)


def glossary_keys(glossary):
    keys = {}
    for entry in glossary.get("entries", []) or []:
        aliases = [entry.get("raw_term")] + list(entry.get("aliases", []) or [])
        for alias in aliases:
            alias = str(alias or "").strip()
            if alias:
                keys[alias.casefold()] = entry
    return keys


def build_occurrences(evidence_rows, glossary, rules, metadata_terms):
    unresolved = []
    known = []
    suppressions = []
    known_keys = glossary_keys(glossary)

    for row in evidence_rows:
        text = str(row.get("text") or "")
        meta = row.get("meta", {}) or {}

        for match in known_term_match(text, glossary):
            known.append(
                occurrence_record(
                    match["raw_term"],
                    text,
                    match["start"],
                    match["end"],
                    row["field"],
                    row["source_type"],
                    row["source_path"],
                    meta,
                ) | {
                    "normalized_meaning": match.get("normalized_meaning"),
                    "known_scope": match.get("scope"),
                    "known_category": match.get("category"),
                }
            )

        candidates, suppressed = candidate_terms(
            text, rules, meta, metadata_terms
        )

        for item in suppressed:
            suppressions.append({
                "term": item["term"],
                "reason": item["reason"],
                "field": row["field"],
                "source_path": str(row["source_path"]),
                "log_number": meta.get("log_number"),
                "serial_number": meta.get("serial_number"),
            })

        for token in candidates:
            term = token["term"]
            if term.casefold() in known_keys:
                continue
            unresolved.append(
                occurrence_record(
                    term,
                    text,
                    token["start"],
                    token["end"],
                    row["field"],
                    row["source_type"],
                    row["source_path"],
                    meta,
                )
            )
    return unresolved, known, suppressions


def year_from_occurrence(row):
    date = str(row.get("repair_date") or "")
    match = re.match(r"(\d{4})", date)
    return int(match.group(1)) if match else None


def top_concentration(values):
    counter = collections.Counter(
        str(value) for value in values if value not in (None, "")
    )
    total = sum(counter.values())
    if not total:
        return None, 0.0, counter
    top_value, top_count = counter.most_common(1)[0]
    return top_value, top_count / total, counter


def scope_suggestion(rows):
    event_rows = {}
    for row in rows:
        event_rows.setdefault(row["event_key"], row)
    unique_rows = list(event_rows.values())

    if len(unique_rows) < 3:
        return {
            "status": "insufficient_evidence",
            "suggested_scope": None,
            "reason": "fewer_than_3_unique_repair_events",
        }

    top_oem, oem_share, oem_counter = top_concentration(
        row.get("oem") for row in unique_rows
    )
    top_model, model_share, _ = top_concentration(
        row.get("model") for row in unique_rows
    )

    if top_oem and top_model and model_share >= 0.85:
        return {
            "status": "suggested",
            "suggested_scope": "OEM={};model={}".format(
                top_oem, top_model
            ),
            "reason": "model_concentration",
            "model_share": round(model_share, 3),
            "oem_share": round(oem_share, 3),
        }

    if top_oem and oem_share >= 0.85:
        return {
            "status": "suggested",
            "suggested_scope": "OEM={}".format(top_oem),
            "reason": "oem_concentration",
            "oem_share": round(oem_share, 3),
        }

    if len(oem_counter) >= 2 and oem_share < 0.65:
        return {
            "status": "suggested",
            "suggested_scope": "DRL_shop",
            "reason": "cross_oem_usage",
            "oem_share": round(oem_share, 3),
        }

    return {
        "status": "insufficient_evidence",
        "suggested_scope": None,
        "reason": "scope_not_concentrated_enough",
    }


def template_repetition_analysis(rows, rules):
    config = rules.get("template_detection", {})
    min_events = int(config.get("minimum_unique_events", 3))
    ocr_fields = set(config.get("single_serial_ocr_fields", []))
    threshold = float(
        config.get("context_signature_dominance_threshold", 0.65)
    )

    events = {row["event_key"] for row in rows}
    serials = {
        str(row.get("serial_number"))
        for row in rows if row.get("serial_number")
    }
    fields = {row["field"] for row in rows}

    signatures = collections.Counter(
        row.get("context_signature")
        for row in rows
        if row.get("context_signature")
    )
    signature_total = sum(signatures.values())
    top_signature = None
    dominance = 0.0
    if signatures:
        top_signature, top_count = signatures.most_common(1)[0]
        dominance = top_count / max(1, signature_total)

    single_serial_ocr_template = (
        len(events) >= min_events
        and len(serials) <= 1
        and fields
        and fields.issubset(ocr_fields)
    )
    repeated_context = (
        len(events) >= min_events
        and dominance >= threshold
        and len(serials) <= 2
    )
    template_like = single_serial_ocr_template or repeated_context

    reason = None
    if single_serial_ocr_template:
        reason = "single_serial_repeated_ocr_field"
    elif repeated_context:
        reason = "dominant_repeated_context_signature"

    return {
        "template_like": template_like,
        "reason": reason,
        "context_signature_dominance": round(dominance, 3),
        "dominant_context_signature": top_signature,
        "unique_serials": len(serials),
        "unique_events": len(events),
    }


def acronym_shape_bonus(term, rules):
    formula = rules.get("priority_formula", {})
    length = len(str(term))
    if length in (2, 3):
        return int(formula.get("acronym_shape_bonus_len_2_3", 0))
    if length == 4:
        return int(formula.get("acronym_shape_bonus_len_4", 0))
    if length == 5:
        return int(formula.get("acronym_shape_bonus_len_5", 0))
    return 0


def aggregate_term(term, rows, rules):
    field_weights = rules.get("field_weights", {})
    formula = rules.get("priority_formula", {})
    label_rules = rules.get("priority_labels", {})
    intervention = rules.get("human_intervention", {})

    unique_events = sorted({row["event_key"] for row in rows})
    unique_serials = sorted({
        str(row.get("serial_number"))
        for row in rows if row.get("serial_number")
    })
    unique_models = sorted({
        str(row.get("model"))
        for row in rows if row.get("model")
    })
    unique_oems = sorted({
        str(row.get("oem"))
        for row in rows if row.get("oem")
    })

    years = sorted({
        year for year in (year_from_occurrence(row) for row in rows)
        if year is not None
    })
    year_span = max(years) - min(years) if len(years) >= 2 else 0

    event_field_keys = {}
    for row in rows:
        key = (row["event_key"], row["field"])
        event_field_keys[key] = max(
            event_field_keys.get(key, 0),
            int(field_weights.get(row["field"], 1)),
        )
    field_weight_score = sum(event_field_keys.values())

    raw_cap_per_event = int(
        formula.get("raw_occurrence_cap_per_event", 2)
    )
    capped_raw_count = min(
        len(rows),
        len(unique_events) * raw_cap_per_event,
    )

    score = (
        len(unique_events)
        * int(formula.get("unique_repair_event_weight", 11))
        + len(unique_serials)
        * int(formula.get("unique_serial_weight", 7))
        + len(unique_models)
        * int(formula.get("unique_model_weight", 3))
        + min(year_span, 20)
        * int(formula.get("year_span_weight", 1))
        + capped_raw_count
        * int(formula.get("raw_occurrence_weight", 1))
        + field_weight_score
        + acronym_shape_bonus(term, rules)
    )

    ocr_only = all(
        row.get("source_type") == "traveler_region_ocr"
        for row in rows
    )
    if ocr_only:
        score -= int(formula.get("ocr_only_penalty", 10))

    template = template_repetition_analysis(rows, rules)
    template_penalty = 0
    if template["template_like"]:
        per_extra = int(
            formula.get(
                "single_serial_template_penalty_per_extra_event", 12
            )
        )
        cap = int(
            formula.get("single_serial_template_penalty_cap", 120)
        )
        template_penalty = min(
            cap,
            max(0, len(unique_events) - 1) * per_extra,
        )
        score -= template_penalty

    score = max(0, int(score))

    high = (
        len(unique_events) >= int(label_rules.get("high_unique_events", 12))
        or score >= int(label_rules.get("high_score", 125))
    )
    medium = (
        len(unique_events) >= int(label_rules.get("medium_unique_events", 3))
        or score >= int(label_rules.get("medium_score", 45))
    )
    label = "HIGH" if high else ("MEDIUM" if medium else "LOW")

    consequential_fields = set(
        intervention.get("consequential_fields", [])
    )
    appears_in_consequential = any(
        row["field"] in consequential_fields for row in rows
    )
    recommendation = (
        "ask_now"
        if label == "HIGH" and appears_in_consequential
        else "queue"
    )

    field_counts = collections.Counter(row["field"] for row in rows)

    examples = []
    seen = set()
    for row in sorted(
        rows,
        key=lambda r: (
            -int(field_weights.get(r["field"], 1)),
            r.get("repair_date") or "",
        ),
    ):
        key = (row["event_key"], row["field"], row["text_snippet"])
        if key in seen:
            continue
        seen.add(key)
        examples.append({
            "log_number": row.get("log_number"),
            "serial_number": row.get("serial_number"),
            "oem": row.get("oem"),
            "model": row.get("model"),
            "field": row.get("field"),
            "snippet": row.get("text_snippet"),
            "source_path": row.get("source_path"),
        })
        if len(examples) >= 5:
            break

    return {
        "term": term,
        "status": "unresolved",
        "raw_occurrences": len(rows),
        "capped_raw_occurrences_for_priority": capped_raw_count,
        "unique_repair_events": len(unique_events),
        "unique_serial_numbers": len(unique_serials),
        "unique_models": len(unique_models),
        "unique_oems": len(unique_oems),
        "models": unique_models,
        "oems": unique_oems,
        "first_year": min(years) if years else None,
        "last_year": max(years) if years else None,
        "year_span": year_span,
        "field_counts": dict(sorted(field_counts.items())),
        "priority_score": score,
        "priority": label,
        "ocr_only": ocr_only,
        "template_repetition": template,
        "template_priority_penalty": template_penalty,
        "appears_in_consequential_field": appears_in_consequential,
        "intervention_recommendation": recommendation,
        "scope_suggestion": scope_suggestion(rows),
        "examples": examples,
        "occurrence_ids": [row["occurrence_id"] for row in rows],
    }


def aggregate_known(known_rows):
    grouped = collections.defaultdict(list)
    for row in known_rows:
        grouped[str(row["term"])].append(row)

    output = []
    for term, rows in grouped.items():
        output.append({
            "term": term,
            "normalized_meaning": rows[0].get("normalized_meaning"),
            "scope": rows[0].get("known_scope"),
            "category": rows[0].get("known_category"),
            "raw_occurrences": len(rows),
            "unique_repair_events": len({row["event_key"] for row in rows}),
            "unique_serial_numbers": len({
                row.get("serial_number")
                for row in rows if row.get("serial_number")
            }),
        })
    return sorted(
        output,
        key=lambda row: (
            -row["unique_repair_events"],
            row["term"].casefold(),
        ),
    )


def suppression_summary(suppressions):
    by_reason = collections.Counter(
        row["reason"] for row in suppressions
    )
    examples = {}
    for row in suppressions:
        examples.setdefault(row["reason"], [])
        if len(examples[row["reason"]]) < 8:
            examples[row["reason"]].append({
                "term": row["term"],
                "log_number": row.get("log_number"),
                "serial_number": row.get("serial_number"),
                "field": row.get("field"),
            })
    return {
        "suppressed_occurrence_count": len(suppressions),
        "counts_by_reason": dict(sorted(by_reason.items())),
        "examples_by_reason": examples,
    }


def decision_path(output_dir):
    return Path(output_dir) / "terminology_review_decisions.json"


def load_decisions(output_dir):
    path = decision_path(output_dir)
    if not path.exists():
        return []
    try:
        data = read_json(path)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def latest_decisions(decisions):
    latest = {}
    for row in decisions:
        term = str(row.get("term") or "").casefold()
        if term:
            latest[term] = row
    return latest


def apply_decisions(queue_rows, decisions):
    latest = latest_decisions(decisions)
    for row in queue_rows:
        decision = latest.get(row["term"].casefold())
        if not decision:
            continue
        action = decision.get("decision")
        row["status"] = action
        row["human_review"] = {
            "decision": action,
            "reviewer": decision.get("reviewer"),
            "reviewed_at_utc": decision.get("reviewed_at_utc"),
            "meaning": decision.get("meaning"),
            "scope": decision.get("scope"),
            "category": decision.get("category"),
            "note": decision.get("note"),
            "decision_id": decision.get("decision_id"),
        }
        if action in {"defined", "ignored"}:
            row["intervention_recommendation"] = "none"
        elif action == "deferred":
            row["intervention_recommendation"] = "queue"
    return queue_rows


def record_decision(
    output_dir,
    term,
    decision,
    reviewer,
    meaning=None,
    scope=None,
    category=None,
    note=None,
):
    if not term or not str(term).strip():
        raise ValueError("--term is required.")
    if not reviewer or not str(reviewer).strip():
        raise ValueError("--reviewer is required.")

    decision = str(decision).strip().lower()
    if decision not in {"define", "defer", "ignore"}:
        raise ValueError("Decision must be define, defer, or ignore.")

    if decision == "define":
        if not meaning or not str(meaning).strip():
            raise ValueError("--meaning is required for --decision=define.")
        stored = "defined"
    elif decision == "defer":
        stored = "deferred"
    else:
        stored = "ignored"

    record = {
        "decision_id": stable_id(
            term, stored, reviewer, now_utc(),
            meaning, scope, category, note
        ),
        "term": str(term).strip(),
        "decision": stored,
        "reviewer": str(reviewer).strip(),
        "reviewed_at_utc": now_utc(),
        "meaning": str(meaning).strip() if meaning else None,
        "scope": str(scope).strip() if scope else None,
        "category": str(category).strip() if category else None,
        "note": note,
        "queue_version": VERSION,
        "source_modified": False,
        "qdrant_entry_created": False,
    }

    decisions = load_decisions(output_dir)
    decisions.append(record)
    write_json(decision_path(output_dir), decisions)
    return record


def effective_glossary(base_glossary, decisions):
    result = json.loads(json.dumps(base_glossary))
    result["glossary_version"] = "{}+review_queue".format(
        base_glossary.get("glossary_version", VERSION)
    )
    entries = list(result.get("entries", []) or [])

    existing = {
        str(entry.get("raw_term") or "").casefold(): index
        for index, entry in enumerate(entries)
        if entry.get("raw_term")
    }

    for decision in decisions:
        if decision.get("decision") != "defined":
            continue
        term = str(decision.get("term") or "").strip()
        if not term:
            continue

        entry = {
            "raw_term": term,
            "normalized_meaning": decision.get("meaning"),
            "category": decision.get("category") or "human_defined_term",
            "scope": decision.get("scope") or "DRL_shop",
            "status": "human_confirmed",
            "preserve_raw": True,
            "aliases": [],
            "notes": decision.get("note"),
            "definition_decision_id": decision.get("decision_id"),
            "defined_by": decision.get("reviewer"),
            "defined_at_utc": decision.get("reviewed_at_utc"),
        }

        key = term.casefold()
        if key in existing:
            entries[existing[key]] = entry
        else:
            existing[key] = len(entries)
            entries.append(entry)

    result["entries"] = entries
    result["generated_by"] = (
        "nova_terminology_review_queue_v1_5_2_3"
    )
    result["source_modified"] = False
    result["qdrant_entry_created"] = False
    return result



def apply_low_support_gates(grouped, rules):
    """
    Suppress short unresolved OCR-only fragments unless they repeat across
    enough unique repair events.

    Structured/human-approved occurrences are never removed by this gate.
    """
    config = rules.get("low_support_gates", {})
    minimum_two = int(
        config.get("two_char_ocr_only_min_unique_events", 3)
    )
    minimum_three = int(
        config.get("three_char_ocr_only_min_unique_events", 2)
    )

    kept = {}
    suppressions = []

    for term, rows in grouped.items():
        length = len(str(term))
        ocr_only = bool(rows) and all(
            row.get("source_type") == "traveler_region_ocr"
            for row in rows
        )
        unique_events = len({row.get("event_key") for row in rows})

        required = None
        if ocr_only and length == 2:
            required = minimum_two
        elif ocr_only and length == 3:
            required = minimum_three

        if required is not None and unique_events < required:
            suppressions.append({
                "term": term,
                "reason": (
                    "low_support_{}_char_ocr_fragment".format(length)
                ),
                "unique_repair_events": unique_events,
                "required_unique_repair_events": required,
                "raw_occurrences": len(rows),
                "source_paths": sorted({
                    str(row.get("source_path"))
                    for row in rows
                }),
            })
            continue

        kept[term] = rows

    return kept, suppressions


def merge_suppression_summary(base_summary, low_support):
    result = json.loads(json.dumps(base_summary))
    counts = result.setdefault("counts_by_reason", {})
    examples = result.setdefault("examples_by_reason", {})

    result["suppressed_occurrence_count"] = int(
        result.get("suppressed_occurrence_count", 0)
    ) + sum(
        int(row.get("raw_occurrences", 0))
        for row in low_support
    )

    for row in low_support:
        reason = row["reason"]
        counts[reason] = counts.get(reason, 0) + int(
            row.get("raw_occurrences", 0)
        )
        examples.setdefault(reason, [])
        if len(examples[reason]) < 8:
            examples[reason].append({
                "term": row.get("term"),
                "unique_repair_events": row.get(
                    "unique_repair_events"
                ),
            })
    return result


def build_queue(
    scan_roots, glossary, rules, metadata_terms, decisions
):
    evidence_rows_raw, scan_stats = discover_evidence(scan_roots)
    evidence_rows, shadowed_evidence = apply_authority_shadowing(
        evidence_rows_raw, rules
    )
    scan_stats["evidence_rows_before_authority_shadowing"] = len(
        evidence_rows_raw
    )
    scan_stats["evidence_rows_after_authority_shadowing"] = len(
        evidence_rows
    )
    scan_stats["authority_shadowed_rows"] = len(shadowed_evidence)

    unresolved_occ, known_occ, suppressions = build_occurrences(
        evidence_rows, glossary, rules, metadata_terms
    )

    grouped = collections.defaultdict(list)
    for row in unresolved_occ:
        grouped[row["term"].upper()].append(row)

    grouped, low_support_suppressions = apply_low_support_gates(
        grouped, rules
    )

    queue_rows = [
        aggregate_term(term, rows, rules)
        for term, rows in grouped.items()
    ]
    queue_rows = apply_decisions(queue_rows, decisions)
    queue_rows.sort(
        key=lambda row: (
            {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(
                row["priority"], 3
            ),
            -row["priority_score"],
            -row["unique_repair_events"],
            row["term"],
        )
    )

    known_usage = aggregate_known(known_occ)
    suppression = merge_suppression_summary(
        suppression_summary(suppressions),
        low_support_suppressions,
    )

    summary = {
        "queue_version": VERSION,
        "generated_at_utc": now_utc(),
        "scan_roots": [str(Path(x).expanduser()) for x in scan_roots],
        "scan_stats": scan_stats,
        "unresolved_term_count": sum(
            1 for row in queue_rows if row["status"] == "unresolved"
        ),
        "high_priority_unresolved_count": sum(
            1 for row in queue_rows
            if row["status"] == "unresolved"
            and row["priority"] == "HIGH"
        ),
        "ask_now_count": sum(
            1 for row in queue_rows
            if row["status"] == "unresolved"
            and row["intervention_recommendation"] == "ask_now"
        ),
        "deferred_count": sum(
            1 for row in queue_rows if row["status"] == "deferred"
        ),
        "ignored_count": sum(
            1 for row in queue_rows if row["status"] == "ignored"
        ),
        "defined_count": sum(
            1 for row in queue_rows if row["status"] == "defined"
        ),
        "known_term_count": len(known_usage),
        "known_term_usage": known_usage,
        "suppression": suppression,
        "authority_shadowing": {
            "shadowed_evidence_row_count": len(shadowed_evidence),
            "shadowed_fields": dict(
                collections.Counter(
                    row.get("field") for row in shadowed_evidence
                )
            ),
        },
        "low_support_suppression_count": len(
            low_support_suppressions
        ),
        "source_modified": False,
        "qdrant_entry_created": False,
    }
    return (
        queue_rows,
        unresolved_occ,
        known_usage,
        suppressions,
        shadowed_evidence,
        low_support_suppressions,
        summary,
    )


def render_queue(queue_rows, summary):
    lines = [
        "NOVA DRL TERMINOLOGY REVIEW QUEUE v{}".format(VERSION),
        "=" * 78,
        "Evidence files scanned: {}".format(
            summary.get("scan_stats", {}).get("evidence_files", 0)
        ),
        "Evidence rows examined: {}".format(
            summary.get("scan_stats", {}).get("evidence_rows", 0)
        ),
        "Unresolved terms: {}".format(
            summary.get("unresolved_term_count", 0)
        ),
        "High-priority unresolved: {}".format(
            summary.get("high_priority_unresolved_count", 0)
        ),
        "Recommended human interventions now: {}".format(
            summary.get("ask_now_count", 0)
        ),
        "Known terms observed: {}".format(
            summary.get("known_term_count", 0)
        ),
        "Noise/metadata occurrences suppressed: {}".format(
            summary.get("suppression", {}).get(
                "suppressed_occurrence_count", 0
            )
        ),
        "",
        "SUPPRESSION SUMMARY",
        "-------------------",
    ]

    counts = (
        summary.get("suppression", {}).get("counts_by_reason", {})
    )
    if not counts:
        lines.append("None")
    else:
        for reason, count in counts.items():
            lines.append("- {}: {}".format(reason, count))

    lines += ["", "PRIORITIZED QUEUE", "-----------------"]

    unresolved = [
        row for row in queue_rows if row["status"] == "unresolved"
    ]
    if not unresolved:
        lines.append("No unresolved acronym-like terms found.")
        lines.append("")
    else:
        for index, row in enumerate(queue_rows, start=1):
            lines += [
                "{}. {}  [{} | score {} | status {}]".format(
                    index,
                    row["term"],
                    row["priority"],
                    row["priority_score"],
                    row["status"],
                ),
                "   Unique repair events: {}".format(
                    row["unique_repair_events"]
                ),
                "   Raw occurrences: {}".format(row["raw_occurrences"]),
                "   Unique serials: {}".format(
                    row["unique_serial_numbers"]
                ),
                "   Models: {}".format(
                    ", ".join(row["models"]) or "unknown"
                ),
                "   OEMs: {}".format(
                    ", ".join(row["oems"]) or "unknown"
                ),
                "   Fields: {}".format(row["field_counts"]),
                "   First/last year: {} / {}".format(
                    row["first_year"], row["last_year"]
                ),
                "   Template repetition: {}{}".format(
                    "YES" if row["template_repetition"]["template_like"]
                    else "NO",
                    (
                        " (penalty {})".format(
                            row["template_priority_penalty"]
                        )
                        if row["template_priority_penalty"]
                        else ""
                    ),
                ),
                "   Intervention: {}".format(
                    row["intervention_recommendation"]
                ),
                "   Suggested scope: {}".format(
                    row["scope_suggestion"].get("suggested_scope")
                    or row["scope_suggestion"].get("reason")
                ),
            ]
            for example in row.get("examples", [])[:3]:
                lines.append(
                    "   Example [{} {} {}]: {}".format(
                        example.get("log_number"),
                        example.get("model"),
                        example.get("field"),
                        example.get("snippet"),
                    )
                )
            lines.append("")

    lines += ["KNOWN TERM USAGE", "----------------"]
    known = summary.get("known_term_usage", [])
    if not known:
        lines.append("None observed.")
    else:
        for row in known:
            lines.append(
                "- {} -> {} | events={} serials={} | scope={}".format(
                    row.get("term"),
                    row.get("normalized_meaning"),
                    row.get("unique_repair_events"),
                    row.get("unique_serial_numbers"),
                    row.get("scope"),
                )
            )

    lines += [
        "",
        "STATUS",
        "------",
        "Processing interruption policy: queue by default",
        "Only HIGH-priority consequential unknowns are marked ask_now.",
        "Common English words suppressed: YES",
        "Known metadata identifiers suppressed: YES",
        "Technician initials suppressed as metadata: YES",
        "Human-approved repair wording shadows lower-authority machine wording: YES",
        "Low-support short OCR fragments gated: YES",
        "Single-serial repeated OCR/template penalty: YES",
        "Definitions modify derived glossary only: YES",
        "DRL source files modified: NO",
        "Qdrant entries created: 0",
    ]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Nova DRL Terminology Review Queue v{}".format(
            VERSION
        )
    )
    parser.add_argument("scan_roots", nargs="+")
    parser.add_argument("--glossary", default=str(default_glossary_path()))
    parser.add_argument("--rules", default=str(default_rules_path()))
    parser.add_argument(
        "--metadata-identifiers",
        default=str(default_metadata_path()),
    )
    parser.add_argument(
        "--output-root",
        default=str(
            Path.cwd()
            / "output"
            / "terminology_review_queue_v1_5_2_3"
        ),
    )
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument(
        "--decision",
        choices=["define", "defer", "ignore"],
    )
    parser.add_argument("--term")
    parser.add_argument("--meaning")
    parser.add_argument("--scope")
    parser.add_argument("--category")
    parser.add_argument("--reviewer")
    parser.add_argument("--note")
    args = parser.parse_args()

    try:
        glossary = load_glossary(args.glossary)
        rules = load_rules(args.rules)
        metadata = load_metadata(args.metadata_identifiers)
        metadata_terms, metadata_meanings = metadata_identifier_set(
            metadata
        )
        output_dir = Path(args.output_root).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        decision_record = None
        if args.decision:
            decision_record = record_decision(
                output_dir,
                term=args.term,
                decision=args.decision,
                reviewer=args.reviewer,
                meaning=args.meaning,
                scope=args.scope,
                category=args.category,
                note=args.note,
            )

        decisions = load_decisions(output_dir)
        (
            queue_rows,
            occurrences,
            known_usage,
            suppressions,
            shadowed_evidence,
            low_support_suppressions,
            summary,
        ) = build_queue(
            args.scan_roots,
            glossary,
            rules,
            metadata_terms,
            decisions,
        )

        write_json(
            output_dir / "terminology_review_queue.json",
            {
                "queue_version": VERSION,
                "summary": summary,
                "terms": queue_rows,
                "source_modified": False,
                "qdrant_entry_created": False,
            },
        )
        write_json(
            output_dir / "terminology_occurrences.json",
            {
                "queue_version": VERSION,
                "occurrences": occurrences,
                "source_modified": False,
                "qdrant_entry_created": False,
            },
        )
        write_json(
            output_dir / "terminology_suppressions.json",
            {
                "queue_version": VERSION,
                "metadata_identifiers": sorted(metadata_terms),
                "metadata_identifier_details": metadata_meanings,
                "suppressions": suppressions,
                "source_modified": False,
                "qdrant_entry_created": False,
            },
        )
        write_json(
            output_dir / "terminology_shadowed_evidence.json",
            {
                "queue_version": VERSION,
                "shadowed_evidence": shadowed_evidence,
                "source_modified": False,
                "qdrant_entry_created": False,
            },
        )
        write_json(
            output_dir / "terminology_low_support_suppressions.json",
            {
                "queue_version": VERSION,
                "suppressions": low_support_suppressions,
                "source_modified": False,
                "qdrant_entry_created": False,
            },
        )
        write_json(
            output_dir / "known_term_usage.json",
            {
                "queue_version": VERSION,
                "known_term_usage": known_usage,
                "source_modified": False,
                "qdrant_entry_created": False,
            },
        )
        write_json(
            output_dir / "effective_glossary.json",
            effective_glossary(glossary, decisions),
        )
        (output_dir / "terminology_review_queue.txt").write_text(
            render_queue(queue_rows, summary),
            encoding="utf-8",
        )

    except Exception as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2

    print()
    print("Nova DRL Terminology Review Queue v{}".format(VERSION))
    print("=" * 72)
    print("Evidence files scanned:          {}".format(
        summary.get("scan_stats", {}).get("evidence_files", 0)
    ))
    print("Evidence rows examined:          {}".format(
        summary.get("scan_stats", {}).get("evidence_rows", 0)
    ))
    print("Unresolved terms:                {}".format(
        summary.get("unresolved_term_count", 0)
    ))
    print("High-priority unresolved:        {}".format(
        summary.get("high_priority_unresolved_count", 0)
    ))
    print("Human interventions recommended: {}".format(
        summary.get("ask_now_count", 0)
    ))
    print("Known terms observed:            {}".format(
        summary.get("known_term_count", 0)
    ))
    print("Noise/metadata suppressed:       {}".format(
        summary.get("suppression", {}).get(
            "suppressed_occurrence_count", 0
        )
    ))
    print("Authority-shadowed evidence:     {}".format(
        summary.get("authority_shadowing", {}).get(
            "shadowed_evidence_row_count", 0
        )
    ))
    print("Low-support OCR terms suppressed: {}".format(
        summary.get("low_support_suppression_count", 0)
    ))
    print("Qdrant entries created:          0")

    if decision_record:
        print(
            "Decision recorded:               {} {} by {}".format(
                decision_record.get("decision"),
                decision_record.get("term"),
                decision_record.get("reviewer"),
            )
        )

    print()
    unresolved = [
        row for row in queue_rows if row["status"] == "unresolved"
    ][: max(0, args.top)]
    if unresolved:
        print("TOP UNRESOLVED TERMS")
        for row in unresolved:
            template = (
                " template"
                if row["template_repetition"]["template_like"]
                else ""
            )
            print(
                "  {:<10} events={:<4} serials={:<3} score={:<4} "
                "priority={:<6} intervention={}{}".format(
                    row["term"],
                    row["unique_repair_events"],
                    row["unique_serial_numbers"],
                    row["priority_score"],
                    row["priority"],
                    row["intervention_recommendation"],
                    template,
                )
            )
        print()

    print("Reports: {}".format(output_dir))
    print("NO DRL SOURCE FILES WERE MODIFIED.")
    print("NO QDRANT ENTRY CREATED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
