#!/usr/bin/env python3
"""
Nova DRL Traveler Reader v1.3.4.4
=================================

Variable-height Repair Block Detection for older DRL Testing Travelers.

Why this release exists
-----------------------
Older travelers may use one logical repair action across several printed table
rows. v1.3.4.2 treated row anchors too much like one-row entries, so a valid
multi-line action could fail the row-coverage safety check.

v1.3.4.4 detects the printed repair table, then treats handwritten marks in
the "Repaired" / "Replaced" columns as START markers for logical repair
blocks. A block continues until the next start marker (or the bottom of the
repair table).

Safety
------
- Reads only derived Traveler Reader v1.3.1 output.
- Never modifies DRL source files.
- Never creates Qdrant entries.
- Detect-only mode performs no MiniCPM-V calls.
- Raw block crops, Tesseract text, and raw vision responses are retained.
- No repair action is accepted as fact automatically.
"""

import argparse
import base64
import json
import math
import re
import statistics
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, ImageFilter

VERSION = "1.3.4.4.1"
DEFAULT_MODEL = "minicpm-v:latest"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"

HLINE_THRESHOLD = 232
MIN_HLINE_WIDTH_FRAC = 0.45
MARK_MIN_PIXELS = 30

# A small DRL vocabulary list used only for literal glossary tagging.
# It never rewrites the source wording.
DRL_GLOSSARY = {
    "comm's": "commutators",
    "comms": "commutators",
    "commutators": "commutators",
    "keal": "KEAL shipping container",
    "fe": "FE",
    "y-fe": "Y-FE",
}


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def safe_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def cluster_positions(values, tolerance=4):
    values = sorted(int(v) for v in values)
    groups = []
    for value in values:
        if not groups or value - groups[-1][-1] > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [int(round(sum(group) / len(group))) for group in groups]


def horizontal_line_records(image):
    arr = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape

    binary = cv2.threshold(
        gray, HLINE_THRESHOLD, 255, cv2.THRESH_BINARY_INV
    )[1]
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(40, int(width * 0.25)), 1)
    )
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(
        horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    records = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w >= width * MIN_HLINE_WIDTH_FRAC and h <= 8:
            records.append(
                {
                    "y": int(y + h // 2),
                    "x": int(x),
                    "width": int(w),
                    "height": int(h),
                }
            )

    # Merge duplicate y detections while retaining the widest segment.
    merged = []
    for rec in sorted(records, key=lambda row: row["y"]):
        if not merged or rec["y"] - merged[-1]["y"] > 4:
            merged.append(rec)
        elif rec["width"] > merged[-1]["width"]:
            merged[-1] = rec
    return merged


def find_regular_body_run(records):
    """
    Find the longest near-regular horizontal-grid sequence.

    v1.3.4.4.1 removes the old fixed 90-pixel row-height ceiling. Traveler
    Reader v1.3.1 crops can be much larger than the manual validation crop,
    so the same printed row may be ~110 pixels high.

    The maximum candidate gap is now derived from the observed vertical span.
    """
    ys = [row["y"] for row in records]
    best = None

    if len(ys) < 2:
        return []

    observed_span = max(1, ys[-1] - ys[0])
    max_gap = max(90, int(observed_span * 0.15))

    for start in range(len(ys)):
        sequence = [ys[start]]
        gaps = []

        for index in range(start + 1, len(ys)):
            gap = ys[index] - sequence[-1]
            if not 8 <= gap <= max_gap:
                break

            if gaps:
                median = statistics.median(gaps)
                if not (0.72 * median <= gap <= 1.32 * median):
                    break

            sequence.append(ys[index])
            gaps.append(gap)

        if len(sequence) < 5:
            continue

        median = statistics.median(gaps)
        mean_dev = sum(abs(gap - median) for gap in gaps) / len(gaps)
        score = (len(sequence), -mean_dev, sequence[-1] - sequence[0])
        if best is None or score > best[0]:
            best = (score, sequence)

    return best[1] if best else []


def vertical_line_positions(image, body_lines):
    arr = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    binary = cv2.threshold(
        gray, HLINE_THRESHOLD, 255, cv2.THRESH_BINARY_INV
    )[1]

    body_height = max(50, body_lines[-1] - body_lines[0])
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(30, int(body_height * 0.35)))
    )
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(
        vertical, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if h < body_height * 0.45 or w > 10:
            continue

        x_center = int(x + w // 2)
        intersection_score = 0
        for y_line in body_lines:
            y0 = max(0, y_line - 3)
            y1 = min(height, y_line + 4)
            x0 = max(0, x_center - 3)
            x1 = min(width, x_center + 4)
            if binary[y0:y1, x0:x1].mean() > 25:
                intersection_score += 1

        if intersection_score >= max(3, int(len(body_lines) * 0.45)):
            candidates.append(x_center)

    return cluster_positions(candidates, tolerance=5)


def detect_table_layout(image):
    records = horizontal_line_records(image)
    body_lines = find_regular_body_run(records)

    if len(body_lines) < 5:
        return {
            "status": "review_required_table_grid",
            "body_lines": body_lines,
            "vertical_lines": [],
            "table_left": None,
            "description_left": None,
            "table_right": None,
        }

    first_body_y = body_lines[0]
    nearest_record = min(
        records, key=lambda row: abs(row["y"] - first_body_y)
    )
    left_guess = nearest_record["x"]

    all_vertical = vertical_line_positions(image, body_lines)
    vertical = [x for x in all_vertical if x >= max(0, left_guess - 5)]

    # Traveler Reader v1.3.1 may crop *inside* the left Repaired column.
    # In that case the table-left border is outside the image, while the first
    # visible verticals are:
    #   repaired/replaced divider, description divider, initials, date...
    crop_left_clipped = (
        left_guess <= 5
        and len(vertical) >= 2
        and vertical[0] > max(12, int(image.width * 0.025))
    )

    minimum_verticals = 2 if crop_left_clipped else 3
    if len(vertical) < minimum_verticals:
        return {
            "status": "review_required_vertical_grid",
            "body_lines": body_lines,
            "vertical_lines": vertical,
            "table_left": left_guess,
            "description_left": None,
            "table_right": None,
            "crop_left_clipped": crop_left_clipped,
        }

    if crop_left_clipped:
        table_left = 0
        repaired_replaced_divider = vertical[0]
        description_left = vertical[1]
    else:
        table_left = vertical[0]
        repaired_replaced_divider = vertical[1]
        description_left = vertical[2]

    # Prefer the farthest detected vertical table boundary. If the source crop
    # clips the right edge, safely use the crop edge.
    table_right = (
        vertical[-1]
        if vertical[-1] > image.width * 0.65
        else image.width - 1
    )
    table_right = min(
        image.width - 1,
        max(table_right, int(image.width * 0.80)),
    )
    crop_right_clipped = table_right >= image.width - 2

    row_heights = [
        body_lines[i + 1] - body_lines[i]
        for i in range(len(body_lines) - 1)
    ]

    return {
        "status": "ok",
        "body_lines": body_lines,
        "vertical_lines": vertical,
        "all_vertical_lines": all_vertical,
        "table_left": table_left,
        "repaired_replaced_divider": repaired_replaced_divider,
        "description_left": description_left,
        "table_right": table_right,
        "crop_left_clipped": crop_left_clipped,
        "crop_right_clipped": crop_right_clipped,
        "physical_row_count": len(body_lines) - 1,
        "median_row_height": statistics.median(row_heights),
    }


def residual_mark_mask(image, layout):
    """
    Create a handwriting/mark mask in the first two action columns.

    Grid lines are removed before connected-component filtering. Color
    saturation helps retain blue-ink X marks, while the grayscale arm keeps
    darker pencil/black-ink marks available.
    """
    arr = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
    saturation = hsv[:, :, 1]

    mask = (
        (gray < 190) | ((saturation > 35) & (gray < 240))
    ).astype(np.uint8) * 255

    body_lines = layout["body_lines"]
    vertical = layout["vertical_lines"]

    for y in body_lines:
        mask[max(0, y - 3): min(mask.shape[0], y + 4), :] = 0
    for x in vertical:
        mask[:, max(0, x - 3): min(mask.shape[1], x + 4)] = 0

    # Use semantic table boundaries rather than assuming the crop contains
    # the physical table-left border. This is required for v1.3.1 partial
    # crops where x=0 begins inside the Repaired column.
    x0 = layout["table_left"]
    x2 = layout["description_left"]
    y0 = body_lines[0]
    y1 = body_lines[-1]

    sub = mask[y0:y1, x0:x2].copy()

    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        sub, connectivity=8
    )
    retained = np.zeros_like(sub)

    for label in range(1, component_count):
        x, y, w, h, area = stats[label]
        if area >= 12 and w >= 3 and h >= 3:
            retained[labels == label] = 255

    return retained


def physical_row_mark_scores(image, layout):
    retained = residual_mark_mask(image, layout)
    y0 = layout["body_lines"][0]
    scores = []

    for row_index, (top, bottom) in enumerate(
        zip(layout["body_lines"][:-1], layout["body_lines"][1:]),
        start=1,
    ):
        local_top = top - y0
        local_bottom = bottom - y0
        score = int((retained[local_top:local_bottom, :] > 0).sum())
        scores.append(
            {
                "physical_row": row_index,
                "mark_pixels": score,
                "marked": score >= MARK_MIN_PIXELS,
            }
        )

    return scores


def consolidate_mark_rows(row_scores):
    marked_indexes = [
        index
        for index, row in enumerate(row_scores)
        if row["marked"]
    ]

    groups = []
    for index in marked_indexes:
        if not groups or index - groups[-1][-1] > 1:
            groups.append([index])
        else:
            groups[-1].append(index)

    starts = []
    for group in groups:
        max_score = max(row_scores[index]["mark_pixels"] for index in group)
        meaningful = [
            index
            for index in group
            if row_scores[index]["mark_pixels"]
            >= max(MARK_MIN_PIXELS, max_score * 0.22)
        ]
        starts.append(min(meaningful or group))

    return starts, groups


def build_blocks(image, layout, row_scores):
    starts, groups = consolidate_mark_rows(row_scores)
    body_lines = layout["body_lines"]

    blocks = []
    for number, start_index in enumerate(starts, start=1):
        next_index = (
            starts[number]
            if number < len(starts)
            else len(body_lines) - 1
        )

        top = body_lines[start_index]
        bottom = body_lines[next_index]

        # Small padding remains inside the table, so the next repair-start mark
        # cannot bleed into the preceding block.
        pad = 3
        full_box = (
            max(0, layout["table_left"] - pad),
            max(0, top + 1),
            min(image.width, layout["table_right"] + pad),
            min(image.height, bottom - 1),
        )
        description_box = (
            max(0, layout["description_left"] - pad),
            max(0, top + 1),
            min(image.width, layout["table_right"] + pad),
            min(image.height, bottom - 1),
        )

        blocks.append(
            {
                "entry_index": number,
                "start_physical_row": start_index + 1,
                "end_physical_row": next_index,
                "start_mark_group_rows": [
                    index + 1 for index in groups[number - 1]
                ],
                "full_box": list(full_box),
                "description_box": list(description_box),
                "height_pixels": int(bottom - top),
                "physical_rows_spanned": int(next_index - start_index),
            }
        )

    return blocks


def enhance_for_ocr(image):
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray)
    if gray.width < 1400:
        gray = gray.resize(
            (gray.width * 2, gray.height * 2),
            Image.Resampling.LANCZOS,
        )
    return gray.filter(ImageFilter.SHARPEN)


def tesseract_text(image_path, psm=6):
    try:
        result = subprocess.run(
            [
                "tesseract",
                str(image_path),
                "stdout",
                "--psm",
                str(psm),
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return ""
    except Exception:
        return ""


def vision_prompt():
    return """You are transcribing ONE handwritten DRL repair block from a Testing Traveler.

Return ONLY one JSON object with exactly these keys:
{
  "description": string or null,
  "notes": string or null,
  "initials": string or null,
  "date": string or null,
  "repaired_mark": true, false, or null,
  "replaced_mark": true, false, or null
}

Rules:
- Transcribe literally. Do not correct spelling, technical terms, abbreviations, or numbers.
- "description" is the handwritten repair/replacement/adjustment action and its continuation lines.
- Keep quantities and part/location wording that belong to that action.
- "notes" is only clearly separate handwritten explanatory/diagnostic narrative inside the same block; otherwise null.
- Ignore printed form labels, grid lines, and preprinted text.
- Do not infer missing words.
- If a value is unreadable, use null.
- Do not add commentary outside the JSON object.
"""


def call_ollama_vision(image_path, model):
    encoded = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
    payload = {
        "model": model,
        "prompt": vision_prompt(),
        "images": [encoded],
        "stream": False,
        "options": {"temperature": 0},
    }

    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=300) as response:
        data = json.loads(response.read().decode("utf-8"))

    return str(data.get("response") or "").strip()


def parse_json_response(response):
    text = str(response or "").strip()
    if not text:
        return None

    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return None

    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def clean_literal(value):
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    if value.lower() in {"", "null", "none", "n/a", "unreadable"}:
        return None
    return value


def validate_initials(value):
    value = clean_literal(value)
    if not value:
        return {"status": "unreadable", "value": None}

    normalized = re.sub(r"\s+", "", value)
    valid = bool(re.fullmatch(r"[A-Za-z]{1,4}(?:[/&+][A-Za-z]{1,4})*", normalized))
    return {
        "status": "valid_format" if valid else "review_required",
        "value": value,
    }


def log_date_from_number(log_number):
    match = re.fullmatch(r"(\d{2})(\d{2})(\d{2})\d{3}", str(log_number))
    if not match:
        return None
    yy, mm, dd = [int(x) for x in match.groups()]
    year = 2000 + yy if yy <= 69 else 1900 + yy
    try:
        return datetime(year, mm, dd)
    except ValueError:
        return None


def validate_date(value, log_number):
    value = clean_literal(value)
    if not value:
        return {"status": "unreadable", "value": None}

    parsed = None
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%m-%d-%y", "%m-%d-%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
            break
        except ValueError:
            continue

    if not parsed:
        return {"status": "review_required_format", "value": value}

    log_date = log_date_from_number(log_number)
    plausible = True
    if log_date:
        plausible = log_date - timedelta(days=31) <= parsed <= log_date + timedelta(days=550)

    return {
        "status": "plausible" if plausible else "review_required_date_range",
        "value": value,
        "parsed_date": parsed.date().isoformat(),
    }


def glossary_matches(text):
    normalized = str(text or "").lower()
    matches = []
    for raw, meaning in DRL_GLOSSARY.items():
        if raw in normalized:
            matches.append({"raw": raw, "normalized_meaning": meaning})
    return matches


def source_metadata(log_dir):
    region_json = log_dir / "traveler_regions.json"
    if not region_json.exists():
        return {}
    try:
        return json.loads(region_json.read_text(encoding="utf-8"))
    except Exception:
        return {}


def locate_repairs_crop(log_dir):
    candidates = [
        log_dir / "crops" / "repairs_replacements.png",
        log_dir / "crops" / "repairs_replacements.jpg",
        log_dir / "crops" / "repairs_replacements.JPG",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def process_log(log_dir, log_number, model, detect_only, expected_entries=None):
    crop_path = locate_repairs_crop(log_dir)
    output_dir = log_dir / "vision_extraction_v1_3_4_4_1"
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata = source_metadata(log_dir)
    source_path = metadata.get("source_path")
    relative_path = metadata.get("relative_path")

    if crop_path is None:
        record = {
            "reader_version": VERSION,
            "log_number": log_number,
            "status": "review_required_missing_repairs_crop",
            "model": model,
            "detect_only": detect_only,
            "vision_processing_stopped": True,
            "source_path": source_path,
            "relative_path": relative_path,
            "entries": [],
            "accepted_as_facts": 0,
        }
        write_json(output_dir / "repair_entries_v1_3_4_4.json", record)
        return record

    image = Image.open(crop_path).convert("RGB")
    layout = detect_table_layout(image)

    if layout["status"] != "ok":
        record = {
            "reader_version": VERSION,
            "log_number": log_number,
            "status": layout["status"],
            "model": model,
            "detect_only": detect_only,
            "vision_processing_stopped": True,
            "source_path": source_path,
            "relative_path": relative_path,
            "repairs_crop_path": str(crop_path),
            "block_detection": {"layout": layout},
            "entries": [],
            "accepted_as_facts": 0,
        }
        write_json(output_dir / "repair_entries_v1_3_4_4.json", record)
        return record

    row_scores = physical_row_mark_scores(image, layout)
    blocks = build_blocks(image, layout, row_scores)

    expected_match = (
        expected_entries is None or len(blocks) == int(expected_entries)
    )

    status = "ok" if blocks and expected_match else (
        "review_required_expected_entry_count"
        if blocks and not expected_match
        else "review_required_no_start_marks"
    )

    debug = {
        "method": "variable_height_repair_blocks",
        "layout": layout,
        "row_mark_scores": row_scores,
        "logical_start_count": len(blocks),
        "expected_entries": expected_entries,
        "expected_entry_count_match": expected_match,
        "blocks": blocks,
    }
    write_json(output_dir / "block_detection_debug.json", debug)

    entries = []
    vision_stopped = status != "ok"

    block_dir = output_dir / "blocks"
    block_dir.mkdir(parents=True, exist_ok=True)

    for block in blocks:
        index = block["entry_index"]
        full_crop = image.crop(tuple(block["full_box"]))
        desc_crop = image.crop(tuple(block["description_box"]))

        full_path = block_dir / "entry_{:02d}_full.png".format(index)
        desc_path = block_dir / "entry_{:02d}_description.png".format(index)
        enhanced_path = block_dir / "entry_{:02d}_enhanced.png".format(index)

        full_crop.save(full_path)
        desc_crop.save(desc_path)
        enhance_for_ocr(full_crop).save(enhanced_path)

        tess = tesseract_text(enhanced_path, psm=6)

        entry = {
            "entry_index": index,
            "start_physical_row": block["start_physical_row"],
            "end_physical_row": block["end_physical_row"],
            "physical_rows_spanned": block["physical_rows_spanned"],
            "blank_rejected": False,
            "literal_fields": {
                "description": None,
                "notes": None,
                "initials": None,
                "date": None,
                "repaired_mark": None,
                "replaced_mark": None,
            },
            "crop_paths": {
                "full_row": str(full_path),
                "description": str(desc_path),
                "enhanced": str(enhanced_path),
            },
            "tesseract": {
                "full_row": {
                    "selected_psm": 6,
                    "selected_text": tess,
                }
            },
            "vision": {
                "model": model,
                "response": None,
                "parsed_json": None,
                "status": "not_run_detect_only" if detect_only else "pending",
            },
            "initials_validation": {"status": "not_assessed"},
            "date_validation": {"status": "not_assessed"},
            "glossary_matches": [],
            "review_reasons": [],
            "eligible_for_evidence_comparison": False,
            "accepted_as_repair_fact": False,
        }

        if detect_only:
            entry["review_reasons"].append(
                "detect_only_no_vision_run"
            )
            entries.append(entry)
            continue

        if status != "ok":
            entry["vision"]["status"] = "blocked_by_detection_review"
            entry["review_reasons"].append(
                "block_detection_requires_review"
            )
            entries.append(entry)
            continue

        try:
            response = call_ollama_vision(full_path, model)
            parsed = parse_json_response(response)
            entry["vision"]["response"] = response
            entry["vision"]["parsed_json"] = parsed

            if not parsed:
                entry["vision"]["status"] = "review_required_non_json_response"
                entry["review_reasons"].append(
                    "vision_prompt_noncompliance"
                )
                entries.append(entry)
                continue

            description = clean_literal(parsed.get("description"))
            notes = clean_literal(parsed.get("notes"))
            initials = clean_literal(parsed.get("initials"))
            date_value = clean_literal(parsed.get("date"))

            entry["literal_fields"] = {
                "description": description,
                "notes": notes,
                "initials": initials,
                "date": date_value,
                "repaired_mark": parsed.get("repaired_mark"),
                "replaced_mark": parsed.get("replaced_mark"),
            }
            entry["initials_validation"] = validate_initials(initials)
            entry["date_validation"] = validate_date(date_value, log_number)
            entry["glossary_matches"] = glossary_matches(
                " ".join(x for x in [description, notes] if x)
            )

            if not description:
                entry["vision"]["status"] = "review_required_blank_description"
                entry["review_reasons"].append(
                    "blank_or_weak_candidate_rejected"
                )
                entries.append(entry)
                continue

            entry["vision"]["status"] = "ok"
            entry["review_reasons"].append(
                "vision_transcription_requires_human_review"
            )

            if entry["initials_validation"]["status"] != "valid_format":
                entry["review_reasons"].append("initials_requires_review")
            if entry["date_validation"]["status"] != "plausible":
                entry["review_reasons"].append("date_requires_review")

            entry["eligible_for_evidence_comparison"] = True

        except Exception as exc:
            entry["vision"]["status"] = "error"
            entry["vision"]["error"] = str(exc)
            entry["review_reasons"].append("vision_processing_error")

        entries.append(entry)

    record = {
        "reader_version": VERSION,
        "log_number": log_number,
        "status": status,
        "model": model,
        "detect_only": detect_only,
        "vision_processing_stopped": vision_stopped,
        "source_path": source_path,
        "relative_path": relative_path,
        "repairs_crop_path": str(crop_path),
        "block_detection_method": "variable_height_repair_blocks",
        "block_detection": debug,
        "detected_start_marks": len(blocks),
        "detected_repair_entries": len(entries),
        "expected_entries": expected_entries,
        "expected_entry_count_match": expected_match,
        "entries": entries,
        "evidence_comparison_eligible": sum(
            1 for entry in entries
            if entry.get("eligible_for_evidence_comparison")
        ),
        "accepted_as_facts": 0,
        "source_modified": False,
        "qdrant_created": False,
    }

    json_path = output_dir / "repair_entries_v1_3_4_4.json"
    write_json(json_path, record)

    # Stable discovery copy at log root. This remains derived output only.
    write_json(log_dir / "repair_entries_v1_3_4_4.json", record)

    text_lines = [
        "# Nova DRL Traveler Reader v1.3.4.4",
        "",
        "Log: {}".format(log_number),
        "Detection method: variable_height_repair_blocks",
        "Status: {}".format(status),
        "Detect only: {}".format("YES" if detect_only else "NO"),
        "Physical table rows: {}".format(layout.get("physical_row_count")),
        "Logical start marks: {}".format(len(blocks)),
        "Expected entries: {}".format(
            expected_entries if expected_entries is not None else "not supplied"
        ),
        "Expected count match: {}".format(expected_match),
        "",
    ]

    for entry in entries:
        text_lines.extend([
            "ENTRY {:02d}".format(entry["entry_index"]),
            "  Physical rows: {}-{}".format(
                entry["start_physical_row"], entry["end_physical_row"]
            ),
            "  Rows spanned: {}".format(entry["physical_rows_spanned"]),
            "  Description: {}".format(
                entry["literal_fields"].get("description") or "NOT TRANSCRIBED"
            ),
            "  Notes: {}".format(
                entry["literal_fields"].get("notes") or "None"
            ),
            "  Initials: {}".format(
                entry["literal_fields"].get("initials") or "None"
            ),
            "  Date: {}".format(
                entry["literal_fields"].get("date") or "None"
            ),
            "  Vision status: {}".format(entry["vision"].get("status")),
            "  Evidence comparison eligible: {}".format(
                "YES" if entry["eligible_for_evidence_comparison"] else "NO"
            ),
            "  Accepted as repair fact: NO",
            "",
        ])

    (output_dir / "repair_entries_v1_3_4_4.txt").write_text(
        "\n".join(text_lines), encoding="utf-8"
    )

    return record


def discover_log_dirs(root, requested_log=None):
    root = Path(root).expanduser().resolve()

    if requested_log:
        candidate = root / str(requested_log)
        if candidate.is_dir():
            return [(str(requested_log), candidate)]

        # Allow caller to pass the log directory itself.
        if root.name == str(requested_log) and root.is_dir():
            return [(str(requested_log), root)]

        return []

    logs = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and re.fullmatch(r"\d{9}", child.name):
            if locate_repairs_crop(child):
                logs.append((child.name, child))
    return logs


def main():
    parser = argparse.ArgumentParser(
        description="Nova DRL Traveler Reader v{}".format(VERSION)
    )
    parser.add_argument(
        "traveler_reader_root",
        help=(
            "Traveler Reader v1.3.1 serial output directory, or one log "
            "directory."
        ),
    )
    parser.add_argument("--log")
    parser.add_argument("--expected-entries", type=int)
    parser.add_argument("--detect-only", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    logs = discover_log_dirs(args.traveler_reader_root, args.log)
    if not logs:
        print("ERROR: No matching traveler log directory found.", file=sys.stderr)
        return 2

    results = []
    for log_number, log_dir in logs:
        print("Processing log {} ...".format(log_number))
        result = process_log(
            log_dir=log_dir,
            log_number=log_number,
            model=args.model,
            detect_only=args.detect_only,
            expected_entries=args.expected_entries,
        )
        results.append(result)

    print()
    print("# Nova DRL Traveler Reader v{}".format(VERSION))
    print()
    print("Model:                         {}".format(args.model))
    print("Detection mode:                variable-height repair blocks")
    print("Detect only:                   {}".format(
        "YES" if args.detect_only else "NO"
    ))
    print("Logs found:                    {}".format(len(logs)))
    print("Logs processed:                {}".format(
        sum(1 for result in results if result.get("status") == "ok")
    ))
    print("Logs requiring review:         {}".format(
        sum(1 for result in results if result.get("status") != "ok")
    ))
    print("Vision runs stopped:           {}".format(
        sum(1 for result in results if result.get("vision_processing_stopped"))
    ))
    print("Detected start marks:          {}".format(
        sum(result.get("detected_start_marks", 0) for result in results)
    ))
    print("Detected repair blocks:        {}".format(
        sum(result.get("detected_repair_entries", 0) for result in results)
    ))
    print("Evidence-comparison eligible:  {}".format(
        sum(result.get("evidence_comparison_eligible", 0) for result in results)
    ))
    print("Accepted as repair facts:      0")
    print()

    for result in results:
        print(
            "{} status={} starts={} blocks={}".format(
                result.get("log_number"),
                result.get("status"),
                result.get("detected_start_marks", 0),
                result.get("detected_repair_entries", 0),
            )
        )

    print()
    print("VARIABLE-HEIGHT REPAIR-BLOCK EXTRACTION COMPLETE.")
    print("No DRL source files were changed.")
    print("No Qdrant entry created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
