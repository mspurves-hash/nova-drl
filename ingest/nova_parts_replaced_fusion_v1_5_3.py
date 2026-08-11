#!/usr/bin/env python3
"""
Nova DRL Parts Replaced Fusion v1.5.3
=====================================

Extracts conservative part candidates from HUMAN-APPROVED repair actions that
have already passed through the v1.5.2 terminology layer.

Core rule:
    A component mention is NOT a replaced part unless the approved action
    contains an explicit install/replacement signal.

Examples:
    "by slipping Y belt a few teeth"
        -> belt is REFERENCED/SERVICED, not replaced.

    "Added Flanges BERS x2 to A1 + A2 upper link"
        -> BERS is human-confirmed DRL shorthand for bearings.
        -> "Added" is an explicit installation signal.
        -> candidate: bearings, quantity 2, pending human review.

Safety:
- Reads only approved/human-reviewed fields.
- Does not modify v1.5.1 or v1.5.2 artifacts.
- Does not use raw OCR as a part source.
- Does not convert every noun into a part.
- Does not infer root cause.
- Does not write to Qdrant.
"""

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.5.3"


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


def stable_id(*parts):
    joined = "\n".join(str(x or "") for x in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def locate_terminology_source(source):
    source = Path(source).expanduser().resolve()
    candidates = []
    if source.is_file():
        candidates.append(source)
    else:
        candidates.extend([
            source / "approved_repair_fields_with_terminology.json",
            source / "approved_repair_fields.json",
        ])

    for candidate in candidates:
        if not candidate.exists() or not candidate.is_file():
            continue
        data = read_json(candidate)
        if "approved_fields" not in data or "repair_identity" not in data:
            continue
        return candidate, data

    raise ValueError(
        "Could not find approved_repair_fields_with_terminology.json "
        "or approved_repair_fields.json beneath {}".format(source)
    )


def default_part_terms_path():
    return (
        Path(__file__).resolve().parents[1]
        / "config"
        / "drl_part_terms_v1_5_3.json"
    )


def load_part_terms(path):
    data = read_json(path)
    terms = data.get("part_terms")
    if not isinstance(terms, list):
        raise ValueError("Part terms config must contain 'part_terms' list.")

    normalized = []
    for index, term in enumerate(terms, start=1):
        canonical = str(term.get("canonical_part") or "").strip()
        if not canonical:
            raise ValueError(
                "Part term {} is missing canonical_part.".format(index)
            )
        aliases = [
            str(alias).strip()
            for alias in term.get("aliases", [])
            if str(alias).strip()
        ]
        if canonical not in aliases:
            aliases.append(canonical)
        normalized.append({
            **term,
            "canonical_part": canonical,
            "aliases": aliases,
        })

    return {**data, "part_terms": normalized}


def phrase_pattern(text):
    chars = []
    for ch in str(text):
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


def canonical_part_for_normalized_meaning(meaning, part_terms):
    meaning_norm = str(meaning or "").strip().lower()
    if not meaning_norm:
        return None

    for term in part_terms.get("part_terms", []):
        canonical = term["canonical_part"]
        if canonical.lower() == meaning_norm:
            return term
        if meaning_norm in [
            str(alias).strip().lower()
            for alias in term.get("normalized_aliases", [])
        ]:
            return term
    return None


def terminology_mentions(action, part_terms):
    mentions = []
    value = str(action.get("value") or "")

    for annotation in action.get("terminology_annotations", []) or []:
        term = canonical_part_for_normalized_meaning(
            annotation.get("normalized_meaning"),
            part_terms,
        )
        if not term:
            continue

        start = annotation.get("start")
        end = annotation.get("end")
        raw_text = annotation.get("raw_text_in_value")

        if not isinstance(start, int) or not isinstance(end, int):
            raw_text = str(raw_text or "")
            match = phrase_pattern(raw_text).search(value) if raw_text else None
            if not match:
                continue
            start, end = match.start(), match.end()
            raw_text = value[start:end]
        else:
            raw_text = value[start:end]

        mentions.append({
            "start": start,
            "end": end,
            "raw_mention": raw_text,
            "canonical_part": term["canonical_part"],
            "part_category": term.get("category"),
            "source": "drl_terminology_annotation",
            "terminology": {
                "raw_term": annotation.get("raw_term"),
                "normalized_meaning": annotation.get("normalized_meaning"),
                "status": annotation.get("status"),
                "preserve_raw": annotation.get("preserve_raw", True),
            },
        })
    return mentions


def lexicon_mentions(action, part_terms):
    value = str(action.get("value") or "")
    mentions = []

    for term in part_terms.get("part_terms", []):
        best_aliases = sorted(
            term.get("aliases", []),
            key=lambda x: len(str(x)),
            reverse=True,
        )
        occupied = []

        for alias in best_aliases:
            for match in phrase_pattern(alias).finditer(value):
                span = (match.start(), match.end())
                if any(
                    not (span[1] <= old[0] or span[0] >= old[1])
                    for old in occupied
                ):
                    continue
                occupied.append(span)
                mentions.append({
                    "start": match.start(),
                    "end": match.end(),
                    "raw_mention": value[match.start():match.end()],
                    "canonical_part": term["canonical_part"],
                    "part_category": term.get("category"),
                    "source": "approved_action_part_lexicon",
                    "matched_alias": alias,
                })
    return mentions


def dedupe_mentions(mentions):
    # Prefer terminology-backed mentions when spans/canonical parts overlap.
    priority = {
        "drl_terminology_annotation": 100,
        "approved_action_part_lexicon": 50,
    }
    output = []

    for row in sorted(
        mentions,
        key=lambda x: (
            x["start"],
            -priority.get(x.get("source"), 0),
            -(x["end"] - x["start"]),
        ),
    ):
        duplicate = False
        for existing in output:
            overlap = not (
                row["end"] <= existing["start"]
                or row["start"] >= existing["end"]
            )
            same_part = (
                row["canonical_part"].lower()
                == existing["canonical_part"].lower()
            )
            if overlap and same_part:
                duplicate = True
                break
        if not duplicate:
            output.append(row)

    return sorted(output, key=lambda x: x["start"])


def word_window(value, start, end, words_before=6, words_after=6):
    tokens = list(re.finditer(r"\S+", value))
    if not tokens:
        return value

    mention_token_indexes = [
        i for i, token in enumerate(tokens)
        if not (token.end() <= start or token.start() >= end)
    ]
    if not mention_token_indexes:
        return value

    first = max(0, mention_token_indexes[0] - words_before)
    last = min(len(tokens) - 1, mention_token_indexes[-1] + words_after)
    return value[tokens[first].start():tokens[last].end()]


INSTALL_PATTERNS = [
    re.compile(r"\breplac(?:e|ed|ing)\b", re.IGNORECASE),
    re.compile(r"\binstall(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\badd(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\bnew\b", re.IGNORECASE),
    re.compile(r"\bfit(?:ted|ting)?\b", re.IGNORECASE),
    re.compile(r"\bswapp(?:ed|ing)?\b", re.IGNORECASE),
]

SERVICE_PATTERNS = [
    re.compile(r"\badjust(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\bslipp(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\brebuild|rebuilt\b", re.IGNORECASE),
    re.compile(r"\bmachin(?:e|ed|ing)\b", re.IGNORECASE),
    re.compile(r"\bclean(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\bgreas(?:e|ed|ing)\b", re.IGNORECASE),
    re.compile(r"\bregreas(?:e|ed|ing)\b", re.IGNORECASE),
    re.compile(r"\balign(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\bre-?align(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\brepair(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\bpolish(?:ed|ing)?\b", re.IGNORECASE),
]


def matching_signal(patterns, text):
    hits = []
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            hits.append(match.group(0))
    return hits


def parse_quantity(value, start, end):
    left = value[max(0, start - 18):start]
    right = value[end:min(len(value), end + 24)]
    combined = "{} {}".format(left, right)

    patterns = [
        r"\bx\s*(\d{1,3})\b",
        r"\b(\d{1,3})\s*x\b",
        r"\bqty\.?\s*[:=]?\s*(\d{1,3})\b",
        r"\bquantity\s*[:=]?\s*(\d{1,3})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, combined, flags=re.IGNORECASE)
        if match:
            return int(match.group(1)), match.group(0)

    # Conservative numeric prefix, such as "2 bearings".
    prefix = re.search(r"(\d{1,3})\s*$", left)
    if prefix:
        return int(prefix.group(1)), prefix.group(0)

    return None, None


def install_context_after(value, end):
    tail = value[end:]
    match = re.search(
        r"\b(?:to|on|at|in|into)\b\s+(.+)$",
        tail,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    context = re.sub(r"\s+", " ", match.group(0)).strip()
    return context[:180]


def classify_mention(action_value, mention):
    window = word_window(
        action_value,
        mention["start"],
        mention["end"],
        words_before=6,
        words_after=6,
    )
    install_hits = matching_signal(INSTALL_PATTERNS, window)
    service_hits = matching_signal(SERVICE_PATTERNS, window)

    if install_hits:
        classification = "installed_or_replaced_candidate"
        confidence = (
            "high"
            if mention.get("source") == "drl_terminology_annotation"
            else "medium_high"
        )
        reason = "explicit_install_or_replacement_signal"
    elif service_hits:
        classification = "referenced_or_serviced_component"
        confidence = "high"
        reason = "service_or_adjustment_signal_without_replacement"
    else:
        classification = "referenced_component"
        confidence = "medium"
        reason = "component_mentioned_without_replacement_signal"

    quantity, quantity_raw = parse_quantity(
        action_value,
        mention["start"],
        mention["end"],
    )

    return {
        **mention,
        "classification": classification,
        "classification_reason": reason,
        "confidence": confidence,
        "signal_window": window,
        "install_signals": install_hits,
        "service_signals": service_hits,
        "quantity": quantity,
        "quantity_raw": quantity_raw,
        "installation_context": install_context_after(
            action_value,
            mention["end"],
        ),
    }


def extract_parts(source_data, part_terms):
    actions = (
        source_data.get("approved_fields", {})
        .get("repair_actions", [])
        or []
    )

    replacement_candidates = []
    component_observations = []

    for action in actions:
        value = str(action.get("value") or "")
        if not value:
            continue

        mentions = dedupe_mentions(
            terminology_mentions(action, part_terms)
            + lexicon_mentions(action, part_terms)
        )

        for mention in mentions:
            item = classify_mention(value, mention)
            item.update({
                "source_action_id": action.get("action_id"),
                "source_action_number": action.get("action_number"),
                "source_action_value": value,
                "source_action_decision_id": action.get("decision_id"),
                "source_action_reviewer": action.get("reviewer"),
                "source_action_reviewed_at_utc": action.get(
                    "reviewed_at_utc"
                ),
                "source_primary_evidence": action.get("primary_source"),
            })
            item["part_candidate_id"] = stable_id(
                "parts_replaced",
                action.get("action_id"),
                item.get("canonical_part"),
                item.get("raw_mention"),
                item.get("start"),
                item.get("quantity"),
            )

            if item["classification"] == "installed_or_replaced_candidate":
                item["human_review"] = {
                    "status": "pending",
                    "reviewer": None,
                    "reviewed_at_utc": None,
                    "approved_part": None,
                    "approved_quantity": None,
                    "note": None,
                }
                item["accepted_as_human_reviewed_part"] = False
                item["qdrant"] = {
                    "eligible_for_future_ingestion": False,
                    "entry_created": False,
                    "reason": "pending_human_review",
                }
                replacement_candidates.append(item)
            else:
                item["accepted_as_replaced_part"] = False
                item["human_review_required_as_replaced_part"] = False
                component_observations.append(item)

    for number, item in enumerate(replacement_candidates, start=1):
        item["part_number"] = number

    return {
        "field": "parts_replaced",
        "candidate_status": (
            "candidates_available"
            if replacement_candidates
            else "no_explicit_replacement_candidates"
        ),
        "candidate_count": len(replacement_candidates),
        "approved_part_count": 0,
        "pending_part_count": len(replacement_candidates),
        "candidates": replacement_candidates,
        "component_observation_count": len(component_observations),
        "component_observations": component_observations,
        "source_policy": (
            "human_approved_repair_actions_only"
        ),
        "raw_ocr_used_as_part_source": False,
        "qdrant_entry_created": False,
    }


def decision_file(output_dir):
    return Path(output_dir) / "human_review_decisions.json"


def load_decisions(output_dir):
    path = decision_file(output_dir)
    if not path.exists():
        return []
    try:
        data = read_json(path)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def latest_part_decisions(decisions):
    latest = {}
    for decision in decisions:
        if (
            decision.get("field") == "parts_replaced"
            and decision.get("part_candidate_id")
        ):
            latest[decision["part_candidate_id"]] = decision
    return latest


def apply_part_decisions(parts_field, decisions):
    latest = latest_part_decisions(decisions)
    approved = 0
    pending = 0

    for item in parts_field.get("candidates", []):
        decision = latest.get(item["part_candidate_id"])
        if not decision:
            pending += 1
            continue

        status = decision.get("decision")
        item["human_review"] = {
            "status": status,
            "reviewer": decision.get("reviewer"),
            "reviewed_at_utc": decision.get("reviewed_at_utc"),
            "approved_part": (
                decision.get("part")
                if status == "approved"
                else None
            ),
            "approved_quantity": (
                decision.get("quantity")
                if status == "approved"
                else None
            ),
            "note": decision.get("note"),
            "decision_id": decision.get("decision_id"),
        }

        is_approved = status == "approved"
        item["accepted_as_human_reviewed_part"] = is_approved
        item["qdrant"] = {
            "eligible_for_future_ingestion": is_approved,
            "entry_created": False,
            "reason": (
                "human_approved_waiting_for_future_ingestion_pipeline"
                if is_approved
                else "human_review_{}".format(status)
            ),
        }
        if is_approved:
            approved += 1

    parts_field["approved_part_count"] = approved
    parts_field["pending_part_count"] = pending
    return parts_field


def find_part_candidate(parts_field, part_number=None, part_candidate_id=None):
    candidates = parts_field.get("candidates", [])

    if part_candidate_id:
        for item in candidates:
            if item.get("part_candidate_id") == part_candidate_id:
                return item
        raise ValueError(
            "Part candidate ID not found: {}".format(part_candidate_id)
        )

    if part_number is not None:
        for item in candidates:
            if int(item.get("part_number") or 0) == int(part_number):
                return item
        raise ValueError(
            "Part candidate number not found: {}".format(part_number)
        )

    raise ValueError(
        "Use --part-number=N or --part-candidate-id=... for a part decision."
    )


def record_part_decision(
    parts_field,
    output_dir,
    decision,
    reviewer,
    part_number=None,
    part_candidate_id=None,
    part=None,
    quantity=None,
    note=None,
):
    if not reviewer or not reviewer.strip():
        raise ValueError("--reviewer is required.")

    decision = str(decision).strip().lower()
    if decision not in {"approve", "reject", "hold"}:
        raise ValueError("Decision must be approve, reject, or hold.")

    item = find_part_candidate(
        parts_field,
        part_number=part_number,
        part_candidate_id=part_candidate_id,
    )

    if decision == "approve":
        approved_part = (
            str(part).strip()
            if part is not None
            else item["canonical_part"]
        )
        if not approved_part:
            raise ValueError("Approved part cannot be blank.")
        approved_quantity = (
            int(quantity)
            if quantity is not None
            else item.get("quantity")
        )
        stored = "approved"
    elif decision == "reject":
        approved_part = None
        approved_quantity = None
        stored = "rejected"
    else:
        approved_part = None
        approved_quantity = None
        stored = "hold"

    record = {
        "decision_id": stable_id(
            "parts_replaced",
            item["part_candidate_id"],
            stored,
            reviewer,
            now_utc(),
            approved_part,
            approved_quantity,
            note,
        ),
        "field": "parts_replaced",
        "part_candidate_id": item["part_candidate_id"],
        "part_number": item.get("part_number"),
        "decision": stored,
        "reviewer": reviewer.strip(),
        "reviewed_at_utc": now_utc(),
        "part": approved_part,
        "quantity": approved_quantity,
        "edited_part_from_candidate": bool(
            stored == "approved"
            and part is not None
            and approved_part != item["canonical_part"]
        ),
        "edited_quantity_from_candidate": bool(
            stored == "approved"
            and quantity is not None
            and approved_quantity != item.get("quantity")
        ),
        "canonical_part_at_review": item["canonical_part"],
        "candidate_quantity_at_review": item.get("quantity"),
        "raw_mention": item.get("raw_mention"),
        "source_action_id": item.get("source_action_id"),
        "source_action_number": item.get("source_action_number"),
        "note": note,
        "fusion_version": VERSION,
        "qdrant_entry_created": False,
    }

    decisions = load_decisions(output_dir)
    decisions.append(record)
    write_json(decision_file(output_dir), decisions)
    return record


def approved_parts(parts_field):
    output = []
    for item in parts_field.get("candidates", []):
        review = item.get("human_review", {})
        if review.get("status") != "approved":
            continue

        output.append({
            "part_candidate_id": item["part_candidate_id"],
            "part_number": item.get("part_number"),
            "part": review.get("approved_part"),
            "quantity": review.get("approved_quantity"),
            "reviewer": review.get("reviewer"),
            "reviewed_at_utc": review.get("reviewed_at_utc"),
            "decision_id": review.get("decision_id"),
            "raw_mention": item.get("raw_mention"),
            "normalized_candidate": item.get("canonical_part"),
            "source_action_id": item.get("source_action_id"),
            "source_action_number": item.get("source_action_number"),
            "source_action_value": item.get("source_action_value"),
            "terminology": item.get("terminology"),
            "installation_context": item.get("installation_context"),
            "eligible_for_future_qdrant_ingestion": True,
            "qdrant_entry_created": False,
        })
    return output


def build_output(source_data, source_path, part_terms, decisions):
    enriched = copy.deepcopy(source_data)
    parts_field = extract_parts(enriched, part_terms)
    apply_part_decisions(parts_field, decisions)

    parts_approved = approved_parts(parts_field)

    output = {
        "fusion_version": VERSION,
        "source_fusion_version": source_data.get("fusion_version"),
        "source_approved_fields_path": str(source_path),
        "repair_identity": source_data.get("repair_identity"),
        "approved_fields": copy.deepcopy(
            source_data.get("approved_fields", {})
        ),
        "parts_replaced_review": parts_field,
        "approved_field_count": int(
            source_data.get("approved_field_count", 0)
        ),
        "approved_repair_action_count": int(
            source_data.get("approved_repair_action_count", 0)
        ),
        "approved_parts_replaced_count": len(parts_approved),
        "accepted_as_final_repair_summary": False,
        "qdrant_entry_created": False,
    }

    if parts_approved:
        output["approved_fields"]["parts_replaced"] = parts_approved

        # Count field groups rather than individual part rows.
        if "parts_replaced" not in source_data.get("approved_fields", {}):
            output["approved_field_count"] += 1

    output["parts_replaced_policy"] = {
        "approved_actions_only": True,
        "explicit_install_or_replacement_signal_required": True,
        "terminology_aware": True,
        "raw_ocr_used_as_part_source": False,
        "referenced_component_is_not_replaced_part": True,
        "automatic_approval": False,
        "qdrant_write_enabled": False,
    }
    return output


def render_review(output):
    identity = output.get("repair_identity", {})
    field = output.get("parts_replaced_review", {})

    lines = [
        "NOVA DRL PARTS REPLACED FUSION v{}".format(VERSION),
        "=" * 76,
        "Log: {}".format(identity.get("log_number")),
        "Model: {}".format(identity.get("model")),
        "Serial: {}".format(identity.get("serial_number")),
        "",
        "PARTS REPLACED CANDIDATES",
        "-------------------------",
        "Candidate count: {}".format(field.get("candidate_count", 0)),
        "Approved parts: {}".format(field.get("approved_part_count", 0)),
        "Pending parts: {}".format(field.get("pending_part_count", 0)),
        "",
    ]

    if not field.get("candidates"):
        lines.append("None")
        lines.append("")

    for item in field.get("candidates", []):
        review = item.get("human_review", {})
        lines += [
            "PART {} [{}]".format(
                item.get("part_number"),
                item.get("part_candidate_id"),
            ),
            "  Candidate part: {}".format(item.get("canonical_part")),
            "  Raw mention: {}".format(item.get("raw_mention")),
            "  Quantity: {}".format(
                item.get("quantity")
                if item.get("quantity") is not None
                else "not established"
            ),
            "  Classification: {}".format(item.get("classification")),
            "  Confidence: {}".format(item.get("confidence")),
            "  Install signals: {}".format(
                ", ".join(item.get("install_signals") or []) or "None"
            ),
            "  Source action {}: {}".format(
                item.get("source_action_number"),
                item.get("source_action_value"),
            ),
            "  Terminology support: {}".format(
                (
                    "{} -> {}".format(
                        item.get("terminology", {}).get("raw_term"),
                        item.get("terminology", {}).get(
                            "normalized_meaning"
                        ),
                    )
                    if item.get("terminology")
                    else "None"
                )
            ),
            "  Installation context: {}".format(
                item.get("installation_context") or "None"
            ),
            "  Human review: {}".format(review.get("status")),
            "  Accepted as human-reviewed part: {}".format(
                "YES"
                if item.get("accepted_as_human_reviewed_part")
                else "NO"
            ),
            "  Future Qdrant eligible: {}".format(
                "YES"
                if item.get("qdrant", {}).get(
                    "eligible_for_future_ingestion"
                )
                else "NO"
            ),
        ]
        if review.get("reviewer"):
            lines += [
                "  Review decision:",
                "    Reviewer: {}".format(review.get("reviewer")),
                "    Approved part: {}".format(
                    review.get("approved_part")
                ),
                "    Approved quantity: {}".format(
                    review.get("approved_quantity")
                ),
                "    Note: {}".format(review.get("note") or "None"),
            ]
        lines.append("")

    lines += [
        "COMPONENT OBSERVATIONS — NOT REPLACED PARTS",
        "-------------------------------------------",
        "Observation count: {}".format(
            field.get("component_observation_count", 0)
        ),
    ]

    if not field.get("component_observations"):
        lines.append("None")
    else:
        for item in field["component_observations"]:
            lines += [
                "- {} | {} | action {}".format(
                    item.get("canonical_part"),
                    item.get("classification"),
                    item.get("source_action_number"),
                ),
                "  Source action: {}".format(
                    item.get("source_action_value")
                ),
                "  Signals: service={} install={}".format(
                    item.get("service_signals") or [],
                    item.get("install_signals") or [],
                ),
                "  Accepted as replaced part: NO",
            ]

    lines += [
        "",
        "STATUS",
        "------",
        "Parts approved: {}".format(field.get("approved_part_count", 0)),
        "Parts pending: {}".format(field.get("pending_part_count", 0)),
        "Raw OCR used as a part source: NO",
        "Accepted as final repair summary: NO",
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
        / "evidence_fusion_v1_5_3"
        / folder
        / "events"
        / str(identity.get("log_number") or "unknown")
    )


def write_outputs(output, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        output_dir / "parts_replaced_review.json",
        output.get("parts_replaced_review", {}),
    )
    write_json(
        output_dir / "approved_repair_fields_with_parts.json",
        output,
    )
    (output_dir / "parts_replaced_review.txt").write_text(
        render_review(output),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Nova DRL Parts Replaced Fusion v{}".format(VERSION)
    )
    parser.add_argument(
        "source",
        help=(
            "v1.5.2 event directory or "
            "approved_repair_fields_with_terminology.json"
        ),
    )
    parser.add_argument(
        "--part-terms",
        default=str(default_part_terms_path()),
    )
    parser.add_argument("--output-root")
    parser.add_argument(
        "--decision",
        choices=["approve", "reject", "hold"],
    )
    parser.add_argument("--part-number", type=int)
    parser.add_argument("--part-candidate-id")
    parser.add_argument("--reviewer")
    parser.add_argument("--part")
    parser.add_argument("--quantity", type=int)
    parser.add_argument("--note")
    args = parser.parse_args()

    try:
        source_path, source_data = locate_terminology_source(args.source)
        part_terms = load_part_terms(args.part_terms)
        output_dir = (
            Path(args.output_root).expanduser().resolve()
            if args.output_root
            else default_output_dir(source_data)
        )

        decisions = load_decisions(output_dir)
        first_pass = build_output(
            source_data,
            source_path,
            part_terms,
            decisions,
        )

        decision_record = None
        if args.decision:
            decision_record = record_part_decision(
                first_pass["parts_replaced_review"],
                output_dir,
                decision=args.decision,
                reviewer=args.reviewer,
                part_number=args.part_number,
                part_candidate_id=args.part_candidate_id,
                part=args.part,
                quantity=args.quantity,
                note=args.note,
            )
            decisions = load_decisions(output_dir)

        output = build_output(
            source_data,
            source_path,
            part_terms,
            decisions,
        )
        write_outputs(output, output_dir)

    except Exception as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2

    field = output["parts_replaced_review"]
    print()
    print("Nova DRL Parts Replaced Fusion v{}".format(VERSION))
    print("=" * 68)
    print("Log:                         {}".format(
        output.get("repair_identity", {}).get("log_number")
    ))
    print("Approved repair actions:     {}".format(
        output.get("approved_repair_action_count", 0)
    ))
    print("Replacement part candidates: {}".format(
        field.get("candidate_count", 0)
    ))
    print("Referenced components:       {}".format(
        field.get("component_observation_count", 0)
    ))
    print("Parts approved:              {}".format(
        field.get("approved_part_count", 0)
    ))
    print("Parts pending:               {}".format(
        field.get("pending_part_count", 0)
    ))
    print("Raw OCR used as part source: NO")
    print("Qdrant entries created:      0")

    if decision_record:
        print(
            "Decision recorded:           {} part {} by {}".format(
                decision_record.get("decision"),
                decision_record.get("part_number"),
                decision_record.get("reviewer"),
            )
        )

    print()
    print("Reports: {}".format(output_dir))
    print("NO APPROVED SOURCE VALUES WERE MODIFIED.")
    print("NO QDRANT ENTRY CREATED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
