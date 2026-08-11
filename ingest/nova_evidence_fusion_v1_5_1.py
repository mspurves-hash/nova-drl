#!/usr/bin/env python3
"""
Nova DRL Evidence Fusion and Human Review v1.5.1
================================================

Adds item-level Repair Actions fusion and human review while preserving the
validated v1.5.0 customer-complaint workflow.

Repair-action evidence policy:
- Prefer structured repair rows produced by Traveler Reader v1.3.4.x.
- Preserve the literal traveler repair description.
- Do not create repair-action facts from raw whole-region OCR alone.
- Optionally corroborate a traveler action against event-specific
  Internal Checklist Notes when a close textual match exists.
- Never infer parts, root cause, testing, or final result from an action line.
- Never auto-approve.
- Never write to Qdrant.
"""

import argparse
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.5.1"

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "nova_evidence_fusion_v1_5.py"
spec = importlib.util.spec_from_file_location("nova_fusion_v150", str(BASE_PATH))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

STRUCTURED_ACTION_RE = re.compile(
    r"repair_entries_v1_3_4(?:_(\d+))?\.json$",
    re.IGNORECASE,
)

SEVERE_REVIEW_REASONS = {
    "blank_or_weak_candidate_rejected",
    "detect_only_no_vision_run",
    "row_format_noncompliance",
    "vision_prompt_noncompliance",
}


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def version_key(path, record=None):
    name = Path(str(path)).name
    match = STRUCTURED_ACTION_RE.search(name)
    patch = int(match.group(1) or 0) if match else -1
    reader_version = str((record or {}).get("reader_version") or "")
    nums = tuple(int(x) for x in re.findall(r"\d+", reader_version))
    return nums + (patch,)


def clean_action_value(value):
    value = str(value or "").strip()
    if not value:
        return None
    if value.lower() in {"[unclear]", "unclear", "none", "n/a", "na"}:
        return None
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" \t\r\n|[]{}<>")
    value = value.rstrip(" .;,|")
    return value or None


def find_structured_repair_source(event):
    candidates = []
    for artifact in event.get("derived_traveler_artifacts", []):
        path = Path(str(artifact.get("path") or ""))
        if artifact.get("role") != "repair_entry_extraction":
            continue
        if path.suffix.lower() != ".json":
            continue
        if not STRUCTURED_ACTION_RE.search(path.name):
            continue
        if not path.exists():
            continue
        try:
            record = base.read_json(path)
        except Exception:
            continue
        if str(record.get("log_number") or "") != str(event.get("log_number") or ""):
            continue
        candidates.append((version_key(path, record), artifact, record))

    if not candidates:
        return None, None
    candidates.sort(key=lambda row: row[0], reverse=True)

    # Prefer the newest complete/non-detect-only artifact with actual rows.
    for _, artifact, record in candidates:
        if (
            record.get("status") == "ok"
            and not record.get("detect_only")
            and record.get("entries")
        ):
            return artifact, record

    # Otherwise return the newest artifact so readiness status is visible.
    _, artifact, record = candidates[0]
    return artifact, record


def find_raw_repairs_region(event):
    for artifact in event.get("derived_traveler_artifacts", []):
        path = Path(str(artifact.get("path") or ""))
        if (
            artifact.get("role") == "region_ocr"
            and path.name.lower() == "traveler_regions.json"
            and path.exists()
        ):
            try:
                data = base.read_json(path)
            except Exception:
                continue
            region = data.get("regions", {}).get("repairs_replacements", {})
            return {
                "artifact_path": str(path),
                "source_path": data.get("source_path"),
                "crop_path": region.get("crop_path"),
                "selected_psm": region.get("selected_psm"),
                "selected_score": region.get("selected_score"),
                "raw_region_text": region.get("selected_text", ""),
                "converted_to_action_candidates": False,
                "reason": (
                    "Whole-region OCR is preserved as evidence but is not "
                    "structured enough to create repair-action facts."
                ),
            }
    return None


def load_extracted_text(evidence):
    extraction = evidence.get("extraction", {})
    text_path = extraction.get("text_path")
    if text_path:
        path = Path(str(text_path))
        if path.exists() and path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
    return str(extraction.get("text_preview") or "")


def note_lines(event):
    rows = []
    for evidence in event.get("evidence_files", []):
        if evidence.get("role") != "internal_checklist_notes":
            continue
        text = load_extracted_text(evidence)
        for line_number, line in enumerate(text.splitlines(), start=1):
            cleaned = clean_action_value(line)
            if not cleaned:
                continue
            if len(cleaned) < 5 or len(cleaned) > 300:
                continue
            rows.append({
                "raw_value": cleaned,
                "source_path": evidence.get("source_path"),
                "source_document": evidence.get("relative_path"),
                "document_role": "internal_checklist_notes",
                "authority": (
                    evidence.get("authority")
                    or "technician_working_notes"
                ),
                "line_number": line_number,
                "extraction_method": (
                    evidence.get("extraction", {}).get("method")
                    or "document_text_extraction"
                ),
            })
    return rows


def action_support_matches(action_value, notes, threshold=0.86):
    matches = []
    for note in notes:
        score = base.candidate_similarity(action_value, note["raw_value"])
        a = base.normalize_for_compare(action_value)
        b = base.normalize_for_compare(note["raw_value"])
        if a and b and (a in b or b in a) and min(len(a.split()), len(b.split())) >= 4:
            score = max(score, 0.95)
        if score >= threshold:
            matches.append({**note, "similarity": score})
    matches.sort(key=lambda row: row["similarity"], reverse=True)
    return matches


def structured_entry_to_action(event, source_artifact, source_record, entry, notes):
    fields = entry.get("literal_fields") or {}
    description = clean_action_value(fields.get("description"))
    if not description or entry.get("blank_rejected"):
        return None

    entry_index = int(entry.get("entry_index") or 0)
    source_path = source_record.get("source_path") or ""
    action_id = base.evidence_id(
        "repair_action",
        event.get("log_number"),
        source_path,
        entry_index,
        base.normalize_for_compare(description),
    )

    review_reasons = list(entry.get("review_reasons") or [])
    severe = any(reason in SEVERE_REVIEW_REASONS for reason in review_reasons)
    structured_eligible = bool(entry.get("eligible_for_evidence_comparison"))
    supporting = action_support_matches(description, notes)

    independent_keys = {str(source_path)}
    independent_keys.update(str(row.get("source_path") or row.get("source_document"))
                            for row in supporting)
    independent_keys.discard("")

    if structured_eligible and supporting:
        confidence = "high"
        status = "cross_source_supported"
    elif structured_eligible and not severe:
        confidence = "medium"
        status = "primary_anchor_structured_candidate"
    else:
        confidence = "low"
        status = "primary_anchor_review_required"

    primary = {
        "document_role": "traveler",
        "authority": "primary_repair_anchor",
        "source_path": source_path,
        "source_document": source_record.get("relative_path"),
        "location": "repairs_replacements entry {}".format(entry_index),
        "entry_index": entry_index,
        "extraction_method": (
            "traveler_reader_{}_structured_repair_row".format(
                source_record.get("reader_version") or "v1_3_4_x"
            )
        ),
        "raw_value": description,
        "crop_paths": entry.get("crop_paths"),
        "tesseract_full_row": (
            (entry.get("tesseract") or {}).get("full_row", {}).get("selected_text")
        ),
        "vision_raw": (entry.get("vision") or {}).get("response"),
        "accepted_as_repair_fact": False,
    }

    return {
        "action_id": action_id,
        "action_number": None,
        "candidate_status": status,
        "canonical_candidate": description,
        "canonicalization": {
            "method": "source_wording_with_whitespace_and_terminal_punctuation_normalization_only",
            "meaning_changed": False,
        },
        "confidence": confidence,
        "independent_source_count": max(1, len(independent_keys)),
        "structured_evidence_comparison_eligible": structured_eligible,
        "primary_source": primary,
        "supporting_sources": supporting,
        "provisional_entry_metadata": {
            "initials": fields.get("initials"),
            "date": fields.get("date"),
            "initials_validation": entry.get("initials_validation"),
            "date_validation": entry.get("date_validation"),
            "glossary_matches": entry.get("glossary_matches") or [],
        },
        "review_reasons": review_reasons,
        "human_review": {
            "status": "pending",
            "recommended_decision": "review",
            "reviewer": None,
            "reviewed_at_utc": None,
            "approved_value": None,
            "note": None,
        },
        "accepted_as_human_reviewed_fact": False,
        "qdrant": {
            "entry_created": False,
            "eligible_for_future_ingestion": False,
            "reason": "pending_human_review",
        },
    }


def build_repair_actions_field(event):
    source_artifact, source_record = find_structured_repair_source(event)
    fallback = find_raw_repairs_region(event)

    if not source_record:
        return {
            "field": "repair_actions",
            "candidate_status": "structured_traveler_extraction_required",
            "structured_source": None,
            "candidate_count": 0,
            "items": [],
            "fallback_region_evidence": fallback,
            "human_review_mode": "item_level",
            "approved_action_count": 0,
            "pending_action_count": 0,
            "accepted_as_human_reviewed_fact": False,
            "qdrant": {
                "entry_created": False,
                "eligible_item_count": 0,
                "reason": "structured_repair_rows_not_available",
            },
        }

    source_summary = {
        "artifact_path": source_artifact.get("path"),
        "reader_version": source_record.get("reader_version"),
        "status": source_record.get("status"),
        "model": source_record.get("model"),
        "detect_only": source_record.get("detect_only"),
        "vision_processing_stopped": source_record.get("vision_processing_stopped"),
        "accepted_as_facts_in_source": source_record.get("accepted_as_facts", 0),
        "source_traveler_path": source_record.get("source_path"),
    }

    if (
        source_record.get("status") != "ok"
        or source_record.get("detect_only")
        or source_record.get("vision_processing_stopped")
    ):
        return {
            "field": "repair_actions",
            "candidate_status": "structured_traveler_extraction_incomplete",
            "structured_source": source_summary,
            "candidate_count": 0,
            "items": [],
            "fallback_region_evidence": fallback,
            "human_review_mode": "item_level",
            "approved_action_count": 0,
            "pending_action_count": 0,
            "accepted_as_human_reviewed_fact": False,
            "qdrant": {
                "entry_created": False,
                "eligible_item_count": 0,
                "reason": "structured_repair_rows_incomplete_or_detect_only",
            },
        }

    notes = note_lines(event)
    items = []
    for entry in source_record.get("entries", []):
        item = structured_entry_to_action(
            event, source_artifact, source_record, entry, notes
        )
        if item:
            items.append(item)

    for number, item in enumerate(items, start=1):
        item["action_number"] = number

    return {
        "field": "repair_actions",
        "candidate_status": (
            "structured_candidates_available" if items else "no_action_candidates"
        ),
        "structured_source": source_summary,
        "candidate_count": len(items),
        "items": items,
        "fallback_region_evidence": fallback,
        "supporting_note_line_count": len(notes),
        "human_review_mode": "item_level",
        "approved_action_count": 0,
        "pending_action_count": len(items),
        "accepted_as_human_reviewed_fact": False,
        "qdrant": {
            "entry_created": False,
            "eligible_item_count": 0,
            "reason": "item_level_human_review_required",
        },
    }


def scalar_decisions(decisions):
    return [
        row for row in decisions
        if row.get("field") != "repair_actions" and not row.get("action_id")
    ]


def action_decisions(decisions):
    return [
        row for row in decisions
        if row.get("field") == "repair_actions" and row.get("action_id")
    ]


def apply_action_decisions(review, decisions):
    field = review.get("fields", {}).get("repair_actions") or {}
    by_id = {}
    for row in action_decisions(decisions):
        by_id[row.get("action_id")] = row

    approved = 0
    pending = 0
    for item in field.get("items", []):
        decision = by_id.get(item.get("action_id"))
        if not decision:
            pending += 1
            continue

        status = decision.get("decision")
        approved_value = decision.get("value") if status == "approved" else None
        item["human_review"] = {
            "status": status,
            "recommended_decision": item.get("human_review", {}).get(
                "recommended_decision"
            ),
            "reviewer": decision.get("reviewer"),
            "reviewed_at_utc": decision.get("reviewed_at_utc"),
            "approved_value": approved_value,
            "note": decision.get("note"),
            "decision_id": decision.get("decision_id"),
        }
        is_approved = status == "approved"
        item["accepted_as_human_reviewed_fact"] = is_approved
        item["qdrant"] = {
            "entry_created": False,
            "eligible_for_future_ingestion": is_approved,
            "reason": (
                "human_approved_waiting_for_future_ingestion_pipeline"
                if is_approved
                else "human_review_{}".format(status)
            ),
        }
        if is_approved:
            approved += 1

    field["approved_action_count"] = approved
    field["pending_action_count"] = pending
    field["accepted_as_human_reviewed_fact"] = approved > 0
    field["qdrant"] = {
        "entry_created": False,
        "eligible_item_count": approved,
        "reason": (
            "human_approved_items_waiting_for_future_ingestion_pipeline"
            if approved
            else "item_level_human_review_required"
        ),
    }


def merge_decisions(prior, local):
    output = []
    seen = set()
    for row in list(prior or []) + list(local or []):
        key = row.get("decision_id") or json.dumps(row, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def repair_folder_name(bundle, collector_source):
    meta = bundle.get("serial_metadata", {})
    return (
        meta.get("original_folder_name")
        or Path(str(collector_source)).name
        or "serial"
    )


def default_output_dir(bundle, collector_source, log_number):
    return (
        Path.cwd()
        / "output"
        / "evidence_fusion_v1_5_1"
        / base.safe_name(repair_folder_name(bundle, collector_source))
        / "events"
        / str(log_number)
    )


def default_prior_dir(bundle, collector_source, log_number):
    return (
        Path.cwd()
        / "output"
        / "evidence_fusion_v1_5"
        / base.safe_name(repair_folder_name(bundle, collector_source))
        / "events"
        / str(log_number)
    )


def resolve_prior_dir(value, bundle, collector_source, log_number):
    if not value:
        return default_prior_dir(bundle, collector_source, log_number)
    root = Path(value).expanduser().resolve()
    if (root / "human_review_decisions.json").exists():
        return root
    return (
        root
        / base.safe_name(repair_folder_name(bundle, collector_source))
        / "events"
        / str(log_number)
    )


def load_decisions_from_dir(directory):
    path = Path(directory) / "human_review_decisions.json"
    if not path.exists():
        return []
    try:
        data = base.read_json(path)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def build_review(bundle_path, bundle, collector_source, log_number,
                 prior_decisions=None, local_decisions=None):
    review = base.build_review(bundle_path, bundle, [])
    review["fusion_version"] = VERSION
    review["fields"]["repair_actions"] = build_repair_actions_field(
        bundle.get("repair_event", {})
    )
    review["notes"] = [
        "v1.5.1 preserves the validated v1.5.0 customer complaint workflow.",
        "Repair actions are item-level candidates from structured Traveler Reader rows.",
        "Raw repairs-region OCR alone is not converted into repair-action facts.",
        "Parts, root cause, testing, and final result remain separate fields.",
        "No Qdrant API is called.",
    ]

    combined = merge_decisions(prior_decisions or [], local_decisions or [])
    base.apply_saved_decisions(review, scalar_decisions(combined))
    apply_action_decisions(review, combined)

    review["imported_prior_decision_count"] = len(prior_decisions or [])
    review["local_decision_count"] = len(local_decisions or [])
    review["accepted_as_final_repair_summary"] = False
    review["qdrant_entry_created"] = False

    scalar_eligible = sum(
        1
        for name, record in review["fields"].items()
        if name != "repair_actions"
        and record.get("qdrant", {}).get("eligible_for_future_ingestion")
    )
    action_eligible = review["fields"]["repair_actions"].get(
        "approved_action_count", 0
    )
    review["qdrant_eligible_field_count"] = scalar_eligible
    review["qdrant_eligible_repair_action_count"] = action_eligible
    return review


def append_local_decision(output_dir, record):
    path = Path(output_dir) / "human_review_decisions.json"
    decisions = load_decisions_from_dir(output_dir)
    decisions.append(record)
    base.write_json(path, decisions)


def find_action(review, action_id=None, action_number=None):
    items = review.get("fields", {}).get("repair_actions", {}).get("items", [])
    if action_id:
        for item in items:
            if item.get("action_id") == action_id:
                return item
        raise ValueError("Repair action ID not found: {}".format(action_id))
    if action_number is not None:
        for item in items:
            if int(item.get("action_number") or 0) == int(action_number):
                return item
        raise ValueError("Repair action number not found: {}".format(action_number))
    raise ValueError(
        "Repair-action decisions require --action-number=N or --action-id=..."
    )


def record_action_decision(review, output_dir, decision, reviewer,
                           action_id=None, action_number=None,
                           value=None, note=None):
    if not reviewer or not reviewer.strip():
        raise ValueError("--reviewer is required for a human decision")

    item = find_action(review, action_id=action_id, action_number=action_number)
    decision = decision.lower().strip()
    if decision not in {"approve", "reject", "hold"}:
        raise ValueError("Decision must be approve, reject, or hold")

    if decision == "approve":
        approved_value = value or item.get("canonical_candidate")
        if not approved_value:
            raise ValueError("No repair-action candidate exists to approve.")
        stored = "approved"
    elif decision == "reject":
        approved_value = None
        stored = "rejected"
    else:
        approved_value = None
        stored = "hold"

    record = {
        "decision_id": base.evidence_id(
            "repair_actions",
            item.get("action_id"),
            decision,
            reviewer,
            now_utc(),
            approved_value,
            note,
        ),
        "field": "repair_actions",
        "action_id": item.get("action_id"),
        "action_number": item.get("action_number"),
        "decision": stored,
        "reviewer": reviewer.strip(),
        "reviewed_at_utc": now_utc(),
        "value": approved_value,
        "edited_from_canonical": bool(
            decision == "approve"
            and value
            and value != item.get("canonical_candidate")
        ),
        "canonical_candidate_at_review": item.get("canonical_candidate"),
        "note": note,
        "fusion_version": VERSION,
        "qdrant_entry_created": False,
    }
    append_local_decision(output_dir, record)
    return record


def record_scalar_decision(review, output_dir, field, decision, reviewer,
                           value=None, note=None):
    if field == "repair_actions":
        raise ValueError("Use record_action_decision for repair actions.")
    if field not in review.get("fields", {}):
        raise ValueError("Unknown review field: {}".format(field))
    if not reviewer or not reviewer.strip():
        raise ValueError("--reviewer is required for a human decision")

    item = review["fields"][field]
    decision = decision.lower().strip()
    if decision not in {"approve", "reject", "hold"}:
        raise ValueError("Decision must be approve, reject, or hold")

    if decision == "approve":
        approved_value = value or item.get("canonical_candidate")
        if not approved_value:
            raise ValueError(
                "No canonical candidate exists. Supply --value for an edited approval."
            )
        stored = "approved"
    elif decision == "reject":
        approved_value = None
        stored = "rejected"
    else:
        approved_value = None
        stored = "hold"

    record = {
        "decision_id": base.evidence_id(
            field, decision, reviewer, now_utc(), approved_value, note
        ),
        "field": field,
        "decision": stored,
        "reviewer": reviewer.strip(),
        "reviewed_at_utc": now_utc(),
        "value": approved_value,
        "edited_from_canonical": bool(
            decision == "approve"
            and value
            and value != item.get("canonical_candidate")
        ),
        "canonical_candidate_at_review": item.get("canonical_candidate"),
        "note": note,
        "fusion_version": VERSION,
        "qdrant_entry_created": False,
    }
    append_local_decision(output_dir, record)
    return record


def render_scalar_field(name, record):
    lines = [
        name.upper(),
        "-" * len(name),
        "Candidate status: {}".format(record.get("candidate_status")),
        "Canonical candidate: {}".format(
            record.get("canonical_candidate") or "NOT CREATED"
        ),
        "Confidence: {}".format(record.get("confidence")),
        "Independent sources: {}".format(record.get("independent_source_count", 0)),
        "Human review: {}".format(record.get("human_review", {}).get("status")),
        "Accepted as human-reviewed fact: {}".format(
            "YES" if record.get("accepted_as_human_reviewed_fact") else "NO"
        ),
        "Future Qdrant eligible: {}".format(
            "YES"
            if record.get("qdrant", {}).get("eligible_for_future_ingestion")
            else "NO"
        ),
    ]
    if record.get("canonicalization"):
        lines.append(
            "Canonicalization: {}".format(
                record["canonicalization"].get("method")
            )
        )
    if record.get("agreement"):
        lines.append(
            "Agreement: min={} avg={} max={}".format(
                record["agreement"].get("minimum_similarity"),
                record["agreement"].get("average_similarity"),
                record["agreement"].get("maximum_similarity"),
            )
        )
    lines.append("Supporting evidence:")
    if record.get("sources"):
        for source in record["sources"]:
            lines.extend(base.render_source(source))
    else:
        lines.append("  None")
    human = record.get("human_review", {})
    if human.get("reviewer"):
        lines += [
            "Review decision:",
            "  Reviewer: {}".format(human.get("reviewer")),
            "  Reviewed at: {}".format(human.get("reviewed_at_utc")),
            "  Approved value: {}".format(human.get("approved_value") or "None"),
            "  Note: {}".format(human.get("note") or "None"),
        ]
    return lines


def render_repair_actions(record):
    lines = [
        "REPAIR_ACTIONS",
        "--------------",
        "Candidate status: {}".format(record.get("candidate_status")),
        "Candidate count: {}".format(record.get("candidate_count", 0)),
        "Approved actions: {}".format(record.get("approved_action_count", 0)),
        "Pending actions: {}".format(record.get("pending_action_count", 0)),
        "Human review mode: item_level",
        "Qdrant entries created: 0",
    ]
    source = record.get("structured_source")
    if source:
        lines += [
            "Structured source:",
            "  Reader version: {}".format(source.get("reader_version")),
            "  Status: {}".format(source.get("status")),
            "  Model: {}".format(source.get("model")),
            "  Detect only: {}".format(source.get("detect_only")),
            "  Artifact: {}".format(source.get("artifact_path")),
            "  Source traveler: {}".format(source.get("source_traveler_path")),
        ]
    else:
        lines.append("Structured source: NOT AVAILABLE")

    fallback = record.get("fallback_region_evidence")
    if fallback:
        lines += [
            "Raw repairs-region evidence preserved:",
            "  Crop: {}".format(fallback.get("crop_path")),
            "  OCR artifact: {}".format(fallback.get("artifact_path")),
            "  Converted to action candidates: NO",
        ]

    for item in record.get("items", []):
        human = item.get("human_review", {})
        lines += [
            "",
            "ACTION {} [{}]".format(
                item.get("action_number"), item.get("action_id")
            ),
            "  Candidate: {}".format(item.get("canonical_candidate")),
            "  Status: {}".format(item.get("candidate_status")),
            "  Confidence: {}".format(item.get("confidence")),
            "  Independent sources: {}".format(
                item.get("independent_source_count", 1)
            ),
            "  Structured comparison eligible: {}".format(
                "YES"
                if item.get("structured_evidence_comparison_eligible")
                else "NO"
            ),
            "  Provisional initials: {}".format(
                item.get("provisional_entry_metadata", {}).get("initials")
            ),
            "  Provisional date: {}".format(
                item.get("provisional_entry_metadata", {}).get("date")
            ),
            "  Human review: {}".format(human.get("status")),
            "  Accepted as human-reviewed fact: {}".format(
                "YES" if item.get("accepted_as_human_reviewed_fact") else "NO"
            ),
            "  Future Qdrant eligible: {}".format(
                "YES"
                if item.get("qdrant", {}).get("eligible_for_future_ingestion")
                else "NO"
            ),
            "  Primary source:",
            "    {}".format(item.get("primary_source", {}).get("source_path")),
            "    {}".format(item.get("primary_source", {}).get("location")),
            "    Raw value: {}".format(
                item.get("primary_source", {}).get("raw_value")
            ),
        ]
        if item.get("supporting_sources"):
            lines.append("  Supporting sources:")
            for support in item["supporting_sources"]:
                lines += [
                    "    - {} line {} | similarity={}".format(
                        support.get("source_document"),
                        support.get("line_number"),
                        support.get("similarity"),
                    ),
                    "      Raw value: {}".format(support.get("raw_value")),
                ]
        else:
            lines.append("  Supporting sources: None")
        if item.get("review_reasons"):
            lines.append(
                "  Review reasons: {}".format(
                    ", ".join(item.get("review_reasons") or [])
                )
            )
        if human.get("reviewer"):
            lines += [
                "  Review decision:",
                "    Reviewer: {}".format(human.get("reviewer")),
                "    Reviewed at: {}".format(human.get("reviewed_at_utc")),
                "    Approved value: {}".format(
                    human.get("approved_value") or "None"
                ),
                "    Note: {}".format(human.get("note") or "None"),
            ]
    return lines


def render_review(review):
    identity = review["repair_identity"]
    lines = [
        "NOVA DRL EVIDENCE FUSION AND HUMAN REVIEW v{}".format(VERSION),
        "=" * 82,
        "Log: {}".format(identity.get("log_number")),
        "Date: {}".format(
            identity.get("repair_date_display") or identity.get("repair_date")
        ),
        "Model: {}".format(identity.get("model")),
        "Serial: {}".format(identity.get("serial_number")),
        "Customer: {}".format(identity.get("customer")),
        "Warranty: {}".format("YES" if identity.get("warranty") else "NO"),
        "Prior decisions imported: {}".format(
            review.get("imported_prior_decision_count", 0)
        ),
        "",
    ]

    fields = review["fields"]
    lines.extend(render_scalar_field("customer_complaint", fields["customer_complaint"]))
    lines.append("")
    lines.extend(render_repair_actions(fields["repair_actions"]))
    lines.append("")

    for field in ["root_cause", "parts_replaced", "testing_performed", "final_result"]:
        lines.extend(render_scalar_field(field, fields[field]))
        lines.append("")

    lines += [
        "STATUS",
        "------",
        "Human review required: YES",
        "Accepted as final repair summary: NO",
        "Qdrant entries created: 0",
        "Future-ingestion eligible scalar fields: {}".format(
            review.get("qdrant_eligible_field_count", 0)
        ),
        "Future-ingestion eligible repair actions: {}".format(
            review.get("qdrant_eligible_repair_action_count", 0)
        ),
    ]
    return "\n".join(lines) + "\n"


def approved_fields_document(review):
    approved = {}
    complaint = review["fields"]["customer_complaint"]
    human = complaint.get("human_review", {})
    if human.get("status") == "approved":
        approved["customer_complaint"] = {
            "value": human.get("approved_value"),
            "reviewer": human.get("reviewer"),
            "reviewed_at_utc": human.get("reviewed_at_utc"),
            "decision_id": human.get("decision_id"),
            "source_candidate_ids": [
                row.get("candidate_id") for row in complaint.get("sources", [])
            ],
            "eligible_for_future_qdrant_ingestion": True,
            "qdrant_entry_created": False,
        }

    approved_actions = []
    for item in review["fields"]["repair_actions"].get("items", []):
        human = item.get("human_review", {})
        if human.get("status") != "approved":
            continue
        approved_actions.append({
            "action_id": item.get("action_id"),
            "action_number": item.get("action_number"),
            "value": human.get("approved_value"),
            "reviewer": human.get("reviewer"),
            "reviewed_at_utc": human.get("reviewed_at_utc"),
            "decision_id": human.get("decision_id"),
            "primary_source": item.get("primary_source"),
            "supporting_sources": item.get("supporting_sources"),
            "provisional_entry_metadata": item.get("provisional_entry_metadata"),
            "eligible_for_future_qdrant_ingestion": True,
            "qdrant_entry_created": False,
        })
    if approved_actions:
        approved["repair_actions"] = approved_actions

    return {
        "fusion_version": VERSION,
        "repair_identity": review.get("repair_identity"),
        "approved_fields": approved,
        "approved_field_count": len(approved),
        "approved_repair_action_count": len(approved_actions),
        "accepted_as_final_repair_summary": False,
        "qdrant_entry_created": False,
    }


def write_outputs(review, output_dir, prior_decisions=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base.write_json(output_dir / "fusion_review.json", review)
    (output_dir / "fusion_review.txt").write_text(
        render_review(review), encoding="utf-8"
    )
    base.write_json(
        output_dir / "approved_repair_fields.json",
        approved_fields_document(review),
    )
    if prior_decisions:
        base.write_json(
            output_dir / "imported_prior_decisions.json",
            prior_decisions,
        )
    (output_dir / "REVIEW_INSTRUCTIONS.txt").write_text(
        "Nova v1.5.1 never approves a repair action automatically.\n"
        "Repair actions are reviewed one item at a time.\n"
        "Use --field=repair_actions with --action-number or --action-id.\n"
        "No Qdrant entry is created by this module.\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Nova DRL Evidence Fusion and Human Review v{}".format(VERSION)
    )
    parser.add_argument(
        "collector_source",
        help=(
            "v1.4.3.2 collector root, event directory, or "
            "repair_evidence_bundle.json"
        ),
    )
    parser.add_argument("--log", required=True)
    parser.add_argument("--output-root")
    parser.add_argument(
        "--prior-review-root",
        help=(
            "Optional v1.5 event directory or v1.5 output root. "
            "Defaults to ./output/evidence_fusion_v1_5."
        ),
    )
    parser.add_argument(
        "--decision", choices=["approve", "reject", "hold"]
    )
    parser.add_argument(
        "--field",
        default="repair_actions",
        choices=[
            "customer_complaint",
            "repair_actions",
            "root_cause",
            "parts_replaced",
            "testing_performed",
            "final_result",
        ],
    )
    parser.add_argument("--action-id")
    parser.add_argument("--action-number", type=int)
    parser.add_argument("--reviewer")
    parser.add_argument("--value")
    parser.add_argument("--note")
    args = parser.parse_args()

    try:
        bundle_path, bundle = base.locate_bundle(args.collector_source, args.log)
        output_dir = (
            Path(args.output_root).expanduser().resolve()
            if args.output_root
            else default_output_dir(bundle, args.collector_source, args.log)
        )
        prior_dir = resolve_prior_dir(
            args.prior_review_root,
            bundle,
            args.collector_source,
            args.log,
        )
        prior = load_decisions_from_dir(prior_dir)
        local = load_decisions_from_dir(output_dir)

        review = build_review(
            bundle_path,
            bundle,
            args.collector_source,
            args.log,
            prior_decisions=prior,
            local_decisions=local,
        )

        decision_record = None
        if args.decision:
            if args.field == "repair_actions":
                decision_record = record_action_decision(
                    review,
                    output_dir,
                    args.decision,
                    args.reviewer,
                    action_id=args.action_id,
                    action_number=args.action_number,
                    value=args.value,
                    note=args.note,
                )
            else:
                decision_record = record_scalar_decision(
                    review,
                    output_dir,
                    args.field,
                    args.decision,
                    args.reviewer,
                    value=args.value,
                    note=args.note,
                )

            local = load_decisions_from_dir(output_dir)
            review = build_review(
                bundle_path,
                bundle,
                args.collector_source,
                args.log,
                prior_decisions=prior,
                local_decisions=local,
            )

        write_outputs(review, output_dir, prior_decisions=prior)
    except Exception as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2

    complaint = review["fields"]["customer_complaint"]
    actions = review["fields"]["repair_actions"]
    structured = actions.get("structured_source") or {}

    print()
    print("Nova DRL Evidence Fusion and Human Review v{}".format(VERSION))
    print("=" * 72)
    print("Log:                              {}".format(
        review["repair_identity"].get("log_number")
    ))
    print("Model:                            {}".format(
        review["repair_identity"].get("model")
    ))
    print("Serial:                           {}".format(
        review["repair_identity"].get("serial_number")
    ))
    print("Customer complaint review:        {}".format(
        complaint.get("human_review", {}).get("status")
    ))
    print("Prior decisions imported:         {}".format(
        review.get("imported_prior_decision_count", 0)
    ))
    print("Repair-action source version:     {}".format(
        structured.get("reader_version") or "NOT AVAILABLE"
    ))
    print("Repair-action source status:      {}".format(
        structured.get("status") or "NOT AVAILABLE"
    ))
    print("Repair-action candidates:         {}".format(
        actions.get("candidate_count", 0)
    ))
    print("Repair actions approved:          {}".format(
        actions.get("approved_action_count", 0)
    ))
    print("Repair actions pending:           {}".format(
        actions.get("pending_action_count", 0)
    ))
    print("Qdrant entries created:           0")
    if decision_record:
        suffix = ""
        if decision_record.get("action_number"):
            suffix = " action {}".format(decision_record.get("action_number"))
        print("Decision recorded:                {}{} by {}".format(
            decision_record.get("decision"),
            suffix,
            decision_record.get("reviewer"),
        ))

    if actions.get("candidate_status") == "structured_traveler_extraction_required":
        print()
        print("REPAIR ACTION READINESS:")
        print("Structured Traveler Reader repair rows are not available yet.")
        print("Raw repairs-region OCR was preserved but was NOT converted into facts.")

    print()
    print("Reports: {}".format(output_dir))
    print("NO DRL SOURCE FILES WERE MODIFIED.")
    print("NO QDRANT ENTRY CREATED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
