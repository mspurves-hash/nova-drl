#!/usr/bin/env python3
"""
Nova DRL Repair Evidence Collector v1.4.3.2
===========================================

Document-Level Family Inheritance and Known-Form Header Extraction.

This release builds on the complete v1.4.3.1/v1.4.2 evidence collector.
It corrects four issues identified in the live GB8 pilot:

1. A document family is established once and inherited by every page.
2. Known first-page form regions are cropped for event-specific fields.
3. OCR/vision candidates are validated against folder and filename anchors.
4. Template OCR quality is separated from event-annotation quality.

The collector preserves raw evidence and comparison records only.
It never accepts extracted text as a repair fact and never writes to Qdrant.
The production DRL NAS remains read-only.
"""

import argparse
import base64
import difflib
import importlib.util
import json
import re
import shutil
import sys
import urllib.request
from collections import Counter
from pathlib import Path

VERSION = "1.4.3.2"
DEFAULT_VISION_MODEL = "minicpm-v:latest"

PRIOR_PATH = Path(__file__).resolve().with_name(
    "nova_repair_evidence_collector_v1_4_3_1.py"
)
if not PRIOR_PATH.exists():
    raise RuntimeError(
        "Required prior module is missing: {}. Keep v1.4.3.1 and v1.4.2 "
        "in the ingest directory.".format(PRIOR_PATH)
    )

spec = importlib.util.spec_from_file_location("nova_v1431_prior", str(PRIOR_PATH))
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
base = prior.base
base.VERSION = VERSION

RUNTIME = {
    "enable_header_vision": True,
    "vision_model": DEFAULT_VISION_MODEL,
    "vision_timeout": 360,
}

FAMILY_BY_ROLE = {
    "robot_checklist": "DRL_INTERNAL_CHECKLIST",
    "robot_test_report": "DRL_ACCEPTANCE_TEST_REPORT",
}

FAMILY_BY_FILENAME = [
    (re.compile(r"robot\s+checklist", re.I), "DRL_INTERNAL_CHECKLIST"),
    (
        re.compile(r"(?:robot\s+)?test\s+report|acceptance\s+test\s+report", re.I),
        "DRL_ACCEPTANCE_TEST_REPORT",
    ),
]

# Normalized boxes: left, top, right, bottom.
# These profiles are deliberately limited to the verified DRL GB8 forms.
KNOWN_FORM_HEADER_PROFILES = {
    "DRL_INTERNAL_CHECKLIST": {
        "page_number": 1,
        "box": (0.10, 0.105, 0.90, 0.31),
        "fields": [
            "date",
            "customer",
            "serial_number",
            "rma_number",
            "log_number",
            "repair_technicians",
            "customer_complaint",
        ],
        "prompt": """You are reading the event-specific handwritten header of a Direct Repair Laboratories internal robot checklist.

Return ONLY one JSON object with exactly these keys:
- date
- customer
- serial_number
- rma_number
- log_number
- repair_technicians
- customer_complaint

Rules:
1. Copy only values visibly written in the form.
2. Do not summarize, correct, or infer.
3. Use null for unreadable or blank values.
4. Preserve numbers and punctuation exactly as visible.
5. Do not include printed field labels in the values.
6. The customer complaint is the handwritten value beside "Customer FA (summary)".
7. Return JSON only, with no Markdown or explanation.
""",
    },
    "DRL_ACCEPTANCE_TEST_REPORT": {
        "page_number": 1,
        "box": (0.08, 0.61, 0.93, 0.95),
        "fields": [
            "customer",
            "product_type",
            "serial_number",
            "controller_serial_number",
            "rma_number",
            "log_number",
            "repair_type",
            "customer_complaint",
        ],
        "prompt": """You are reading the event-specific handwritten fields on page 1 of a Direct Repair Laboratories acceptance test report.

Return ONLY one JSON object with exactly these keys:
- customer
- product_type
- serial_number
- controller_serial_number
- rma_number
- log_number
- repair_type
- customer_complaint

Rules:
1. Copy only values visibly written or checked in the form.
2. Do not summarize, correct, or infer.
3. Use null for unreadable or blank values.
4. Preserve numbers and punctuation exactly as visible.
5. Do not include printed field labels in the values.
6. For repair_type, return the visibly checked option only: warranty, non-warranty, inspection, repair, refurbishment, other, or null.
7. The customer complaint is the handwritten content in the Customer Problem/Symptom Description box.
8. Return JSON only, with no Markdown or explanation.
""",
    },
}

INVALID_LABEL_VALUES = {
    "number",
    "serialnumber",
    "rmanumber",
    "lognumber",
    "travelernumber",
    "serial",
    "rma",
    "log",
    "traveler",
    "customer",
    "date",
}

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "for",
    "from", "in", "is", "it", "of", "on", "or", "the", "to", "was",
    "were", "with", "needs", "need", "fixed", "fix",
}


def normalize_compact(value):
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def expected_context_from_path(path):
    path = Path(path)
    serial = None
    log_number = None

    match = re.search(r"\bSN\s+([^\s]+)", path.parent.name, re.IGNORECASE)
    if match:
        serial = match.group(1)

    match = re.match(r"^(\d{9})\b", path.name)
    if match:
        log_number = match.group(1)

    return {
        "serial_number": serial,
        "log_number": log_number,
        "source": "serial_folder_and_filename",
    }


def infer_document_family(path, document_role, first_page_text):
    if document_role in FAMILY_BY_ROLE:
        return {
            "document_family": FAMILY_BY_ROLE[document_role],
            "source": "document_role",
            "confidence": "confirmed",
        }

    name = Path(path).name
    for pattern, family in FAMILY_BY_FILENAME:
        if pattern.search(name):
            return {
                "document_family": family,
                "source": "filename",
                "confidence": "high",
            }

    page = prior.classify_scanned_page(first_page_text, page_number=1)
    family = page.get("document_family", "UNKNOWN_SCANNED_DOCUMENT")
    return {
        "document_family": family,
        "source": "first_page_ocr" if family != "UNKNOWN_SCANNED_DOCUMENT" else "unresolved",
        "confidence": "medium" if family != "UNKNOWN_SCANNED_DOCUMENT" else "low",
    }


def classify_inherited_page(text, page_number, family):
    lower = str(text or "").lower()

    if family == "DRL_INTERNAL_CHECKLIST":
        if page_number == 1:
            page_type = "checklist_event_header_and_initial_checkout"
        elif "final checkout" in lower or "final check" in lower:
            page_type = "checklist_final_checkout"
        elif "functional test" in lower or "functional testing" in lower:
            page_type = "checklist_functional_test"
        elif "visual inspection" in lower:
            page_type = "checklist_visual_inspection"
        elif "completion" in lower or "final o.k" in lower:
            page_type = "checklist_completion"
        else:
            page_type = "checklist_continuation"
        static_template = True

    elif family == "DRL_ACCEPTANCE_TEST_REPORT":
        if page_number == 1:
            page_type = "test_report_front_matter_and_event_header"
        elif "visual inspection" in lower:
            page_type = "test_report_visual_inspection"
        elif "functional test" in lower:
            page_type = "test_report_functional_test"
        elif "completion" in lower or "signature" in lower:
            page_type = "test_report_completion"
        elif "appendix" in lower:
            page_type = "test_report_appendix"
        else:
            page_type = "test_report_continuation"
        static_template = True

    else:
        fallback = prior.classify_scanned_page(text, page_number=page_number)
        page_type = fallback.get("page_type", "unknown_page")
        static_template = fallback.get("static_template_content_present", "unknown")

    return {
        "document_family": family,
        "page_type": page_type,
        "page_number": page_number,
        "family_inherited_from_document": family != "UNKNOWN_SCANNED_DOCUMENT",
        "static_template_content_present": static_template,
        "event_annotations_possible": True,
        "accepted_as_repair_fact": False,
    }


def require_pillow():
    try:
        from PIL import Image, ImageOps, ImageEnhance
        return Image, ImageOps, ImageEnhance
    except Exception as exc:
        raise RuntimeError(
            "Pillow is required for known-form header crops: {}".format(exc)
        )


def fractional_box_to_pixels(box, width, height):
    left = max(0, min(width, int(round(box[0] * width))))
    top = max(0, min(height, int(round(box[1] * height))))
    right = max(left + 1, min(width, int(round(box[2] * width))))
    bottom = max(top + 1, min(height, int(round(box[3] * height))))
    return (left, top, right, bottom)


def prepare_header_crop(page_image, family, header_dir):
    profile = KNOWN_FORM_HEADER_PROFILES[family]
    Image, ImageOps, ImageEnhance = require_pillow()
    header_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(page_image) as image:
        image.load()
        pixels = fractional_box_to_pixels(profile["box"], image.width, image.height)
        crop = image.crop(pixels)
        raw_path = header_dir / "header_crop.png"
        crop.save(raw_path)

        enhanced = ImageOps.autocontrast(crop.convert("L"))
        enhanced = ImageEnhance.Contrast(enhanced).enhance(1.25)
        if enhanced.width < 1800:
            scale = 1800 / max(1, enhanced.width)
            enhanced = enhanced.resize(
                (int(enhanced.width * scale), int(enhanced.height * scale)),
                Image.Resampling.LANCZOS,
            )
        enhanced_path = header_dir / "header_crop_enhanced.png"
        enhanced.save(enhanced_path)

    return {
        "fractional_box": profile["box"],
        "pixel_box": pixels,
        "raw_crop_path": str(raw_path),
        "enhanced_crop_path": str(enhanced_path),
    }


def run_header_tesseract(enhanced_crop_path, header_dir, dpi):
    passes = []
    for psm in (6, 11):
        code, text, stderr = base.run_command(
            [
                "tesseract",
                str(enhanced_crop_path),
                "stdout",
                "-l", "eng",
                "--dpi", str(dpi),
                "--psm", str(psm),
            ],
            timeout=240,
        )
        metrics = prior.semantic_ocr_metrics(text if code == 0 else "")
        text_path = header_dir / "tesseract_psm{}.txt".format(psm)
        text_path.write_text(text if code == 0 else "", encoding="utf-8")
        passes.append({
            "psm": psm,
            "status": "ok" if code == 0 else "error",
            "text": text if code == 0 else "",
            "text_path": str(text_path),
            "metrics": metrics,
            "warning": stderr.strip() or None,
        })
    return max(passes, key=lambda row: row["metrics"]["semantic_score"]), passes


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
    names = [item.get("name", "") for item in data.get("models", [])]
    if model in names:
        return True
    if ":" not in model:
        return any(name == model or name.startswith(model + ":") for name in names)
    return False


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
            "warning": str(exc),
        }


def parse_json_object(text):
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def clean_candidate_value(value):
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip(" \t\r\n|:[]")
    if not value or value.lower() in {"null", "none", "n/a", "na", "unknown"}:
        return None
    return value


def validate_field_candidate(field, value, expected_context, source):
    raw_value = clean_candidate_value(value)
    record = {
        "field": field,
        "raw_value": raw_value,
        "source": source,
        "format_valid": False,
        "anchor_status": "not_applicable",
        "expected_anchor": None,
        "rejected": False,
        "rejection_reason": None,
        "human_review_required": True,
        "eligible_for_evidence_comparison": False,
        "accepted_as_repair_fact": False,
    }

    if raw_value is None:
        record.update({
            "rejected": True,
            "rejection_reason": "blank_or_unreadable",
        })
        return record

    compact = normalize_compact(raw_value)
    if compact.lower() in INVALID_LABEL_VALUES:
        record.update({
            "rejected": True,
            "rejection_reason": "field_label_not_value",
        })
        return record

    if field == "serial_number":
        record["format_valid"] = bool(re.fullmatch(r"[A-Za-z0-9\-]{6,24}", raw_value))
        expected = expected_context.get("serial_number")
        record["expected_anchor"] = expected
        if expected:
            record["anchor_status"] = (
                "exact_match"
                if normalize_compact(raw_value) == normalize_compact(expected)
                else "mismatch"
            )
        else:
            record["anchor_status"] = "not_available"

    elif field == "log_number":
        digits = re.sub(r"\D", "", raw_value)
        record["format_valid"] = len(digits) == 9
        expected = expected_context.get("log_number")
        record["expected_anchor"] = expected
        if expected:
            record["anchor_status"] = (
                "exact_match" if digits == re.sub(r"\D", "", expected) else "mismatch"
            )
        else:
            record["anchor_status"] = "not_available"

    elif field == "rma_number":
        digits = re.sub(r"\D", "", raw_value)
        record["format_valid"] = 3 <= len(digits) <= 10

    elif field == "repair_type":
        normalized = raw_value.lower().replace("_", "-").strip()
        allowed = {
            "warranty", "non-warranty", "inspection", "repair",
            "refurbishment", "other",
        }
        record["format_valid"] = normalized in allowed

    elif field == "customer_complaint":
        words = re.findall(r"[A-Za-z0-9]+", raw_value)
        record["format_valid"] = len(raw_value) >= 4 and len(words) >= 2

    elif field in {
        "date", "customer", "repair_technicians", "product_type",
        "controller_serial_number",
    }:
        record["format_valid"] = len(raw_value) >= 2

    else:
        record["format_valid"] = len(raw_value) >= 1

    if not record["format_valid"]:
        record.update({
            "rejected": True,
            "rejection_reason": "invalid_field_format",
        })
        return record

    if record["anchor_status"] == "mismatch":
        record["rejection_reason"] = "does_not_match_folder_or_filename_anchor"
        return record

    record["eligible_for_evidence_comparison"] = True
    return record


def extract_tesseract_known_fields(text, family, expected_context):
    """Conservative fallback. Exact anchors are preferred over loose regexes."""
    text = str(text or "")
    compact = normalize_compact(text)
    records = []

    expected_serial = expected_context.get("serial_number")
    if expected_serial and normalize_compact(expected_serial) in compact:
        records.append(validate_field_candidate(
            "serial_number", expected_serial, expected_context, "header_tesseract_anchor_match"
        ))

    expected_log = expected_context.get("log_number")
    if expected_log and normalize_compact(expected_log) in compact:
        records.append(validate_field_candidate(
            "log_number", expected_log, expected_context, "header_tesseract_anchor_match"
        ))

    rma_match = re.search(
        r"RMA\s*(?:Number|#)?\s*[:#]?\s*([0-9][0-9\s/\-]{2,12})",
        text,
        re.IGNORECASE,
    )
    if rma_match:
        records.append(validate_field_candidate(
            "rma_number", rma_match.group(1), expected_context, "header_tesseract"
        ))

    complaint_patterns = (
        r"Customer\s+FA\s*(?:\(summary\))?\s*[:\-]?\s*([^\n]{4,140})",
        r"Customer\s+Problem(?:/Symptom)?(?:\s+Description)?\s*[:\-]?\s*([^\n]{4,180})",
    )
    for pattern in complaint_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            records.append(validate_field_candidate(
                "customer_complaint", match.group(1), expected_context, "header_tesseract"
            ))
            break

    # Remove duplicate exact-anchor records.
    unique = []
    seen = set()
    for record in records:
        key = (record["field"], record.get("raw_value"), record["source"])
        if key not in seen:
            seen.add(key)
            unique.append(record)
    return unique


def select_best_provisional_fields(candidates):
    grouped = {}
    for record in candidates:
        if record.get("rejected") or not record.get("format_valid"):
            continue
        grouped.setdefault(record["field"], []).append(record)

    result = {}
    for field, rows in grouped.items():
        rows = sorted(
            rows,
            key=lambda row: (
                row.get("anchor_status") == "exact_match",
                row.get("source", "").startswith("header_vision"),
                len(row.get("raw_value") or ""),
            ),
            reverse=True,
        )
        selected = dict(rows[0])
        selected["selection_status"] = "provisional_best_candidate"
        selected["accepted_as_repair_fact"] = False
        result[field] = selected
    return result


def annotation_quality(candidate_records, vision_status):
    usable = [
        row for row in candidate_records
        if row.get("eligible_for_evidence_comparison") and not row.get("rejected")
    ]
    mismatches = [row for row in candidate_records if row.get("anchor_status") == "mismatch"]
    anchor_matches = [row for row in usable if row.get("anchor_status") == "exact_match"]
    complaints = [row for row in usable if row.get("field") == "customer_complaint"]

    if mismatches:
        return "low"
    if vision_status == "ok" and anchor_matches and complaints:
        return "good"
    if usable:
        return "usable"
    return "low"


def analyze_known_form_header(
    page_image,
    page_number,
    family,
    expected_context,
    header_root,
    dpi,
):
    profile = KNOWN_FORM_HEADER_PROFILES.get(family)
    if not profile or page_number != profile["page_number"]:
        return None

    page_dir = header_root / "page_{:03d}".format(page_number)
    crop_info = prepare_header_crop(page_image, family, page_dir)
    best_tesseract, tesseract_passes = run_header_tesseract(
        crop_info["enhanced_crop_path"], page_dir, dpi
    )

    candidates = extract_tesseract_known_fields(
        best_tesseract["text"], family, expected_context
    )

    vision_record = {
        "status": "not_requested",
        "model": RUNTIME["vision_model"],
        "raw_response": "",
        "parsed_json": None,
        "warning": None,
    }

    if RUNTIME["enable_header_vision"]:
        if ollama_model_exists(RUNTIME["vision_model"]):
            result = call_ollama_vision(
                RUNTIME["vision_model"],
                profile["prompt"],
                crop_info["enhanced_crop_path"],
                RUNTIME["vision_timeout"],
            )
            parsed = parse_json_object(result.get("response"))
            vision_record.update({
                "status": "ok" if result["status"] == "ok" and parsed else (
                    "response_not_json" if result["status"] == "ok" else "error"
                ),
                "raw_response": result.get("response", ""),
                "parsed_json": parsed,
                "warning": result.get("warning"),
                "done_reason": result.get("done_reason"),
                "eval_count": result.get("eval_count"),
            })
            if parsed:
                for field in profile["fields"]:
                    candidates.append(validate_field_candidate(
                        field,
                        parsed.get(field),
                        expected_context,
                        "header_vision_{}".format(RUNTIME["vision_model"]),
                    ))
        else:
            vision_record.update({
                "status": "model_unavailable",
                "warning": "Ollama model not found or Ollama API unavailable.",
            })

    best_fields = select_best_provisional_fields(candidates)
    event_quality = annotation_quality(candidates, vision_record["status"])
    anchor_mismatches = [
        row for row in candidates if row.get("anchor_status") == "mismatch"
    ]

    analysis = {
        "profile_version": VERSION,
        "document_family": family,
        "page_number": page_number,
        "crop": crop_info,
        "expected_context": expected_context,
        "tesseract": {
            "selected_psm": best_tesseract["psm"],
            "selected_text": best_tesseract["text"],
            "selected_metrics": best_tesseract["metrics"],
            "passes": [
                {key: value for key, value in row.items() if key != "text"}
                for row in tesseract_passes
            ],
        },
        "vision": vision_record,
        "field_candidates": candidates,
        "best_provisional_fields": best_fields,
        "template_ocr_quality": best_tesseract["metrics"]["quality"],
        "event_annotation_quality": event_quality,
        "handwriting_annotation_quality": event_quality,
        "anchor_mismatch_count": len(anchor_mismatches),
        "eligible_for_evidence_comparison": (
            event_quality in {"good", "usable"}
            and bool(best_fields)
            and not anchor_mismatches
        ),
        "accepted_as_repair_fact": False,
        "human_review_required": True,
    }

    (page_dir / "header_analysis.json").write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (page_dir / "vision_raw.txt").write_text(
        vision_record.get("raw_response", ""), encoding="utf-8"
    )
    return analysis


def normalize_complaint(value):
    value = str(value or "").lower()
    value = value.replace("¥", "y")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def complaint_tokens(value):
    return {
        token for token in normalize_complaint(value).split()
        if len(token) >= 2 and token not in STOPWORDS
    }


def compare_complaint_records(records):
    comparisons = []
    max_similarity = 0.0
    shared_all = None

    for i in range(len(records)):
        tokens = complaint_tokens(records[i]["raw_value"])
        shared_all = tokens if shared_all is None else shared_all & tokens
        for j in range(i + 1, len(records)):
            left = normalize_complaint(records[i]["raw_value"])
            right = normalize_complaint(records[j]["raw_value"])
            similarity = difflib.SequenceMatcher(None, left, right).ratio()
            max_similarity = max(max_similarity, similarity)
            comparisons.append({
                "left_source": records[i]["source_document"],
                "right_source": records[j]["source_document"],
                "similarity": round(similarity, 4),
                "shared_tokens": sorted(
                    complaint_tokens(records[i]["raw_value"])
                    & complaint_tokens(records[j]["raw_value"])
                ),
            })

    if len(records) < 2:
        status = "insufficient_sources"
    elif max_similarity >= 0.82:
        status = "strong_cross_document_agreement"
    elif max_similarity >= 0.42 or any(row["shared_tokens"] for row in comparisons):
        status = "possible_cross_document_agreement"
    else:
        status = "agreement_not_established"

    return {
        "status": status,
        "source_count": len(records),
        "max_pairwise_similarity": round(max_similarity, 4),
        "shared_tokens_across_all_sources": sorted(shared_all or []),
        "raw_candidates": records,
        "pairwise_comparisons": comparisons,
        "canonical_complaint": None,
        "human_review_required": True,
        "accepted_as_repair_fact": False,
    }


def extract_scanned_pdf_text_v1432(
    path,
    artifact_dir,
    dpi=300,
    max_pages=50,
    document_role="document",
):
    path = Path(path)
    artifact_dir = Path(artifact_dir)
    deps = base.scanned_pdf_dependencies()
    missing = [name for name in ("pdftoppm", "tesseract") if not deps.get(name)]
    if missing:
        return {
            "status": "dependency_missing",
            "method": "scanned_pdf_ocr",
            "text": "",
            "warning": "Missing required command(s): {}.".format(", ".join(missing)),
            "page_count": None,
            "pages_processed": 0,
            "page_records": [],
            "artifact_dir": None,
            "ocr_review_required": True,
        }

    total_pages = base.pdf_page_count(path)
    page_limit = max_pages if total_pages is None else min(total_pages, max_pages)

    ocr_dir = artifact_dir / "scanned_pdf_ocr"
    if ocr_dir.exists():
        shutil.rmtree(ocr_dir)
    pages_dir = ocr_dir / "pages"
    text_dir = ocr_dir / "page_text"
    passes_dir = ocr_dir / "ocr_passes"
    header_root = ocr_dir / "known_form_headers"
    pages_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    passes_dir.mkdir(parents=True, exist_ok=True)

    prefix = pages_dir / "page"
    code, _, err = base.run_command(
        [
            "pdftoppm", "-f", "1", "-l", str(page_limit),
            "-r", str(dpi), "-gray", "-png", str(path), str(prefix),
        ],
        timeout=max(300, page_limit * 90),
    )
    if code != 0:
        return {
            "status": "error",
            "method": "scanned_pdf_ocr",
            "text": "",
            "warning": err.strip() or "pdftoppm failed.",
            "page_count": total_pages,
            "pages_processed": 0,
            "page_records": [],
            "artifact_dir": str(ocr_dir),
            "ocr_review_required": True,
        }

    page_images = sorted(
        pages_dir.glob("page-*.png"), key=base.page_number_from_rendered_name
    )
    preliminary = []

    # OCR every page first; document family is established once after page 1.
    for page_image in page_images:
        page_number = base.page_number_from_rendered_name(page_image)
        passes = []
        for psm in (3, 4, 6, 11):
            code, text, stderr = base.run_command(
                [
                    "tesseract", str(page_image), "stdout", "-l", "eng",
                    "--dpi", str(dpi), "--psm", str(psm),
                ],
                timeout=240,
            )
            metrics = prior.semantic_ocr_metrics(text if code == 0 else "")
            pass_path = passes_dir / "page_{:03d}_psm{}.txt".format(
                page_number, psm
            )
            pass_path.write_text(text if code == 0 else "", encoding="utf-8")
            passes.append({
                "psm": psm,
                "status": "ok" if code == 0 else "error",
                "semantic_score": metrics["semantic_score"],
                "quality": metrics["quality"],
                "metrics": metrics,
                "text_path": str(pass_path),
                "warning": stderr.strip() or None,
                "text": text if code == 0 else "",
            })
        best = max(passes, key=lambda row: row["semantic_score"])
        preliminary.append({
            "page_image": page_image,
            "page_number": page_number,
            "passes": passes,
            "best": best,
        })

    first_page_text = preliminary[0]["best"]["text"] if preliminary else ""
    family_record = infer_document_family(path, document_role, first_page_text)
    document_family = family_record["document_family"]
    expected_context = expected_context_from_path(path)

    page_records = []
    combined_parts = []
    quality_counts = Counter()
    family_counts = Counter()
    template_quality_counts = Counter()
    annotation_quality_counts = Counter()
    all_candidates = []

    for item in preliminary:
        page_image = item["page_image"]
        page_number = item["page_number"]
        best = item["best"]
        passes = item["passes"]

        classification = classify_inherited_page(
            best["text"], page_number, document_family
        )
        classification["document_family_source"] = family_record["source"]
        classification["document_family_confidence"] = family_record["confidence"]

        header_analysis = analyze_known_form_header(
            page_image,
            page_number,
            document_family,
            expected_context,
            header_root,
            dpi,
        )
        candidates = (
            header_analysis.get("field_candidates", [])
            if header_analysis else []
        )
        all_candidates.extend([
            {"page_number": page_number, **candidate}
            for candidate in candidates
        ])

        template_quality = best["quality"]
        event_quality = (
            header_analysis.get("event_annotation_quality")
            if header_analysis else "not_assessed"
        )
        handwriting_quality = (
            header_analysis.get("handwriting_annotation_quality")
            if header_analysis else "not_assessed"
        )
        eligible = bool(
            header_analysis
            and header_analysis.get("eligible_for_evidence_comparison")
        )

        selected_path = text_dir / "page_{:03d}.txt".format(page_number)
        selected_path.write_text(best["text"], encoding="utf-8")
        combined_parts.append(
            "\n===== PAGE {} | {} | TEMPLATE={} | EVENT={} =====\n{}".format(
                page_number,
                document_family,
                template_quality,
                event_quality,
                best["text"].rstrip(),
            )
        )

        quality_counts[best["quality"]] += 1
        family_counts[document_family] += 1
        template_quality_counts[template_quality] += 1
        annotation_quality_counts[event_quality] += 1

        page_records.append({
            "page_number": page_number,
            "image_path": str(page_image),
            "selected_psm": best["psm"],
            "selected_semantic_score": best["semantic_score"],
            "selected_quality": best["quality"],
            "selected_metrics": best["metrics"],
            "selected_text_path": str(selected_path),
            "selected_char_count": len(best["text"]),
            "status": "ok" if best["text"].strip() else "empty",
            "classification": classification,
            "template_ocr_quality": template_quality,
            "event_annotation_quality": event_quality,
            "handwriting_annotation_quality": handwriting_quality,
            "known_form_header_analysis": header_analysis,
            "event_field_candidates": candidates,
            "eligible_for_evidence_comparison": eligible,
            "accepted_as_repair_fact": False,
            "passes": [
                {key: value for key, value in row.items() if key != "text"}
                for row in passes
            ],
        })

    combined_text = "\n".join(combined_parts).strip()
    combined_path = ocr_dir / "combined_ocr.txt"
    combined_path.write_text(
        combined_text + ("\n" if combined_text else ""), encoding="utf-8"
    )

    usable_template_pages = sum(
        template_quality_counts.get(key, 0) for key in ("good", "usable")
    )
    event_usable_pages = sum(
        annotation_quality_counts.get(key, 0) for key in ("good", "usable")
    )
    truncated = total_pages is not None and total_pages > page_limit

    if not combined_text:
        status = "empty_ocr"
        overall_quality = "empty"
    elif usable_template_pages:
        status = "ok_template_text_event_review_required"
        overall_quality = "usable_template_text"
    else:
        status = "review_required_low_quality"
        overall_quality = "low"

    semantics = base.document_semantics_for_role(document_role)
    manifest = {
        "collector_version": VERSION,
        "source_pdf": str(path),
        "document_role": document_role,
        "document_semantics": semantics,
        "document_family": document_family,
        "document_family_source": family_record["source"],
        "document_family_confidence": family_record["confidence"],
        "expected_context": expected_context,
        "dpi": dpi,
        "total_pages": total_pages,
        "pages_processed": len(page_records),
        "page_limit": max_pages,
        "truncated_by_page_limit": truncated,
        "combined_text_path": str(combined_path),
        "overall_ocr_quality": overall_quality,
        "template_quality_counts": dict(sorted(template_quality_counts.items())),
        "event_annotation_quality_counts": dict(
            sorted(annotation_quality_counts.items())
        ),
        "document_family_counts": dict(sorted(family_counts.items())),
        "event_field_candidates": all_candidates,
        "event_annotation_usable_page_count": event_usable_pages,
        "pages": page_records,
        "interpretation_status": "template_text_and_provisional_event_annotations_only",
        "accepted_as_repair_fact": False,
        "qdrant_eligible": False,
    }
    manifest_path = ocr_dir / "scanned_pdf_ocr_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    warning_parts = [
        "Template text and event annotations are graded separately.",
        "All known-form values remain provisional and human-review-required.",
        "Printed template instructions are not proof that work was completed.",
    ]
    if not event_usable_pages:
        warning_parts.append(
            "No page produced event annotations eligible for evidence comparison."
        )
    if truncated:
        warning_parts.append(
            "Only the first {} of {} pages were processed.".format(
                page_limit, total_pages
            )
        )

    return {
        "status": status,
        "method": "scanned_pdf_ocr",
        "text": combined_text,
        "warning": " ".join(warning_parts),
        "page_count": total_pages,
        "pages_processed": len(page_records),
        "page_records": page_records,
        "artifact_dir": str(ocr_dir),
        "manifest_path": str(manifest_path),
        "combined_ocr_path": str(combined_path),
        "ocr_review_required": True,
        "truncated_by_page_limit": truncated,
        "overall_ocr_quality": overall_quality,
        "document_family": document_family,
        "document_family_source": family_record["source"],
        "template_quality_counts": dict(sorted(template_quality_counts.items())),
        "event_annotation_quality_counts": dict(
            sorted(annotation_quality_counts.items())
        ),
        "event_field_candidates": all_candidates,
        "event_annotation_usable_page_count": event_usable_pages,
    }


_original_render_event_text = prior._original_render_event_text


def render_event_text_v1432(serial_meta, event):
    original = _original_render_event_text(serial_meta, event)
    lines = [
        "",
        "SCANNED DOCUMENT FAMILY INHERITANCE AND KNOWN-FORM EVENT FIELDS",
    ]
    found = False

    for record in event.get("evidence_files", []):
        extraction = record.get("extraction", {})
        if extraction.get("method") != "scanned_pdf_ocr":
            continue
        found = True
        lines.append("")
        lines.append("[{}] {}".format(
            record.get("role"), record.get("relative_path")
        ))
        lines.append("  Document family: {}".format(
            extraction.get("document_family", "UNKNOWN_SCANNED_DOCUMENT")
        ))
        lines.append("  Family source: {}".format(
            extraction.get("document_family_source", "unknown")
        ))
        lines.append("  Template quality counts: {}".format(
            extraction.get("template_quality_counts", {})
        ))
        lines.append("  Event annotation quality counts: {}".format(
            extraction.get("event_annotation_quality_counts", {})
        ))

        for page in extraction.get("page_records", []):
            classification = page.get("classification", {})
            lines.append(
                "  Page {}: family={} type={}".format(
                    page.get("page_number"),
                    classification.get("document_family", "UNKNOWN"),
                    classification.get("page_type", "unknown"),
                )
            )
            lines.append(
                "    Template OCR quality: {}".format(
                    page.get("template_ocr_quality", "unknown")
                )
            )
            lines.append(
                "    Event annotation quality: {}".format(
                    page.get("event_annotation_quality", "not_assessed")
                )
            )
            lines.append(
                "    Handwriting annotation quality: {}".format(
                    page.get("handwriting_annotation_quality", "not_assessed")
                )
            )
            header = page.get("known_form_header_analysis")
            if header:
                lines.append("    Known-form header profile: YES")
                lines.append("    Expected serial anchor: {}".format(
                    header.get("expected_context", {}).get("serial_number")
                ))
                lines.append("    Expected log anchor: {}".format(
                    header.get("expected_context", {}).get("log_number")
                ))
                lines.append("    Vision status: {}".format(
                    header.get("vision", {}).get("status")
                ))
                lines.append("    Provisional best fields:")
                best_fields = header.get("best_provisional_fields", {})
                if best_fields:
                    for field, candidate in sorted(best_fields.items()):
                        lines.append(
                            "      - {} = {} | source={} | anchor={} "
                            "[PROVISIONAL; REVIEW REQUIRED]".format(
                                field,
                                candidate.get("raw_value"),
                                candidate.get("source"),
                                candidate.get("anchor_status"),
                            )
                        )
                else:
                    lines.append("      None")
                rejected = [
                    row for row in header.get("field_candidates", [])
                    if row.get("rejected") or row.get("anchor_status") == "mismatch"
                ]
                if rejected:
                    lines.append("    Rejected/mismatched candidates:")
                    for row in rejected:
                        lines.append(
                            "      - {} = {} | reason={} | anchor={}".format(
                                row.get("field"), row.get("raw_value"),
                                row.get("rejection_reason"),
                                row.get("anchor_status"),
                            )
                        )
            else:
                lines.append("    Known-form header profile: NO")
            lines.append(
                "    Eligible for evidence comparison: {}".format(
                    "YES" if page.get("eligible_for_evidence_comparison") else "NO"
                )
            )
            lines.append("    Accepted as repair fact: NO")

    if not found:
        lines.append("  None")

    comparison = event.get("cross_document_complaint_comparison")
    lines.extend(["", "CROSS-DOCUMENT COMPLAINT COMPARISON"])
    if comparison:
        lines.append("  Status: {}".format(comparison.get("status")))
        lines.append("  Source count: {}".format(comparison.get("source_count")))
        lines.append("  Maximum pairwise similarity: {}".format(
            comparison.get("max_pairwise_similarity")
        ))
        lines.append("  Canonical complaint: NOT CREATED")
        for item in comparison.get("raw_candidates", []):
            lines.append(
                "  - {} page {}: {}".format(
                    item.get("source_document"),
                    item.get("page_number"),
                    item.get("raw_value"),
                )
            )
        lines.append("  Human review required: YES")
        lines.append("  Accepted as repair fact: NO")
    else:
        lines.append("  None")

    marker = "\nSYSTEM METADATA (ACCOUNTED, EXCLUDED FROM REPAIR EVIDENCE)"
    insert = "\n".join(lines) + "\n"
    if marker in original:
        return original.replace(marker, insert + marker, 1)
    return original + insert


# Patch the complete collector.
_original_make_original_record = base.make_original_record


def make_original_record_v1432(*args, **kwargs):
    """Preserve v1.4.3.2 manifest fields that v1.4.2 did not copy."""
    record = _original_make_original_record(*args, **kwargs)
    extraction = record.get("extraction", {})
    manifest_path = extraction.get("manifest_path")
    if extraction.get("method") == "scanned_pdf_ocr" and manifest_path:
        try:
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            for key in (
                "document_family",
                "document_family_source",
                "document_family_confidence",
                "expected_context",
                "overall_ocr_quality",
                "template_quality_counts",
                "event_annotation_quality_counts",
                "document_family_counts",
                "event_field_candidates",
                "event_annotation_usable_page_count",
            ):
                extraction[key] = manifest.get(key)
        except Exception as exc:
            extraction["manifest_metadata_warning"] = str(exc)
    return record


base.extract_scanned_pdf_text = extract_scanned_pdf_text_v1432
base.write_extracted_text = prior.write_extracted_text_v1431
base.render_event_text = render_event_text_v1432
base.make_original_record = make_original_record_v1432


def add_document_analysis(summary):
    template_quality = Counter()
    annotation_quality = Counter()
    families = Counter()
    provisional_fields = 0
    comparison_eligible = 0
    header_vision_pages = 0
    anchor_matches = 0
    anchor_mismatches = 0
    comparison_events = 0

    for event in summary.get("repair_events", []):
        complaint_records = []
        for evidence in event.get("evidence_files", []):
            extraction = evidence.get("extraction", {})
            if extraction.get("method") != "scanned_pdf_ocr":
                continue
            for page in extraction.get("page_records", []):
                template_quality[page.get("template_ocr_quality", "unknown")] += 1
                annotation_quality[page.get("event_annotation_quality", "not_assessed")] += 1
                family = page.get("classification", {}).get(
                    "document_family", "UNKNOWN_SCANNED_DOCUMENT"
                )
                families[family] += 1
                if page.get("eligible_for_evidence_comparison"):
                    comparison_eligible += 1

                header = page.get("known_form_header_analysis")
                if not header:
                    continue
                if header.get("vision", {}).get("status") == "ok":
                    header_vision_pages += 1
                for candidate in header.get("field_candidates", []):
                    if candidate.get("anchor_status") == "exact_match":
                        anchor_matches += 1
                    elif candidate.get("anchor_status") == "mismatch":
                        anchor_mismatches += 1
                best_fields = header.get("best_provisional_fields", {})
                provisional_fields += len(best_fields)
                complaint = best_fields.get("customer_complaint")
                if complaint:
                    complaint_records.append({
                        "raw_value": complaint.get("raw_value"),
                        "source_document": evidence.get("relative_path"),
                        "document_role": evidence.get("role"),
                        "page_number": page.get("page_number"),
                        "source_method": complaint.get("source"),
                        "accepted_as_repair_fact": False,
                    })

        if complaint_records:
            event["cross_document_complaint_comparison"] = compare_complaint_records(
                complaint_records
            )
            if len(complaint_records) >= 2:
                comparison_events += 1
        else:
            event["cross_document_complaint_comparison"] = None

    summary["scanned_template_quality_counts"] = dict(sorted(template_quality.items()))
    summary["scanned_event_annotation_quality_counts"] = dict(
        sorted(annotation_quality.items())
    )
    summary["scanned_document_family_page_counts"] = dict(sorted(families.items()))
    summary["provisional_best_event_field_count"] = provisional_fields
    summary["evidence_comparison_eligible_page_count"] = comparison_eligible
    summary["header_vision_success_page_count"] = header_vision_pages
    summary["anchor_exact_match_count"] = anchor_matches
    summary["anchor_mismatch_count"] = anchor_mismatches
    summary["cross_document_complaint_event_count"] = comparison_events
    summary["qdrant_entry_created"] = False
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Nova DRL Repair Evidence Collector v{}".format(VERSION)
    )
    parser.add_argument("serial_folder")
    parser.add_argument("--output-root")
    parser.add_argument(
        "--traveler-output-root",
        default="/opt/nova-drl/output/traveler_reader_v1_3_1",
    )
    parser.add_argument("--config")
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--hash", action="store_true")
    parser.add_argument("--max-text-mb", type=int, default=25)
    parser.add_argument("--extract-log", action="append", default=[])
    parser.add_argument("--pdf-dpi", type=int, default=300)
    parser.add_argument("--max-pdf-pages", type=int, default=50)
    parser.add_argument("--no-scanned-pdf-ocr", action="store_true")
    parser.add_argument("--no-header-vision", action="store_true")
    parser.add_argument("--vision-model", default=DEFAULT_VISION_MODEL)
    parser.add_argument("--vision-timeout", type=int, default=360)
    parser.add_argument("--expect-events", type=int)
    parser.add_argument("--expect-files", type=int)
    parser.add_argument("--expect-event-assigned-files", type=int)
    parser.add_argument("--expect-event-evidence-files", type=int)
    parser.add_argument("--expect-warranty-events", type=int)
    parser.add_argument("--expect-missing-travelers", type=int)
    parser.add_argument("--expect-unit-items", type=int)
    parser.add_argument("--expect-system-metadata", type=int)
    args = parser.parse_args()

    RUNTIME["enable_header_vision"] = not args.no_header_vision
    RUNTIME["vision_model"] = args.vision_model
    RUNTIME["vision_timeout"] = args.vision_timeout

    source = Path(args.serial_folder).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        print("ERROR: Serial folder not found: {}".format(source), file=sys.stderr)
        return 2

    config_dir = (
        Path(args.config).resolve()
        if args.config
        else Path(__file__).resolve().parent.parent / "config"
    )
    refs = base.load_reference_config(config_dir)
    safe = base.safe_name(source.name)
    output = (
        Path(args.output_root).resolve()
        if args.output_root
        else Path.cwd() / "output" / "repair_evidence_collector_v1_4_3_2" / safe
    )

    try:
        summary = base.collect_evidence(
            source,
            output,
            refs,
            traveler_output_root=args.traveler_output_root,
            inventory_only=args.inventory_only,
            hash_files=args.hash,
            max_text_mb=args.max_text_mb,
            extract_logs=args.extract_log,
            enable_scanned_pdf_ocr=not args.no_scanned_pdf_ocr,
            pdf_dpi=args.pdf_dpi,
            max_pdf_pages=args.max_pdf_pages,
        )
        summary = add_document_analysis(summary)
        checks = base.validate_expectations(summary, args)
        summary["expectation_checks"] = checks
        base.write_outputs(summary, output)
    except Exception as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2

    print()
    print("Nova DRL Repair Evidence Collector v{}".format(VERSION))
    print("=" * 72)
    print("Model:                          {}".format(
        summary["serial_metadata"].get("model")
    ))
    print("Serial:                         {}".format(
        summary["serial_metadata"].get("serial_number")
    ))
    print("Repair events:                  {}".format(summary["repair_event_count"]))
    print("Original source files:          {}".format(
        summary["original_source_file_count"]
    ))
    print("Unaccounted files:              {}".format(
        summary["unaccounted_original_file_count"]
    ))
    print("Scanned-PDF OCR documents:      {}".format(
        summary.get("scanned_pdf_ocr_document_count", 0)
    ))
    print("Scanned-PDF OCR pages:          {}".format(
        summary.get("scanned_pdf_ocr_page_count", 0)
    ))
    print("Document-family page counts:    {}".format(
        summary.get("scanned_document_family_page_counts", {})
    ))
    print("Template OCR quality counts:    {}".format(
        summary.get("scanned_template_quality_counts", {})
    ))
    print("Event annotation quality:       {}".format(
        summary.get("scanned_event_annotation_quality_counts", {})
    ))
    print("Header vision successful pages: {}".format(
        summary.get("header_vision_success_page_count", 0)
    ))
    print("Provisional best event fields:  {}".format(
        summary.get("provisional_best_event_field_count", 0)
    ))
    print("Exact anchor matches:            {}".format(
        summary.get("anchor_exact_match_count", 0)
    ))
    print("Anchor mismatches:               {}".format(
        summary.get("anchor_mismatch_count", 0)
    ))
    print("Complaint comparison events:    {}".format(
        summary.get("cross_document_complaint_event_count", 0)
    ))
    print("Inventory only:                  {}".format(
        "YES" if summary["inventory_only"] else "NO"
    ))

    if checks:
        print("\nEXPECTED PILOT COUNTS")
        for check in checks:
            print(
                "  {:32} expected={} actual={} {}".format(
                    check["label"] + ":",
                    check["expected"],
                    check["actual"],
                    "PASS" if check["pass"] else "FAIL",
                )
            )

    print("\nReports: {}".format(output))
    print("READ-ONLY COMPLETE: No DRL source files were changed.")
    print("NO QDRANT ENTRY CREATED.")

    if (
        summary["unaccounted_original_file_count"] != 0
        or any(not check["pass"] for check in checks)
    ):
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
