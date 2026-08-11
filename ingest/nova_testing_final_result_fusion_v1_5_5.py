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
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.5.5"

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
        / "testing_final_result_rules_v1_5_5.json"
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
        / "evidence_fusion_v1_5_5"
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
        review = build_review(analyses, rules, decisions)

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
            )
            decisions = load_decisions(output_dir)
            review = build_review(analyses, rules, decisions)

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
    print("Cached analyses reused:      {}".format(cache_reused))
    print("Testing candidates:          {}".format(
        review["testing"]["candidate_count"]
    ))
    print("Testing items approved:      {}".format(
        review["testing"]["approved_count"]
    ))
    print("Final-result candidates:     {}".format(
        review["final_result"]["candidate_count"]
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
