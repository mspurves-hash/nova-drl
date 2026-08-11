#!/usr/bin/env python3
"""
Nova DRL Validated Repair Event Knowledge Record v1.5.6
=======================================================

This stage performs NO OCR and NO vision.

It assembles already-reviewed upstream knowledge into one event-level record
without filling gaps, correcting approved wording, or converting
"not_established" into a guessed value.

Primary input:
  v1.5.5.4 event directory or approved_repair_fields_with_testing_final.json

Required upstream layers:
  v1.5.5.4 Testing / Final Result Fusion
  v1.5.4   Diagnostic Hypothesis / Root Cause Fusion

Record-level human validation is separate from field-level approval.

No DRL source files are modified.
No Qdrant entries are created.
"""

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.5.6"

FIELD_STATES = {
    "approved",
    "not_established",
    "not_available",
    "pending_review",
}

RECORD_REVIEW_STATES = {
    "pending",
    "approved",
    "hold",
    "rejected",
}


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_hash(value):
    raw = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def stable_id(*parts):
    return stable_hash(list(parts))[:16]


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_") or "unknown"


def default_rules_path():
    return (
        Path(__file__).resolve().parents[1]
        / "config"
        / "repair_event_record_rules_v1_5_6.json"
    )


def load_rules(path):
    return read_json(path)


def locate_testing_source(source):
    source = Path(source).expanduser().resolve()
    candidates = [source] if source.is_file() else [
        source / "approved_repair_fields_with_testing_final.json",
    ]
    for candidate in candidates:
        if not candidate.exists() or not candidate.is_file():
            continue
        data = read_json(candidate)
        if "repair_identity" in data and "approved_fields" in data:
            return candidate, data
    raise ValueError(
        "Could not find approved_repair_fields_with_testing_final.json beneath {}".format(
            source
        )
    )


def resolve_source_path(raw_path, base_dir):
    if not raw_path:
        return None
    path = Path(str(raw_path)).expanduser()
    if not path.is_absolute():
        path = (Path(base_dir) / path).resolve()
    else:
        path = path.resolve()
    return path


def locate_diagnostic_source(testing_source_path, testing_data):
    raw = testing_data.get("source_approved_fields_path")
    candidate = resolve_source_path(raw, testing_source_path.parent)
    if candidate and candidate.exists() and candidate.is_file():
        try:
            data = read_json(candidate)
            if (
                "repair_identity" in data
                and "approved_fields" in data
            ):
                return candidate, data, None
        except Exception as exc:
            return None, None, "diagnostic_source_read_error: {}".format(exc)

    # Conservative fallback for the standard v1.5.4 event directory.
    event_dir = testing_source_path.parent
    possible = (
        Path(str(event_dir).replace(
            "evidence_fusion_v1_5_5_4",
            "evidence_fusion_v1_5_4",
        ))
        / "approved_repair_fields_with_diagnostics.json"
    )
    if possible.exists():
        try:
            data = read_json(possible)
            return possible, data, None
        except Exception as exc:
            return None, None, "diagnostic_source_read_error: {}".format(exc)

    return (
        None,
        None,
        "required v1.5.4 diagnostic/root-cause source was not found",
    )


def identity_matches(a, b):
    keys = [
        "log_number",
        "equipment_type",
        "oem",
        "model",
        "serial_number",
        "customer",
    ]
    compared = 0
    for key in keys:
        left = a.get(key)
        right = b.get(key)
        if left in (None, "") or right in (None, ""):
            continue
        compared += 1
        if str(left).casefold() != str(right).casefold():
            return False, key, left, right
    return compared > 0, None, None, None


def source_manifest_entry(name, path, data):
    if path is None:
        return {
            "layer": name,
            "available": False,
            "path": None,
            "sha256": None,
            "fusion_version": None,
        }
    return {
        "layer": name,
        "available": True,
        "path": str(path),
        "sha256": sha256_file(path),
        "fusion_version": data.get("fusion_version"),
    }


def approved_list(fields, key):
    value = fields.get(key)
    if isinstance(value, list):
        return copy.deepcopy(value)
    return []


def approved_dict(fields, key):
    value = fields.get(key)
    if isinstance(value, dict):
        return copy.deepcopy(value)
    return None


def field_record(
    field_name,
    state,
    value=None,
    approved_items=None,
    source_layer=None,
    source_path=None,
    state_basis=None,
    notes=None,
):
    if state not in FIELD_STATES:
        raise ValueError("Invalid field state: {}".format(state))
    record = {
        "field": field_name,
        "state": state,
        "source_layer": source_layer,
        "source_path": str(source_path) if source_path else None,
        "state_basis": state_basis,
    }
    if value is not None:
        record["value"] = copy.deepcopy(value)
    if approved_items is not None:
        record["approved_items"] = copy.deepcopy(approved_items)
        record["approved_item_count"] = len(approved_items)
    if notes:
        record["notes"] = notes
    return record


def testing_state(testing_data, testing_source_path):
    fields = testing_data.get("approved_fields", {}) or {}
    approved = approved_list(fields, "testing_performed")
    review = (
        testing_data.get("testing_final_result_review", {})
        .get("testing", {})
        or {}
    )
    if approved:
        return field_record(
            "testing_performed",
            "approved",
            approved_items=approved,
            source_layer="v1.5.5.4_testing_final_result",
            source_path=testing_source_path,
            state_basis="human_approved_testing_items_present",
        )

    candidate_count = int(review.get("candidate_count", 0) or 0)
    pending_count = int(review.get("pending_count", 0) or 0)
    if candidate_count > 0 or pending_count > 0:
        return field_record(
            "testing_performed",
            "pending_review",
            approved_items=[],
            source_layer="v1.5.5.4_testing_final_result",
            source_path=testing_source_path,
            state_basis="upstream_testing_candidates_pending_human_review",
        )

    return field_record(
        "testing_performed",
        "not_established",
        approved_items=[],
        source_layer="v1.5.5.4_testing_final_result",
        source_path=testing_source_path,
        state_basis="no_human_approved_testing_items_and_no_pending_candidates",
        notes=(
            "not_established is retained as a valid evidence state; "
            "no testing fact is inferred."
        ),
    )


def final_result_state(testing_data, testing_source_path):
    fields = testing_data.get("approved_fields", {}) or {}
    approved = approved_list(fields, "final_result")
    review = (
        testing_data.get("testing_final_result_review", {})
        .get("final_result", {})
        or {}
    )
    if approved:
        return field_record(
            "final_result",
            "approved",
            approved_items=approved,
            source_layer="v1.5.5.4_testing_final_result",
            source_path=testing_source_path,
            state_basis="human_approved_final_result_present",
        )

    candidate_count = int(review.get("candidate_count", 0) or 0)
    pending_count = int(review.get("pending_count", 0) or 0)
    upstream_status = str(review.get("status") or "").strip()

    if candidate_count > 0 or pending_count > 0:
        return field_record(
            "final_result",
            "pending_review",
            approved_items=[],
            source_layer="v1.5.5.4_testing_final_result",
            source_path=testing_source_path,
            state_basis="upstream_final_result_candidates_pending_human_review",
        )

    if upstream_status == "not_established" or candidate_count == 0:
        return field_record(
            "final_result",
            "not_established",
            approved_items=[],
            source_layer="v1.5.5.4_testing_final_result",
            source_path=testing_source_path,
            state_basis="v1.5.5.4_final_result_status_not_established",
            notes=(
                "The absence of an approved final result is not converted "
                "into pass, fail, unknown, or any inferred disposition."
            ),
        )

    return field_record(
        "final_result",
        "not_established",
        approved_items=[],
        source_layer="v1.5.5.4_testing_final_result",
        source_path=testing_source_path,
        state_basis="no_human_approved_final_result",
    )


def root_cause_state(diagnostic_data, diagnostic_source_path):
    if not diagnostic_data:
        return field_record(
            "root_cause",
            "not_available",
            approved_items=[],
            source_layer="v1.5.4_diagnostic_root_cause",
            source_path=diagnostic_source_path,
            state_basis="required_diagnostic_source_unavailable",
        )

    fields = diagnostic_data.get("approved_fields", {}) or {}
    approved = approved_list(fields, "root_causes")
    if not approved:
        approved = approved_list(fields, "confirmed_root_causes")

    confirmed_count = int(
        diagnostic_data.get("confirmed_root_cause_count", 0) or 0
    )
    review = diagnostic_data.get("diagnostic_root_cause_review", {}) or {}
    root_status = (
        diagnostic_data.get("root_cause_status")
        or review.get("root_cause_status")
        or "not_established"
    )
    pending_root_candidates = int(
        review.get("root_cause_candidate_count", 0) or 0
    ) - int(review.get("confirmed_root_cause_count", 0) or 0)

    if approved:
        return field_record(
            "root_cause",
            "approved",
            approved_items=approved,
            source_layer="v1.5.4_diagnostic_root_cause",
            source_path=diagnostic_source_path,
            state_basis="human_confirmed_root_cause_present",
        )

    if confirmed_count > 0:
        # This is intentionally not promoted. Consistency checks will make it
        # a hard error because a confirmed count without approved evidence is
        # incomplete provenance.
        return field_record(
            "root_cause",
            "not_available",
            approved_items=[],
            source_layer="v1.5.4_diagnostic_root_cause",
            source_path=diagnostic_source_path,
            state_basis="confirmed_root_cause_count_without_approved_root_cause_object",
        )

    if pending_root_candidates > 0 and root_status != "not_established":
        return field_record(
            "root_cause",
            "pending_review",
            approved_items=[],
            source_layer="v1.5.4_diagnostic_root_cause",
            source_path=diagnostic_source_path,
            state_basis="unresolved_root_cause_candidates_pending_review",
        )

    return field_record(
        "root_cause",
        "not_established",
        approved_items=[],
        source_layer="v1.5.4_diagnostic_root_cause",
        source_path=diagnostic_source_path,
        state_basis="v1.5.4_root_cause_status_not_established",
        notes=(
            "Approved diagnostic hypotheses remain hypotheses and are "
            "never promoted to root cause."
        ),
    )


def build_knowledge_fields(
    testing_data,
    testing_source_path,
    diagnostic_data,
    diagnostic_source_path,
):
    fields = testing_data.get("approved_fields", {}) or {}

    complaint = approved_dict(fields, "customer_complaint")
    if complaint:
        complaint_field = field_record(
            "customer_complaint",
            "approved",
            value=complaint.get("value"),
            source_layer="approved_upstream_fields",
            source_path=testing_source_path,
            state_basis="human_approved_customer_complaint_present",
        )
        complaint_field["approved_record"] = complaint
    else:
        complaint_field = field_record(
            "customer_complaint",
            "not_established",
            source_layer="approved_upstream_fields",
            source_path=testing_source_path,
            state_basis="no_human_approved_customer_complaint_present",
        )

    actions = approved_list(fields, "repair_actions")
    actions_field = field_record(
        "repair_actions",
        "approved" if actions else "not_established",
        approved_items=actions,
        source_layer="approved_upstream_fields",
        source_path=testing_source_path,
        state_basis=(
            "human_approved_repair_actions_present"
            if actions
            else "no_human_approved_repair_actions_present"
        ),
    )

    parts = approved_list(fields, "parts_replaced")
    parts_field = field_record(
        "parts_replaced",
        "approved" if parts else "not_established",
        approved_items=parts,
        source_layer="approved_upstream_fields",
        source_path=testing_source_path,
        state_basis=(
            "human_approved_parts_replaced_present"
            if parts
            else "no_human_approved_parts_replaced_present"
        ),
    )

    hypotheses = approved_list(fields, "diagnostic_hypotheses")
    hypotheses_field = field_record(
        "diagnostic_hypotheses",
        "approved" if hypotheses else "not_established",
        approved_items=hypotheses,
        source_layer="v1.5.4_diagnostic_root_cause",
        source_path=diagnostic_source_path or testing_source_path,
        state_basis=(
            "human_approved_diagnostic_hypotheses_present"
            if hypotheses
            else "no_human_approved_diagnostic_hypotheses_present"
        ),
        notes=(
            "Diagnostic hypotheses are independent knowledge and do not "
            "establish root cause."
        ),
    )

    return {
        "customer_complaint": complaint_field,
        "repair_actions": actions_field,
        "parts_replaced": parts_field,
        "diagnostic_hypotheses": hypotheses_field,
        "root_cause": root_cause_state(
            diagnostic_data,
            diagnostic_source_path,
        ),
        "testing_performed": testing_state(
            testing_data,
            testing_source_path,
        ),
        "final_result": final_result_state(
            testing_data,
            testing_source_path,
        ),
    }


def check_result(check_id, severity, passed, message, details=None):
    return {
        "check_id": check_id,
        "severity": severity,
        "passed": bool(passed),
        "message": message,
        "details": details,
    }


def build_consistency_checks(
    rules,
    testing_data,
    testing_source_path,
    diagnostic_data,
    diagnostic_source_path,
    knowledge_fields,
):
    checks = []
    identity = testing_data.get("repair_identity", {}) or {}

    for key in rules.get("required_identity_fields", []):
        value = identity.get(key)
        checks.append(check_result(
            "identity_{}".format(key),
            "error",
            value not in (None, ""),
            "{} present".format(key),
            {"value": value},
        ))

    expected_testing = (
        rules.get("required_source_versions", {})
        .get("testing_final_result")
    )
    checks.append(check_result(
        "testing_source_version",
        "error",
        str(testing_data.get("fusion_version")) == str(expected_testing),
        "testing/final source version is {}".format(expected_testing),
        {
            "expected": expected_testing,
            "actual": testing_data.get("fusion_version"),
            "path": str(testing_source_path),
        },
    ))

    expected_diag = (
        rules.get("required_source_versions", {})
        .get("diagnostic_root_cause")
    )
    if diagnostic_data is None:
        checks.append(check_result(
            "diagnostic_source_available",
            "error",
            False,
            "required diagnostic/root-cause source is available",
            {"path": None},
        ))
    else:
        checks.append(check_result(
            "diagnostic_source_available",
            "error",
            True,
            "required diagnostic/root-cause source is available",
            {"path": str(diagnostic_source_path)},
        ))
        checks.append(check_result(
            "diagnostic_source_version",
            "error",
            str(diagnostic_data.get("fusion_version")) == str(expected_diag),
            "diagnostic/root-cause source version is {}".format(expected_diag),
            {
                "expected": expected_diag,
                "actual": diagnostic_data.get("fusion_version"),
                "path": str(diagnostic_source_path),
            },
        ))
        same, mismatch_key, left, right = identity_matches(
            identity,
            diagnostic_data.get("repair_identity", {}) or {},
        )
        checks.append(check_result(
            "upstream_identity_match",
            "error",
            same,
            "testing/final and diagnostic sources describe the same repair event",
            {
                "mismatch_key": mismatch_key,
                "testing_value": left,
                "diagnostic_value": right,
            },
        ))

    fields = testing_data.get("approved_fields", {}) or {}

    count_checks = [
        (
            "approved_repair_action_count",
            len(approved_list(fields, "repair_actions")),
        ),
        (
            "approved_parts_replaced_count",
            len(approved_list(fields, "parts_replaced")),
        ),
        (
            "approved_diagnostic_hypothesis_count",
            len(approved_list(fields, "diagnostic_hypotheses")),
        ),
        (
            "approved_testing_item_count",
            len(approved_list(fields, "testing_performed")),
        ),
        (
            "approved_final_result_count",
            len(approved_list(fields, "final_result")),
        ),
    ]
    for source_key, actual in count_checks:
        reported = int(testing_data.get(source_key, 0) or 0)
        checks.append(check_result(
            "count_{}".format(source_key),
            "error",
            reported == actual,
            "{} matches approved source objects".format(source_key),
            {"reported": reported, "actual": actual},
        ))

    approved_group_count = sum(
        1
        for key in [
            "customer_complaint",
            "repair_actions",
            "parts_replaced",
            "diagnostic_hypotheses",
            "testing_performed",
            "final_result",
        ]
        if key in fields
        and (
            isinstance(fields.get(key), dict)
            or (
                isinstance(fields.get(key), list)
                and len(fields.get(key)) > 0
            )
        )
    )
    reported_group_count = int(
        testing_data.get("approved_field_count", 0) or 0
    )
    checks.append(check_result(
        "approved_field_group_count",
        "error",
        reported_group_count == approved_group_count,
        "approved_field_count matches populated approved field groups",
        {
            "reported": reported_group_count,
            "actual": approved_group_count,
        },
    ))

    hypotheses = approved_list(fields, "diagnostic_hypotheses")
    promoted = [
        item
        for item in hypotheses
        if bool(item.get("confirmed_root_cause"))
    ]
    root_field = knowledge_fields["root_cause"]
    checks.append(check_result(
        "hypothesis_not_auto_promoted",
        "error",
        not (
            promoted
            and root_field.get("state") == "approved"
            and not root_field.get("approved_items")
        ),
        "diagnostic hypotheses are not automatically promoted to root cause",
        {
            "approved_hypotheses_marked_confirmed": len(promoted),
            "root_cause_state": root_field.get("state"),
        },
    ))

    if diagnostic_data is not None:
        confirmed_count = int(
            diagnostic_data.get("confirmed_root_cause_count", 0) or 0
        )
        root_items = root_field.get("approved_items", []) or []
        checks.append(check_result(
            "confirmed_root_cause_provenance",
            "error",
            confirmed_count == len(root_items),
            "confirmed root-cause count matches approved root-cause objects",
            {
                "reported_confirmed_count": confirmed_count,
                "approved_root_cause_objects": len(root_items),
            },
        ))

    testing_review = (
        testing_data.get("testing_final_result_review", {})
        .get("testing", {})
        or {}
    )
    final_review = (
        testing_data.get("testing_final_result_review", {})
        .get("final_result", {})
        or {}
    )

    checks.append(check_result(
        "testing_not_established_preserved",
        "error",
        not (
            knowledge_fields["testing_performed"]["state"]
            == "not_established"
            and (
                int(testing_review.get("candidate_count", 0) or 0) > 0
                or int(testing_review.get("pending_count", 0) or 0) > 0
            )
        ),
        "testing not_established is used only when no candidate remains pending",
    ))

    checks.append(check_result(
        "final_not_established_preserved",
        "error",
        not (
            knowledge_fields["final_result"]["state"]
            == "not_established"
            and (
                int(final_review.get("candidate_count", 0) or 0) > 0
                or int(final_review.get("pending_count", 0) or 0) > 0
            )
        ),
        "final_result not_established is used only when no candidate remains pending",
    ))

    checks.append(check_result(
        "no_qdrant_write",
        "error",
        not bool(testing_data.get("qdrant_entry_created")),
        "upstream testing/final layer has not created a Qdrant entry",
    ))

    checks.append(check_result(
        "no_final_summary_acceptance",
        "error",
        not bool(testing_data.get("accepted_as_final_repair_summary")),
        "upstream record is not accepted as a final repair summary",
    ))

    error_failures = [
        c for c in checks
        if c["severity"] == "error" and not c["passed"]
    ]
    warning_failures = [
        c for c in checks
        if c["severity"] == "warning" and not c["passed"]
    ]

    return {
        "check_count": len(checks),
        "hard_error_count": len(error_failures),
        "warning_count": len(warning_failures),
        "eligible_for_record_level_human_validation": (
            len(error_failures) == 0
        ),
        "checks": checks,
    }


def default_output_dir(testing_data):
    identity = testing_data.get("repair_identity", {}) or {}
    folder = "_".join(
        safe_name(v)
        for v in [
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
        / "evidence_fusion_v1_5_6"
        / folder
        / "events"
        / str(identity.get("log_number") or "unknown")
    )


def decision_path(output_dir):
    return Path(output_dir) / "human_record_review_decisions.json"


def load_decisions(output_dir):
    path = decision_path(output_dir)
    if not path.exists():
        return []
    data = read_json(path)
    return data if isinstance(data, list) else []


def latest_applicable_decision(
    decisions,
    record_id,
    field_state_digest,
    source_digest,
):
    applicable = []
    stale = []
    for decision in decisions:
        matches = (
            decision.get("record_id") == record_id
            and decision.get("field_state_digest") == field_state_digest
            and decision.get("source_digest") == source_digest
        )
        if matches:
            applicable.append(decision)
        else:
            stale.append(decision)
    return (
        applicable[-1] if applicable else None,
        len(stale),
    )


def make_record_id(identity, source_manifest):
    stable_sources = [
        {
            "layer": row.get("layer"),
            "sha256": row.get("sha256"),
            "fusion_version": row.get("fusion_version"),
        }
        for row in source_manifest
    ]
    return stable_id(
        VERSION,
        identity.get("log_number"),
        identity.get("model"),
        identity.get("serial_number"),
        stable_sources,
    )


def build_record(
    rules,
    testing_source_path,
    testing_data,
    diagnostic_source_path,
    diagnostic_data,
    diagnostic_warning=None,
    output_dir=None,
):
    identity = copy.deepcopy(
        testing_data.get("repair_identity", {}) or {}
    )

    source_manifest = [
        source_manifest_entry(
            "testing_final_result",
            testing_source_path,
            testing_data,
        ),
        source_manifest_entry(
            "diagnostic_root_cause",
            diagnostic_source_path,
            diagnostic_data or {},
        ),
    ]

    evidence_bundle = testing_data.get("source_evidence_bundle_path")
    if evidence_bundle:
        bundle_path = Path(evidence_bundle)
        source_manifest.append({
            "layer": "repair_evidence_bundle",
            "available": bundle_path.exists(),
            "path": str(bundle_path),
            "sha256": (
                sha256_file(bundle_path)
                if bundle_path.exists() and bundle_path.is_file()
                else None
            ),
            "fusion_version": "1.4.3.2",
        })

    knowledge_fields = build_knowledge_fields(
        testing_data,
        testing_source_path,
        diagnostic_data,
        diagnostic_source_path,
    )

    checks = build_consistency_checks(
        rules,
        testing_data,
        testing_source_path,
        diagnostic_data,
        diagnostic_source_path,
        knowledge_fields,
    )

    field_state_digest = stable_hash(knowledge_fields)
    source_digest = stable_hash([
        {
            "layer": row.get("layer"),
            "sha256": row.get("sha256"),
            "fusion_version": row.get("fusion_version"),
        }
        for row in source_manifest
    ])
    record_id = make_record_id(identity, source_manifest)

    decisions = load_decisions(output_dir) if output_dir else []
    decision, stale_count = latest_applicable_decision(
        decisions,
        record_id,
        field_state_digest,
        source_digest,
    )

    review_status = "pending"
    if decision:
        review_status = decision.get("decision", "pending")
        if review_status not in RECORD_REVIEW_STATES:
            review_status = "pending"

    state_counts = {}
    for row in knowledge_fields.values():
        state = row["state"]
        state_counts[state] = state_counts.get(state, 0) + 1

    record = {
        "record_schema_version": VERSION,
        "record_id": record_id,
        "generated_at_utc": now_utc(),
        "repair_identity": identity,
        "knowledge_fields": knowledge_fields,
        "field_state_counts": state_counts,
        "field_state_digest": field_state_digest,
        "source_digest": source_digest,
        "source_manifest": source_manifest,
        "consistency_checks": checks,
        "record_human_validation": {
            "status": review_status,
            "decision_id": decision.get("decision_id") if decision else None,
            "reviewer": decision.get("reviewer") if decision else None,
            "reviewed_at_utc": (
                decision.get("reviewed_at_utc") if decision else None
            ),
            "note": decision.get("note") if decision else None,
            "stale_prior_decisions_ignored": stale_count,
            "approved_record_matches_current_sources": bool(
                decision and review_status == "approved"
            ),
        },
        "diagnostic_source_warning": diagnostic_warning,
        "record_is_human_validated": (
            review_status == "approved"
            and checks["hard_error_count"] == 0
        ),
        "accepted_as_final_repair_summary": False,
        "record_level_qdrant_eligible": False,
        "qdrant_entry_created": False,
        "policy": {
            "new_ocr_performed": False,
            "new_vision_performed": False,
            "new_facts_created_automatically": False,
            "approved_wording_modified": False,
            "not_established_preserved_as_valid_state": True,
            "diagnostic_hypothesis_auto_promoted_to_root_cause": False,
            "qdrant_write_enabled": False,
        },
    }
    return record


def record_decision(
    output_dir,
    decision,
    reviewer,
    note,
    current_record,
):
    if not reviewer:
        raise ValueError("--reviewer is required.")

    if decision == "approve-record":
        stored = "approved"
        if (
            current_record.get("consistency_checks", {})
            .get("hard_error_count", 0)
            > 0
        ):
            raise ValueError(
                "Record approval blocked: consistency checks contain hard errors."
            )
    elif decision == "hold-record":
        stored = "hold"
    elif decision == "reject-record":
        stored = "rejected"
    else:
        raise ValueError("Unsupported record decision.")

    row = {
        "decision_id": stable_id(
            VERSION,
            current_record["record_id"],
            stored,
            reviewer,
            now_utc(),
            current_record["field_state_digest"],
            current_record["source_digest"],
        ),
        "record_id": current_record["record_id"],
        "decision": stored,
        "reviewer": str(reviewer).strip(),
        "reviewed_at_utc": now_utc(),
        "note": note,
        "field_state_digest": current_record["field_state_digest"],
        "source_digest": current_record["source_digest"],
        "hard_error_count_at_review": (
            current_record["consistency_checks"]["hard_error_count"]
        ),
        "record_schema_version": VERSION,
        "accepted_as_final_repair_summary": False,
        "qdrant_entry_created": False,
    }

    decisions = load_decisions(output_dir)
    decisions.append(row)
    write_json(decision_path(output_dir), decisions)
    return row


def short_value(record):
    if "value" in record:
        return str(record.get("value"))
    items = record.get("approved_items")
    if isinstance(items, list):
        return "{} approved item{}".format(
            len(items),
            "" if len(items) == 1 else "s",
        )
    return ""


def render_text(record):
    identity = record.get("repair_identity", {})
    fields = record.get("knowledge_fields", {})
    checks = record.get("consistency_checks", {})
    review = record.get("record_human_validation", {})

    lines = [
        "NOVA DRL VALIDATED REPAIR EVENT KNOWLEDGE RECORD v{}".format(VERSION),
        "=" * 92,
        "Record ID: {}".format(record.get("record_id")),
        "Log: {}".format(identity.get("log_number")),
        "Model: {}".format(identity.get("model")),
        "Serial: {}".format(identity.get("serial_number")),
        "Customer: {}".format(identity.get("customer")),
        "Warranty: {}".format(identity.get("warranty")),
        "",
        "RECORD VALIDATION",
        "-----------------",
        "Human validation status: {}".format(review.get("status")),
        "Record is human validated: {}".format(
            "YES" if record.get("record_is_human_validated") else "NO"
        ),
        "Hard consistency errors: {}".format(
            checks.get("hard_error_count", 0)
        ),
        "Warnings: {}".format(checks.get("warning_count", 0)),
        "Eligible for human validation: {}".format(
            "YES"
            if checks.get("eligible_for_record_level_human_validation")
            else "NO"
        ),
        "Stale prior decisions ignored: {}".format(
            review.get("stale_prior_decisions_ignored", 0)
        ),
        "",
        "KNOWLEDGE FIELDS",
        "----------------",
    ]

    order = [
        "customer_complaint",
        "repair_actions",
        "parts_replaced",
        "diagnostic_hypotheses",
        "root_cause",
        "testing_performed",
        "final_result",
    ]
    for key in order:
        row = fields[key]
        lines.append(
            "{:<25} {:<16} {}".format(
                key.upper(),
                row.get("state"),
                short_value(row),
            ).rstrip()
        )

    lines += [
        "",
        "SOURCE MANIFEST",
        "---------------",
    ]
    for source in record.get("source_manifest", []):
        lines += [
            "{}:".format(source.get("layer")),
            "  available: {}".format(source.get("available")),
            "  version: {}".format(source.get("fusion_version")),
            "  path: {}".format(source.get("path")),
            "  sha256: {}".format(source.get("sha256")),
        ]

    lines += [
        "",
        "CONSISTENCY CHECKS",
        "------------------",
    ]
    for check in checks.get("checks", []):
        lines.append(
            "{} [{}] {} - {}".format(
                "PASS" if check.get("passed") else "FAIL",
                check.get("severity"),
                check.get("check_id"),
                check.get("message"),
            )
        )

    lines += [
        "",
        "POLICY",
        "------",
        "New OCR performed: NO",
        "New vision performed: NO",
        "Approved wording modified: NO",
        "Diagnostic hypothesis automatically promoted to root cause: NO",
        "not_established converted to guessed value: NO",
        "Accepted as final repair summary: NO",
        "Record-level Qdrant eligible: NO",
        "Qdrant entries created: 0",
    ]
    return "\n".join(lines) + "\n"


def write_outputs(record, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        output_dir / "validated_repair_event_record.json",
        record,
    )
    write_json(
        output_dir / "record_validation_checks.json",
        record.get("consistency_checks", {}),
    )
    write_json(
        output_dir / "record_source_manifest.json",
        {
            "record_schema_version": VERSION,
            "record_id": record.get("record_id"),
            "source_digest": record.get("source_digest"),
            "sources": record.get("source_manifest", []),
            "qdrant_entry_created": False,
        },
    )
    (output_dir / "validated_repair_event_record.txt").write_text(
        render_text(record),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Nova DRL Validated Repair Event Knowledge Record "
            "v{}".format(VERSION)
        )
    )
    parser.add_argument(
        "source",
        help=(
            "v1.5.5.4 event directory or "
            "approved_repair_fields_with_testing_final.json"
        ),
    )
    parser.add_argument(
        "--rules",
        default=str(default_rules_path()),
    )
    parser.add_argument("--output-root")
    parser.add_argument(
        "--decision",
        choices=[
            "approve-record",
            "hold-record",
            "reject-record",
        ],
    )
    parser.add_argument("--reviewer")
    parser.add_argument("--note")
    args = parser.parse_args()

    try:
        rules = load_rules(args.rules)
        testing_path, testing_data = locate_testing_source(
            args.source
        )
        diagnostic_path, diagnostic_data, diagnostic_warning = (
            locate_diagnostic_source(testing_path, testing_data)
        )

        output_dir = (
            Path(args.output_root).expanduser().resolve()
            if args.output_root
            else default_output_dir(testing_data)
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        record = build_record(
            rules,
            testing_path,
            testing_data,
            diagnostic_path,
            diagnostic_data,
            diagnostic_warning,
            output_dir,
        )

        decision_row = None
        if args.decision:
            decision_row = record_decision(
                output_dir,
                args.decision,
                args.reviewer,
                args.note,
                record,
            )
            record = build_record(
                rules,
                testing_path,
                testing_data,
                diagnostic_path,
                diagnostic_data,
                diagnostic_warning,
                output_dir,
            )

        write_outputs(record, output_dir)

    except Exception as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2

    identity = record.get("repair_identity", {})
    fields = record.get("knowledge_fields", {})
    checks = record.get("consistency_checks", {})
    validation = record.get("record_human_validation", {})

    print()
    print(
        "Nova DRL Validated Repair Event Knowledge Record "
        "v{}".format(VERSION)
    )
    print("=" * 80)
    print("Record ID:                    {}".format(
        record.get("record_id")
    ))
    print("Log:                          {}".format(
        identity.get("log_number")
    ))
    print("Model:                        {}".format(
        identity.get("model")
    ))
    print("Serial:                       {}".format(
        identity.get("serial_number")
    ))
    print("Testing/final source:         {}".format(
        testing_data.get("fusion_version")
    ))
    print("Diagnostic/root-cause source: {}".format(
        diagnostic_data.get("fusion_version")
        if diagnostic_data
        else "NOT AVAILABLE"
    ))
    print("Hard consistency errors:      {}".format(
        checks.get("hard_error_count", 0)
    ))
    print("Record review status:         {}".format(
        validation.get("status")
    ))
    print("Record human validated:       {}".format(
        "YES" if record.get("record_is_human_validated") else "NO"
    ))
    print()
    print("FIELD STATES")
    print("------------")
    for key in [
        "customer_complaint",
        "repair_actions",
        "parts_replaced",
        "diagnostic_hypotheses",
        "root_cause",
        "testing_performed",
        "final_result",
    ]:
        row = fields[key]
        print("{:<29} {}".format(
            key + ":",
            row.get("state"),
        ))
    print()
    print("Accepted as final summary:    NO")
    print("Record-level Qdrant eligible: NO")
    print("Qdrant entries created:       0")
    if decision_row:
        print(
            "Decision recorded:            {} by {}".format(
                decision_row.get("decision"),
                decision_row.get("reviewer"),
            )
        )
    print()
    print("Reports: {}".format(output_dir))
    print("NO OCR OR VISION WAS RUN.")
    print("NO APPROVED UPSTREAM WORDING WAS MODIFIED.")
    print("NO DRL SOURCE FILES WERE MODIFIED.")
    print("NO QDRANT ENTRY CREATED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
