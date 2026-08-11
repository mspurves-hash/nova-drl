#!/usr/bin/env python3
"""
Nova DRL Evidence Fusion and Human Review v1.5
==============================================

Creates source-backed, field-level repair fact candidates from evidence bundles
produced by Nova Repair Evidence Collector v1.4.3.2.

v1.5.0 supports the first high-value field:
    customer_complaint

Safety:
- No DRL source file is modified.
- No collector artifact is modified.
- No Qdrant API is called.
- No field is approved automatically.
- Human approvals are audit logged.
"""

import argparse
import difflib
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.5.0"

FIELD_ORDER = [
    "customer_complaint",
    "root_cause",
    "repair_actions",
    "parts_replaced",
    "testing_performed",
    "final_result",
]

AUTHORITY_PRIORITY = {
    "primary_repair_anchor": 100,
    "root_cause_evidence": 95,
    "final_test_evidence": 90,
    "procedure_completion_evidence": 80,
    "repair_supporting_evidence": 70,
    "unclassified_evidence": 20,
}

ROLE_PRIORITY = {
    "traveler": 100,
    "failure_analysis_report": 95,
    "robot_test_report": 90,
    "robot_checklist": 80,
}

TRAVELER_COMPLAINT_PATTERNS = [
    re.compile(
        r"(?:customer\s+fa(?:\s*\(summary\))?|customer\s+complaint|"
        r"customer\s+problem(?:/symptom)?(?:\s+description)?|\bfa)\s*[:\-]\s*"
        r"([^\n]{3,180})",
        re.IGNORECASE,
    ),
]


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_") or "unknown"


def evidence_id(*parts):
    joined = "\n".join(str(x or "") for x in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def locate_bundle(source, log_number):
    source = Path(source).expanduser().resolve()
    candidates = []

    if source.is_file():
        candidates.append(source)
    else:
        candidates.extend([
            source / "repair_evidence_bundle.json",
            source / "events" / str(log_number) / "repair_evidence_bundle.json",
            source / str(log_number) / "repair_evidence_bundle.json",
        ])

    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            bundle = read_json(candidate)
            actual = str(bundle.get("repair_event", {}).get("log_number") or "")
            if actual and actual != str(log_number):
                continue
            return candidate, bundle

    raise ValueError(
        "Repair evidence bundle not found for log {} beneath {}".format(
            log_number, source
        )
    )


def normalize_for_compare(value):
    value = str(value or "")
    value = value.replace("¥", "Y")
    value = value.replace("’", "'").replace("‘", "'")
    value = value.replace("–", "-").replace("—", "-")
    value = re.sub(r"[^A-Za-z0-9]+", " ", value).lower()
    return re.sub(r"\s+", " ", value).strip()


def token_sequence(value):
    return normalize_for_compare(value).split()


def candidate_similarity(left, right):
    a = normalize_for_compare(left)
    b = normalize_for_compare(right)
    if not a or not b:
        return 0.0
    sequence = difflib.SequenceMatcher(None, a, b).ratio()
    at = set(a.split())
    bt = set(b.split())
    jaccard = len(at & bt) / max(1, len(at | bt))
    return round(max(sequence, jaccard), 4)


def clean_complaint_value(value):
    value = str(value or "").strip()
    value = re.split(r"\s+[\[|]", value, maxsplit=1)[0]
    value = value.strip(" \t\r\n|[]{}<>;:")
    value = re.sub(r"\s+", " ", value)
    value = value.rstrip(" .;,|")
    return value or None


def readability_score(candidate):
    value = str(candidate.get("clean_value") or "")
    if not value:
        return -100000
    weird = len(re.findall(r"[^A-Za-z0-9 .,'/#()+\-]", value))
    alpha = sum(1 for c in value if c.isalpha())
    authority = AUTHORITY_PRIORITY.get(candidate.get("authority"), 0)
    role = ROLE_PRIORITY.get(candidate.get("document_role"), 0)
    return authority * 10 + role * 5 + alpha + len(token_sequence(value)) * 3 - weird * 30


def make_candidate(
    field,
    raw_value,
    document_role,
    authority,
    source_path,
    source_document,
    extraction_method,
    page_number=None,
    region=None,
    source_detail=None,
):
    clean = clean_complaint_value(raw_value)
    return {
        "candidate_id": evidence_id(
            field, source_path, page_number, region, raw_value
        ),
        "field": field,
        "raw_value": str(raw_value or ""),
        "clean_value": clean,
        "normalized_value": normalize_for_compare(clean),
        "document_role": document_role,
        "authority": authority,
        "source_path": str(source_path or ""),
        "source_document": str(source_document or ""),
        "page_number": page_number,
        "region": region,
        "extraction_method": extraction_method,
        "source_detail": source_detail,
        "independent_source_key": str(source_path or source_document or ""),
        "accepted_as_repair_fact": False,
        "human_review_required": True,
    }


def evidence_lookup(event):
    rows = event.get("evidence_files", [])
    by_relative = {str(row.get("relative_path")): row for row in rows}
    by_name = {Path(str(row.get("relative_path"))).name: row for row in rows}
    return by_relative, by_name


def collect_document_complaints(event):
    output = []
    comparison = event.get("cross_document_complaint_comparison") or {}
    by_relative, by_name = evidence_lookup(event)

    for row in comparison.get("raw_candidates", []):
        relative = str(row.get("source_document") or "")
        evidence = by_relative.get(relative) or by_name.get(Path(relative).name) or {}
        raw = row.get("raw_value")
        if not clean_complaint_value(raw):
            continue
        output.append(make_candidate(
            field="customer_complaint",
            raw_value=raw,
            document_role=(
                row.get("document_role")
                or evidence.get("role")
                or "supporting_document"
            ),
            authority=evidence.get("authority") or "repair_supporting_evidence",
            source_path=evidence.get("source_path") or relative,
            source_document=relative,
            extraction_method=row.get("source_method") or "known_form_header_extraction",
            page_number=row.get("page_number"),
            source_detail="v1.4.3.2 cross-document complaint candidate",
        ))
    return output


def extract_traveler_complaints_from_text(text):
    rows = []
    for pattern in TRAVELER_COMPLAINT_PATTERNS:
        for match in pattern.finditer(str(text or "")):
            value = clean_complaint_value(match.group(1))
            if value and len(token_sequence(value)) >= 2:
                rows.append(value)

    seen = set()
    result = []
    for value in rows:
        key = normalize_for_compare(value)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def collect_traveler_complaints(event):
    artifacts = event.get("derived_traveler_artifacts", [])
    json_paths = [
        Path(row.get("path"))
        for row in artifacts
        if row.get("role") == "region_ocr"
        and str(row.get("path", "")).lower().endswith("traveler_regions.json")
    ]
    output = []

    for path in json_paths:
        if not path.exists():
            continue
        try:
            data = read_json(path)
        except Exception:
            continue

        region = data.get("regions", {}).get("special_notes", {})
        selected_text = region.get("selected_text", "")
        values = extract_traveler_complaints_from_text(selected_text)

        for value in values:
            output.append(make_candidate(
                field="customer_complaint",
                raw_value=value,
                document_role="traveler",
                authority="primary_repair_anchor",
                source_path=data.get("source_path") or path,
                source_document=(
                    data.get("relative_path")
                    or Path(str(data.get("source_path") or path)).name
                ),
                extraction_method="traveler_region_ocr_tesseract",
                region="special_notes",
                source_detail=str(path),
            ))
    return output


def dedupe_candidates(candidates):
    output = []
    seen = set()
    for row in candidates:
        key = (
            row.get("field"),
            row.get("independent_source_key"),
            row.get("normalized_value"),
            row.get("page_number"),
            row.get("region"),
        )
        if not row.get("clean_value") or key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def cluster_candidates(candidates, threshold=0.82):
    clusters = []
    for candidate in candidates:
        placed = False
        for cluster in clusters:
            similarities = [
                candidate_similarity(candidate["clean_value"], other["clean_value"])
                for other in cluster
            ]
            if similarities and max(similarities) >= threshold:
                cluster.append(candidate)
                placed = True
                break
        if not placed:
            clusters.append([candidate])
    return clusters


def choose_canonical(cluster):
    if not cluster:
        return None, None
    sequences = {tuple(token_sequence(row["clean_value"])) for row in cluster}
    if len(sequences) != 1:
        return None, "meaningful_word_sequences_differ"
    selected = max(cluster, key=readability_score)
    value = selected["clean_value"].strip().rstrip(" .;,|")
    return value, "source_wording_with_case_and_terminal_punctuation_normalization_only"


def pairwise_agreement(cluster):
    comparisons = []
    values = []
    for i, left in enumerate(cluster):
        for right in cluster[i + 1:]:
            score = candidate_similarity(left["clean_value"], right["clean_value"])
            comparisons.append({
                "left_candidate_id": left["candidate_id"],
                "right_candidate_id": right["candidate_id"],
                "similarity": score,
            })
            values.append(score)

    return {
        "comparisons": comparisons,
        "minimum_similarity": min(values) if values else 1.0,
        "maximum_similarity": max(values) if values else 1.0,
        "average_similarity": round(sum(values) / len(values), 4) if values else 1.0,
    }


def build_customer_complaint_field(event):
    candidates = dedupe_candidates(
        collect_document_complaints(event)
        + collect_traveler_complaints(event)
    )
    clusters = cluster_candidates(candidates)

    if not clusters:
        return {
            "field": "customer_complaint",
            "candidate_status": "no_supported_candidate",
            "canonical_candidate": None,
            "confidence": "none",
            "independent_source_count": 0,
            "sources": [],
            "human_review": {"status": "not_ready"},
            "accepted_as_human_reviewed_fact": False,
            "qdrant": {
                "entry_created": False,
                "eligible_for_future_ingestion": False,
                "reason": "no_supported_candidate",
            },
        }

    ranked = sorted(
        clusters,
        key=lambda cluster: (
            len({row["independent_source_key"] for row in cluster}),
            sum(
                AUTHORITY_PRIORITY.get(row.get("authority"), 0)
                for row in cluster
            ),
            len(cluster),
        ),
        reverse=True,
    )

    cluster = ranked[0]
    independent_sources = {
        row["independent_source_key"] for row in cluster
    }
    canonical, method = choose_canonical(cluster)
    agreement = pairwise_agreement(cluster)
    source_count = len(independent_sources)

    if canonical and source_count >= 3 and agreement["minimum_similarity"] >= 0.95:
        confidence = "high"
        status = "strong_cross_source_agreement"
    elif canonical and source_count >= 2 and agreement["minimum_similarity"] >= 0.92:
        confidence = "medium_high"
        status = "strong_cross_source_agreement"
    elif source_count >= 2 and agreement["minimum_similarity"] >= 0.80:
        confidence = "medium"
        status = "possible_cross_source_agreement"
    else:
        confidence = "low"
        status = "insufficient_agreement"

    ready = canonical is not None and source_count >= 2

    return {
        "field": "customer_complaint",
        "candidate_status": status,
        "canonical_candidate": canonical,
        "canonicalization": {
            "method": method,
            "meaning_changed": False if canonical else None,
        },
        "confidence": confidence,
        "candidate_count": len(candidates),
        "cluster_candidate_count": len(cluster),
        "independent_source_count": source_count,
        "source_roles": sorted({row["document_role"] for row in cluster}),
        "agreement": agreement,
        "sources": cluster,
        "other_candidate_clusters": [
            [row["candidate_id"] for row in other]
            for other in ranked[1:]
        ],
        "human_review": {
            "status": "pending" if ready else "not_ready",
            "recommended_decision": (
                "approve" if ready and confidence == "high" else "review"
            ),
            "reviewer": None,
            "reviewed_at_utc": None,
            "approved_value": None,
            "note": None,
        },
        "accepted_as_human_reviewed_fact": False,
        "qdrant": {
            "entry_created": False,
            "eligible_for_future_ingestion": False,
            "reason": "pending_human_review" if ready else "field_not_ready",
        },
    }


def empty_future_field(name):
    return {
        "field": name,
        "candidate_status": "not_established_in_v1_5_0",
        "canonical_candidate": None,
        "confidence": "none",
        "independent_source_count": 0,
        "sources": [],
        "human_review": {"status": "not_ready"},
        "accepted_as_human_reviewed_fact": False,
        "qdrant": {
            "entry_created": False,
            "eligible_for_future_ingestion": False,
            "reason": "not_established",
        },
    }


def latest_decisions(decisions):
    result = {}
    for decision in decisions:
        result[decision.get("field")] = decision
    return result


def apply_saved_decisions(review, decisions):
    for field, decision in latest_decisions(decisions).items():
        record = review.get("fields", {}).get(field)
        if not record:
            continue

        status = decision.get("decision")
        record["human_review"] = {
            "status": status,
            "recommended_decision": record.get("human_review", {}).get(
                "recommended_decision"
            ),
            "reviewer": decision.get("reviewer"),
            "reviewed_at_utc": decision.get("reviewed_at_utc"),
            "approved_value": (
                decision.get("value") if status == "approved" else None
            ),
            "note": decision.get("note"),
            "decision_id": decision.get("decision_id"),
        }

        approved = status == "approved"
        record["accepted_as_human_reviewed_fact"] = approved
        record["qdrant"] = {
            "entry_created": False,
            "eligible_for_future_ingestion": approved,
            "reason": (
                "human_approved_waiting_for_future_ingestion_pipeline"
                if approved
                else "human_review_{}".format(status)
            ),
        }


def build_review(bundle_path, bundle, decisions=None):
    event = bundle.get("repair_event", {})
    meta = bundle.get("serial_metadata", {})
    complaint = build_customer_complaint_field(event)

    fields = {"customer_complaint": complaint}
    for field in FIELD_ORDER:
        if field not in fields:
            fields[field] = empty_future_field(field)

    review = {
        "fusion_version": VERSION,
        "created_at_utc": now_utc(),
        "source_bundle_path": str(bundle_path),
        "collector_version": bundle.get("collector_version"),
        "scope": "repair_event_field_review",
        "repair_identity": {
            "log_number": event.get("log_number"),
            "repair_date": event.get("repair_date"),
            "repair_date_display": event.get("repair_date_display"),
            "daily_sequence": event.get("daily_sequence"),
            "equipment_type": meta.get("equipment_type"),
            "oem": meta.get("oem"),
            "model": meta.get("model"),
            "serial_number": meta.get("serial_number"),
            "customer": meta.get("customer"),
            "site_code": meta.get("site_code"),
            "site_name": meta.get("site_name"),
            "warranty": event.get("warranty"),
        },
        "fields": fields,
        "human_review_required": True,
        "accepted_as_final_repair_summary": False,
        "qdrant_entry_created": False,
        "qdrant_eligible_field_count": 0,
        "notes": [
            "v1.5.0 fuses customer_complaint only.",
            "Other repair fields remain unestablished.",
            "Raw evidence is never replaced by the canonical candidate.",
        ],
    }

    apply_saved_decisions(review, decisions or [])
    review["qdrant_eligible_field_count"] = sum(
        1
        for value in review["fields"].values()
        if value.get("qdrant", {}).get("eligible_for_future_ingestion")
    )
    return review


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


def record_decision(
    review,
    output_dir,
    field,
    decision,
    reviewer,
    value=None,
    note=None,
):
    if field not in review.get("fields", {}):
        raise ValueError("Unknown review field: {}".format(field))
    if not reviewer or not reviewer.strip():
        raise ValueError("--reviewer is required for a human decision")

    decision = decision.lower().strip()
    if decision not in {"approve", "reject", "hold"}:
        raise ValueError("Decision must be approve, reject, or hold")

    field_record = review["fields"][field]

    if decision == "approve":
        approved_value = value or field_record.get("canonical_candidate")
        if not approved_value:
            raise ValueError(
                "No canonical candidate exists. Supply --value for an edited approval."
            )
        stored_decision = "approved"
    elif decision == "reject":
        approved_value = None
        stored_decision = "rejected"
    else:
        approved_value = None
        stored_decision = "hold"

    record = {
        "decision_id": evidence_id(
            field, decision, reviewer, now_utc(), approved_value, note
        ),
        "field": field,
        "decision": stored_decision,
        "reviewer": reviewer.strip(),
        "reviewed_at_utc": now_utc(),
        "value": approved_value,
        "edited_from_canonical": bool(
            decision == "approve"
            and value
            and value != field_record.get("canonical_candidate")
        ),
        "canonical_candidate_at_review": field_record.get(
            "canonical_candidate"
        ),
        "note": note,
        "source_bundle_path": review.get("source_bundle_path"),
        "fusion_version": VERSION,
        "qdrant_entry_created": False,
    }

    decisions = load_decisions(output_dir)
    decisions.append(record)
    write_json(decision_file(output_dir), decisions)
    apply_saved_decisions(review, decisions)

    review["qdrant_eligible_field_count"] = sum(
        1
        for item in review["fields"].values()
        if item.get("qdrant", {}).get("eligible_for_future_ingestion")
    )
    return record


def render_source(row):
    location = []
    if row.get("page_number") is not None:
        location.append("page {}".format(row["page_number"]))
    if row.get("region"):
        location.append("region {}".format(row["region"]))
    where = ", ".join(location) or "document level"

    return [
        "    - Role: {}".format(row.get("document_role")),
        "      Authority: {}".format(row.get("authority")),
        "      Source: {}".format(row.get("source_path")),
        "      Location: {}".format(where),
        "      Method: {}".format(row.get("extraction_method")),
        "      Raw value: {}".format(row.get("raw_value")),
        "      Accepted as fact: NO",
    ]


def render_review(review):
    identity = review["repair_identity"]
    lines = [
        "NOVA DRL EVIDENCE FUSION AND HUMAN REVIEW v{}".format(VERSION),
        "=" * 82,
        "Log: {}".format(identity.get("log_number")),
        "Date: {}".format(
            identity.get("repair_date_display")
            or identity.get("repair_date")
        ),
        "Model: {}".format(identity.get("model")),
        "Serial: {}".format(identity.get("serial_number")),
        "Customer: {}".format(identity.get("customer")),
        "Warranty: {}".format("YES" if identity.get("warranty") else "NO"),
        "",
    ]

    for field in FIELD_ORDER:
        record = review["fields"][field]
        lines += [
            field.upper(),
            "-" * len(field),
            "Candidate status: {}".format(record.get("candidate_status")),
            "Canonical candidate: {}".format(
                record.get("canonical_candidate") or "NOT CREATED"
            ),
            "Confidence: {}".format(record.get("confidence")),
            "Independent sources: {}".format(
                record.get("independent_source_count", 0)
            ),
            "Human review: {}".format(
                record.get("human_review", {}).get("status")
            ),
            "Accepted as human-reviewed fact: {}".format(
                "YES"
                if record.get("accepted_as_human_reviewed_fact")
                else "NO"
            ),
            "Future Qdrant eligible: {}".format(
                "YES"
                if record.get("qdrant", {}).get(
                    "eligible_for_future_ingestion"
                )
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

        lines.append("  Supporting evidence:")
        if record.get("sources"):
            for source in record["sources"]:
                lines.extend(render_source(source))
        else:
            lines.append("    None")

        review_info = record.get("human_review", {})
        if review_info.get("reviewer"):
            lines += [
                "  Review decision:",
                "    Reviewer: {}".format(review_info.get("reviewer")),
                "    Reviewed at: {}".format(
                    review_info.get("reviewed_at_utc")
                ),
                "    Approved value: {}".format(
                    review_info.get("approved_value") or "None"
                ),
                "    Note: {}".format(review_info.get("note") or "None"),
            ]
        lines.append("")

    lines += [
        "STATUS",
        "------",
        "Human review required: YES",
        "Accepted as final repair summary: NO",
        "Qdrant entries created: 0",
        "Future-ingestion eligible fields: {}".format(
            review.get("qdrant_eligible_field_count", 0)
        ),
    ]
    return "\n".join(lines) + "\n"


def approved_fields_document(review):
    approved = {}

    for field, record in review.get("fields", {}).items():
        human = record.get("human_review", {})
        if human.get("status") == "approved":
            approved[field] = {
                "value": human.get("approved_value"),
                "reviewer": human.get("reviewer"),
                "reviewed_at_utc": human.get("reviewed_at_utc"),
                "decision_id": human.get("decision_id"),
                "source_candidate_ids": [
                    row.get("candidate_id")
                    for row in record.get("sources", [])
                ],
                "eligible_for_future_qdrant_ingestion": True,
                "qdrant_entry_created": False,
            }

    return {
        "fusion_version": VERSION,
        "repair_identity": review.get("repair_identity"),
        "approved_fields": approved,
        "approved_field_count": len(approved),
        "accepted_as_final_repair_summary": False,
        "qdrant_entry_created": False,
    }


def write_outputs(review, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    write_json(output_dir / "fusion_review.json", review)
    (output_dir / "fusion_review.txt").write_text(
        render_review(review),
        encoding="utf-8",
    )
    write_json(
        output_dir / "approved_repair_fields.json",
        approved_fields_document(review),
    )
    (output_dir / "REVIEW_INSTRUCTIONS.txt").write_text(
        "Nova v1.5.0 never approves a field automatically.\n"
        "Review fusion_review.txt and use the CLI --decision option.\n"
        "No Qdrant entry is created by this module.\n",
        encoding="utf-8",
    )


def default_output_dir(bundle, collector_source, log_number):
    meta = bundle.get("serial_metadata", {})
    folder = (
        meta.get("original_folder_name")
        or Path(collector_source).name
        or "serial"
    )

    return (
        Path.cwd()
        / "output"
        / "evidence_fusion_v1_5"
        / safe_name(folder)
        / "events"
        / str(log_number)
    )


def main():
    parser = argparse.ArgumentParser(
        description="Nova DRL Evidence Fusion and Human Review v{}".format(
            VERSION
        )
    )
    parser.add_argument(
        "collector_source",
        help=(
            "v1.4.3.2 collector root, an event directory, or a "
            "repair_evidence_bundle.json file"
        ),
    )
    parser.add_argument("--log", required=True)
    parser.add_argument("--output-root")
    parser.add_argument(
        "--decision",
        choices=["approve", "reject", "hold"],
    )
    parser.add_argument("--field", default="customer_complaint")
    parser.add_argument("--reviewer")
    parser.add_argument("--value")
    parser.add_argument("--note")
    args = parser.parse_args()

    try:
        bundle_path, bundle = locate_bundle(
            args.collector_source,
            args.log,
        )
        output_dir = (
            Path(args.output_root).expanduser().resolve()
            if args.output_root
            else default_output_dir(
                bundle,
                args.collector_source,
                args.log,
            )
        )

        decisions = load_decisions(output_dir)
        review = build_review(bundle_path, bundle, decisions)

        decision_record = None
        if args.decision:
            decision_record = record_decision(
                review,
                output_dir,
                field=args.field,
                decision=args.decision,
                reviewer=args.reviewer,
                value=args.value,
                note=args.note,
            )

        write_outputs(review, output_dir)
    except Exception as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2

    complaint = review["fields"]["customer_complaint"]

    print()
    print(
        "Nova DRL Evidence Fusion and Human Review v{}".format(VERSION)
    )
    print("=" * 68)
    print(
        "Log:                         {}".format(
            review["repair_identity"].get("log_number")
        )
    )
    print(
        "Model:                       {}".format(
            review["repair_identity"].get("model")
        )
    )
    print(
        "Serial:                      {}".format(
            review["repair_identity"].get("serial_number")
        )
    )
    print(
        "Customer complaint sources: {}".format(
            complaint.get("independent_source_count", 0)
        )
    )
    print(
        "Complaint confidence:        {}".format(
            complaint.get("confidence")
        )
    )
    print(
        "Complaint candidate:         {}".format(
            complaint.get("canonical_candidate") or "NOT CREATED"
        )
    )
    print(
        "Human review status:         {}".format(
            complaint.get("human_review", {}).get("status")
        )
    )
    print(
        "Future Qdrant eligible:      {}".format(
            "YES"
            if complaint.get("qdrant", {}).get(
                "eligible_for_future_ingestion"
            )
            else "NO"
        )
    )
    print("Qdrant entries created:      0")

    if decision_record:
        print(
            "Decision recorded:           {} by {}".format(
                decision_record["decision"],
                decision_record["reviewer"],
            )
        )

    print()
    print("Reports: {}".format(output_dir))
    print("NO DRL SOURCE FILES WERE MODIFIED.")
    print("NO QDRANT ENTRY CREATED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
