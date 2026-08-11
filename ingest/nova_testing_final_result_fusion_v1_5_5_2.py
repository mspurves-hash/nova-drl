#!/usr/bin/env python3
"""
Nova DRL Testing Performed / Final Result Fusion v1.5.5
=======================================================

Purpose
-------
Extract reviewable event-specific testing and final-result evidence from:
  * DRL Robot Checklist page images
  * DRL Acceptance / Robot Test Report page images
  * Traveler final_test and shipping_final_ok crops

Critical evidence rule
----------------------
PRINTED TEMPLATE TEXT IS NOT PROOF A TEST WAS PERFORMED.

A testing candidate requires a visible event-specific mark/value such as:
  * technician initials
  * checkmark / X / circle
  * handwritten value
  * handwritten pass/fail/completion entry

A final-result candidate requires an explicit event-specific result. The
document title "Acceptance Test Report" alone can never create a final result.

All machine-vision findings remain candidates until human review.

No DRL source files are modified.
No Qdrant entries are created.
"""

import argparse
import base64
import copy
import collections
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.5.5.2"

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


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


def stable_id(*parts):
    raw = "\n".join(str(x or "") for x in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_") or "unknown"


def default_rules_path():
    return (
        Path(__file__).resolve().parents[1]
        / "config"
        / "testing_final_result_rules_v1_5_5_2.json"
    )


def load_rules(path):
    return read_json(path)


def locate_approved_source(source):
    source = Path(source).expanduser().resolve()
    candidates = [source] if source.is_file() else [
        source / "approved_repair_fields_with_diagnostics.json",
        source / "approved_repair_fields_with_parts.json",
        source / "approved_repair_fields_with_terminology.json",
        source / "approved_repair_fields.json",
    ]
    for candidate in candidates:
        if not candidate.exists() or not candidate.is_file():
            continue
        data = read_json(candidate)
        if "repair_identity" in data and "approved_fields" in data:
            return candidate, data
    raise ValueError(
        "Could not find approved repair fields beneath {}".format(source)
    )


def identity_matches(identity, bundle):
    meta = bundle.get("serial_metadata", {}) or {}
    event = bundle.get("repair_event", {}) or {}

    checks = [
        (identity.get("serial_number"), meta.get("serial_number")),
        (identity.get("model"), meta.get("model")),
        (identity.get("oem"), meta.get("oem")),
        (identity.get("log_number"), event.get("log_number")),
    ]

    compared = 0
    for expected, actual in checks:
        if expected in (None, "") or actual in (None, ""):
            continue
        compared += 1
        if str(expected).casefold() != str(actual).casefold():
            return False
    return compared >= 2


def auto_find_evidence_bundle(identity, evidence_root):
    root = Path(evidence_root).expanduser().resolve()
    log_number = str(identity.get("log_number") or "")
    if not root.exists():
        return None

    paths = list(root.glob("*/events/{}/repair_evidence_bundle.json".format(log_number)))
    exact = []
    for path in paths:
        try:
            bundle = read_json(path)
        except Exception:
            continue
        if identity_matches(identity, bundle):
            exact.append(path)

    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        # Stable newest-name selection is safer than an arbitrary filesystem
        # order, but the ambiguity is retained in the output warning.
        return sorted(exact, key=lambda p: str(p))[-1]
    if len(paths) == 1:
        return paths[0]
    return None


def locate_evidence_bundle(identity, explicit_path=None, evidence_root=None):
    if explicit_path:
        path = Path(explicit_path).expanduser().resolve()
        if path.is_dir():
            path = path / "repair_evidence_bundle.json"
        if not path.exists():
            raise ValueError("Evidence bundle not found: {}".format(path))
        bundle = read_json(path)
        if not identity_matches(identity, bundle):
            raise ValueError(
                "Evidence bundle identity does not match the approved repair event."
            )
        return path, bundle, None

    roots = []
    if evidence_root:
        roots.append(Path(evidence_root))
    roots.extend([
        Path("/opt/nova-drl/output/repair_evidence_collector_v1_4_3_2"),
        Path.cwd() / "output" / "repair_evidence_collector_v1_4_3_2",
    ])

    seen = set()
    for root in roots:
        key = str(root.expanduser().resolve())
        if key in seen:
            continue
        seen.add(key)
        found = auto_find_evidence_bundle(identity, root)
        if found:
            return found, read_json(found), None

    return None, None, (
        "Matching v1.4.3.2 repair_evidence_bundle.json was not found. "
        "Use --evidence-bundle=... or --evidence-root=..."
    )


def page_family(page, extraction):
    classification = page.get("classification", {}) or {}
    return (
        classification.get("document_family")
        or extraction.get("document_family")
        or "UNKNOWN"
    )


def load_optional_text(path, max_chars=3500):
    if not path:
        return ""
    path = Path(path)
    if not path.exists() or not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def collect_source_images(bundle, rules):
    if not bundle:
        return [], []

    event = bundle.get("repair_event", {}) or {}
    allowed_roles = set(rules.get("supporting_document_roles", []))
    crop_names = {
        str(x).lower()
        for x in rules.get("traveler_crop_basenames", [])
    }

    sources = []
    skipped = []

    for record in event.get("evidence_files", []) or []:
        role = record.get("role")
        if role not in allowed_roles:
            continue
        extraction = record.get("extraction", {}) or {}
        page_records = extraction.get("page_records", []) or []

        if not page_records:
            skipped.append({
                "source_path": record.get("source_path"),
                "reason": "supporting_document_has_no_rendered_page_records",
                "document_role": role,
            })
            continue

        for page in page_records:
            image_path = page.get("image_path")
            if not image_path:
                skipped.append({
                    "source_path": record.get("source_path"),
                    "page_number": page.get("page_number"),
                    "reason": "page_image_path_missing",
                    "document_role": role,
                })
                continue

            sources.append({
                "source_kind": "supporting_document_page",
                "document_role": role,
                "document_family": page_family(page, extraction),
                "source_document": record.get("filename"),
                "source_path": record.get("source_path"),
                "evidence_id": record.get("evidence_id"),
                "page_number": page.get("page_number"),
                "image_path": image_path,
                "template_ocr_text": load_optional_text(
                    page.get("selected_text_path")
                ),
                "template_ocr_quality": page.get("template_ocr_quality"),
                "event_annotation_quality_prior": page.get(
                    "event_annotation_quality"
                ),
                "accepted_as_repair_fact": False,
            })

    for artifact in event.get("derived_traveler_artifacts", []) or []:
        path = artifact.get("path")
        if not path:
            continue
        if Path(path).name.lower() not in crop_names:
            continue
        sources.append({
            "source_kind": "traveler_event_crop",
            "document_role": "traveler",
            "document_family": "DRL_TRAVELER",
            "source_document": Path(path).name,
            "source_path": path,
            "evidence_id": artifact.get("artifact_id"),
            "page_number": None,
            "image_path": path,
            "template_ocr_text": "",
            "template_ocr_quality": None,
            "event_annotation_quality_prior": None,
            "accepted_as_repair_fact": False,
        })

    # Stable dedupe.
    unique = []
    seen = set()
    for row in sources:
        key = (
            row.get("source_kind"),
            row.get("source_path"),
            row.get("page_number"),
            row.get("image_path"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)

    return unique, skipped


def ollama_tags():
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:11434/api/tags", timeout=5
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def ollama_model_exists(model):
    data = ollama_tags()
    if not data:
        return False
    names = [x.get("name", "") for x in data.get("models", [])]
    if model in names:
        return True
    if ":" not in model:
        return any(name == model or name.startswith(model + ":") for name in names)
    return False


def prepare_vision_image(image_path, output_path, max_dimension):
    from PIL import Image

    source = Path(image_path)
    if not source.exists():
        raise FileNotFoundError(str(source))

    image = Image.open(source).convert("RGB")
    width, height = image.size
    scale = min(1.0, float(max_dimension) / float(max(width, height)))
    if scale < 1.0:
        image = image.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="JPEG", quality=93)
    return {
        "source_size": [width, height],
        "vision_size": list(image.size),
        "vision_image_path": str(output_path),
    }


def build_prompt(source):
    ocr_hint = source.get("template_ocr_text") or ""
    ocr_section = ""
    if ocr_hint:
        ocr_section = (
            "\nOCR HINT FOR READING PRINTED LABELS ONLY. "
            "THIS TEXT IS NOT EVIDENCE THAT A TEST WAS COMPLETED:\n"
            + ocr_hint[:3500]
            + "\n"
        )

    return """You are reading a Direct Repair Laboratories repair-event document image.

Your job is ONLY to identify EVENT-SPECIFIC visual evidence that a technician
actually performed, recorded, or completed a test/inspection, and any explicit
event-specific final result.

CRITICAL RULES:
1. Printed template instructions, printed step names, and the document title
   are NOT proof that work was performed.
2. Do NOT infer completion merely because this is a checklist or Acceptance
   Test Report.
3. Report a testing item ONLY when a visible event-specific mark/value is
   clearly associated with a printed or handwritten test/inspection label.
   Event-specific evidence can be a checkmark, X, initials, handwritten value,
   circle, pass/fail mark, date, or other clearly associated handwritten mark.
4. If a printed step has no associated event-specific mark/value, OMIT it.
5. A final result requires an explicit event-specific result such as a marked
   PASS/FAIL, handwritten acceptance/final-OK result, or equivalent.
6. The phrase/title "Acceptance Test Report" alone is NEVER a final result.
7. Preserve literal wording. Do not normalize, correct, summarize, or invent
   test names or results.
8. If uncertain, put it in uncertain_marks instead of testing_items.
9. Return JSON only.

Return exactly this structure:
{
  "page_has_event_specific_testing_evidence": true,
  "testing_items": [
    {
      "step_label": "literal visible label",
      "event_mark": "literal visible mark/value/initials",
      "mark_type": "checkmark|x_mark|initials|handwritten_value|circle|pass_fail_mark|other",
      "result": "pass|fail|completed|recorded_value|unknown",
      "recorded_value": null,
      "technician_initials": null,
      "date": null,
      "confidence": "high|medium|low"
    }
  ],
  "final_result_items": [
    {
      "value": "literal visible final result",
      "basis_label": "literal associated label",
      "event_mark": "literal visible mark/value",
      "result": "pass|fail|accepted|rejected|final_ok|other",
      "confidence": "high|medium|low"
    }
  ],
  "printed_template_only_labels": [
    "important printed labels that had NO associated event-specific mark"
  ],
  "uncertain_marks": [
    "literal uncertain mark or short description"
  ]
}
""" + ocr_section


def call_ollama_vision(model, prompt, image_path, timeout):
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "images": [
            base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        ],
        "stream": False,
        "options": {"temperature": 0},
    }).encode("utf-8")

    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        return {
            "status": "ok",
            "response": body.get("response", ""),
            "done_reason": body.get("done_reason"),
            "eval_count": body.get("eval_count"),
            "warning": None,
        }
    except Exception as exc:
        return {
            "status": "error",
            "response": "",
            "done_reason": None,
            "eval_count": None,
            "warning": str(exc),
        }


def parse_json_object(text):
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(raw[start:end + 1])
            return value if isinstance(value, dict) else None
        except Exception:
            return None
    return None


def normalize_confidence(value):
    value = str(value or "").strip().lower()
    return value if value in CONFIDENCE_RANK else "low"


def normalize_page_analysis(parsed):
    parsed = parsed if isinstance(parsed, dict) else {}
    testing = parsed.get("testing_items")
    finals = parsed.get("final_result_items")
    printed = parsed.get("printed_template_only_labels")
    uncertain = parsed.get("uncertain_marks")

    if not isinstance(testing, list):
        testing = []
    if not isinstance(finals, list):
        finals = []
    if not isinstance(printed, list):
        printed = []
    if not isinstance(uncertain, list):
        uncertain = []

    return {
        "page_has_event_specific_testing_evidence": bool(
            parsed.get("page_has_event_specific_testing_evidence")
        ),
        "testing_items": [
            x for x in testing if isinstance(x, dict)
        ],
        "final_result_items": [
            x for x in finals if isinstance(x, dict)
        ],
        "printed_template_only_labels": [
            str(x) for x in printed if str(x).strip()
        ],
        "uncertain_marks": [
            str(x) for x in uncertain if str(x).strip()
        ],
    }


def page_cache_key(source):
    return stable_id(
        source.get("source_kind"),
        source.get("source_path"),
        source.get("page_number"),
        source.get("image_path"),
    )


def analyze_sources(
    sources,
    output_dir,
    model,
    timeout,
    max_dimension,
    refresh=False,
    no_vision=False,
):
    analyses = []
    cache_dir = Path(output_dir) / "page_analysis"
    cache_dir.mkdir(parents=True, exist_ok=True)

    model_available = ollama_model_exists(model) if not no_vision else False

    for index, source in enumerate(sources, start=1):
        key = page_cache_key(source)
        cache_path = cache_dir / "{}.json".format(key)

        if cache_path.exists() and not refresh:
            record = read_json(cache_path)
            record["cache_status"] = "reused"
            analyses.append(record)
            continue

        record = {
            "analysis_id": key,
            "source_index": index,
            "source": source,
            "vision_model": model,
            "vision_status": "not_run",
            "raw_vision_response": "",
            "parsed_analysis": normalize_page_analysis({}),
            "warning": None,
            "cache_status": "created",
            "accepted_as_repair_fact": False,
            "qdrant_entry_created": False,
        }

        image_path = Path(source.get("image_path") or "")
        if not image_path.exists():
            record["vision_status"] = "image_missing"
            record["warning"] = "Image path does not exist."
            write_json(cache_path, record)
            analyses.append(record)
            continue

        if no_vision:
            record["vision_status"] = "vision_disabled"
            write_json(cache_path, record)
            analyses.append(record)
            continue

        if not model_available:
            record["vision_status"] = "model_unavailable"
            record["warning"] = (
                "Ollama model {} was not found.".format(model)
            )
            write_json(cache_path, record)
            analyses.append(record)
            continue

        vision_image = (
            cache_dir / "vision_images" / "{}.jpg".format(key)
        )
        try:
            image_info = prepare_vision_image(
                image_path, vision_image, max_dimension
            )
            record["vision_image"] = image_info
        except Exception as exc:
            record["vision_status"] = "image_prepare_error"
            record["warning"] = str(exc)
            write_json(cache_path, record)
            analyses.append(record)
            continue

        response = call_ollama_vision(
            model,
            build_prompt(source),
            image_info["vision_image_path"],
            timeout,
        )
        record["raw_vision_response"] = response.get("response", "")
        record["vision_status"] = response.get("status")
        record["warning"] = response.get("warning")
        record["done_reason"] = response.get("done_reason")
        record["eval_count"] = response.get("eval_count")

        if response.get("status") == "ok":
            parsed = parse_json_object(response.get("response"))
            if parsed is None:
                record["vision_status"] = "response_not_json"
            else:
                record["parsed_analysis"] = normalize_page_analysis(parsed)

        write_json(cache_path, record)
        analyses.append(record)

    return analyses


def valid_event_mark(item):
    mark = str(item.get("event_mark") or "").strip()
    mark_type = str(item.get("mark_type") or "").strip().lower()
    if not mark:
        return False
    if mark_type in {"", "none", "printed_text"}:
        return False
    return True


def confidence_meets(value, minimum):
    return CONFIDENCE_RANK.get(
        normalize_confidence(value), 0
    ) >= CONFIDENCE_RANK.get(str(minimum).lower(), 1)


def build_testing_candidates(analyses, rules):
    minimum = (
        rules.get("testing_candidate_policy", {})
        .get("minimum_confidence", "medium")
    )
    rows = []
    for analysis in analyses:
        if analysis.get("vision_status") != "ok":
            continue
        parsed = analysis.get("parsed_analysis", {})
        source = analysis.get("source", {})
        for item in parsed.get("testing_items", []):
            if not valid_event_mark(item):
                continue
            step = re.sub(
                r"\s+", " ", str(item.get("step_label") or "")
            ).strip()
            if not step:
                continue
            confidence = normalize_confidence(item.get("confidence"))
            if not confidence_meets(confidence, minimum):
                continue

            candidate_id = stable_id(
                "testing",
                analysis.get("analysis_id"),
                step,
                item.get("event_mark"),
                item.get("recorded_value"),
                item.get("result"),
            )
            rows.append({
                "candidate_id": candidate_id,
                "test_number": None,
                "candidate_type": "testing_performed_candidate",
                "step_label": step,
                "event_mark": item.get("event_mark"),
                "mark_type": item.get("mark_type"),
                "result": item.get("result"),
                "recorded_value": item.get("recorded_value"),
                "technician_initials": item.get("technician_initials"),
                "date": item.get("date"),
                "confidence": confidence,
                "source": copy.deepcopy(source),
                "source_analysis_id": analysis.get("analysis_id"),
                "human_review": {
                    "status": "pending",
                    "reviewer": None,
                    "reviewed_at_utc": None,
                    "approved_value": None,
                    "note": None,
                },
                "accepted_as_human_reviewed_testing": False,
                "qdrant": {
                    "eligible_for_future_ingestion": False,
                    "entry_created": False,
                    "reason": "pending_human_review",
                },
            })

    # Dedupe identical evidence statements while preserving first source.
    unique = []
    seen = set()
    for row in rows:
        key = (
            row["step_label"].casefold(),
            str(row.get("event_mark") or "").casefold(),
            str(row.get("recorded_value") or "").casefold(),
            str(row.get("result") or "").casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)

    for index, row in enumerate(unique, start=1):
        row["test_number"] = index
    return unique


def build_final_candidates(analyses):
    rows = []
    for analysis in analyses:
        if analysis.get("vision_status") != "ok":
            continue
        parsed = analysis.get("parsed_analysis", {})
        source = analysis.get("source", {})
        for item in parsed.get("final_result_items", []):
            value = re.sub(
                r"\s+", " ", str(item.get("value") or "")
            ).strip()
            mark = re.sub(
                r"\s+", " ", str(item.get("event_mark") or "")
            ).strip()
            if not value or not mark:
                continue
            confidence = normalize_confidence(item.get("confidence"))
            candidate_id = stable_id(
                "final_result",
                analysis.get("analysis_id"),
                value,
                item.get("basis_label"),
                mark,
            )
            rows.append({
                "candidate_id": candidate_id,
                "final_number": None,
                "candidate_type": "final_result_candidate",
                "value": value,
                "basis_label": item.get("basis_label"),
                "event_mark": mark,
                "result": item.get("result"),
                "confidence": confidence,
                "source": copy.deepcopy(source),
                "source_analysis_id": analysis.get("analysis_id"),
                "human_review": {
                    "status": "pending",
                    "reviewer": None,
                    "reviewed_at_utc": None,
                    "approved_value": None,
                    "note": None,
                },
                "accepted_as_human_reviewed_final_result": False,
                "qdrant": {
                    "eligible_for_future_ingestion": False,
                    "entry_created": False,
                    "reason": "pending_human_review",
                },
            })

    unique = []
    seen = set()
    for row in rows:
        key = (
            row["value"].casefold(),
            str(row.get("basis_label") or "").casefold(),
            str(row.get("event_mark") or "").casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)

    for index, row in enumerate(unique, start=1):
        row["final_number"] = index
    return unique


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


def candidate_by_number(rows, number, key):
    for row in rows:
        if int(row.get(key) or 0) == int(number):
            return row
    raise ValueError(
        "Candidate number {} not found for {}.".format(number, key)
    )


def record_decision(
    output_dir,
    decision,
    reviewer,
    testing_candidates,
    final_candidates,
    test_number=None,
    final_number=None,
    value=None,
    note=None,
):
    if not reviewer:
        raise ValueError("--reviewer is required.")

    if decision in {"approve-test", "reject-test", "hold-test"}:
        if test_number is None:
            raise ValueError("--test-number is required.")
        candidate = candidate_by_number(
            testing_candidates, test_number, "test_number"
        )
        kind = "testing_performed"
        if decision == "approve-test":
            stored = "approved"
            approved_value = (
                str(value).strip()
                if value
                else candidate["step_label"]
            )
        elif decision == "reject-test":
            stored, approved_value = "rejected", None
        else:
            stored, approved_value = "hold", None

    elif decision in {"approve-final", "reject-final", "hold-final"}:
        if final_number is None:
            raise ValueError("--final-number is required.")
        candidate = candidate_by_number(
            final_candidates, final_number, "final_number"
        )
        kind = "final_result"
        if decision == "approve-final":
            stored = "approved"
            approved_value = (
                str(value).strip()
                if value
                else candidate["value"]
            )
        elif decision == "reject-final":
            stored, approved_value = "rejected", None
        else:
            stored, approved_value = "hold", None

    else:
        raise ValueError("Unsupported decision.")

    record = {
        "decision_id": stable_id(
            VERSION,
            candidate["candidate_id"],
            stored,
            reviewer,
            now_utc(),
            approved_value,
            note,
        ),
        "field": kind,
        "candidate_id": candidate["candidate_id"],
        "candidate_number": (
            candidate.get("test_number")
            if kind == "testing_performed"
            else candidate.get("final_number")
        ),
        "decision": stored,
        "reviewer": str(reviewer).strip(),
        "reviewed_at_utc": now_utc(),
        "value": approved_value,
        "candidate_value_at_review": (
            candidate.get("step_label")
            if kind == "testing_performed"
            else candidate.get("value")
        ),
        "edited_from_candidate": bool(
            approved_value is not None
            and approved_value != (
                candidate.get("step_label")
                if kind == "testing_performed"
                else candidate.get("value")
            )
        ),
        "note": note,
        "fusion_version": VERSION,
        "qdrant_entry_created": False,
    }

    decisions = load_decisions(output_dir)
    decisions.append(record)
    write_json(decision_path(output_dir), decisions)
    return record


def apply_decisions(testing, finals, decisions):
    latest = {}
    for decision in decisions:
        if decision.get("candidate_id"):
            latest[decision["candidate_id"]] = decision

    for rows, accepted_key in [
        (testing, "accepted_as_human_reviewed_testing"),
        (finals, "accepted_as_human_reviewed_final_result"),
    ]:
        for row in rows:
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
                "edited_from_candidate": decision.get(
                    "edited_from_candidate"
                ),
            }
            if status == "approved":
                row[accepted_key] = True
                row["qdrant"] = {
                    "eligible_for_future_ingestion": True,
                    "entry_created": False,
                    "reason": "human_approved_waiting_for_future_ingestion_pipeline",
                }
            else:
                row[accepted_key] = False
                row["qdrant"] = {
                    "eligible_for_future_ingestion": False,
                    "entry_created": False,
                    "reason": "human_review_{}".format(status),
                }
    return testing, finals


def build_review(analyses, rules, decisions):
    testing = build_testing_candidates(analyses, rules)
    finals = build_final_candidates(analyses)
    apply_decisions(testing, finals, decisions)

    printed_only = []
    uncertain = []
    for analysis in analyses:
        parsed = analysis.get("parsed_analysis", {})
        source = analysis.get("source", {})
        for label in parsed.get("printed_template_only_labels", []):
            printed_only.append({
                "label": label,
                "source": copy.deepcopy(source),
                "accepted_as_testing_evidence": False,
                "reason": "printed_template_without_event_specific_mark",
            })
        for mark in parsed.get("uncertain_marks", []):
            uncertain.append({
                "mark": mark,
                "source": copy.deepcopy(source),
                "accepted_as_testing_evidence": False,
                "human_review_required": False,
                "reason": "vision_mark_uncertain_not_promoted",
            })

    approved_tests = sum(
        row["human_review"]["status"] == "approved"
        for row in testing
    )
    approved_finals = sum(
        row["human_review"]["status"] == "approved"
        for row in finals
    )

    return {
        "field_group": "testing_performed_and_final_result",
        "testing": {
            "candidate_count": len(testing),
            "approved_count": approved_tests,
            "pending_count": sum(
                row["human_review"]["status"] == "pending"
                for row in testing
            ),
            "candidates": testing,
        },
        "final_result": {
            "candidate_count": len(finals),
            "approved_count": approved_finals,
            "pending_count": sum(
                row["human_review"]["status"] == "pending"
                for row in finals
            ),
            "status": (
                "human_approved"
                if approved_finals
                else (
                    "candidate_pending_human_review"
                    if finals
                    else "not_established"
                )
            ),
            "candidates": finals,
        },
        "printed_template_only_observations": printed_only,
        "uncertain_mark_observations": uncertain,
        "source_policy": {
            "printed_template_is_not_completion_evidence": True,
            "document_title_is_not_final_result": True,
            "event_specific_mark_required_for_testing_candidate": True,
            "explicit_event_specific_result_required_for_final_result": True,
            "machine_vision_requires_human_review": True,
        },
        "qdrant_entry_created": False,
    }


def approved_testing_rows(review):
    rows = []
    for candidate in review["testing"]["candidates"]:
        human = candidate["human_review"]
        if human["status"] != "approved":
            continue
        rows.append({
            "candidate_id": candidate["candidate_id"],
            "test_number": candidate["test_number"],
            "value": human["approved_value"],
            "source_step_label": candidate["step_label"],
            "event_mark": candidate["event_mark"],
            "mark_type": candidate["mark_type"],
            "result": candidate["result"],
            "recorded_value": candidate["recorded_value"],
            "technician_initials": candidate["technician_initials"],
            "date": candidate["date"],
            "reviewer": human["reviewer"],
            "reviewed_at_utc": human["reviewed_at_utc"],
            "decision_id": human["decision_id"],
            "edited_from_candidate": human["edited_from_candidate"],
            "source": candidate["source"],
            "eligible_for_future_qdrant_ingestion": True,
            "qdrant_entry_created": False,
        })
    return rows


def approved_final_rows(review):
    rows = []
    for candidate in review["final_result"]["candidates"]:
        human = candidate["human_review"]
        if human["status"] != "approved":
            continue
        rows.append({
            "candidate_id": candidate["candidate_id"],
            "final_number": candidate["final_number"],
            "value": human["approved_value"],
            "source_value": candidate["value"],
            "basis_label": candidate["basis_label"],
            "event_mark": candidate["event_mark"],
            "result": candidate["result"],
            "reviewer": human["reviewer"],
            "reviewed_at_utc": human["reviewed_at_utc"],
            "decision_id": human["decision_id"],
            "edited_from_candidate": human["edited_from_candidate"],
            "source": candidate["source"],
            "eligible_for_future_qdrant_ingestion": True,
            "qdrant_entry_created": False,
        })
    return rows


def build_output(
    approved_source_path,
    source_data,
    bundle_path,
    review,
    analyses,
    source_images,
    skipped_sources,
    bundle_warning,
):
    output = {
        "fusion_version": VERSION,
        "source_fusion_version": source_data.get("fusion_version"),
        "source_approved_fields_path": str(approved_source_path),
        "source_evidence_bundle_path": (
            str(bundle_path) if bundle_path else None
        ),
        "repair_identity": copy.deepcopy(
            source_data.get("repair_identity", {})
        ),
        "approved_fields": copy.deepcopy(
            source_data.get("approved_fields", {})
        ),
        "testing_final_result_review": review,
        "source_image_count": len(source_images),
        "vision_analysis_count": len(analyses),
        "skipped_source_count": len(skipped_sources),
        "skipped_sources": skipped_sources,
        "bundle_warning": bundle_warning,
        "approved_field_count": int(
            source_data.get("approved_field_count", 0)
        ),
        "approved_repair_action_count": int(
            source_data.get("approved_repair_action_count", 0)
        ),
        "approved_parts_replaced_count": int(
            source_data.get("approved_parts_replaced_count", 0)
        ),
        "approved_diagnostic_hypothesis_count": int(
            source_data.get("approved_diagnostic_hypothesis_count", 0)
        ),
        "approved_testing_item_count": 0,
        "approved_final_result_count": 0,
        "accepted_as_final_repair_summary": False,
        "qdrant_entry_created": False,
    }

    approved_tests = approved_testing_rows(review)
    approved_finals = approved_final_rows(review)

    if approved_tests:
        output["approved_fields"]["testing_performed"] = approved_tests
        output["approved_testing_item_count"] = len(approved_tests)
        if "testing_performed" not in source_data.get(
            "approved_fields", {}
        ):
            output["approved_field_count"] += 1

    if approved_finals:
        output["approved_fields"]["final_result"] = approved_finals
        output["approved_final_result_count"] = len(approved_finals)
        if "final_result" not in source_data.get("approved_fields", {}):
            output["approved_field_count"] += 1

    return output


def default_output_dir(source_data):
    identity = source_data.get("repair_identity", {}) or {}
    folder = "_".join(
        safe_name(value)
        for value in [
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
        / "evidence_fusion_v1_5_5_2"
        / folder
        / "events"
        / str(identity.get("log_number") or "unknown")
    )


def render_review(output):
    identity = output.get("repair_identity", {})
    review = output.get("testing_final_result_review", {})
    testing = review.get("testing", {})
    final = review.get("final_result", {})

    lines = [
        "NOVA DRL TESTING PERFORMED / FINAL RESULT FUSION v{}".format(
            VERSION
        ),
        "=" * 82,
        "Log: {}".format(identity.get("log_number")),
        "Model: {}".format(identity.get("model")),
        "Serial: {}".format(identity.get("serial_number")),
        "",
        "SOURCE EVIDENCE",
        "---------------",
        "Candidate source images: {}".format(
            output.get("source_image_count", 0)
        ),
        "Vision analyses: {}".format(
            output.get("vision_analysis_count", 0)
        ),
        "Skipped sources: {}".format(
            output.get("skipped_source_count", 0)
        ),
        "Evidence bundle: {}".format(
            output.get("source_evidence_bundle_path") or "NOT FOUND"
        ),
        "",
        "TESTING PERFORMED CANDIDATES",
        "----------------------------",
        "Candidate count: {}".format(testing.get("candidate_count", 0)),
        "Approved items: {}".format(testing.get("approved_count", 0)),
        "Pending items: {}".format(testing.get("pending_count", 0)),
        "",
    ]

    if not testing.get("candidates"):
        lines += ["None", ""]
    else:
        for row in testing.get("candidates", []):
            human = row.get("human_review", {})
            lines += [
                "TEST {} [{}]".format(
                    row.get("test_number"),
                    row.get("candidate_id"),
                ),
                "  Step label: {}".format(row.get("step_label")),
                "  Event mark: {}".format(row.get("event_mark")),
                "  Mark type: {}".format(row.get("mark_type")),
                "  Result: {}".format(row.get("result")),
                "  Recorded value: {}".format(row.get("recorded_value")),
                "  Technician initials: {}".format(
                    row.get("technician_initials")
                ),
                "  Date: {}".format(row.get("date")),
                "  Confidence: {}".format(row.get("confidence")),
                "  Source: {}{}".format(
                    row.get("source", {}).get("source_document"),
                    (
                        " page {}".format(
                            row.get("source", {}).get("page_number")
                        )
                        if row.get("source", {}).get("page_number")
                        else ""
                    ),
                ),
                "  Human review: {}".format(human.get("status")),
                "  Accepted as human-reviewed testing: {}".format(
                    "YES"
                    if row.get("accepted_as_human_reviewed_testing")
                    else "NO"
                ),
                "  Future Qdrant eligible: {}".format(
                    "YES"
                    if row.get("qdrant", {}).get(
                        "eligible_for_future_ingestion"
                    )
                    else "NO"
                ),
            ]
            if human.get("reviewer"):
                lines += [
                    "  Review decision:",
                    "    Reviewer: {}".format(human.get("reviewer")),
                    "    Approved value: {}".format(
                        human.get("approved_value")
                    ),
                    "    Note: {}".format(human.get("note") or "None"),
                ]
            lines.append("")

    lines += [
        "FINAL RESULT CANDIDATES",
        "-----------------------",
        "Candidate count: {}".format(final.get("candidate_count", 0)),
        "Approved results: {}".format(final.get("approved_count", 0)),
        "Pending results: {}".format(final.get("pending_count", 0)),
        "Final result status: {}".format(final.get("status")),
        "",
    ]

    if not final.get("candidates"):
        lines += ["None", ""]
    else:
        for row in final.get("candidates", []):
            human = row.get("human_review", {})
            lines += [
                "FINAL {} [{}]".format(
                    row.get("final_number"),
                    row.get("candidate_id"),
                ),
                "  Candidate: {}".format(row.get("value")),
                "  Basis label: {}".format(row.get("basis_label")),
                "  Event mark: {}".format(row.get("event_mark")),
                "  Result type: {}".format(row.get("result")),
                "  Confidence: {}".format(row.get("confidence")),
                "  Source: {}{}".format(
                    row.get("source", {}).get("source_document"),
                    (
                        " page {}".format(
                            row.get("source", {}).get("page_number")
                        )
                        if row.get("source", {}).get("page_number")
                        else ""
                    ),
                ),
                "  Human review: {}".format(human.get("status")),
                "  Accepted as human-reviewed final result: {}".format(
                    "YES"
                    if row.get(
                        "accepted_as_human_reviewed_final_result"
                    )
                    else "NO"
                ),
            ]
            if human.get("reviewer"):
                lines += [
                    "  Review decision:",
                    "    Reviewer: {}".format(human.get("reviewer")),
                    "    Approved value: {}".format(
                        human.get("approved_value")
                    ),
                    "    Note: {}".format(human.get("note") or "None"),
                ]
            lines.append("")

    printed = review.get("printed_template_only_observations", [])
    uncertain = review.get("uncertain_mark_observations", [])

    lines += [
        "NON-PROMOTED EVIDENCE",
        "---------------------",
        "Printed-template-only labels: {}".format(len(printed)),
        "Uncertain marks: {}".format(len(uncertain)),
        "Printed template accepted as completed testing: NO",
        "Acceptance Test Report title accepted as final result: NO",
        "",
        "STATUS",
        "------",
        "Approved testing items: {}".format(
            output.get("approved_testing_item_count", 0)
        ),
        "Approved final results: {}".format(
            output.get("approved_final_result_count", 0)
        ),
        "Accepted as final repair summary: NO",
        "Qdrant entries created: 0",
    ]
    return "\n".join(lines) + "\n"


def write_outputs(output, analyses, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        output_dir / "testing_final_result_review.json",
        output.get("testing_final_result_review", {}),
    )
    write_json(
        output_dir / "testing_final_result_page_analyses.json",
        {
            "fusion_version": VERSION,
            "analyses": analyses,
            "qdrant_entry_created": False,
        },
    )
    write_json(
        output_dir / "approved_repair_fields_with_testing_final.json",
        output,
    )
    (output_dir / "testing_final_result_review.txt").write_text(
        render_review(output), encoding="utf-8"
    )



# ============================================================================
# v1.5.5.2 HARDENING OVERRIDES
# ============================================================================

ALLOWED_MARK_TYPES = {
    "checkmark",
    "x_mark",
    "initials",
    "handwritten_value",
    "circle",
    "pass_fail_mark",
    "other",
}

ALLOWED_TEST_RESULTS = {
    "pass",
    "fail",
    "completed",
    "recorded_value",
    "unknown",
}

ALLOWED_FINAL_RESULTS = {
    "pass",
    "fail",
    "accepted",
    "rejected",
    "final_ok",
    "other",
}

GENERIC_MARK_SENTINELS = {
    "",
    "none",
    "not recorded",
    "not_recorded",
    "n/a",
    "na",
    "unknown",
    "handwritten mark",
    "handwritten",
    "mark",
    "no mark",
    "not visible",
    "unreadable",
    "handwritten_value",
    "checked",
}

CUSTOMER_PROBLEM_LABEL_RE = re.compile(
    r"\b(customer\s+(?:problem|complaint|symptom)|"
    r"problem/symptom|failure\s+description)\b",
    re.IGNORECASE,
)

PRINTED_INSTRUCTION_MARK_RE = re.compile(
    r"\b("
    r"try\s+(?:a|to)|by\s+issuing|command|"
    r"check\s+(?:the|for|all)|verify|inspect|remove|"
    r"note\s+(?:if|of)|issue\s+the|see\s+appendix"
    r")\b",
    re.IGNORECASE,
)

PAGE_HEADER_RE = re.compile(
    r"\bpage\s+\d+\s+of\s+\d+\b|"
    r"\bdrl\s+internal\s+checklist\b|"
    r"\bacceptance\s+test\s+report\b",
    re.IGNORECASE,
)

EXPLICIT_FINAL_CONTEXT_RE = re.compile(
    r"\b("
    r"final|result|outcome|pass(?:ed)?|fail(?:ed)?|"
    r"accept(?:ed|ance)?|reject(?:ed)?|final\s*o\.?\s*k\.?"
    r")\b",
    re.IGNORECASE,
)

TRAVELER_FINAL_DISPOSITION_RE = re.compile(
    r"^\s*("
    r"passed\s+all\s+tests|"
    r"no\s+trouble\s+found|"
    r"untestable,?\s+inspection\s+only|"
    r"final\s*o\.?\s*k\.?"
    r")\s*$",
    re.IGNORECASE,
)

ADMIN_OR_FINAL_CONDITION_RE = re.compile(
    r"^\s*("
    r"ttl\s+time\s+spent(?:\s*\(hours\))?|"
    r"total\s+time\s+spent(?:\s*\(hours\))?|"
    r"ttl\s+money\s+spent(?:\s*\(dollars\))?|"
    r"total\s+money\s+spent(?:\s*\(dollars\))?|"
    r"cleaned|aligned|adjusted|"
    r"latest\s+firmware\s+applied|"
    r"all\s+screws\s+appearance|"
    r"warranty\s+sticker\s+applied"
    r")\s*$",
    re.IGNORECASE,
)


def build_prompt(source):
    """
    v1.5.5.2 prompt: choose ONE enum value, never echo schema alternatives,
    and route non-test traveler fields away from TESTING_PERFORMED.
    """
    ocr_hint = source.get("template_ocr_text") or ""
    ocr_section = ""
    if ocr_hint:
        ocr_section = (
            "\nOCR HINT FOR READING PRINTED LABELS ONLY. "
            "THIS OCR IS NOT PROOF THAT ANY STEP WAS COMPLETED:\n"
            + ocr_hint[:3500]
            + "\n"
        )

    source_kind = source.get("source_kind")
    source_document = source.get("source_document")
    page_number = source.get("page_number")
    source_note = (
        "SOURCE KIND: {}\nSOURCE DOCUMENT: {}\nPAGE: {}\n".format(
            source_kind,
            source_document,
            page_number if page_number is not None else "n/a",
        )
    )

    return """You are reading a Direct Repair Laboratories repair-event image.

{}Your job is ONLY to identify EVENT-SPECIFIC visual evidence.

CRITICAL RULES:
1. Printed template text, a printed checklist step, and a document title are
   NOT proof that a test was performed.
2. A testing item requires a visible event-specific mark/value clearly
   associated with a test or inspection label.
3. Choose EXACTLY ONE mark_type from:
   checkmark, x_mark, initials, handwritten_value, circle, pass_fail_mark, other
4. Choose EXACTLY ONE testing result from:
   pass, fail, completed, recorded_value, unknown
5. NEVER return a pipe-separated enum string such as
   "checkmark|x_mark|initials".
6. NEVER use generic placeholders such as "handwritten mark", "none", or
   "not recorded" as event_mark.
7. If the visible mark cannot be stated literally, put it in uncertain_marks.
8. For final_test.png or shipping_final_ok.png:
   - do NOT report hours, dollars, Cleaned, Aligned, Adjusted, firmware,
     appearance, or warranty-sticker fields as testing items.
   - final disposition fields such as Passed All Tests or Final O.K. belong in
     final_result_items, not testing_items.
9. Customer Problem / Symptom / Complaint fields can NEVER be final results.
10. The title "Acceptance Test Report" can NEVER be a final result.
11. A final result requires an explicit event-specific result mark.
12. Choose EXACTLY ONE final result type from:
    pass, fail, accepted, rejected, final_ok, other
13. Preserve literal visible wording. Do not correct or summarize.
14. If uncertain, omit the candidate and use uncertain_marks instead.
15. Return JSON only.

Return exactly:
{{
  "page_has_event_specific_testing_evidence": true,
  "testing_items": [
    {{
      "step_label": "literal visible test/inspection label",
      "event_mark": "literal visible mark/value/initials",
      "mark_type": "checkmark",
      "result": "completed",
      "recorded_value": null,
      "technician_initials": null,
      "date": null,
      "confidence": "high"
    }}
  ],
  "final_result_items": [
    {{
      "value": "literal visible final disposition/result",
      "basis_label": "literal associated final-result label",
      "event_mark": "literal visible mark/value",
      "result": "pass",
      "confidence": "high"
    }}
  ],
  "other_event_observations": [
    {{
      "label": "literal visible non-test field",
      "value": "literal visible event-specific mark/value",
      "category": "administrative|final_condition|other",
      "confidence": "high"
    }}
  ],
  "printed_template_only_labels": [
    "important printed labels with NO associated event-specific mark"
  ],
  "uncertain_marks": [
    "literal uncertain mark or short description"
  ]
}}
""".format(source_note) + ocr_section


def normalize_page_analysis(parsed):
    parsed = parsed if isinstance(parsed, dict) else {}
    testing = parsed.get("testing_items")
    finals = parsed.get("final_result_items")
    observations = parsed.get("other_event_observations")
    printed = parsed.get("printed_template_only_labels")
    uncertain = parsed.get("uncertain_marks")

    if not isinstance(testing, list):
        testing = []
    if not isinstance(finals, list):
        finals = []
    if not isinstance(observations, list):
        observations = []
    if not isinstance(printed, list):
        printed = []
    if not isinstance(uncertain, list):
        uncertain = []

    return {
        "page_has_event_specific_testing_evidence": bool(
            parsed.get("page_has_event_specific_testing_evidence")
        ),
        "testing_items": [x for x in testing if isinstance(x, dict)],
        "final_result_items": [x for x in finals if isinstance(x, dict)],
        "other_event_observations": [
            x for x in observations if isinstance(x, dict)
        ],
        "printed_template_only_labels": [
            str(x) for x in printed if str(x).strip()
        ],
        "uncertain_marks": [
            str(x) for x in uncertain if str(x).strip()
        ],
    }


def page_cache_key(source):
    """
    Stable identity only. The changing image/prompt/model details belong in the
    signature so a stable cache file can be reused or explicitly invalidated.
    """
    return stable_id(
        source.get("source_kind"),
        source.get("source_path"),
        source.get("page_number"),
        source.get("source_document"),
    )


def cache_signature(source, model, max_dimension, no_vision):
    image_path = Path(source.get("image_path") or "")
    image_stat = None
    if image_path.exists() and image_path.is_file():
        stat = image_path.stat()
        image_stat = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }

    prompt_hash = hashlib.sha256(
        build_prompt(source).encode("utf-8")
    ).hexdigest()

    data = {
        "cache_schema": "1.5.5.2",
        "model": model,
        "max_dimension": int(max_dimension),
        "no_vision": bool(no_vision),
        "source_identity": {
            "source_kind": source.get("source_kind"),
            "source_path": source.get("source_path"),
            "source_document": source.get("source_document"),
            "page_number": source.get("page_number"),
            "image_path": source.get("image_path"),
        },
        "image_stat": image_stat,
        "prompt_sha256": prompt_hash,
    }
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), data


def analyze_sources(
    sources,
    output_dir,
    model,
    timeout,
    max_dimension,
    refresh=False,
    no_vision=False,
):
    """
    Deterministic v1.5.5.2 cache.

    A cache hit requires an exact signature match for:
      source identity + image stat + prompt + model + vision mode.
    """
    analyses = []
    cache_dir = (
        Path(output_dir) / "page_analysis_cache_v1_5_5_2"
    )
    cache_dir.mkdir(parents=True, exist_ok=True)

    model_available = ollama_model_exists(model) if not no_vision else False

    for index, source in enumerate(sources, start=1):
        key = page_cache_key(source)
        cache_path = cache_dir / "{}.json".format(key)
        signature, signature_inputs = cache_signature(
            source, model, max_dimension, no_vision
        )

        if cache_path.exists() and not refresh:
            try:
                cached = read_json(cache_path)
            except Exception:
                cached = None

            if (
                isinstance(cached, dict)
                and cached.get("cache_signature") == signature
            ):
                cached["cache_status"] = "reused"
                cached["source_index"] = index
                analyses.append(cached)
                continue

        previous_cache_exists = cache_path.exists()
        record = {
            "analysis_id": key,
            "source_index": index,
            "source": source,
            "vision_model": model,
            "vision_status": "not_run",
            "raw_vision_response": "",
            "parsed_analysis": normalize_page_analysis({}),
            "warning": None,
            "cache_status": (
                "refreshed"
                if refresh and previous_cache_exists
                else (
                    "invalidated"
                    if previous_cache_exists
                    else "created"
                )
            ),
            "cache_signature": signature,
            "cache_signature_inputs": signature_inputs,
            "accepted_as_repair_fact": False,
            "qdrant_entry_created": False,
        }

        image_path = Path(source.get("image_path") or "")
        if not image_path.exists():
            record["vision_status"] = "image_missing"
            record["warning"] = "Image path does not exist."
            write_json(cache_path, record)
            analyses.append(record)
            continue

        if no_vision:
            record["vision_status"] = "vision_disabled"
            write_json(cache_path, record)
            analyses.append(record)
            continue

        if not model_available:
            record["vision_status"] = "model_unavailable"
            record["warning"] = "Ollama model {} was not found.".format(
                model
            )
            write_json(cache_path, record)
            analyses.append(record)
            continue

        vision_image = (
            cache_dir / "vision_images" / "{}.jpg".format(key)
        )
        try:
            image_info = prepare_vision_image(
                image_path, vision_image, max_dimension
            )
            record["vision_image"] = image_info
        except Exception as exc:
            record["vision_status"] = "image_prepare_error"
            record["warning"] = str(exc)
            write_json(cache_path, record)
            analyses.append(record)
            continue

        response = call_ollama_vision(
            model,
            build_prompt(source),
            image_info["vision_image_path"],
            timeout,
        )
        record["raw_vision_response"] = response.get("response", "")
        record["vision_status"] = response.get("status")
        record["warning"] = response.get("warning")
        record["done_reason"] = response.get("done_reason")
        record["eval_count"] = response.get("eval_count")

        if response.get("status") == "ok":
            parsed = parse_json_object(response.get("response"))
            if parsed is None:
                record["vision_status"] = "response_not_json"
            else:
                record["parsed_analysis"] = normalize_page_analysis(
                    parsed
                )

        write_json(cache_path, record)
        analyses.append(record)

    # Persistent manifest makes cache behavior easy to inspect.
    write_json(
        cache_dir / "cache_manifest.json",
        {
            "cache_schema": "1.5.5.2",
            "generated_at_utc": now_utc(),
            "record_count": len(analyses),
            "status_counts": dict(
                collections.Counter(
                    row.get("cache_status") for row in analyses
                )
            ),
            "vision_status_counts": dict(
                collections.Counter(
                    row.get("vision_status") for row in analyses
                )
            ),
            "qdrant_entry_created": False,
        },
    )

    return analyses


def _clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _exact_enum(value, allowed):
    normalized = _clean_text(value).lower()
    return normalized if normalized in allowed else None


def _mark_validation(event_mark, mark_type=None, max_length=96):
    mark = _clean_text(event_mark)
    mark_lower = mark.lower()

    if not mark or mark_lower in GENERIC_MARK_SENTINELS:
        return False, "generic_or_blank_event_mark"

    if len(mark) > int(max_length):
        return False, "event_mark_too_long"

    if "|" in mark:
        return False, "event_mark_contains_schema_enum_echo"

    if PRINTED_INSTRUCTION_MARK_RE.search(mark):
        return False, "event_mark_looks_like_printed_instruction"

    if mark_type is not None:
        mark_type_normalized = _clean_text(mark_type).lower()
        if mark_type_normalized not in ALLOWED_MARK_TYPES:
            return False, "invalid_mark_type_enum"

        if (
            mark_type_normalized == "initials"
            and not re.fullmatch(r"[A-Za-z]{1,4}(?:/[A-Za-z]{1,4})?", mark)
        ):
            return False, "initials_mark_shape_invalid"

    return True, None


def _is_traveler_final_crop(source):
    if source.get("source_kind") != "traveler_event_crop":
        return False
    name = Path(
        source.get("source_document")
        or source.get("image_path")
        or ""
    ).name.lower()
    return name in {"final_test.png", "shipping_final_ok.png"}


def _observation_category(label):
    label_lower = _clean_text(label).lower()
    if re.search(r"\b(time|money|hours|dollars)\b", label_lower):
        return "administrative"
    if ADMIN_OR_FINAL_CONDITION_RE.match(label_lower):
        return "final_condition"
    return "other"


def _observation_record(
    source,
    label,
    value,
    category,
    reason,
    source_analysis_id=None,
):
    return {
        "observation_id": stable_id(
            "routed_observation",
            source_analysis_id,
            source.get("source_path"),
            source.get("page_number"),
            label,
            value,
            reason,
        ),
        "label": _clean_text(label),
        "value": _clean_text(value),
        "category": category,
        "routing_reason": reason,
        "source": copy.deepcopy(source),
        "source_analysis_id": source_analysis_id,
        "accepted_as_testing_evidence": False,
        "accepted_as_final_result": False,
        "qdrant_entry_created": False,
    }


def _rejection_record(
    field,
    source,
    item,
    reason,
    source_analysis_id=None,
):
    return {
        "rejection_id": stable_id(
            field,
            source_analysis_id,
            source.get("source_path"),
            source.get("page_number"),
            json.dumps(item, sort_keys=True, default=str),
            reason,
        ),
        "field": field,
        "reason": reason,
        "raw_item": copy.deepcopy(item),
        "source": copy.deepcopy(source),
        "source_analysis_id": source_analysis_id,
        "accepted_as_repair_fact": False,
        "qdrant_entry_created": False,
    }


def build_testing_candidates_hardened(analyses, rules):
    minimum = (
        rules.get("testing_candidate_policy", {})
        .get("minimum_confidence", "medium")
    )
    max_mark_length = int(
        rules.get("strict_schema", {})
        .get("maximum_event_mark_length", 96)
    )

    rows = []
    rejected = []
    routed = []

    for analysis in analyses:
        if analysis.get("vision_status") != "ok":
            continue

        parsed = analysis.get("parsed_analysis", {})
        source = analysis.get("source", {})
        analysis_id = analysis.get("analysis_id")

        # Preserve model-routed non-test observations.
        for observation in parsed.get(
            "other_event_observations", []
        ):
            label = _clean_text(observation.get("label"))
            value = _clean_text(observation.get("value"))
            if not label and not value:
                continue
            category = _clean_text(
                observation.get("category")
            ).lower()
            if category not in {
                "administrative",
                "final_condition",
                "other",
            }:
                category = _observation_category(label)
            routed.append(
                _observation_record(
                    source,
                    label,
                    value,
                    category,
                    "model_routed_non_test_observation",
                    analysis_id,
                )
            )

        for item in parsed.get("testing_items", []):
            step = _clean_text(item.get("step_label"))
            mark = _clean_text(item.get("event_mark"))
            mark_type = _exact_enum(
                item.get("mark_type"), ALLOWED_MARK_TYPES
            )
            test_result = _exact_enum(
                item.get("result"), ALLOWED_TEST_RESULTS
            )

            if not step:
                rejected.append(
                    _rejection_record(
                        "testing_performed",
                        source,
                        item,
                        "blank_step_label",
                        analysis_id,
                    )
                )
                continue

            # Traveler final crops are disposition/final-condition sources,
            # not TESTING_PERFORMED sources.
            if _is_traveler_final_crop(source):
                routed.append(
                    _observation_record(
                        source,
                        step,
                        mark or item.get("recorded_value"),
                        (
                            "final_disposition"
                            if TRAVELER_FINAL_DISPOSITION_RE.match(step)
                            else _observation_category(step)
                        ),
                        "traveler_final_crop_not_testing_source",
                        analysis_id,
                    )
                )
                continue

            if source.get("source_kind") != "supporting_document_page":
                rejected.append(
                    _rejection_record(
                        "testing_performed",
                        source,
                        item,
                        "source_not_allowed_for_testing",
                        analysis_id,
                    )
                )
                continue

            if ADMIN_OR_FINAL_CONDITION_RE.match(step):
                routed.append(
                    _observation_record(
                        source,
                        step,
                        mark or item.get("recorded_value"),
                        _observation_category(step),
                        "administrative_or_final_condition_not_testing",
                        analysis_id,
                    )
                )
                continue

            if mark_type is None:
                rejected.append(
                    _rejection_record(
                        "testing_performed",
                        source,
                        item,
                        "invalid_mark_type_enum",
                        analysis_id,
                    )
                )
                continue

            if test_result is None:
                rejected.append(
                    _rejection_record(
                        "testing_performed",
                        source,
                        item,
                        "invalid_testing_result_enum",
                        analysis_id,
                    )
                )
                continue

            valid_mark, mark_reason = _mark_validation(
                mark, mark_type, max_mark_length
            )
            if not valid_mark:
                rejected.append(
                    _rejection_record(
                        "testing_performed",
                        source,
                        item,
                        mark_reason,
                        analysis_id,
                    )
                )
                continue

            confidence = normalize_confidence(
                item.get("confidence")
            )
            if not confidence_meets(confidence, minimum):
                rejected.append(
                    _rejection_record(
                        "testing_performed",
                        source,
                        item,
                        "confidence_below_threshold",
                        analysis_id,
                    )
                )
                continue

            candidate_id = stable_id(
                "testing",
                analysis_id,
                step,
                mark,
                item.get("recorded_value"),
                test_result,
            )
            rows.append({
                "candidate_id": candidate_id,
                "test_number": None,
                "candidate_type": "testing_performed_candidate",
                "step_label": step,
                "event_mark": mark,
                "mark_type": mark_type,
                "result": test_result,
                "recorded_value": item.get("recorded_value"),
                "technician_initials": item.get(
                    "technician_initials"
                ),
                "date": item.get("date"),
                "confidence": confidence,
                "source": copy.deepcopy(source),
                "source_analysis_id": analysis_id,
                "human_review": {
                    "status": "pending",
                    "reviewer": None,
                    "reviewed_at_utc": None,
                    "approved_value": None,
                    "note": None,
                },
                "accepted_as_human_reviewed_testing": False,
                "qdrant": {
                    "eligible_for_future_ingestion": False,
                    "entry_created": False,
                    "reason": "pending_human_review",
                },
            })

    # Dedupe exact repeated evidence.
    unique = []
    seen = set()
    for row in rows:
        key = (
            row["step_label"].casefold(),
            row["event_mark"].casefold(),
            str(row.get("recorded_value") or "").casefold(),
            row["result"],
            str(row.get("source", {}).get("source_path")),
            row.get("source", {}).get("page_number"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)

    for index, row in enumerate(unique, start=1):
        row["test_number"] = index

    return unique, rejected, routed


def _final_item_from_traveler_testing_item(
    analysis,
    item,
):
    source = analysis.get("source", {})
    if not _is_traveler_final_crop(source):
        return None

    step = _clean_text(item.get("step_label"))
    if not TRAVELER_FINAL_DISPOSITION_RE.match(step):
        return None

    result = "other"
    normalized = step.casefold()
    if "passed all tests" in normalized:
        result = "pass"
    elif "final" in normalized and "ok" in normalized.replace(".", ""):
        result = "final_ok"
    elif "no trouble found" in normalized:
        result = "other"
    elif "untestable" in normalized:
        result = "other"

    return {
        "value": step,
        "basis_label": "Final Unit Test Results and Notes",
        "event_mark": item.get("event_mark"),
        "result": result,
        "confidence": item.get("confidence"),
        "_derived_from_testing_item": True,
    }


def _approved_complaint_values(source_data):
    complaint = (
        source_data.get("approved_fields", {})
        .get("customer_complaint")
    )
    if isinstance(complaint, dict):
        value = _clean_text(complaint.get("value"))
        return {value.casefold()} if value else set()
    return set()


def _has_approved_repairs(source_data):
    actions = (
        source_data.get("approved_fields", {})
        .get("repair_actions", [])
        or []
    )
    return any(
        isinstance(row, dict) and _clean_text(row.get("value"))
        for row in actions
    )


def _has_approved_parts(source_data):
    parts = (
        source_data.get("approved_fields", {})
        .get("parts_replaced", [])
        or []
    )
    return any(isinstance(row, dict) for row in parts)


def _final_conflict_flags(value, source_data):
    flags = []
    normalized = _clean_text(value).casefold()

    if "no trouble found" in normalized:
        if _has_approved_repairs(source_data):
            flags.append(
                "conflicts_with_approved_repair_actions"
            )
        if _has_approved_parts(source_data):
            flags.append(
                "conflicts_with_approved_parts_replaced"
            )

    return flags


def build_final_candidates_hardened(
    analyses, source_data, rules
):
    max_mark_length = int(
        rules.get("strict_schema", {})
        .get("maximum_event_mark_length", 96)
    )
    complaint_values = _approved_complaint_values(source_data)

    rows = []
    rejected = []
    routed = []

    for analysis in analyses:
        if analysis.get("vision_status") != "ok":
            continue

        parsed = analysis.get("parsed_analysis", {})
        source = analysis.get("source", {})
        analysis_id = analysis.get("analysis_id")

        items = list(parsed.get("final_result_items", []))

        # Recover traveler final-disposition items even when MiniCPM placed
        # them under testing_items.
        for testing_item in parsed.get("testing_items", []):
            derived = _final_item_from_traveler_testing_item(
                analysis, testing_item
            )
            if derived:
                items.append(derived)

        for item in items:
            value = _clean_text(item.get("value"))
            basis = _clean_text(item.get("basis_label"))
            mark = _clean_text(item.get("event_mark"))
            final_result = _exact_enum(
                item.get("result"), ALLOWED_FINAL_RESULTS
            )

            if not value:
                rejected.append(
                    _rejection_record(
                        "final_result",
                        source,
                        item,
                        "blank_final_value",
                        analysis_id,
                    )
                )
                continue

            if value.casefold() in complaint_values:
                rejected.append(
                    _rejection_record(
                        "final_result",
                        source,
                        item,
                        "matches_approved_customer_complaint",
                        analysis_id,
                    )
                )
                continue

            if CUSTOMER_PROBLEM_LABEL_RE.search(basis):
                rejected.append(
                    _rejection_record(
                        "final_result",
                        source,
                        item,
                        "customer_problem_field_cannot_be_final_result",
                        analysis_id,
                    )
                )
                continue

            if PAGE_HEADER_RE.search(value):
                rejected.append(
                    _rejection_record(
                        "final_result",
                        source,
                        item,
                        "page_header_or_document_title_not_final_result",
                        analysis_id,
                    )
                )
                continue

            if final_result is None:
                rejected.append(
                    _rejection_record(
                        "final_result",
                        source,
                        item,
                        "invalid_final_result_enum",
                        analysis_id,
                    )
                )
                continue

            valid_mark, mark_reason = _mark_validation(
                mark, None, max_mark_length
            )
            if not valid_mark:
                rejected.append(
                    _rejection_record(
                        "final_result",
                        source,
                        item,
                        mark_reason,
                        analysis_id,
                    )
                )
                continue

            # Supporting document pages need explicit final-result context.
            if (
                source.get("source_kind")
                == "supporting_document_page"
            ):
                combined = "{} {}".format(value, basis)
                if not EXPLICIT_FINAL_CONTEXT_RE.search(combined):
                    rejected.append(
                        _rejection_record(
                            "final_result",
                            source,
                            item,
                            "supporting_document_lacks_explicit_final_result_context",
                            analysis_id,
                        )
                    )
                    continue

            confidence = normalize_confidence(
                item.get("confidence")
            )
            conflict_flags = _final_conflict_flags(
                value, source_data
            )

            candidate_id = stable_id(
                "final_result",
                analysis_id,
                value,
                basis,
                mark,
                final_result,
            )
            rows.append({
                "candidate_id": candidate_id,
                "final_number": None,
                "candidate_type": "final_result_candidate",
                "value": value,
                "basis_label": basis,
                "event_mark": mark,
                "result": final_result,
                "confidence": confidence,
                "source": copy.deepcopy(source),
                "source_analysis_id": analysis_id,
                "conflict_flags": conflict_flags,
                "conflict_review_required": bool(conflict_flags),
                "approval_requires_conflict_acknowledgement": bool(
                    conflict_flags
                ),
                "human_review": {
                    "status": "pending",
                    "reviewer": None,
                    "reviewed_at_utc": None,
                    "approved_value": None,
                    "note": None,
                },
                "accepted_as_human_reviewed_final_result": False,
                "qdrant": {
                    "eligible_for_future_ingestion": False,
                    "entry_created": False,
                    "reason": (
                        "conflict_review_required"
                        if conflict_flags
                        else "pending_human_review"
                    ),
                },
            })

    # Dedupe. Prefer a native final_result item over one derived from a
    # traveler testing item by preserving the first occurrence.
    unique = []
    seen = set()
    for row in rows:
        key = (
            row["value"].casefold(),
            row["basis_label"].casefold(),
            row["event_mark"].casefold(),
            str(row.get("source", {}).get("source_path")),
            row.get("source", {}).get("page_number"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)

    # Detect mutually-exclusive dispositions on the same source analysis.
    mutually_exclusive = [
        {
            "passed all tests",
            "no trouble found",
            "untestable, inspection only",
            "untestable inspection only",
        }
    ]
    by_analysis = collections.defaultdict(list)
    for row in unique:
        by_analysis[row.get("source_analysis_id")].append(row)

    for analysis_id, analysis_rows in by_analysis.items():
        normalized_values = {
            row["value"].casefold(): row
            for row in analysis_rows
        }
        disposition_hits = [
            value
            for value in normalized_values
            if value in mutually_exclusive[0]
        ]
        if len(disposition_hits) >= 2:
            for value in disposition_hits:
                row = normalized_values[value]
                flag = (
                    "mutually_exclusive_final_options_detected_same_source"
                )
                if flag not in row["conflict_flags"]:
                    row["conflict_flags"].append(flag)
                row["conflict_review_required"] = True
                row[
                    "approval_requires_conflict_acknowledgement"
                ] = True
                row["qdrant"]["reason"] = (
                    "conflict_review_required"
                )

    for index, row in enumerate(unique, start=1):
        row["final_number"] = index

    return unique, rejected, routed


def build_review(analyses, rules, decisions, source_data=None):
    source_data = source_data or {}

    testing, testing_rejections, testing_routed = (
        build_testing_candidates_hardened(analyses, rules)
    )
    finals, final_rejections, final_routed = (
        build_final_candidates_hardened(
            analyses, source_data, rules
        )
    )
    apply_decisions(testing, finals, decisions)

    printed_only = []
    uncertain = []
    model_observations = []

    for analysis in analyses:
        parsed = analysis.get("parsed_analysis", {})
        source = analysis.get("source", {})
        analysis_id = analysis.get("analysis_id")

        for label in parsed.get(
            "printed_template_only_labels", []
        ):
            printed_only.append({
                "label": label,
                "source": copy.deepcopy(source),
                "accepted_as_testing_evidence": False,
                "reason": (
                    "printed_template_without_event_specific_mark"
                ),
            })

        for mark in parsed.get("uncertain_marks", []):
            uncertain.append({
                "mark": mark,
                "source": copy.deepcopy(source),
                "accepted_as_testing_evidence": False,
                "human_review_required": False,
                "reason": "vision_mark_uncertain_not_promoted",
            })

        for observation in parsed.get(
            "other_event_observations", []
        ):
            label = _clean_text(observation.get("label"))
            value = _clean_text(observation.get("value"))
            if label or value:
                model_observations.append(
                    _observation_record(
                        source,
                        label,
                        value,
                        _observation_category(label),
                        "model_routed_non_test_observation",
                        analysis_id,
                    )
                )

    all_routed = testing_routed + final_routed + model_observations

    # Dedupe routed observations.
    routed_unique = []
    routed_seen = set()
    for row in all_routed:
        key = (
            row.get("label", "").casefold(),
            row.get("value", "").casefold(),
            row.get("category"),
            str(row.get("source", {}).get("source_path")),
            row.get("source", {}).get("page_number"),
        )
        if key in routed_seen:
            continue
        routed_seen.add(key)
        routed_unique.append(row)

    approved_tests = sum(
        row["human_review"]["status"] == "approved"
        for row in testing
    )
    approved_finals = sum(
        row["human_review"]["status"] == "approved"
        for row in finals
    )

    conflict_count = sum(
        bool(row.get("conflict_flags")) for row in finals
    )

    return {
        "field_group": "testing_performed_and_final_result",
        "testing": {
            "candidate_count": len(testing),
            "approved_count": approved_tests,
            "pending_count": sum(
                row["human_review"]["status"] == "pending"
                for row in testing
            ),
            "candidates": testing,
        },
        "final_result": {
            "candidate_count": len(finals),
            "approved_count": approved_finals,
            "pending_count": sum(
                row["human_review"]["status"] == "pending"
                for row in finals
            ),
            "conflict_candidate_count": conflict_count,
            "status": (
                "human_approved"
                if approved_finals
                else (
                    "candidate_pending_human_review"
                    if finals
                    else "not_established"
                )
            ),
            "candidates": finals,
        },
        "hardening": {
            "testing_rejection_count": len(
                testing_rejections
            ),
            "final_rejection_count": len(final_rejections),
            "routed_observation_count": len(routed_unique),
            "testing_rejections": testing_rejections,
            "final_rejections": final_rejections,
            "routed_observations": routed_unique,
            "strict_schema_enforced": True,
            "source_aware_routing_enabled": True,
            "customer_complaint_final_result_block_enabled": True,
            "conflict_checks_enabled": True,
        },
        "printed_template_only_observations": printed_only,
        "uncertain_mark_observations": uncertain,
        "source_policy": {
            "printed_template_is_not_completion_evidence": True,
            "document_title_is_not_final_result": True,
            "event_specific_mark_required_for_testing_candidate": True,
            "explicit_event_specific_result_required_for_final_result": True,
            "machine_vision_requires_human_review": True,
            "traveler_final_crops_are_not_testing_sources": True,
            "schema_enum_echoes_are_rejected": True,
            "conflicted_final_approval_requires_acknowledgement": True,
        },
        "qdrant_entry_created": False,
    }


def record_decision(
    output_dir,
    decision,
    reviewer,
    testing_candidates,
    final_candidates,
    test_number=None,
    final_number=None,
    value=None,
    note=None,
    acknowledge_conflict=False,
):
    if not reviewer:
        raise ValueError("--reviewer is required.")

    if decision in {
        "approve-test",
        "reject-test",
        "hold-test",
    }:
        if test_number is None:
            raise ValueError("--test-number is required.")
        candidate = candidate_by_number(
            testing_candidates, test_number, "test_number"
        )
        kind = "testing_performed"
        if decision == "approve-test":
            stored = "approved"
            approved_value = (
                str(value).strip()
                if value
                else candidate["step_label"]
            )
        elif decision == "reject-test":
            stored, approved_value = "rejected", None
        else:
            stored, approved_value = "hold", None

    elif decision in {
        "approve-final",
        "reject-final",
        "hold-final",
    }:
        if final_number is None:
            raise ValueError("--final-number is required.")
        candidate = candidate_by_number(
            final_candidates, final_number, "final_number"
        )
        kind = "final_result"

        if (
            decision == "approve-final"
            and candidate.get(
                "approval_requires_conflict_acknowledgement"
            )
            and not acknowledge_conflict
        ):
            raise ValueError(
                "Final-result candidate {} carries conflict flags: {}. "
                "Verify the source evidence and rerun with "
                "--acknowledge-conflict only if you intend to approve it."
                .format(
                    candidate.get("final_number"),
                    ", ".join(
                        candidate.get("conflict_flags", [])
                    ),
                )
            )

        if decision == "approve-final":
            stored = "approved"
            approved_value = (
                str(value).strip()
                if value
                else candidate["value"]
            )
        elif decision == "reject-final":
            stored, approved_value = "rejected", None
        else:
            stored, approved_value = "hold", None
    else:
        raise ValueError("Unsupported decision.")

    record = {
        "decision_id": stable_id(
            VERSION,
            candidate["candidate_id"],
            stored,
            reviewer,
            now_utc(),
            approved_value,
            note,
        ),
        "field": kind,
        "candidate_id": candidate["candidate_id"],
        "candidate_number": (
            candidate.get("test_number")
            if kind == "testing_performed"
            else candidate.get("final_number")
        ),
        "decision": stored,
        "reviewer": str(reviewer).strip(),
        "reviewed_at_utc": now_utc(),
        "value": approved_value,
        "candidate_value_at_review": (
            candidate.get("step_label")
            if kind == "testing_performed"
            else candidate.get("value")
        ),
        "edited_from_candidate": bool(
            approved_value is not None
            and approved_value
            != (
                candidate.get("step_label")
                if kind == "testing_performed"
                else candidate.get("value")
            )
        ),
        "conflict_flags_at_review": copy.deepcopy(
            candidate.get("conflict_flags", [])
        ),
        "conflict_acknowledged": bool(
            acknowledge_conflict
        ),
        "note": note,
        "fusion_version": VERSION,
        "qdrant_entry_created": False,
    }

    decisions = load_decisions(output_dir)
    decisions.append(record)
    write_json(decision_path(output_dir), decisions)
    return record


def render_review(output):
    identity = output.get("repair_identity", {})
    review = output.get("testing_final_result_review", {})
    testing = review.get("testing", {})
    final = review.get("final_result", {})
    hardening = review.get("hardening", {})

    lines = [
        "NOVA DRL TESTING PERFORMED / FINAL RESULT FUSION v{}".format(
            VERSION
        ),
        "=" * 86,
        "Log: {}".format(identity.get("log_number")),
        "Model: {}".format(identity.get("model")),
        "Serial: {}".format(identity.get("serial_number")),
        "",
        "SOURCE EVIDENCE",
        "---------------",
        "Candidate source images: {}".format(
            output.get("source_image_count", 0)
        ),
        "Vision analyses: {}".format(
            output.get("vision_analysis_count", 0)
        ),
        "Evidence bundle: {}".format(
            output.get("source_evidence_bundle_path")
            or "NOT FOUND"
        ),
        "",
        "HARDENING SUMMARY",
        "-----------------",
        "Testing candidates rejected before review: {}".format(
            hardening.get("testing_rejection_count", 0)
        ),
        "Final-result candidates rejected before review: {}".format(
            hardening.get("final_rejection_count", 0)
        ),
        "Non-test/admin/final-condition observations routed away: {}".format(
            hardening.get("routed_observation_count", 0)
        ),
        "Strict enum/schema validation: YES",
        "Traveler final crops used as TESTING_PERFORMED sources: NO",
        "Customer complaint allowed as final result: NO",
        "Conflicted final approval requires explicit acknowledgement: YES",
        "",
        "TESTING PERFORMED CANDIDATES",
        "----------------------------",
        "Candidate count: {}".format(
            testing.get("candidate_count", 0)
        ),
        "Approved items: {}".format(
            testing.get("approved_count", 0)
        ),
        "Pending items: {}".format(
            testing.get("pending_count", 0)
        ),
        "",
    ]

    if not testing.get("candidates"):
        lines += ["None", ""]
    else:
        for row in testing.get("candidates", []):
            human = row.get("human_review", {})
            lines += [
                "TEST {} [{}]".format(
                    row.get("test_number"),
                    row.get("candidate_id"),
                ),
                "  Step label: {}".format(
                    row.get("step_label")
                ),
                "  Event mark: {}".format(
                    row.get("event_mark")
                ),
                "  Mark type: {}".format(
                    row.get("mark_type")
                ),
                "  Result: {}".format(row.get("result")),
                "  Recorded value: {}".format(
                    row.get("recorded_value")
                ),
                "  Technician initials: {}".format(
                    row.get("technician_initials")
                ),
                "  Date: {}".format(row.get("date")),
                "  Confidence: {}".format(
                    row.get("confidence")
                ),
                "  Source: {}{}".format(
                    row.get("source", {}).get(
                        "source_document"
                    ),
                    (
                        " page {}".format(
                            row.get("source", {}).get(
                                "page_number"
                            )
                        )
                        if row.get("source", {}).get(
                            "page_number"
                        )
                        else ""
                    ),
                ),
                "  Human review: {}".format(
                    human.get("status")
                ),
                "  Accepted as human-reviewed testing: {}".format(
                    "YES"
                    if row.get(
                        "accepted_as_human_reviewed_testing"
                    )
                    else "NO"
                ),
                "",
            ]

    lines += [
        "FINAL RESULT CANDIDATES",
        "-----------------------",
        "Candidate count: {}".format(
            final.get("candidate_count", 0)
        ),
        "Approved results: {}".format(
            final.get("approved_count", 0)
        ),
        "Pending results: {}".format(
            final.get("pending_count", 0)
        ),
        "Conflict candidates: {}".format(
            final.get("conflict_candidate_count", 0)
        ),
        "Final result status: {}".format(
            final.get("status")
        ),
        "",
    ]

    if not final.get("candidates"):
        lines += ["None", ""]
    else:
        for row in final.get("candidates", []):
            human = row.get("human_review", {})
            lines += [
                "FINAL {} [{}]".format(
                    row.get("final_number"),
                    row.get("candidate_id"),
                ),
                "  Candidate: {}".format(row.get("value")),
                "  Basis label: {}".format(
                    row.get("basis_label")
                ),
                "  Event mark: {}".format(
                    row.get("event_mark")
                ),
                "  Result type: {}".format(
                    row.get("result")
                ),
                "  Confidence: {}".format(
                    row.get("confidence")
                ),
                "  Conflict flags: {}".format(
                    row.get("conflict_flags") or "None"
                ),
                "  Conflict review required: {}".format(
                    "YES"
                    if row.get("conflict_review_required")
                    else "NO"
                ),
                "  Source: {}{}".format(
                    row.get("source", {}).get(
                        "source_document"
                    ),
                    (
                        " page {}".format(
                            row.get("source", {}).get(
                                "page_number"
                            )
                        )
                        if row.get("source", {}).get(
                            "page_number"
                        )
                        else ""
                    ),
                ),
                "  Human review: {}".format(
                    human.get("status")
                ),
                "",
            ]

    lines += [
        "NON-PROMOTED / ROUTED EVIDENCE",
        "------------------------------",
        "Printed-template-only labels: {}".format(
            len(
                review.get(
                    "printed_template_only_observations",
                    [],
                )
            )
        ),
        "Uncertain marks: {}".format(
            len(review.get("uncertain_mark_observations", []))
        ),
        "Testing rejections: {}".format(
            hardening.get("testing_rejection_count", 0)
        ),
        "Final-result rejections: {}".format(
            hardening.get("final_rejection_count", 0)
        ),
        "Routed observations: {}".format(
            hardening.get("routed_observation_count", 0)
        ),
        "Printed template accepted as completed testing: NO",
        "Acceptance Test Report title accepted as final result: NO",
        "",
        "STATUS",
        "------",
        "Approved testing items: {}".format(
            output.get("approved_testing_item_count", 0)
        ),
        "Approved final results: {}".format(
            output.get("approved_final_result_count", 0)
        ),
        "Accepted as final repair summary: NO",
        "Qdrant entries created: 0",
    ]
    return "\n".join(lines) + "\n"


def write_outputs(output, analyses, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    review = output.get("testing_final_result_review", {})
    hardening = review.get("hardening", {})

    write_json(
        output_dir / "testing_final_result_review.json",
        review,
    )
    write_json(
        output_dir / "testing_final_result_page_analyses.json",
        {
            "fusion_version": VERSION,
            "analyses": analyses,
            "qdrant_entry_created": False,
        },
    )
    write_json(
        output_dir / "testing_final_result_rejections.json",
        {
            "fusion_version": VERSION,
            "testing_rejections": hardening.get(
                "testing_rejections", []
            ),
            "final_rejections": hardening.get(
                "final_rejections", []
            ),
            "qdrant_entry_created": False,
        },
    )
    write_json(
        output_dir / "testing_final_result_routed_observations.json",
        {
            "fusion_version": VERSION,
            "observations": hardening.get(
                "routed_observations", []
            ),
            "qdrant_entry_created": False,
        },
    )
    write_json(
        output_dir / "approved_repair_fields_with_testing_final.json",
        output,
    )
    (output_dir / "testing_final_result_review.txt").write_text(
        render_review(output), encoding="utf-8"
    )


# ============================================================================
# v1.5.5.2 FORM-AWARE MARK SEMANTICS OVERRIDES
# ============================================================================

TESTING_SEMANTIC_ROLES = {"test", "inspection", "setup", "procedure", "unknown"}
FINAL_SEMANTIC_ROLES = {"final_disposition", "final_result_field", "unknown"}
ASSOCIATION_BASES = {"same_row", "same_box", "adjacent_label", "selected_option", "unknown"}
FINAL_RESULT_ENUMS_1552 = {
    "pass", "fail", "accepted", "rejected", "final_ok",
    "no_trouble_found", "untestable_inspection_only", "other",
}

DOCUMENT_TITLE_BASIS = {
    "acceptance test report",
    "drl acceptance test report",
    "robot test report",
    "drl internal checklist",
}

UNRESOLVED_RESULT_VALUES = {
    "pass/fail", "pass fail", "pass or fail", "pass_fail_mark",
    "pass_fail", "result", "final result",
}

SETUP_RE = re.compile(
    r"\b(move\s+the\s+robot\s+into\s+the\s+test\s+area|hook\s+up\s+the\s+cables|"
    r"remove\s+shipping\s+bracket|install\s+shipping\s+bracket|connect\s+the\s+controller)\b",
    re.IGNORECASE,
)

RESULT_FIELD_RE = re.compile(
    r"\b(final\s+(?:unit\s+)?test\s+results?|final\s+result|overall\s+result|"
    r"test\s+result|pass\s*/\s*fail|acceptance\s+result)\b",
    re.IGNORECASE,
)

TRAVELER_FINAL_CANONICAL = {
    "passed all tests": ("Passed All Tests", "pass"),
    "no trouble found": ("No Trouble Found", "no_trouble_found"),
    "untestable, inspection only": ("Untestable, Inspection Only", "untestable_inspection_only"),
    "untestable inspection only": ("Untestable, Inspection Only", "untestable_inspection_only"),
    "final o.k.": ("Final O.K.", "final_ok"),
    "final o.k": ("Final O.K.", "final_ok"),
    "final ok": ("Final O.K.", "final_ok"),
}

SELECTION_MARK_RE = re.compile(
    r"^(?:x|check(?:mark|ed\s+box)?|checked\s+box|selected|yes|✓|✔|☒)$",
    re.IGNORECASE,
)

UNRELATED_NUMERIC_MARK_RE = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s*(?:hours?|hrs?|dollars?)\b|\$\s*\d)",
    re.IGNORECASE,
)


def _form_profile(source):
    family = str(source.get("document_family") or "").upper()
    role = str(source.get("document_role") or "").lower()
    basename = Path(
        source.get("source_document") or source.get("image_path") or ""
    ).name.lower()

    if basename == "final_test.png":
        return "DRL_TRAVELER_FINAL_TEST"
    if basename == "shipping_final_ok.png":
        return "DRL_TRAVELER_SHIPPING_FINAL_OK"
    if family == "DRL_INTERNAL_CHECKLIST" or role == "robot_checklist":
        return "DRL_INTERNAL_CHECKLIST"
    if family == "DRL_ACCEPTANCE_TEST_REPORT" or role in {
        "robot_test_report", "test_report", "rbt_report"
    }:
        return "DRL_ACCEPTANCE_TEST_REPORT"
    return "UNKNOWN"


def _clean_enum(value, allowed, default="unknown"):
    normalized = _clean_text(value).lower()
    return normalized if normalized in allowed else default


def _infer_testing_semantic_role(step_label):
    step = _clean_text(step_label)
    if SETUP_RE.search(step):
        return "setup"
    if re.search(r"\b(inspect|visual|check|verify|measure|level|clearance|movement|servo|home|vacuum|alignment|tension)\b", step, re.I):
        return "inspection" if re.search(r"\b(inspect|visual|check|verify|clearance|alignment|tension)\b", step, re.I) else "test"
    return "unknown"


def build_prompt(source):
    ocr_hint = source.get("template_ocr_text") or ""
    ocr_section = ""
    if ocr_hint:
        ocr_section = (
            "\nOCR HINT FOR READING PRINTED LABELS ONLY. THIS OCR IS NOT "
            "PROOF THAT A STEP WAS COMPLETED:\n" + ocr_hint[:3500] + "\n"
        )

    profile = _form_profile(source)
    return """You are reading a Direct Repair Laboratories repair-event image.

FORM PROFILE: {profile}
SOURCE: {doc}
PAGE: {page}

Identify EVENT-SPECIFIC visual marks and associate each mark with the correct
printed or handwritten field.

FORM-AWARE RULES:
1. A printed checklist/test step is not evidence until a visible event-specific
   mark is associated with that step.
2. On DRL checklist-style forms, an X or checkmark beside a normal step means
   CHECKED/COMPLETED. It does NOT mean FAIL by itself.
3. PASS or FAIL may be reported only when the mark is associated with an
   explicit result field/choice and the selected result is unambiguous.
4. The generic printed choice "Pass/Fail" is not itself a result.
5. Choose exactly one mark_type:
   checkmark, x_mark, initials, handwritten_value, circle, pass_fail_mark, other
6. Choose exactly one testing result:
   pass, fail, completed, recorded_value, unknown
7. For each testing item choose semantic_role:
   test, inspection, setup, procedure, unknown
8. For each item choose association_basis:
   same_row, same_box, adjacent_label, selected_option, unknown
9. Setup/procedure steps are not testing results. Still report them in
   other_event_observations when event-specific.
10. For final_test.png, only these are final-disposition options:
    Passed All Tests; No Trouble Found; Untestable, Inspection Only.
    Hours, dollars, Cleaned, Aligned, Adjusted, firmware, screw appearance and
    warranty-sticker fields are NOT final results.
11. For shipping_final_ok.png, Final O.K./Final OK is a known final field.
12. Customer complaint/problem/symptom fields can never be final results.
13. The document title "Acceptance Test Report" can never be a final-result
    field or basis.
14. For supporting documents, a final result requires an explicit result field
    such as Final Result / Test Result / Overall Result / Pass-Fail selection.
15. If you cannot tell which option a mark selects, put it in uncertain_marks.
16. Preserve literal visible wording. Return JSON only.

Return exactly:
{{
  "page_has_event_specific_testing_evidence": true,
  "testing_items": [
    {{
      "step_label": "literal visible label",
      "event_mark": "literal visible mark/value",
      "mark_type": "checkmark",
      "result": "completed",
      "semantic_role": "inspection",
      "association_basis": "same_row",
      "selected_result": null,
      "recorded_value": null,
      "technician_initials": null,
      "date": null,
      "confidence": "high"
    }}
  ],
  "final_result_items": [
    {{
      "value": "literal visible final disposition/result",
      "basis_label": "literal associated result field label",
      "event_mark": "literal visible selected mark/value",
      "result": "pass",
      "semantic_role": "final_result_field",
      "association_basis": "selected_option",
      "selected_result": "pass",
      "confidence": "high"
    }}
  ],
  "other_event_observations": [
    {{
      "label": "literal visible non-test field or setup/procedure step",
      "value": "literal visible event-specific mark/value",
      "category": "setup|procedure|administrative|final_condition|other",
      "confidence": "high"
    }}
  ],
  "printed_template_only_labels": [],
  "uncertain_marks": []
}}
""".format(
        profile=profile,
        doc=source.get("source_document"),
        page=source.get("page_number") if source.get("page_number") is not None else "n/a",
    ) + ocr_section


def _selection_mark_valid(mark, mark_type):
    mark = _clean_text(mark)
    mark_type = _clean_text(mark_type).lower()
    if not mark:
        return False
    if UNRELATED_NUMERIC_MARK_RE.search(mark):
        return False
    if mark.lower() in GENERIC_MARK_SENTINELS:
        return False
    if mark_type in {"checkmark", "x_mark", "circle"}:
        return bool(SELECTION_MARK_RE.match(mark) or mark.lower() in {"checkmark", "x_mark", "circle"})
    if mark_type in {"initials", "handwritten_value", "other"}:
        return len(mark) <= 32 and not PRINTED_INSTRUCTION_MARK_RE.search(mark)
    return False


def build_testing_candidates_hardened(analyses, rules):
    minimum = rules.get("testing_candidate_policy", {}).get("minimum_confidence", "medium")
    max_mark_length = int(rules.get("strict_schema", {}).get("maximum_event_mark_length", 96))
    rows, rejected, routed = [], [], []

    for analysis in analyses:
        if analysis.get("vision_status") != "ok":
            continue
        parsed = analysis.get("parsed_analysis", {})
        source = analysis.get("source", {})
        analysis_id = analysis.get("analysis_id")
        profile = _form_profile(source)

        for observation in parsed.get("other_event_observations", []):
            label = _clean_text(observation.get("label"))
            value = _clean_text(observation.get("value"))
            if label or value:
                category = _clean_text(observation.get("category")).lower()
                if category not in {"setup","procedure","administrative","final_condition","other"}:
                    category = _observation_category(label)
                routed.append(_observation_record(source, label, value, category, "model_routed_non_testing_observation", analysis_id))

        for item in parsed.get("testing_items", []):
            step = _clean_text(item.get("step_label"))
            mark = _clean_text(item.get("event_mark"))
            mark_type = _exact_enum(item.get("mark_type"), ALLOWED_MARK_TYPES)
            raw_result = _clean_text(item.get("result")).lower()
            model_result = _exact_enum(item.get("result"), ALLOWED_TEST_RESULTS)
            semantic_role = _clean_enum(item.get("semantic_role"), TESTING_SEMANTIC_ROLES)
            if semantic_role == "unknown":
                semantic_role = _infer_testing_semantic_role(step)
            association_basis = _clean_enum(item.get("association_basis"), ASSOCIATION_BASES)
            selected_result = _clean_text(item.get("selected_result")).lower() or None

            if not step:
                rejected.append(_rejection_record("testing_performed", source, item, "blank_step_label", analysis_id)); continue

            if _is_traveler_final_crop(source):
                routed.append(_observation_record(source, step, mark or item.get("recorded_value"), "final_disposition" if TRAVELER_FINAL_DISPOSITION_RE.match(step) else _observation_category(step), "traveler_final_crop_not_testing_source", analysis_id)); continue

            if source.get("source_kind") != "supporting_document_page":
                rejected.append(_rejection_record("testing_performed", source, item, "source_not_allowed_for_testing", analysis_id)); continue

            if semantic_role in {"setup", "procedure"}:
                routed.append(_observation_record(source, step, mark, semantic_role, "form_semantics_setup_or_procedure_not_testing", analysis_id)); continue

            if ADMIN_OR_FINAL_CONDITION_RE.match(step):
                routed.append(_observation_record(source, step, mark or item.get("recorded_value"), _observation_category(step), "administrative_or_final_condition_not_testing", analysis_id)); continue

            if mark_type is None:
                rejected.append(_rejection_record("testing_performed", source, item, "invalid_mark_type_enum", analysis_id)); continue
            if model_result is None:
                rejected.append(_rejection_record("testing_performed", source, item, "invalid_testing_result_enum", analysis_id)); continue

            valid_mark, mark_reason = _mark_validation(mark, mark_type, max_mark_length)
            if not valid_mark:
                rejected.append(_rejection_record("testing_performed", source, item, mark_reason, analysis_id)); continue

            confidence = normalize_confidence(item.get("confidence"))
            if not confidence_meets(confidence, minimum):
                rejected.append(_rejection_record("testing_performed", source, item, "confidence_below_threshold", analysis_id)); continue

            canonical_result = model_result
            semantic_correction = None
            explicit_result_field = bool(RESULT_FIELD_RE.search(step)) or semantic_role == "test_result"

            # Critical v1.5.5.2 rule: X/check on a normal checklist/test step
            # means completion, not failure.
            if mark_type in {"checkmark", "x_mark"} and not explicit_result_field:
                if model_result in {"pass", "fail", "unknown"}:
                    canonical_result = "completed"
                    semantic_correction = "x_or_checkmark_on_checklist_step_means_completed_not_pass_fail"

            # Even on an explicit pass/fail field, a generic X is not enough to
            # know which side was selected unless MiniCPM reports selected_result.
            if explicit_result_field and model_result in {"pass", "fail"}:
                if selected_result not in {"pass", "fail"} and association_basis != "selected_option":
                    rejected.append(_rejection_record("testing_performed", source, item, "pass_fail_result_not_unambiguously_selected", analysis_id)); continue
                if selected_result in {"pass", "fail"}:
                    canonical_result = selected_result

            candidate_id = stable_id("testing1552", analysis_id, step, mark, canonical_result, association_basis)
            rows.append({
                "candidate_id": candidate_id,
                "test_number": None,
                "candidate_type": "testing_performed_candidate",
                "form_profile": profile,
                "step_label": step,
                "event_mark": mark,
                "mark_type": mark_type,
                "raw_model_result": raw_result,
                "result": canonical_result,
                "semantic_role": semantic_role,
                "association_basis": association_basis,
                "selected_result": selected_result,
                "semantic_correction": semantic_correction,
                "recorded_value": item.get("recorded_value"),
                "technician_initials": item.get("technician_initials"),
                "date": item.get("date"),
                "confidence": confidence,
                "source": copy.deepcopy(source),
                "source_analysis_id": analysis_id,
                "human_review": {"status":"pending","reviewer":None,"reviewed_at_utc":None,"approved_value":None,"note":None},
                "accepted_as_human_reviewed_testing": False,
                "qdrant": {"eligible_for_future_ingestion":False,"entry_created":False,"reason":"pending_human_review"},
            })

    unique, seen = [], set()
    for row in rows:
        key=(row["step_label"].casefold(),row["event_mark"].casefold(),row["result"],str(row.get("source",{}).get("source_path")),row.get("source",{}).get("page_number"))
        if key in seen: continue
        seen.add(key); unique.append(row)
    for i,row in enumerate(unique,1): row["test_number"]=i
    return unique, rejected, routed


def _canonical_traveler_final(value):
    normalized = _clean_text(value).casefold().replace("  "," ")
    if normalized in TRAVELER_FINAL_CANONICAL:
        return TRAVELER_FINAL_CANONICAL[normalized]
    # Allow explicit extended phrase from shipping crop.
    if "final" in normalized and "ok" in normalized.replace(".", ""):
        return (_clean_text(value), "final_ok")
    return None


def _supporting_final_candidate_allowed(value, basis, item):
    value_n = _clean_text(value).casefold()
    basis_n = _clean_text(basis).casefold()
    if basis_n in DOCUMENT_TITLE_BASIS:
        return False, "document_title_used_as_result_basis"
    if value_n in UNRESOLVED_RESULT_VALUES:
        return False, "unresolved_pass_fail_choice"
    semantic_role = _clean_enum(item.get("semantic_role"), FINAL_SEMANTIC_ROLES)
    association_basis = _clean_enum(item.get("association_basis"), ASSOCIATION_BASES)
    selected_result = _clean_text(item.get("selected_result")).lower()
    if semantic_role != "final_result_field":
        return False, "supporting_document_not_explicit_final_result_field"
    if not RESULT_FIELD_RE.search(basis):
        return False, "supporting_document_lacks_known_result_field_label"
    if value_n in {"pass", "fail"}:
        if selected_result not in {"pass", "fail"} and association_basis != "selected_option":
            return False, "pass_fail_result_not_unambiguously_selected"
    return True, None


def build_final_candidates_hardened(analyses, source_data, rules):
    max_mark_length = int(rules.get("strict_schema", {}).get("maximum_event_mark_length", 96))
    complaint_values = _approved_complaint_values(source_data)
    rows, rejected, routed = [], [], []

    for analysis in analyses:
        if analysis.get("vision_status") != "ok": continue
        parsed=analysis.get("parsed_analysis",{})
        source=analysis.get("source",{})
        analysis_id=analysis.get("analysis_id")
        profile=_form_profile(source)
        items=list(parsed.get("final_result_items",[]))

        for testing_item in parsed.get("testing_items",[]):
            derived=_final_item_from_traveler_testing_item(analysis,testing_item)
            if derived:
                derived["semantic_role"]="final_disposition"
                derived["association_basis"]="selected_option"
                derived["selected_result"]=None
                items.append(derived)

        for item in items:
            value=_clean_text(item.get("value")); basis=_clean_text(item.get("basis_label")); mark=_clean_text(item.get("event_mark"))
            if not value:
                rejected.append(_rejection_record("final_result",source,item,"blank_final_value",analysis_id)); continue
            if value.casefold() in complaint_values:
                rejected.append(_rejection_record("final_result",source,item,"matches_approved_customer_complaint",analysis_id)); continue
            if CUSTOMER_PROBLEM_LABEL_RE.search(basis):
                rejected.append(_rejection_record("final_result",source,item,"customer_problem_field_cannot_be_final_result",analysis_id)); continue
            if PAGE_HEADER_RE.search(value):
                rejected.append(_rejection_record("final_result",source,item,"page_header_or_document_title_not_final_result",analysis_id)); continue

            mark_type=_exact_enum(item.get("mark_type"),ALLOWED_MARK_TYPES) if item.get("mark_type") is not None else None
            semantic_role=_clean_enum(item.get("semantic_role"),FINAL_SEMANTIC_ROLES)
            association_basis=_clean_enum(item.get("association_basis"),ASSOCIATION_BASES)
            selected_result=_clean_text(item.get("selected_result")).lower() or None
            raw_result=_clean_text(item.get("result")).lower()

            if _is_traveler_final_crop(source):
                canonical=_canonical_traveler_final(value)
                if not canonical:
                    rejected.append(_rejection_record("final_result",source,item,"traveler_crop_value_not_known_final_disposition_field",analysis_id)); continue
                canonical_value, canonical_result=canonical

                # final_test options must have an option-selection-like mark;
                # unrelated values such as "4 Hours" cannot attach to a disposition.
                if profile == "DRL_TRAVELER_FINAL_TEST":
                    if UNRELATED_NUMERIC_MARK_RE.search(mark):
                        rejected.append(_rejection_record("final_result",source,item,"traveler_disposition_mark_looks_like_unrelated_numeric_field",analysis_id)); continue
                    mt = mark_type or ("checkmark" if "check" in mark.lower() else "other")
                    if not _selection_mark_valid(mark, mt):
                        rejected.append(_rejection_record("final_result",source,item,"traveler_disposition_not_visibly_selected",analysis_id)); continue
                    association_basis = "selected_option"
                    semantic_role = "final_disposition"
                else:
                    # shipping Final O.K. can carry compact initials/date/value.
                    if not mark or len(mark) > 32 or PRINTED_INSTRUCTION_MARK_RE.search(mark):
                        rejected.append(_rejection_record("final_result",source,item,"shipping_final_ok_mark_not_compact_event_specific",analysis_id)); continue
                    semantic_role = "final_disposition"
                    association_basis = association_basis if association_basis != "unknown" else "adjacent_label"

            else:
                allowed, reason=_supporting_final_candidate_allowed(value,basis,item)
                if not allowed:
                    rejected.append(_rejection_record("final_result",source,item,reason,analysis_id)); continue
                final_result=_clean_text(item.get("result")).lower()
                if final_result not in FINAL_RESULT_ENUMS_1552:
                    rejected.append(_rejection_record("final_result",source,item,"invalid_final_result_enum",analysis_id)); continue
                canonical_value=value
                canonical_result=selected_result if selected_result in {"pass","fail"} else final_result
                valid_mark,mark_reason=_mark_validation(mark,None,max_mark_length)
                if not valid_mark:
                    rejected.append(_rejection_record("final_result",source,item,mark_reason,analysis_id)); continue

            confidence=normalize_confidence(item.get("confidence"))
            conflict_flags=_final_conflict_flags(canonical_value,source_data)
            candidate_id=stable_id("final1552",analysis_id,canonical_value,basis,mark,canonical_result)
            rows.append({
                "candidate_id":candidate_id,
                "final_number":None,
                "candidate_type":"final_result_candidate",
                "form_profile":profile,
                "value":canonical_value,
                "source_value":value,
                "basis_label":basis,
                "event_mark":mark,
                "raw_model_result":raw_result,
                "result":canonical_result,
                "semantic_role":semantic_role,
                "association_basis":association_basis,
                "selected_result":selected_result,
                "confidence":confidence,
                "source":copy.deepcopy(source),
                "source_analysis_id":analysis_id,
                "conflict_flags":conflict_flags,
                "conflict_review_required":bool(conflict_flags),
                "approval_requires_conflict_acknowledgement":bool(conflict_flags),
                "human_review":{"status":"pending","reviewer":None,"reviewed_at_utc":None,"approved_value":None,"note":None},
                "accepted_as_human_reviewed_final_result":False,
                "qdrant":{"eligible_for_future_ingestion":False,"entry_created":False,"reason":"conflict_review_required" if conflict_flags else "pending_human_review"},
            })

    unique=[]; seen=set()
    for row in rows:
        key=(row["value"].casefold(),row["event_mark"].casefold(),str(row.get("source",{}).get("source_path")),row.get("source",{}).get("page_number"))
        if key in seen: continue
        seen.add(key); unique.append(row)

    # Mutually exclusive traveler final-test options on same source are conflict flagged.
    by_analysis=collections.defaultdict(list)
    for row in unique: by_analysis[row.get("source_analysis_id")].append(row)
    exclusive={"passed all tests","no trouble found","untestable, inspection only"}
    for analysis_id,items in by_analysis.items():
        hits=[r for r in items if r.get("form_profile")=="DRL_TRAVELER_FINAL_TEST" and r["value"].casefold() in exclusive]
        if len(hits)>1:
            for r in hits:
                flag="mutually_exclusive_final_options_detected_same_source"
                if flag not in r["conflict_flags"]: r["conflict_flags"].append(flag)
                r["conflict_review_required"]=True
                r["approval_requires_conflict_acknowledgement"]=True
                r["qdrant"]["reason"]="conflict_review_required"

    for i,row in enumerate(unique,1): row["final_number"]=i
    return unique,rejected,routed


def render_review(output):
    identity=output.get("repair_identity",{})
    review=output.get("testing_final_result_review",{})
    testing=review.get("testing",{}); final=review.get("final_result",{}); hard=review.get("hardening",{})
    corrected=sum(bool(r.get("semantic_correction")) for r in testing.get("candidates",[]))
    lines=[
        "NOVA DRL TESTING PERFORMED / FINAL RESULT FUSION v{}".format(VERSION),
        "="*90,
        "Log: {}".format(identity.get("log_number")),
        "Model: {}".format(identity.get("model")),
        "Serial: {}".format(identity.get("serial_number")),
        "",
        "FORM-AWARE HARDENING SUMMARY",
        "----------------------------",
        "Testing candidates rejected before review: {}".format(hard.get("testing_rejection_count",0)),
        "Final-result candidates rejected before review: {}".format(hard.get("final_rejection_count",0)),
        "Non-test/setup/admin observations routed away: {}".format(hard.get("routed_observation_count",0)),
        "Checklist X/check marks semantically corrected from pass/fail to completed: {}".format(corrected),
        "X/check automatically means FAIL: NO",
        "Generic Pass/Fail accepted as a result: NO",
        "Traveler final-disposition field-label gating: YES",
        "Supporting final result requires explicit result field: YES",
        "Hard-coded pixel geometry invented: NO",
        "",
        "TESTING PERFORMED CANDIDATES",
        "----------------------------",
        "Candidate count: {}".format(testing.get("candidate_count",0)),
        "Approved items: {}".format(testing.get("approved_count",0)),
        "Pending items: {}".format(testing.get("pending_count",0)),
        "",
    ]
    if not testing.get("candidates"): lines += ["None",""]
    for r in testing.get("candidates",[]):
        h=r.get("human_review",{})
        lines += [
            "TEST {} [{}]".format(r.get("test_number"),r.get("candidate_id")),
            "  Form profile: {}".format(r.get("form_profile")),
            "  Step label: {}".format(r.get("step_label")),
            "  Event mark: {}".format(r.get("event_mark")),
            "  Mark type: {}".format(r.get("mark_type")),
            "  Semantic role: {}".format(r.get("semantic_role")),
            "  Association basis: {}".format(r.get("association_basis")),
            "  Raw model result: {}".format(r.get("raw_model_result")),
            "  Canonical result: {}".format(r.get("result")),
            "  Semantic correction: {}".format(r.get("semantic_correction") or "None"),
            "  Confidence: {}".format(r.get("confidence")),
            "  Source: {}{}".format(r.get("source",{}).get("source_document")," page {}".format(r.get("source",{}).get("page_number")) if r.get("source",{}).get("page_number") else ""),
            "  Human review: {}".format(h.get("status")),
            ""
        ]
    lines += [
        "FINAL RESULT CANDIDATES","-----------------------",
        "Candidate count: {}".format(final.get("candidate_count",0)),
        "Approved results: {}".format(final.get("approved_count",0)),
        "Pending results: {}".format(final.get("pending_count",0)),
        "Conflict candidates: {}".format(final.get("conflict_candidate_count",0)),
        "Final result status: {}".format(final.get("status")),""
    ]
    if not final.get("candidates"): lines += ["None",""]
    for r in final.get("candidates",[]):
        h=r.get("human_review",{})
        lines += [
            "FINAL {} [{}]".format(r.get("final_number"),r.get("candidate_id")),
            "  Form profile: {}".format(r.get("form_profile")),
            "  Candidate: {}".format(r.get("value")),
            "  Basis label: {}".format(r.get("basis_label")),
            "  Event mark: {}".format(r.get("event_mark")),
            "  Semantic role: {}".format(r.get("semantic_role")),
            "  Association basis: {}".format(r.get("association_basis")),
            "  Raw model result: {}".format(r.get("raw_model_result")),
            "  Canonical result: {}".format(r.get("result")),
            "  Conflict flags: {}".format(r.get("conflict_flags") or "None"),
            "  Conflict review required: {}".format("YES" if r.get("conflict_review_required") else "NO"),
            "  Source: {}{}".format(r.get("source",{}).get("source_document")," page {}".format(r.get("source",{}).get("page_number")) if r.get("source",{}).get("page_number") else ""),
            "  Human review: {}".format(h.get("status")),""
        ]
    lines += [
        "NON-PROMOTED / ROUTED EVIDENCE","------------------------------",
        "Printed-template-only labels: {}".format(len(review.get("printed_template_only_observations",[]))),
        "Uncertain marks: {}".format(len(review.get("uncertain_mark_observations",[]))),
        "Testing rejections: {}".format(hard.get("testing_rejection_count",0)),
        "Final-result rejections: {}".format(hard.get("final_rejection_count",0)),
        "Routed observations: {}".format(hard.get("routed_observation_count",0)),
        "","STATUS","------",
        "Approved testing items: {}".format(output.get("approved_testing_item_count",0)),
        "Approved final results: {}".format(output.get("approved_final_result_count",0)),
        "Accepted as final repair summary: NO",
        "Qdrant entries created: 0",
    ]
    return "\n".join(lines)+"\n"

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Nova DRL Testing Performed / Final Result Fusion "
            "v{}".format(VERSION)
        )
    )
    parser.add_argument(
        "source",
        help="v1.5.4 event directory or approved repair fields JSON",
    )
    parser.add_argument("--rules", default=str(default_rules_path()))
    parser.add_argument("--evidence-bundle")
    parser.add_argument("--evidence-root")
    parser.add_argument("--output-root")
    parser.add_argument("--model")
    parser.add_argument("--vision-timeout", type=int)
    parser.add_argument("--refresh-vision", action="store_true")
    parser.add_argument("--no-vision", action="store_true")
    parser.add_argument(
        "--decision",
        choices=[
            "approve-test",
            "reject-test",
            "hold-test",
            "approve-final",
            "reject-final",
            "hold-final",
        ],
    )
    parser.add_argument("--test-number", type=int)
    parser.add_argument("--final-number", type=int)
    parser.add_argument("--reviewer")
    parser.add_argument("--value")
    parser.add_argument("--note")
    parser.add_argument(
        "--acknowledge-conflict",
        action="store_true",
        help="Required to approve a final-result candidate carrying conflict flags.",
    )
    args = parser.parse_args()

    try:
        rules = load_rules(args.rules)
        source_path, source_data = locate_approved_source(args.source)
        identity = source_data.get("repair_identity", {}) or {}

        bundle_path, bundle, bundle_warning = locate_evidence_bundle(
            identity,
            explicit_path=args.evidence_bundle,
            evidence_root=args.evidence_root,
        )

        output_dir = (
            Path(args.output_root).expanduser().resolve()
            if args.output_root
            else default_output_dir(source_data)
        )
        output_dir.mkdir(parents=True, exist_ok=True)

        source_images, skipped_sources = collect_source_images(
            bundle, rules
        )

        model = args.model or rules.get(
            "vision_model", "minicpm-v:latest"
        )
        timeout = (
            args.vision_timeout
            if args.vision_timeout is not None
            else int(rules.get("vision_timeout_seconds", 180))
        )
        max_dimension = int(
            rules.get("max_vision_image_dimension", 2200)
        )

        analyses = analyze_sources(
            source_images,
            output_dir,
            model,
            timeout,
            max_dimension,
            refresh=args.refresh_vision,
            no_vision=args.no_vision,
        )

        decisions = load_decisions(output_dir)
        review = build_review(analyses, rules, decisions, source_data)

        decision_record = None
        if args.decision:
            decision_record = record_decision(
                output_dir,
                args.decision,
                args.reviewer,
                review["testing"]["candidates"],
                review["final_result"]["candidates"],
                test_number=args.test_number,
                final_number=args.final_number,
                value=args.value,
                note=args.note,
                acknowledge_conflict=args.acknowledge_conflict,
            )
            decisions = load_decisions(output_dir)
            review = build_review(analyses, rules, decisions, source_data)

        output = build_output(
            source_path,
            source_data,
            bundle_path,
            review,
            analyses,
            source_images,
            skipped_sources,
            bundle_warning,
        )
        write_outputs(output, analyses, output_dir)

    except Exception as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2

    cache_reused = sum(
        x.get("cache_status") == "reused" for x in analyses
    )
    vision_ok = sum(
        x.get("vision_status") == "ok" for x in analyses
    )

    print()
    print(
        "Nova DRL Testing Performed / Final Result Fusion v{}".format(
            VERSION
        )
    )
    print("=" * 76)
    print("Log:                         {}".format(
        output.get("repair_identity", {}).get("log_number")
    ))
    print("Evidence bundle found:       {}".format(
        "YES" if bundle_path else "NO"
    ))
    print("Source images considered:    {}".format(len(source_images)))
    print("Vision analyses OK:          {}".format(vision_ok))
    cache_invalidated = sum(
        str(x.get("cache_status", "")).startswith("invalidated")
        for x in analyses
    )
    cache_created = sum(
        x.get("cache_status") == "created" for x in analyses
    )
    vision_status_counts = dict(
        collections.Counter(
            x.get("vision_status") for x in analyses
        )
    )

    print("Cached analyses reused:      {}".format(cache_reused))
    print("Cache records created:       {}".format(cache_created))
    print("Cache records invalidated:   {}".format(cache_invalidated))
    print("Vision status counts:        {}".format(vision_status_counts))
    print("Testing candidates:          {}".format(
        review["testing"]["candidate_count"]
    ))
    print("Checklist mark corrections:  {}".format(
        sum(bool(x.get("semantic_correction")) for x in review["testing"].get("candidates", []))
    ))
    print("Testing candidates rejected: {}".format(
        review.get("hardening", {}).get(
            "testing_rejection_count", 0
        )
    ))
    print("Routed non-test observations: {}".format(
        review.get("hardening", {}).get(
            "routed_observation_count", 0
        )
    ))
    print("Testing items approved:      {}".format(
        review["testing"]["approved_count"]
    ))
    print("Final-result candidates:     {}".format(
        review["final_result"]["candidate_count"]
    ))
    print("Final candidates rejected:   {}".format(
        review.get("hardening", {}).get(
            "final_rejection_count", 0
        )
    ))
    print("Final conflict candidates:   {}".format(
        review["final_result"].get(
            "conflict_candidate_count", 0
        )
    ))
    print("Final results approved:      {}".format(
        review["final_result"]["approved_count"]
    ))
    print("Final result status:         {}".format(
        review["final_result"]["status"]
    ))
    print("Printed-template-only items: {}".format(
        len(review["printed_template_only_observations"])
    ))
    print("Uncertain marks retained:    {}".format(
        len(review["uncertain_mark_observations"])
    ))
    print("Qdrant entries created:      0")

    if decision_record:
        print(
            "Decision recorded:           {} {} {} by {}".format(
                decision_record.get("decision"),
                decision_record.get("field"),
                decision_record.get("candidate_number"),
                decision_record.get("reviewer"),
            )
        )

    if bundle_warning:
        print()
        print("WARNING: {}".format(bundle_warning))

    print()
    print("Reports: {}".format(output_dir))
    print("NO APPROVED SOURCE VALUES WERE MODIFIED.")
    print("NO DRL SOURCE FILES WERE MODIFIED.")
    print("NO QDRANT ENTRY CREATED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
