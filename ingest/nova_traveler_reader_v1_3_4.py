#!/usr/bin/env python3
"""
Nova DRL Traveler Reader v1.3.4
================================

Anchored Repair-Entry Extraction
--------------------------------

This release replaces equal-height repair bands with entry boundaries derived
from handwritten initials/date activity in the right side of the DRL repairs
and replacements table.

Core goals
----------
- Detect actual repair-entry anchors in the initials/date columns.
- Build one crop per detected repair entry, even when descriptions use unequal
  numbers of handwritten lines.
- Preserve separate description, initials, and date crops.
- Reject weak/blank candidate rows before vision processing.
- Validate transcribed dates against the YYMMDD### DRL log date.
- Preserve literal transcription separately from confirmed DRL terminology.
- Never silently accept a vision result as a trusted repair fact.
- Keep all DRL source files read-only.

This release does NOT write to Qdrant and does NOT perform final evidence fusion.
"""

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

VERSION = '1.3.4'
DEFAULT_MODEL = 'minicpm-v:latest'

ROW_PROMPT = """You are transcribing exactly one repair-entry row from a Direct Repair Laboratories repair traveler.

The image contains three columns: repair description, technician initials, and date.

Return exactly one plain-text line in this format:
DESCRIPTION | INITIALS | DATE

Rules:
1. Transcribe only text visibly present in this one row.
2. Do not summarize, explain, interpret, correct, or infer.
3. Preserve part names, axis names, abbreviations, numbers, initials, and dates exactly as visible.
4. If any field is unreadable, use [unclear] for that field.
5. If initials or date are visibly blank, leave that field blank.
6. Do not output headings, bullets, Markdown, commentary, or more than one line.
7. Do not convert DRL abbreviations into expanded terms.
"""

DESCRIPTION_PROMPT = """Transcribe only the repair description visibly present in this crop.
Return plain text only. Do not summarize, explain, correct, or infer.
Preserve abbreviations and technical wording exactly as visible.
If unreadable, return [unclear].
"""

INITIALS_PROMPT = """Transcribe only the technician initials visibly present in this crop.
Return only the initials. If blank, return [blank]. If unreadable, return [unclear].
Do not explain or infer.
"""

DATE_PROMPT = """Transcribe only the handwritten date visibly present in this crop.
Return only the date exactly as visible. If blank, return [blank]. If unreadable, return [unclear].
Do not explain, correct, or infer.
"""

NONCOMPLIANCE_PATTERNS = [
    r'(?im)^\s*(title|subtitle|summary|note|explanation)\s*:',
    r'(?i)the\s+image\s+(shows|contains|depicts)',
    r'(?i)this\s+(appears|seems)\s+to',
    r'(?i)could\s+refer\s+to',
    r'(?i)it\s+appears\s+that',
    r'(?m)^\s*[-*]\s+',
    r'(?m)^\s*\*\*[^\n]+\*\*\s*$',
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def require_pillow():
    try:
        from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageDraw
        return Image, ImageOps, ImageEnhance, ImageFilter, ImageDraw
    except Exception as exc:
        raise RuntimeError(
            'Pillow is required. Install with: sudo apt install python3-pil'
        ) from exc


def normalize_apostrophes(text: str) -> str:
    return (text or '').replace('’', "'").replace('‘', "'")


def load_glossary(path: Path) -> Dict[str, Any]:
    data = load_json(path)
    if not data:
        return {'version': 1, 'entries': []}
    return data


def glossary_matches(text: str, glossary: Dict[str, Any]) -> List[Dict[str, Any]]:
    normalized = normalize_apostrophes(text).lower()
    results = []
    for entry in glossary.get('entries', []):
        aliases = entry.get('aliases', [])
        matched_alias = None
        for alias in aliases:
            alias_norm = normalize_apostrophes(str(alias)).lower()
            if alias_norm and alias_norm in normalized:
                matched_alias = alias
                break
        if matched_alias is not None:
            results.append({
                'matched_alias': matched_alias,
                'canonical_term': entry.get('canonical_term'),
                'meaning': entry.get('meaning'),
                'category': entry.get('category'),
                'context': entry.get('context', []),
                'user_confirmed': bool(entry.get('user_confirmed')),
            })
    return results


def decode_log_number(log_number: str) -> Dict[str, Any]:
    value = str(log_number or '')
    result = {
        'log_number': value,
        'valid': False,
        'log_date': None,
        'daily_sequence': None,
    }
    if not re.fullmatch(r'\d{9}', value):
        return result
    try:
        parsed = date(2000 + int(value[0:2]), int(value[2:4]), int(value[4:6]))
    except ValueError:
        return result
    result.update({
        'valid': True,
        'log_date': parsed.isoformat(),
        'daily_sequence': value[6:9],
    })
    return result


def parse_date_field(raw: str, log_number: str) -> Dict[str, Any]:
    text = (raw or '').strip()
    result = {
        'raw': text,
        'status': 'unknown',
        'parsed_date': None,
        'days_from_log': None,
        'plausible': None,
        'reason': None,
    }
    lower = text.lower()
    if not text or lower in {'[blank]', 'blank'}:
        result.update({'status': 'blank', 'plausible': True})
        return result
    if '[unclear]' in lower or lower == 'unclear':
        result.update({'status': 'unclear', 'plausible': None})
        return result

    matches = re.findall(r'(?<!\d)(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?(?!\d)', text)
    if len(matches) != 1:
        result.update({
            'status': 'invalid' if not matches else 'ambiguous',
            'plausible': False if not matches else None,
            'reason': 'Expected exactly one visible date.',
        })
        return result

    month, day, year_text = matches[0]
    if not year_text:
        result.update({
            'status': 'incomplete',
            'plausible': None,
            'reason': 'Month/day found without a year.',
        })
        return result

    year = int(year_text)
    if len(year_text) == 2:
        year = 1900 + year if year >= 70 else 2000 + year

    try:
        parsed = date(year, int(month), int(day))
    except ValueError as exc:
        result.update({
            'status': 'invalid',
            'plausible': False,
            'reason': str(exc),
        })
        return result

    log_info = decode_log_number(log_number)
    result['parsed_date'] = parsed.isoformat()
    if not log_info['valid']:
        result.update({
            'status': 'valid_unchecked',
            'plausible': None,
            'reason': 'Log date unavailable.',
        })
        return result

    log_date = date.fromisoformat(log_info['log_date'])
    delta = (parsed - log_date).days
    plausible = -31 <= delta <= 370
    result.update({
        'status': 'plausible' if plausible else 'implausible',
        'days_from_log': delta,
        'plausible': plausible,
        'reason': None if plausible else 'Date is implausible relative to the DRL log date.',
    })
    return result


def validate_initials(raw: str) -> Dict[str, Any]:
    text = (raw or '').strip()
    lower = text.lower()
    if not text or lower in {'[blank]', 'blank'}:
        return {'raw': text, 'status': 'blank', 'normalized': None}
    if '[unclear]' in lower or lower == 'unclear':
        return {'raw': text, 'status': 'unclear', 'normalized': None}
    cleaned = re.sub(r'[^A-Za-z]', '', text)
    if 1 <= len(cleaned) <= 4 and re.fullmatch(r'[A-Za-z]{1,4}', cleaned):
        return {'raw': text, 'status': 'valid', 'normalized': cleaned.upper()}
    return {'raw': text, 'status': 'review_required', 'normalized': None}


def fractional_box_to_pixels(
    box: Sequence[float], width: int, height: int
) -> Tuple[int, int, int, int]:
    left = max(0, min(width, int(round(box[0] * width))))
    top = max(0, min(height, int(round(box[1] * height))))
    right = max(left + 1, min(width, int(round(box[2] * width))))
    bottom = max(top + 1, min(height, int(round(box[3] * height))))
    return left, top, right, bottom


def preprocess_crop(image):
    Image, ImageOps, ImageEnhance, ImageFilter, _ = require_pillow()
    output = ImageOps.autocontrast(image.convert('L'))
    output = output.resize(
        (max(1, output.width * 2), max(1, output.height * 2)),
        Image.Resampling.LANCZOS,
    )
    output = ImageEnhance.Contrast(output).enhance(1.40)
    output = ImageEnhance.Sharpness(output).enhance(1.30)
    try:
        output = output.filter(ImageFilter.MedianFilter(size=3))
    except Exception:
        pass
    return output


def build_clean_mask(image, threshold: int = 165) -> Dict[str, Any]:
    _, ImageOps, _, _, _ = require_pillow()
    gray = ImageOps.autocontrast(image.convert('L'))
    width, height = gray.size
    pixels = list(gray.getdata())
    mask = [value < threshold for value in pixels]

    row_counts = [
        sum(mask[y * width:(y + 1) * width]) for y in range(height)
    ]
    col_counts = [
        sum(mask[y * width + x] for y in range(height)) for x in range(width)
    ]

    horizontal_lines = {
        y for y, count in enumerate(row_counts) if count >= width * 0.38
    }
    vertical_lines = {
        x for x, count in enumerate(col_counts) if count >= height * 0.45
    }

    return {
        'gray': gray,
        'width': width,
        'height': height,
        'mask': mask,
        'horizontal_lines': horizontal_lines,
        'vertical_lines': vertical_lines,
    }


def ink_density(image, threshold: int = 165) -> float:
    data = build_clean_mask(image, threshold)
    width = data['width']
    height = data['height']
    mask = data['mask']
    horizontal = data['horizontal_lines']
    vertical = data['vertical_lines']

    ink = 0
    available = 0
    for y in range(height):
        if y in horizontal:
            continue
        offset = y * width
        for x in range(width):
            if x in vertical:
                continue
            available += 1
            if mask[offset + x]:
                ink += 1
    return float(ink) / float(available) if available else 0.0


def smooth_profile(values: List[int], window: int = 9) -> List[float]:
    half = max(1, window // 2)
    result = []
    for index in range(len(values)):
        start = max(0, index - half)
        end = min(len(values), index + half + 1)
        result.append(float(sum(values[start:end])) / float(end - start))
    return result


def detect_repair_anchors(
    image,
    x_start: float = 0.82,
    x_end: float = 0.995,
    y_start: float = 0.30,
    threshold: int = 165,
    expected_entries: Optional[int] = None,
) -> Dict[str, Any]:
    width, height = image.size
    strip_box = fractional_box_to_pixels(
        (x_start, 0.0, x_end, 1.0), width, height
    )
    strip = image.crop(strip_box)
    data = build_clean_mask(strip, threshold)
    strip_width = data['width']
    strip_height = data['height']
    mask = data['mask']
    horizontal = data['horizontal_lines']
    vertical = data['vertical_lines']

    profile = []
    for y in range(strip_height):
        if y in horizontal:
            profile.append(0)
            continue
        offset = y * strip_width
        count = 0
        for x in range(strip_width):
            if x not in vertical and mask[offset + x]:
                count += 1
        profile.append(count)

    smoothed = smooth_profile(profile, 9)
    minimum_y = int(round(strip_height * y_start))
    maximum_y = int(round(strip_height * 0.98))
    base_threshold = max(4.0, float(strip_width) * 0.035)

    candidates = []
    for y in range(max(minimum_y, 1), min(maximum_y, strip_height - 1)):
        value = smoothed[y]
        if value < base_threshold:
            continue
        if value >= smoothed[y - 1] and value >= smoothed[y + 1]:
            candidates.append({'y': y, 'score': value})

    maximum_score = max([item['score'] for item in candidates], default=0.0)
    score_floor = max(base_threshold, maximum_score * 0.25)
    candidates = [
        item for item in candidates if item['score'] >= score_floor
    ]

    minimum_distance = max(12, int(round(strip_height * 0.09)))
    selected = []
    for item in sorted(candidates, key=lambda value: value['score'], reverse=True):
        if all(abs(item['y'] - prior['y']) >= minimum_distance for prior in selected):
            selected.append(item)

    if expected_entries and len(selected) > expected_entries:
        selected = sorted(
            selected, key=lambda value: value['score'], reverse=True
        )[:expected_entries]

    selected.sort(key=lambda value: value['y'])
    return {
        'strip_box': strip_box,
        'anchor_strip_x_fraction': [x_start, x_end],
        'anchor_y_start_fraction': y_start,
        'base_threshold': base_threshold,
        'score_floor': score_floor,
        'minimum_anchor_distance_pixels': minimum_distance,
        'candidate_count': len(candidates),
        'anchors': selected,
        'profile': profile,
        'smoothed_profile': smoothed,
    }


def build_entry_bands(
    centers: List[int], height: int, padding_fraction: float = 0.03
) -> List[Tuple[int, int]]:
    if not centers:
        return []
    centers = sorted(centers)
    padding = int(round(height * padding_fraction))
    header_floor = int(round(height * 0.27))
    bottom_ceiling = int(round(height * 0.98))

    midpoints = [
        int(round((centers[index] + centers[index + 1]) / 2.0))
        for index in range(len(centers) - 1)
    ]

    bands = []
    for index, center in enumerate(centers):
        if index == 0:
            if len(centers) > 1:
                start = int(round(center - 0.55 * (centers[1] - center)))
            else:
                start = center - int(round(height * 0.10))
        else:
            start = midpoints[index - 1]

        if index == len(centers) - 1:
            if len(centers) > 1:
                end = int(round(center + 0.55 * (center - centers[index - 1])))
            else:
                end = center + int(round(height * 0.10))
        else:
            end = midpoints[index]

        start = max(header_floor, start - padding)
        end = min(bottom_ceiling, end + padding)
        if end > start:
            bands.append((start, end))
    return bands


def create_detection_debug(image, detection, bands, output_path: Path) -> None:
    _, _, _, _, ImageDraw = require_pillow()
    debug = image.convert('RGB').copy()
    draw = ImageDraw.Draw(debug)
    left, _, right, _ = detection['strip_box']
    draw.rectangle((left, 0, right, image.height - 1), outline=(255, 0, 0), width=3)
    for index, anchor in enumerate(detection['anchors'], 1):
        y = int(anchor['y'])
        draw.line((0, y, image.width, y), fill=(0, 180, 0), width=3)
        draw.text((5, y + 3), 'A{}'.format(index), fill=(0, 120, 0))
    for index, (top, bottom) in enumerate(bands, 1):
        draw.rectangle((1, top, image.width - 2, bottom), outline=(0, 80, 255), width=2)
        draw.text((40, top + 5), 'ENTRY {}'.format(index), fill=(0, 80, 255))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    debug.save(output_path)


def run_command(args: List[str], timeout: int = 180) -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        return {
            'returncode': completed.returncode,
            'stdout': completed.stdout,
            'stderr': completed.stderr,
        }
    except Exception as exc:
        return {'returncode': 999, 'stdout': '', 'stderr': str(exc)}


def ocr_quality_score(text: str) -> int:
    text = text or ''
    if not text.strip():
        return -100000
    alnum = sum(1 for char in text if char.isalnum())
    words = re.findall(r'[A-Za-z0-9][A-Za-z0-9#./+\-\']{1,}', text)
    short_words = sum(1 for word in words if len(word) <= 2)
    return alnum + 4 * len(words) - 2 * short_words


def tesseract_crop(path: Path) -> Dict[str, Any]:
    if not shutil.which('tesseract'):
        return {
            'status': 'dependency_missing',
            'selected_psm': None,
            'selected_text': '',
            'passes': [],
        }
    passes = []
    for psm in (6, 7, 11, 12):
        result = run_command(
            ['tesseract', str(path), 'stdout', '--psm', str(psm)], 180
        )
        text = result['stdout'] if result['returncode'] == 0 else ''
        passes.append({
            'psm': psm,
            'status': 'ok' if result['returncode'] == 0 else 'error',
            'text': text,
            'score': ocr_quality_score(text),
            'stderr': result['stderr'].strip(),
        })
    best = max(passes, key=lambda item: item['score'])
    return {
        'status': best['status'],
        'selected_psm': best['psm'],
        'selected_text': best['text'],
        'selected_score': best['score'],
        'passes': passes,
    }


def ollama_tags() -> Optional[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(
            'http://127.0.0.1:11434/api/tags', timeout=5
        ) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception:
        return None


def resolve_model(requested: str) -> Optional[str]:
    data = ollama_tags()
    if not data:
        return None
    names = [item.get('name', '') for item in data.get('models', [])]
    if requested in names:
        return requested
    if ':' not in requested:
        for name in names:
            if name == requested or name.startswith(requested + ':'):
                return name
    return None


def call_ollama_vision(
    model: str, prompt: str, image_path: Path, timeout: int
) -> Dict[str, Any]:
    payload = json.dumps({
        'model': model,
        'prompt': prompt,
        'images': [base64.b64encode(image_path.read_bytes()).decode('ascii')],
        'stream': False,
        'options': {'temperature': 0},
    }).encode('utf-8')
    request = urllib.request.Request(
        'http://127.0.0.1:11434/api/generate',
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode('utf-8'))
        return {
            'status': 'ok',
            'response': body.get('response', ''),
            'done_reason': body.get('done_reason'),
            'eval_count': body.get('eval_count'),
            'warning': None,
        }
    except Exception as exc:
        return {'status': 'error', 'response': '', 'warning': str(exc)}


def prompt_noncompliance(text: str) -> Dict[str, Any]:
    matches = [
        pattern for pattern in NONCOMPLIANCE_PATTERNS
        if re.search(pattern, text or '')
    ]
    return {
        'prompt_noncompliance': bool(matches),
        'matched_patterns': matches,
    }


def parse_row_response(text: str) -> Dict[str, Any]:
    raw = (text or '').strip()
    cleaned = raw.replace('```', '').strip()
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    pipe_lines = [line for line in lines if '|' in line]
    selected = pipe_lines[0] if len(pipe_lines) == 1 else (lines[0] if len(lines) == 1 else '')
    parts = [part.strip() for part in selected.split('|')] if selected else []
    compliant = len(lines) == 1 and len(parts) == 3
    return {
        'raw_response': raw,
        'selected_line': selected,
        'description': parts[0] if len(parts) == 3 else None,
        'initials': parts[1] if len(parts) == 3 else None,
        'date': parts[2] if len(parts) == 3 else None,
        'format_compliant': compliant,
        'nonempty_line_count': len(lines),
    }


def clean_single_field(text: str) -> str:
    value = (text or '').strip().replace('```', '').strip()
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return lines[0] if len(lines) == 1 else value


def create_entry_crops(parent_image, bands, output_dir: Path) -> List[Dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    width, _ = parent_image.size
    results = []
    for index, (top, bottom) in enumerate(bands, 1):
        boxes = {
            'full_row': (0.07, top, 0.995, bottom),
            'description': (0.10, top, 0.82, bottom),
            'initials': (0.79, top, 0.90, bottom),
            'date': (0.88, top, 0.995, bottom),
        }
        paths = {}
        densities = {}
        pixel_boxes = {}
        for field, mixed_box in boxes.items():
            left = int(round(width * mixed_box[0]))
            right = int(round(width * mixed_box[2]))
            box = (left, int(top), right, int(bottom))
            crop = parent_image.crop(box)
            densities[field] = ink_density(crop)
            processed = preprocess_crop(crop)
            path = output_dir / 'entry_{:02d}_{}.png'.format(index, field)
            processed.save(path)
            paths[field] = str(path)
            pixel_boxes[field] = box
        results.append({
            'entry_index': index,
            'band': [top, bottom],
            'pixel_boxes': pixel_boxes,
            'crop_paths': paths,
            'ink_density': densities,
        })
    return results


def process_entry(
    entry: Dict[str, Any],
    model: str,
    timeout: int,
    log_number: str,
    glossary: Dict[str, Any],
    blank_threshold: float,
    detect_only: bool,
) -> Dict[str, Any]:
    full_path = Path(entry['crop_paths']['full_row'])
    tesseract = {
        field: tesseract_crop(Path(path))
        for field, path in entry['crop_paths'].items()
    }
    full_density = entry['ink_density']['full_row']
    blank_rejected = full_density < blank_threshold

    result = {
        **entry,
        'blank_threshold': blank_threshold,
        'blank_rejected': blank_rejected,
        'tesseract': tesseract,
        'vision': None,
        'parsed': None,
        'field_retries': {},
        'literal_fields': {
            'description': None,
            'initials': None,
            'date': None,
        },
        'initials_validation': None,
        'date_validation': None,
        'glossary_matches': [],
        'eligible_for_evidence_comparison': False,
        'accepted_as_fact': False,
        'human_review_required': True,
        'review_reasons': [],
    }

    if blank_rejected:
        result['review_reasons'].append('blank_or_weak_candidate_rejected')
        return result
    if detect_only:
        result['review_reasons'].append('detect_only_no_vision_run')
        return result

    vision = call_ollama_vision(model, ROW_PROMPT, full_path, timeout)
    result['vision'] = vision
    parsed = parse_row_response(vision.get('response', ''))
    parsed['prompt_compliance'] = prompt_noncompliance(vision.get('response', ''))
    result['parsed'] = parsed

    description = parsed.get('description')
    initials = parsed.get('initials')
    date_text = parsed.get('date')

    if not parsed['format_compliant'] or not description or description.lower() == '[unclear]':
        retry = call_ollama_vision(
            model, DESCRIPTION_PROMPT,
            Path(entry['crop_paths']['description']), timeout
        )
        result['field_retries']['description'] = retry
        candidate = clean_single_field(retry.get('response', ''))
        if candidate:
            description = candidate

    initials_check = validate_initials(initials or '')
    if initials_check['status'] not in {'valid', 'blank'} and entry['ink_density']['initials'] >= blank_threshold:
        retry = call_ollama_vision(
            model, INITIALS_PROMPT,
            Path(entry['crop_paths']['initials']), timeout
        )
        result['field_retries']['initials'] = retry
        candidate = clean_single_field(retry.get('response', ''))
        retry_check = validate_initials(candidate)
        if retry_check['status'] in {'valid', 'blank', 'unclear'}:
            initials = candidate
            initials_check = retry_check

    date_check = parse_date_field(date_text or '', log_number)
    if date_check['status'] in {'invalid', 'ambiguous', 'implausible'} and entry['ink_density']['date'] >= blank_threshold:
        retry = call_ollama_vision(
            model, DATE_PROMPT,
            Path(entry['crop_paths']['date']), timeout
        )
        result['field_retries']['date'] = retry
        candidate = clean_single_field(retry.get('response', ''))
        retry_check = parse_date_field(candidate, log_number)
        if retry_check['status'] in {'plausible', 'blank', 'unclear', 'incomplete'}:
            date_text = candidate
            date_check = retry_check

    result['literal_fields'] = {
        'description': description,
        'initials': initials,
        'date': date_text,
    }
    result['initials_validation'] = initials_check
    result['date_validation'] = date_check
    result['glossary_matches'] = glossary_matches(description or '', glossary)

    compliance = parsed.get('prompt_compliance', {})
    eligible = (
        vision.get('status') == 'ok'
        and parsed.get('format_compliant')
        and not compliance.get('prompt_noncompliance')
        and bool((description or '').strip())
    )
    result['eligible_for_evidence_comparison'] = eligible
    result['review_reasons'].append('vision_transcription_requires_human_review')
    if not parsed.get('format_compliant'):
        result['review_reasons'].append('row_format_noncompliance')
    if compliance.get('prompt_noncompliance'):
        result['review_reasons'].append('vision_prompt_noncompliance')
    if initials_check['status'] not in {'valid', 'blank'}:
        result['review_reasons'].append('initials_require_review')
    if date_check['status'] not in {'plausible', 'blank'}:
        result['review_reasons'].append('date_requires_review')
    return result


def locate_log_directories(root: Path, selected_logs: List[str]) -> List[Path]:
    if not root.exists() or not root.is_dir():
        raise ValueError('v1.3.1 output folder not found: {}'.format(root))
    selected = set(selected_logs)
    results = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or not re.fullmatch(r'\d{9}', child.name):
            continue
        if selected and child.name not in selected:
            continue
        results.append(child)
    return results


def write_text_report(record: Dict[str, Any], output_dir: Path) -> None:
    lines = [
        'NOVA DRL TRAVELER READER v{}'.format(VERSION),
        'Log: {}'.format(record.get('log_number')),
        'Source: {}'.format(record.get('source_path')),
        'Model: {}'.format(record.get('model')),
        'Detected anchors: {}'.format(record['anchor_detection']['detected_anchor_count']),
        'Detect only: {}'.format('YES' if record.get('detect_only') else 'NO'),
        '',
    ]
    for entry in record.get('entries', []):
        lines.extend([
            '=' * 76,
            'ENTRY {}'.format(entry['entry_index']),
            'Band: {}'.format(entry['band']),
            'Full-row ink density: {:.5f}'.format(entry['ink_density']['full_row']),
            'Blank rejected: {}'.format('YES' if entry['blank_rejected'] else 'NO'),
            'Eligible for evidence comparison: {}'.format(
                'YES' if entry['eligible_for_evidence_comparison'] else 'NO'
            ),
            'Accepted as fact: NO',
            'Review reasons: {}'.format(', '.join(entry['review_reasons'])),
            '',
            'LITERAL DESCRIPTION:',
            str(entry['literal_fields']['description'] or ''),
            'LITERAL INITIALS: {}'.format(entry['literal_fields']['initials'] or ''),
            'LITERAL DATE: {}'.format(entry['literal_fields']['date'] or ''),
            'Initials status: {}'.format(
                (entry.get('initials_validation') or {}).get('status')
            ),
            'Date status: {}'.format(
                (entry.get('date_validation') or {}).get('status')
            ),
            '',
            'CONFIRMED DRL GLOSSARY MATCHES:',
        ])
        if entry['glossary_matches']:
            for match in entry['glossary_matches']:
                lines.append(
                    '  {} -> {} ({})'.format(
                        match['matched_alias'],
                        match['canonical_term'],
                        match['meaning'],
                    )
                )
        else:
            lines.append('  None')
        lines.extend([
            '',
            'TESSERACT FULL ROW:',
            entry['tesseract']['full_row'].get('selected_text', '').rstrip(),
            '',
            'VISION RAW:',
            (entry.get('vision') or {}).get('response', '').rstrip(),
            '',
        ])
    (output_dir / 'repair_entries_v1_3_4.txt').write_text(
        '\n'.join(lines) + '\n', encoding='utf-8'
    )


def process_log(
    log_dir: Path,
    model: str,
    timeout: int,
    glossary: Dict[str, Any],
    expected_entries: Optional[int],
    blank_threshold: float,
    detect_only: bool,
) -> Dict[str, Any]:
    prior = load_json(log_dir / 'traveler_regions.json')
    if not prior:
        return {
            'reader_version': VERSION,
            'log_number': log_dir.name,
            'status': 'missing_v1_3_1_data',
            'entries': [],
        }

    repairs_region = prior.get('regions', {}).get('repairs_replacements')
    if not repairs_region:
        return {
            'reader_version': VERSION,
            'log_number': log_dir.name,
            'status': 'repairs_region_not_found',
            'entries': [],
        }

    parent_crop = Path(repairs_region.get('crop_path', ''))
    if not parent_crop.exists():
        return {
            'reader_version': VERSION,
            'log_number': log_dir.name,
            'status': 'repairs_crop_not_found',
            'entries': [],
        }

    Image, _, _, _, _ = require_pillow()
    with Image.open(parent_crop) as image:
        image.load()
        parent_image = image.copy()

    output_dir = log_dir / 'vision_extraction_v1_3_4'
    crop_dir = output_dir / 'crops'
    output_dir.mkdir(parents=True, exist_ok=True)

    detection = detect_repair_anchors(
        parent_image, expected_entries=expected_entries
    )
    centers = [int(item['y']) for item in detection['anchors']]
    bands = build_entry_bands(centers, parent_image.height)
    detection['detected_anchor_count'] = len(centers)
    detection['entry_bands'] = [list(band) for band in bands]
    detection['expected_entries'] = expected_entries
    detection['expected_entry_mismatch'] = (
        expected_entries is not None and len(centers) != expected_entries
    )

    create_detection_debug(
        parent_image, detection, bands, output_dir / 'anchor_detection_debug.png'
    )

    candidates = create_entry_crops(parent_image, bands, crop_dir)
    entries = []
    for candidate in candidates:
        print('  entry {:02d} ...'.format(candidate['entry_index']), flush=True)
        entries.append(process_entry(
            candidate, model, timeout, log_dir.name, glossary,
            blank_threshold, detect_only
        ))

    record = {
        'reader_version': VERSION,
        'processed_at_utc': now_utc(),
        'log_number': log_dir.name,
        'log_information': decode_log_number(log_dir.name),
        'source_path': prior.get('source_path'),
        'relative_path': prior.get('relative_path'),
        'traveler_kind': prior.get('traveler_kind'),
        'warranty': prior.get('warranty'),
        'status': 'ok' if centers else 'anchor_detection_failed',
        'model': model,
        'detect_only': detect_only,
        'parent_repairs_crop': str(parent_crop),
        'anchor_detection': detection,
        'entries': entries,
        'accepted_as_facts': 0,
    }

    (output_dir / 'repair_entries_v1_3_4.json').write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding='utf-8'
    )
    write_text_report(record, output_dir)
    return record


def run_serial(
    root: Path,
    selected_logs: List[str],
    model: str,
    timeout: int,
    glossary: Dict[str, Any],
    expected_entries: Optional[int],
    blank_threshold: float,
    detect_only: bool,
) -> Dict[str, Any]:
    records = []
    for log_dir in locate_log_directories(root, selected_logs):
        print('Processing log {} ...'.format(log_dir.name), flush=True)
        records.append(process_log(
            log_dir, model, timeout, glossary, expected_entries,
            blank_threshold, detect_only
        ))

    summary = {
        'reader_version': VERSION,
        'processed_at_utc': now_utc(),
        'form_output_root': str(root),
        'model': model,
        'selected_logs': selected_logs,
        'detect_only': detect_only,
        'log_count': len(records),
        'logs_ok': sum(1 for record in records if record.get('status') == 'ok'),
        'detected_entries': sum(len(record.get('entries', [])) for record in records),
        'blank_rejected_entries': sum(
            1 for record in records for entry in record.get('entries', [])
            if entry.get('blank_rejected')
        ),
        'eligible_for_evidence_comparison': sum(
            1 for record in records for entry in record.get('entries', [])
            if entry.get('eligible_for_evidence_comparison')
        ),
        'accepted_as_facts': 0,
        'records': [
            {
                'log_number': record.get('log_number'),
                'status': record.get('status'),
                'anchor_count': record.get('anchor_detection', {}).get(
                    'detected_anchor_count', 0
                ),
                'entry_count': len(record.get('entries', [])),
            }
            for record in records
        ],
    }
    (root / 'repair_entries_v1_3_4_summary.json').write_text(
        json.dumps(summary, indent=2), encoding='utf-8'
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Nova DRL Traveler Reader v1.3.4 - anchored repair entries'
    )
    parser.add_argument(
        'form_output_root',
        help='The v1.3.1 local output folder for one serial number.',
    )
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument(
        '--log', action='append', default=[],
        help='Nine-digit log number. Repeat for multiple logs.'
    )
    parser.add_argument('--all-logs', action='store_true')
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--expected-entries', type=int)
    parser.add_argument('--blank-threshold', type=float, default=0.02)
    parser.add_argument('--detect-only', action='store_true')
    parser.add_argument('--glossary')
    args = parser.parse_args()

    if not args.log and not args.all_logs:
        print('ERROR: Specify --log=######### or use --all-logs.', file=sys.stderr)
        return 2
    for value in args.log:
        if not re.fullmatch(r'\d{9}', value):
            print('ERROR: Invalid log number: {}'.format(value), file=sys.stderr)
            return 2

    try:
        require_pillow()
    except Exception as exc:
        print('ERROR: {}'.format(exc), file=sys.stderr)
        return 2

    resolved_model = args.model
    if not args.detect_only:
        resolved_model = resolve_model(args.model)
        if not resolved_model:
            print('ERROR: Ollama model not found: {}'.format(args.model), file=sys.stderr)
            return 2

    root = Path(args.form_output_root).expanduser().resolve()
    glossary_path = (
        Path(args.glossary).expanduser().resolve()
        if args.glossary
        else Path(__file__).resolve().parent.parent / 'config' / 'drl_glossary.json'
    )
    glossary = load_glossary(glossary_path)
    selected_logs = [] if args.all_logs else args.log

    try:
        summary = run_serial(
            root, selected_logs, resolved_model, args.timeout, glossary,
            args.expected_entries, args.blank_threshold, args.detect_only
        )
    except Exception as exc:
        print('ERROR: {}'.format(exc), file=sys.stderr)
        return 2

    print()
    print('Nova DRL Traveler Reader v{}'.format(VERSION))
    print('=' * 60)
    print('Model:                         {}'.format(summary['model']))
    print('Detect only:                   {}'.format('YES' if summary['detect_only'] else 'NO'))
    print('Logs found:                    {}'.format(summary['log_count']))
    print('Logs processed:                {}'.format(summary['logs_ok']))
    print('Detected repair entries:       {}'.format(summary['detected_entries']))
    print('Blank candidates rejected:     {}'.format(summary['blank_rejected_entries']))
    print('Evidence-comparison eligible:  {}'.format(summary['eligible_for_evidence_comparison']))
    print('Accepted as repair facts:      0')
    print()
    for record in summary['records']:
        print(
            '{} status={} anchors={} entries={}'.format(
                record['log_number'], record['status'],
                record['anchor_count'], record['entry_count']
            )
        )
    print()
    print('ANCHORED REPAIR-ENTRY EXTRACTION COMPLETE.')
    print('No DRL source files were changed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
