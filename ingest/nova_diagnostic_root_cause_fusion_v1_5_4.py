#!/usr/bin/env python3
import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.5.4"


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def stable_id(*parts):
    raw = "\n".join(str(x or "") for x in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_") or "unknown"


def default_glossary_path():
    return Path(__file__).resolve().parents[1] / "config" / "drl_terminology_v1_5_2_3.json"


def locate_source(source):
    source = Path(source).expanduser().resolve()
    candidates = [source] if source.is_file() else [
        source / "approved_repair_fields_with_parts.json",
        source / "approved_repair_fields_with_terminology.json",
        source / "approved_repair_fields.json",
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            data = read_json(candidate)
            if "approved_fields" in data and "repair_identity" in data:
                return candidate, data
    raise ValueError("Approved repair fields were not found beneath {}".format(source))


def load_glossary(path):
    data = read_json(path)
    if not isinstance(data.get("entries", []), list):
        raise ValueError("Glossary must contain an entries list.")
    return data


def term_pattern(term):
    pieces = []
    for ch in str(term):
        if ch in {"'", "’", "‘"}:
            pieces.append(r"['’‘]?")
        elif ch.isspace():
            pieces.append(r"\s+")
        else:
            pieces.append(re.escape(ch))
    return re.compile(
        r"(?<![A-Za-z0-9])" + "".join(pieces) + r"(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def terminology_annotations(text, glossary):
    text = str(text or "")
    rows, seen = [], set()
    entries = sorted(
        glossary.get("entries", []),
        key=lambda x: len(str(x.get("raw_term") or "")),
        reverse=True,
    )
    for entry in entries:
        for alias in [entry.get("raw_term")] + list(entry.get("aliases", []) or []):
            alias = str(alias or "").strip()
            if not alias:
                continue
            for match in term_pattern(alias).finditer(text):
                key = (match.start(), match.end(), str(entry.get("raw_term")).casefold())
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "raw_text_in_value": text[match.start():match.end()],
                    "raw_term": entry.get("raw_term"),
                    "normalized_meaning": entry.get("normalized_meaning"),
                    "category": entry.get("category"),
                    "scope": entry.get("scope"),
                    "status": entry.get("status"),
                    "preserve_raw": bool(entry.get("preserve_raw", True)),
                    "start": match.start(),
                    "end": match.end(),
                })
    return sorted(rows, key=lambda x: (x["start"], x["end"]))


def parse_json_object(text):
    text = str(text or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except Exception:
        pass

    for start in [m.start() for m in re.finditer(r"\{", text)]:
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start:idx + 1])
                        if isinstance(value, dict):
                            return value
                    except Exception:
                        break
    return None


def extract_note(primary_source):
    primary_source = primary_source or {}
    raw = primary_source.get("vision_raw")
    parsed = parse_json_object(raw)
    note = parsed.get("notes") if parsed else None
    if note is None:
        return None
    note = re.sub(r"\s+", " ", str(note)).strip()
    return None if note.lower() in {"", "none", "null", "n/a", "na"} else note


UNCERTAINTY = [
    ("suspect", re.compile(r"\bsuspect(?:ed|ing)?\b", re.I)),
    ("may", re.compile(r"\bmay\b", re.I)),
    ("might", re.compile(r"\bmight\b", re.I)),
    ("could", re.compile(r"\bcould\b", re.I)),
    ("possible", re.compile(r"\bpossibl(?:e|y)\b", re.I)),
    ("believe", re.compile(r"\bbeliev(?:e|ed)\b", re.I)),
    ("think", re.compile(r"\bthink\b|\bthought\b", re.I)),
    ("appears", re.compile(r"\bappears?\b", re.I)),
    ("seems", re.compile(r"\bseems?\b", re.I)),
    (
        "no_concrete_proof",
        re.compile(
            r"(?:don['’]?t|do\s+not)\s+have\b.{0,30}\bconcrete\s+proof\b|"
            r"\bno\b.{0,20}\bconcrete\s+proof\b|"
            r"\bwithout\b.{0,20}\bconcrete\s+proof\b",
            re.I,
        ),
    ),
    ("unconfirmed", re.compile(r"\bunconfirmed\b|\bnot\s+confirmed\b", re.I)),
]

CAUSAL = [
    ("root_cause", re.compile(r"\broot\s+cause\b", re.I)),
    ("caused_by", re.compile(r"\bcaused\s+by\b", re.I)),
    ("causing", re.compile(r"\bcaus(?:e|ed|ing)\b", re.I)),
    ("due_to", re.compile(r"\bdue\s+to\b", re.I)),
    ("because_of", re.compile(r"\bbecause\s+of\b", re.I)),
]


def cue_hits(text, patterns):
    rows = []
    for name, pattern in patterns:
        for match in pattern.finditer(str(text or "")):
            rows.append({
                "cue": name,
                "raw_text": match.group(0),
                "start": match.start(),
                "end": match.end(),
            })
    return rows


def classify_note(note):
    uncertainty = cue_hits(note, UNCERTAINTY)
    causal = cue_hits(note, CAUSAL)
    if uncertainty:
        return {
            "candidate_type": "diagnostic_hypothesis",
            "classification_reason": "uncertainty_language_present",
            "uncertainty_cues": uncertainty,
            "causal_cues": causal,
            "root_cause_status": "not_confirmed",
        }
    if causal:
        return {
            "candidate_type": "root_cause_candidate",
            "classification_reason": "causal_language_without_uncertainty_cue",
            "uncertainty_cues": [],
            "causal_cues": causal,
            "root_cause_status": "pending_human_confirmation",
        }
    return {
        "candidate_type": "diagnostic_observation",
        "classification_reason": "diagnostic_note_without_explicit_causal_or_uncertainty_language",
        "uncertainty_cues": [],
        "causal_cues": [],
        "root_cause_status": "not_established",
    }


def approved_actions(source_data):
    return [
        row
        for row in source_data.get("approved_fields", {}).get("repair_actions", []) or []
        if isinstance(row, dict) and row.get("value")
    ]


def build_candidates(source_data, glossary):
    rows = []
    for action in approved_actions(source_data):
        primary = action.get("primary_source") or {}
        note = extract_note(primary)
        if not note:
            continue
        classification = classify_note(note)
        crop_paths = primary.get("crop_paths") or {}
        rows.append({
            "candidate_id": stable_id("diag", action.get("action_id"), note),
            "candidate_number": None,
            "candidate_type": classification["candidate_type"],
            "classification_reason": classification["classification_reason"],
            "raw_note": note,
            "machine_transcription": True,
            "source_action_id": action.get("action_id"),
            "source_action_number": action.get("action_number"),
            "source_action_value": action.get("value"),
            "source_action_reviewer": action.get("reviewer"),
            "source_action_decision_id": action.get("decision_id"),
            "source_primary_evidence": {
                "source_path": primary.get("source_path"),
                "source_document": primary.get("source_document"),
                "location": primary.get("location"),
                "authority": primary.get("authority"),
                "crop_path": (
                    crop_paths.get("full_row")
                    or crop_paths.get("enhanced")
                    or crop_paths.get("description")
                ),
                "vision_raw": primary.get("vision_raw"),
            },
            "terminology_annotations": terminology_annotations(note, glossary),
            "uncertainty_cues": classification["uncertainty_cues"],
            "causal_cues": classification["causal_cues"],
            "root_cause_status": classification["root_cause_status"],
            "human_review": {
                "status": "pending",
                "reviewer": None,
                "reviewed_at_utc": None,
                "approved_value": None,
                "note": None,
            },
            "accepted_as_human_reviewed_diagnostic": False,
            "confirmed_root_cause": False,
            "qdrant": {
                "eligible_for_future_ingestion": False,
                "entry_created": False,
                "reason": "pending_human_review",
            },
        })
    for idx, row in enumerate(rows, start=1):
        row["candidate_number"] = idx
    return rows


def decision_path(output_dir):
    return Path(output_dir) / "human_review_decisions.json"


def load_decisions(output_dir):
    path = decision_path(output_dir)
    if not path.exists():
        return []
    try:
        data = read_json(path)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def apply_decisions(candidates, decisions):
    latest = {}
    for decision in decisions:
        if decision.get("candidate_id"):
            latest[decision["candidate_id"]] = decision

    for row in candidates:
        decision = latest.get(row["candidate_id"])
        if not decision:
            continue
        status = decision.get("decision")
        row["human_review"] = {
            "status": status,
            "reviewer": decision.get("reviewer"),
            "reviewed_at_utc": decision.get("reviewed_at_utc"),
            "approved_value": decision.get("value"),
            "note": decision.get("note"),
            "decision_id": decision.get("decision_id"),
            "edited_from_candidate": decision.get("edited_from_candidate"),
        }
        if status == "approved_hypothesis":
            row["accepted_as_human_reviewed_diagnostic"] = True
            row["confirmed_root_cause"] = False
            row["root_cause_status"] = "not_confirmed"
            row["qdrant"] = {
                "eligible_for_future_ingestion": True,
                "entry_created": False,
                "reason": "human_approved_diagnostic_hypothesis",
            }
        elif status == "confirmed_root_cause":
            row["accepted_as_human_reviewed_diagnostic"] = True
            row["confirmed_root_cause"] = True
            row["root_cause_status"] = "confirmed"
            row["qdrant"] = {
                "eligible_for_future_ingestion": True,
                "entry_created": False,
                "reason": "human_confirmed_root_cause",
            }
        elif status in {"rejected", "hold"}:
            row["accepted_as_human_reviewed_diagnostic"] = False
            row["confirmed_root_cause"] = False
            row["qdrant"] = {
                "eligible_for_future_ingestion": False,
                "entry_created": False,
                "reason": "human_review_{}".format(status),
            }
    return candidates


def build_review(source_data, glossary, decisions):
    candidates = build_candidates(source_data, glossary)
    apply_decisions(candidates, decisions)
    hypothesis_count = sum(x["candidate_type"] == "diagnostic_hypothesis" for x in candidates)
    observation_count = sum(x["candidate_type"] == "diagnostic_observation" for x in candidates)
    root_candidate_count = sum(x["candidate_type"] == "root_cause_candidate" for x in candidates)
    approved_hypotheses = sum(x["human_review"]["status"] == "approved_hypothesis" for x in candidates)
    confirmed = sum(bool(x["confirmed_root_cause"]) for x in candidates)
    pending = sum(x["human_review"]["status"] == "pending" for x in candidates)

    root_status = (
        "confirmed"
        if confirmed
        else ("candidate_pending_human_confirmation" if root_candidate_count else "not_established")
    )
    return {
        "field": "diagnostic_root_cause",
        "candidate_status": "candidates_available" if candidates else "no_candidates",
        "candidate_count": len(candidates),
        "diagnostic_hypothesis_count": hypothesis_count,
        "diagnostic_observation_count": observation_count,
        "root_cause_candidate_count": root_candidate_count,
        "approved_hypothesis_count": approved_hypotheses,
        "confirmed_root_cause_count": confirmed,
        "pending_count": pending,
        "root_cause_status": root_status,
        "candidates": candidates,
        "source_policy": {
            "human_approved_repair_actions_required": True,
            "machine_note_requires_human_review": True,
            "uncertainty_blocks_root_cause_confirmation": True,
            "approved_hypothesis_is_not_root_cause": True,
        },
        "qdrant_entry_created": False,
    }


def find_candidate(candidates, number=None, candidate_id=None):
    if candidate_id:
        for row in candidates:
            if row["candidate_id"] == candidate_id:
                return row
        raise ValueError("Candidate ID not found.")
    if number is not None:
        for row in candidates:
            if int(row.get("candidate_number") or 0) == int(number):
                return row
        raise ValueError("Candidate number not found.")
    raise ValueError("Use --candidate-number or --candidate-id.")


def record_decision(
    candidates,
    output_dir,
    decision,
    reviewer,
    candidate_number=None,
    candidate_id=None,
    value=None,
    note=None,
):
    if not reviewer:
        raise ValueError("--reviewer is required.")
    row = find_candidate(candidates, candidate_number, candidate_id)

    if decision == "approve-hypothesis":
        if row["candidate_type"] not in {"diagnostic_hypothesis", "diagnostic_observation"}:
            raise ValueError("Root-cause candidate requires confirm-root-cause.")
        stored = "approved_hypothesis"
        approved_value = str(value).strip() if value else row["raw_note"]
    elif decision == "confirm-root-cause":
        if row["candidate_type"] != "root_cause_candidate":
            raise ValueError(
                "Cannot confirm root cause from a candidate containing uncertainty "
                "or lacking explicit root-cause classification."
            )
        stored = "confirmed_root_cause"
        approved_value = str(value).strip() if value else row["raw_note"]
    elif decision == "reject":
        stored, approved_value = "rejected", None
    elif decision == "hold":
        stored, approved_value = "hold", None
    else:
        raise ValueError("Unsupported decision.")

    record = {
        "decision_id": stable_id(
            "diag", row["candidate_id"], stored, reviewer, now_utc(), approved_value, note
        ),
        "field": "diagnostic_root_cause",
        "candidate_id": row["candidate_id"],
        "candidate_number": row["candidate_number"],
        "candidate_type_at_review": row["candidate_type"],
        "decision": stored,
        "reviewer": reviewer,
        "reviewed_at_utc": now_utc(),
        "value": approved_value,
        "candidate_value_at_review": row["raw_note"],
        "edited_from_candidate": bool(
            approved_value is not None and approved_value != row["raw_note"]
        ),
        "note": note,
        "fusion_version": VERSION,
        "qdrant_entry_created": False,
    }
    decisions = load_decisions(output_dir)
    decisions.append(record)
    write_json(decision_path(output_dir), decisions)
    return record


def approved_hypotheses(review):
    rows = []
    for candidate in review["candidates"]:
        human = candidate["human_review"]
        if human["status"] != "approved_hypothesis":
            continue
        rows.append({
            "candidate_id": candidate["candidate_id"],
            "candidate_number": candidate["candidate_number"],
            "value": human["approved_value"],
            "reviewer": human["reviewer"],
            "reviewed_at_utc": human["reviewed_at_utc"],
            "decision_id": human["decision_id"],
            "edited_from_candidate": human["edited_from_candidate"],
            "raw_machine_note": candidate["raw_note"],
            "source_action_id": candidate["source_action_id"],
            "source_action_number": candidate["source_action_number"],
            "source_action_value": candidate["source_action_value"],
            "source_primary_evidence": candidate["source_primary_evidence"],
            "terminology_annotations": candidate["terminology_annotations"],
            "confirmed_root_cause": False,
            "eligible_for_future_qdrant_ingestion": True,
            "qdrant_entry_created": False,
        })
    return rows


def approved_root_causes(review):
    rows = []
    for candidate in review["candidates"]:
        human = candidate["human_review"]
        if human["status"] != "confirmed_root_cause":
            continue
        rows.append({
            "candidate_id": candidate["candidate_id"],
            "candidate_number": candidate["candidate_number"],
            "value": human["approved_value"],
            "reviewer": human["reviewer"],
            "reviewed_at_utc": human["reviewed_at_utc"],
            "decision_id": human["decision_id"],
            "edited_from_candidate": human["edited_from_candidate"],
            "raw_machine_note": candidate["raw_note"],
            "source_action_id": candidate["source_action_id"],
            "source_primary_evidence": candidate["source_primary_evidence"],
            "terminology_annotations": candidate["terminology_annotations"],
            "confirmed_root_cause": True,
            "eligible_for_future_qdrant_ingestion": True,
            "qdrant_entry_created": False,
        })
    return rows


def build_output(source_path, source_data, review):
    output = {
        "fusion_version": VERSION,
        "source_fusion_version": source_data.get("fusion_version"),
        "source_approved_fields_path": str(source_path),
        "repair_identity": copy.deepcopy(source_data.get("repair_identity")),
        "approved_fields": copy.deepcopy(source_data.get("approved_fields", {})),
        "diagnostic_root_cause_review": review,
        "approved_field_count": int(source_data.get("approved_field_count", 0)),
        "approved_repair_action_count": int(source_data.get("approved_repair_action_count", 0)),
        "approved_parts_replaced_count": int(source_data.get("approved_parts_replaced_count", 0)),
        "approved_diagnostic_hypothesis_count": 0,
        "confirmed_root_cause_count": 0,
        "root_cause_status": review["root_cause_status"],
        "accepted_as_final_repair_summary": False,
        "qdrant_entry_created": False,
    }

    hypotheses = approved_hypotheses(review)
    root_causes = approved_root_causes(review)

    if hypotheses:
        output["approved_fields"]["diagnostic_hypotheses"] = hypotheses
        output["approved_diagnostic_hypothesis_count"] = len(hypotheses)
        if "diagnostic_hypotheses" not in source_data.get("approved_fields", {}):
            output["approved_field_count"] += 1

    if root_causes:
        output["approved_fields"]["root_cause"] = root_causes
        output["confirmed_root_cause_count"] = len(root_causes)
        output["root_cause_status"] = "confirmed"
        if "root_cause" not in source_data.get("approved_fields", {}):
            output["approved_field_count"] += 1

    return output


def render_review(output):
    identity = output["repair_identity"]
    review = output["diagnostic_root_cause_review"]
    lines = [
        "NOVA DRL DIAGNOSTIC HYPOTHESIS / ROOT CAUSE FUSION v{}".format(VERSION),
        "=" * 84,
        "Log: {}".format(identity.get("log_number")),
        "Model: {}".format(identity.get("model")),
        "Serial: {}".format(identity.get("serial_number")),
        "",
        "DIAGNOSTIC / ROOT CAUSE CANDIDATES",
        "----------------------------------",
        "Candidates: {}".format(review["candidate_count"]),
        "Diagnostic hypotheses: {}".format(review["diagnostic_hypothesis_count"]),
        "Root-cause candidates: {}".format(review["root_cause_candidate_count"]),
        "Approved hypotheses: {}".format(review["approved_hypothesis_count"]),
        "Confirmed root causes: {}".format(review["confirmed_root_cause_count"]),
        "Root cause status: {}".format(review["root_cause_status"]),
        "",
    ]
    for row in review["candidates"]:
        human = row["human_review"]
        lines += [
            "CANDIDATE {} [{}]".format(row["candidate_number"], row["candidate_id"]),
            "  Type: {}".format(row["candidate_type"]),
            "  Raw machine note: {}".format(row["raw_note"]),
            "  Source action {}: {}".format(row["source_action_number"], row["source_action_value"]),
            "  Uncertainty cues: {}".format(
                [x["raw_text"] for x in row["uncertainty_cues"]] or "None"
            ),
            "  Causal cues: {}".format(
                [x["raw_text"] for x in row["causal_cues"]] or "None"
            ),
            "  Root cause status: {}".format(row["root_cause_status"]),
            "  Terminology:",
        ]
        if row["terminology_annotations"]:
            for term in row["terminology_annotations"]:
                lines.append(
                    "    - {} -> {} | scope={}".format(
                        term["raw_term"], term["normalized_meaning"], term["scope"]
                    )
                )
        else:
            lines.append("    None")
        lines += [
            "  Source traveler: {}".format(row["source_primary_evidence"].get("source_path")),
            "  Source crop: {}".format(row["source_primary_evidence"].get("crop_path")),
            "  Human review: {}".format(human["status"]),
            "  Accepted as human-reviewed diagnostic: {}".format(
                "YES" if row["accepted_as_human_reviewed_diagnostic"] else "NO"
            ),
            "  Confirmed root cause: {}".format(
                "YES" if row["confirmed_root_cause"] else "NO"
            ),
            "  Future Qdrant eligible: {}".format(
                "YES" if row["qdrant"]["eligible_for_future_ingestion"] else "NO"
            ),
        ]
        if human.get("reviewer"):
            lines += [
                "  Review decision:",
                "    Reviewer: {}".format(human["reviewer"]),
                "    Approved value: {}".format(human["approved_value"]),
                "    Edited from candidate: {}".format(human["edited_from_candidate"]),
                "    Note: {}".format(human.get("note") or "None"),
            ]
        lines.append("")
    lines += [
        "STATUS",
        "------",
        "Approved diagnostic hypotheses: {}".format(
            output["approved_diagnostic_hypothesis_count"]
        ),
        "Confirmed root causes: {}".format(output["confirmed_root_cause_count"]),
        "Root cause status: {}".format(output["root_cause_status"]),
        "Approved hypothesis automatically becomes root cause: NO",
        "Accepted as final repair summary: NO",
        "Qdrant entries created: 0",
    ]
    return "\n".join(lines) + "\n"


def default_output_dir(source_data):
    i = source_data.get("repair_identity", {})
    folder = "_".join(
        safe_name(x)
        for x in [
            i.get("equipment_type") or "UNK",
            i.get("model") or "UNK",
            i.get("oem") or "UNK",
            "SN",
            i.get("serial_number") or "UNKNOWN",
            i.get("customer") or "UNKNOWN",
        ]
    )
    return Path.cwd() / "output" / "evidence_fusion_v1_5_4" / folder / "events" / str(
        i.get("log_number") or "unknown"
    )


def write_outputs(output, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        output_dir / "diagnostic_root_cause_review.json",
        output["diagnostic_root_cause_review"],
    )
    write_json(
        output_dir / "approved_repair_fields_with_diagnostics.json",
        output,
    )
    (output_dir / "diagnostic_root_cause_review.txt").write_text(
        render_review(output), encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Nova DRL Diagnostic Hypothesis / Root Cause Fusion v{}".format(VERSION)
    )
    parser.add_argument("source")
    parser.add_argument("--glossary", default=str(default_glossary_path()))
    parser.add_argument("--output-root")
    parser.add_argument(
        "--decision",
        choices=["approve-hypothesis", "confirm-root-cause", "reject", "hold"],
    )
    parser.add_argument("--candidate-number", type=int)
    parser.add_argument("--candidate-id")
    parser.add_argument("--reviewer")
    parser.add_argument("--value")
    parser.add_argument("--note")
    args = parser.parse_args()

    try:
        source_path, source_data = locate_source(args.source)
        glossary = load_glossary(args.glossary)
        output_dir = (
            Path(args.output_root).expanduser().resolve()
            if args.output_root
            else default_output_dir(source_data)
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        decisions = load_decisions(output_dir)
        review = build_review(source_data, glossary, decisions)
        decision_record = None

        if args.decision:
            decision_record = record_decision(
                review["candidates"],
                output_dir,
                args.decision,
                args.reviewer,
                candidate_number=args.candidate_number,
                candidate_id=args.candidate_id,
                value=args.value,
                note=args.note,
            )
            decisions = load_decisions(output_dir)
            review = build_review(source_data, glossary, decisions)

        output = build_output(source_path, source_data, review)
        write_outputs(output, output_dir)

    except Exception as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2

    print()
    print("Nova DRL Diagnostic Hypothesis / Root Cause Fusion v{}".format(VERSION))
    print("=" * 76)
    print("Log:                         {}".format(
        output["repair_identity"].get("log_number")
    ))
    print("Approved repair actions:     {}".format(output["approved_repair_action_count"]))
    print("Diagnostic candidates:       {}".format(review["candidate_count"]))
    print("Hypothesis candidates:       {}".format(review["diagnostic_hypothesis_count"]))
    print("Root-cause candidates:       {}".format(review["root_cause_candidate_count"]))
    print("Approved hypotheses:         {}".format(review["approved_hypothesis_count"]))
    print("Confirmed root causes:       {}".format(review["confirmed_root_cause_count"]))
    print("Root cause status:           {}".format(output["root_cause_status"]))
    print("Qdrant entries created:      0")
    if decision_record:
        print(
            "Decision recorded:           {} candidate {} by {}".format(
                decision_record["decision"],
                decision_record["candidate_number"],
                decision_record["reviewer"],
            )
        )
    print()
    print("Reports: {}".format(output_dir))
    print("NO APPROVED SOURCE VALUES WERE MODIFIED.")
    print("NO DRL SOURCE FILES WERE MODIFIED.")
    print("NO QDRANT ENTRY CREATED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
