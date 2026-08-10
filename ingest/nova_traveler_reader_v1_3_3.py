#!/usr/bin/env python3
"""
Nova DRL Traveler Reader v1.3.3
================================

Region-Specific Vision Extraction
---------------------------------

This release refines the vision stage proven in v1.3.2. It divides the broad
repairs/replacements and special-notes crops created by v1.3.1 into smaller,
overlapping subregions before sending them to a local Ollama vision model.

Goals
-----
- Reduce merged repair lines and confused dates/initials.
- Separate static customer requirements from unit-specific technical notes.
- Preserve every crop, prompt, Tesseract pass, vision response, and coordinate.
- Flag model responses that ignore the literal-transcription instructions.
- Keep all DRL source files read-only.

This release does NOT perform evidence fusion and does NOT write to Qdrant.
"""

import argparse
import base64
import json
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

VERSION = '1.3.3'
DEFAULT_MODEL = 'minicpm-v:latest'

# Boxes are fractions of the already-created v1.3.1 region crop.
# Overlap is intentional so writing near a band boundary is not lost.
REPAIR_SUBREGIONS = [
    {
        'name': 'repair_rows_01',
        'box': (0.00, 0.08, 1.00, 0.38),
        'description': 'Upper repair rows, including initials and date columns',
    },
    {
        'name': 'repair_rows_02',
        'box': (0.00, 0.28, 1.00, 0.60),
        'description': 'Upper-middle repair rows, including initials and dates',
    },
    {
        'name': 'repair_rows_03',
        'box': (0.00, 0.50, 1.00, 0.82),
        'description': 'Lower-middle repair rows, including initials and dates',
    },
    {
        'name': 'repair_rows_04',
        'box': (0.00, 0.72, 1.00, 1.00),
        'description': 'Bottom repair rows, including initials and dates',
    },
]

SPECIAL_NOTES_SUBREGIONS = [
    {
        'name': 'special_customer_requirements',
        'box': (0.00, 0.00, 1.00, 0.48),
        'description': 'Customer/template requirements and upper notes',
    },
    {
        'name': 'special_unit_notes',
        'box': (0.00, 0.34, 1.00, 0.76),
        'description': 'Unit-specific and line-item technical notes',
    },
    {
        'name': 'special_handwritten_bottom',
        'box': (0.00, 0.62, 1.00, 1.00),
        'description': 'Bottom handwritten and technician-entered notes',
    },
]

REPAIR_PROMPT = """You are transcribing one cropped band from the repairs/replacements table of a Direct Repair Laboratories repair traveler.

Transcribe only the filled-in repair entries visibly present in this crop.

Output rules:
1. Return one visible repair entry per line.
2. Use this format when visible: DESCRIPTION | INITIALS | DATE
3. If initials or date are absent, leave that position blank.
4. If a line is cut off by the crop boundary, prefix it with [partial].
5. If text is not confidently readable, write [unclear].
6. Preserve part names, axis names, error codes, numbers, initials, and dates exactly as visible.
7. Do not summarize, explain, interpret, correct, or infer.
8. Do not output headings, bullets, table descriptions, commentary, or Markdown.
9. Ignore static preprinted form labels.
10. Return plain text only.

Crop: {subregion}
"""

SPECIAL_PROMPT = """You are transcribing one cropped band from the Special Notes area of a Direct Repair Laboratories repair traveler.

Transcribe all filled-in content visibly present in this crop, whether typed or handwritten.

Output rules:
1. Return one complete note per line.
2. Ignore static form labels and empty checkboxes.
3. Preserve customer requirements, technical notes, error codes, numbers, initials, and dates exactly as visible.
4. If a line is cut off by the crop boundary, prefix it with [partial].
5. If text is not confidently readable, write [unclear].
6. Do not summarize, explain, interpret, correct, or infer.
7. Do not output headings, bullets, commentary, or Markdown.
8. Return plain text only.

Crop: {subregion}
"""

NONCOMPLIANCE_PATTERNS = [
    r'(?im)^\s*title\s*:',
    r'(?im)^\s*subtitle\s*:',
    r'(?im)^\s*body\s+text\s*:',
    r'(?im)^\s*table\s+columns?\s*:',
    r'(?im)^\s*note\s*:',
    r'(?im)^\s*summary\s*:',
    r'(?i)the\s+image\s+(shows|contains|depicts)',
    r'(?i)the\s+table\s+(shows|includes|contains)',
    r'(?i)this\s+(appears|seems)\s+to',
    r'(?i)could\s+refer\s+to',
    r'(?i)it\s+appears\s+that',
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
        from PIL import Image, ImageOps, ImageEnhance, ImageFilter
        return Image, ImageOps, ImageEnhance, ImageFilter
    except Exception as exc:
        raise RuntimeError(
            'Pillow is required. Install with: sudo apt install python3-pil'
        ) from exc


def fractional_box_to_pixels(
    box: Sequence[float], width: int, height: int
) -> Tuple[int, int, int, int]:
    left = max(0, min(width, int(round(box[0] * width))))
    top = max(0, min(height, int(round(box[1] * height))))
    right = max(left + 1, min(width, int(round(box[2] * width))))
    bottom = max(top + 1, min(height, int(round(box[3] * height))))
    return left, top, right, bottom


def preprocess_subcrop(image):
    Image, ImageOps, ImageEnhance, ImageFilter = require_pillow()
    out = ImageOps.autocontrast(image.convert('L'))
    out = out.resize(
        (max(1, out.width * 2), max(1, out.height * 2)),
        Image.Resampling.LANCZOS,
    )
    out = ImageEnhance.Contrast(out).enhance(1.40)
    out = ImageEnhance.Sharpness(out).enhance(1.30)
    try:
        out = out.filter(ImageFilter.MedianFilter(size=3))
    except Exception:
        pass
    return out


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
    alnum = sum(1 for c in text if c.isalnum())
    words = re.findall(r'[A-Za-z0-9][A-Za-z0-9#./+\-]{1,}', text)
    short_words = sum(1 for word in words if len(word) <= 2)
    return alnum + 4 * len(words) - 2 * short_words


def tesseract_subcrop(path: Path) -> Dict[str, Any]:
    if not shutil.which('tesseract'):
        return {
            'status': 'dependency_missing',
            'selected_psm': None,
            'selected_text': '',
            'passes': [],
        }

    passes = []
    for psm in (6, 11, 12):
        result = run_command(
            ['tesseract', str(path), 'stdout', '--psm', str(psm)],
            timeout=180,
        )
        text = result['stdout'] if result['returncode'] == 0 else ''
        passes.append(
            {
                'psm': psm,
                'status': 'ok' if result['returncode'] == 0 else 'error',
                'text': text,
                'score': ocr_quality_score(text),
                'stderr': result['stderr'].strip(),
            }
        )

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
    payload = json.dumps(
        {
            'model': model,
            'prompt': prompt,
            'images': [
                base64.b64encode(image_path.read_bytes()).decode('ascii')
            ],
            'stream': False,
            'options': {'temperature': 0},
        }
    ).encode('utf-8')

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
            'total_duration': body.get('total_duration'),
            'load_duration': body.get('load_duration'),
            'prompt_eval_count': body.get('prompt_eval_count'),
            'eval_count': body.get('eval_count'),
            'warning': None,
        }
    except Exception as exc:
        return {
            'status': 'error',
            'response': '',
            'warning': str(exc),
        }


def prompt_compliance(text: str) -> Dict[str, Any]:
    matches = []
    for pattern in NONCOMPLIANCE_PATTERNS:
        if re.search(pattern, text or ''):
            matches.append(pattern)

    line_count = len([line for line in (text or '').splitlines() if line.strip()])
    return {
        'prompt_noncompliance': bool(matches),
        'matched_patterns': matches,
        'nonempty_line_count': line_count,
        'eligible_for_fusion_review': bool(text.strip()) and not bool(matches),
    }


def create_subcrops(
    source_crop: Path,
    subregions: List[Dict[str, Any]],
    output_dir: Path,
) -> List[Dict[str, Any]]:
    Image, _, _, _ = require_pillow()
    output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    with Image.open(source_crop) as image:
        image.load()
        width, height = image.size
        for spec in subregions:
            pixel_box = fractional_box_to_pixels(spec['box'], width, height)
            processed = preprocess_subcrop(image.crop(pixel_box))
            crop_path = output_dir / (spec['name'] + '.png')
            processed.save(crop_path)
            results.append(
                {
                    'name': spec['name'],
                    'description': spec['description'],
                    'fractional_box': spec['box'],
                    'pixel_box_within_parent_region': pixel_box,
                    'parent_region_crop': str(source_crop),
                    'subcrop_path': str(crop_path),
                }
            )
    return results


def process_subregion(
    subcrop: Dict[str, Any],
    prompt_template: str,
    model: str,
    timeout: int,
) -> Dict[str, Any]:
    crop_path = Path(subcrop['subcrop_path'])
    prompt = prompt_template.format(subregion=subcrop['name'])
    tesseract = tesseract_subcrop(crop_path)
    vision = call_ollama_vision(model, prompt, crop_path, timeout)
    compliance = prompt_compliance(vision.get('response', ''))

    return {
        **subcrop,
        'model': model,
        'prompt': prompt,
        'tesseract': tesseract,
        'vision': vision,
        'compliance': compliance,
    }


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


def process_log(log_dir: Path, model: str, timeout: int) -> Dict[str, Any]:
    prior = load_json(log_dir / 'traveler_regions.json')
    if not prior:
        return {
            'reader_version': VERSION,
            'log_number': log_dir.name,
            'status': 'missing_v1_3_1_data',
            'sections': {},
        }

    v133_dir = log_dir / 'vision_extraction_v1_3_3'
    crop_dir = v133_dir / 'crops'
    v133_dir.mkdir(parents=True, exist_ok=True)

    section_specs = [
        (
            'repairs_replacements',
            REPAIR_SUBREGIONS,
            REPAIR_PROMPT,
        ),
        (
            'special_notes',
            SPECIAL_NOTES_SUBREGIONS,
            SPECIAL_PROMPT,
        ),
    ]

    sections = {}
    for parent_region, subregions, prompt_template in section_specs:
        prior_region = prior.get('regions', {}).get(parent_region)
        if not prior_region:
            sections[parent_region] = {
                'status': 'parent_region_not_found',
                'subregions': [],
            }
            continue

        parent_crop = Path(prior_region.get('crop_path', ''))
        if not parent_crop.exists():
            sections[parent_region] = {
                'status': 'parent_crop_not_found',
                'parent_crop': str(parent_crop),
                'subregions': [],
            }
            continue

        subcrop_specs = create_subcrops(
            parent_crop,
            subregions,
            crop_dir / parent_region,
        )
        processed = []
        for index, subcrop in enumerate(subcrop_specs, 1):
            print(
                '  {} {}/{} ...'.format(
                    subcrop['name'], index, len(subcrop_specs)
                ),
                flush=True,
            )
            processed.append(
                process_subregion(
                    subcrop,
                    prompt_template,
                    model,
                    timeout,
                )
            )

        sections[parent_region] = {
            'status': 'ok',
            'parent_crop': str(parent_crop),
            'parent_region_pixel_box': prior_region.get('pixel_box'),
            'subregions': processed,
        }

    record = {
        'reader_version': VERSION,
        'processed_at_utc': now_utc(),
        'log_number': log_dir.name,
        'source_path': prior.get('source_path'),
        'relative_path': prior.get('relative_path'),
        'traveler_kind': prior.get('traveler_kind'),
        'warranty': prior.get('warranty'),
        'status': 'ok',
        'model': model,
        'sections': sections,
    }

    (v133_dir / 'vision_extraction_v1_3_3.json').write_text(
        json.dumps(record, indent=2, ensure_ascii=False),
        encoding='utf-8',
    )

    lines = [
        'NOVA DRL TRAVELER READER v{}'.format(VERSION),
        'Log: {}'.format(record.get('log_number')),
        'Source: {}'.format(record.get('source_path')),
        'Model: {}'.format(model),
        '',
    ]

    for section_name, section in sections.items():
        lines.extend(['#' * 76, section_name.upper(), ''])
        for item in section.get('subregions', []):
            compliance = item.get('compliance', {})
            lines.extend(
                [
                    '=' * 72,
                    item['name'].upper(),
                    item['description'],
                    'Crop: {}'.format(item['subcrop_path']),
                    'Prompt noncompliance: {}'.format(
                        'YES'
                        if compliance.get('prompt_noncompliance')
                        else 'NO'
                    ),
                    'Eligible for fusion review: {}'.format(
                        'YES'
                        if compliance.get('eligible_for_fusion_review')
                        else 'NO'
                    ),
                    '-' * 72,
                    'TESSERACT:',
                    item.get('tesseract', {}).get(
                        'selected_text', ''
                    ).rstrip(),
                    '',
                    'VISION:',
                    item.get('vision', {}).get('response', '').rstrip(),
                    '',
                ]
            )

    (v133_dir / 'vision_extraction_v1_3_3.txt').write_text(
        '\n'.join(lines) + '\n', encoding='utf-8'
    )
    return record


def run_serial(
    root: Path, selected_logs: List[str], model: str, timeout: int
) -> Dict[str, Any]:
    log_dirs = locate_log_directories(root, selected_logs)
    records = []

    for log_dir in log_dirs:
        print('Processing log {} ...'.format(log_dir.name), flush=True)
        records.append(process_log(log_dir, model, timeout))

    subregion_count = 0
    successful_vision = 0
    noncompliant_count = 0
    eligible_count = 0

    for record in records:
        for section in record.get('sections', {}).values():
            for item in section.get('subregions', []):
                subregion_count += 1
                if item.get('vision', {}).get('status') == 'ok':
                    successful_vision += 1
                compliance = item.get('compliance', {})
                if compliance.get('prompt_noncompliance'):
                    noncompliant_count += 1
                if compliance.get('eligible_for_fusion_review'):
                    eligible_count += 1

    summary = {
        'reader_version': VERSION,
        'processed_at_utc': now_utc(),
        'form_output_root': str(root),
        'model': model,
        'selected_logs': selected_logs,
        'log_count': len(records),
        'logs_ok': sum(
            1 for record in records if record.get('status') == 'ok'
        ),
        'subregion_count': subregion_count,
        'successful_vision_count': successful_vision,
        'prompt_noncompliance_count': noncompliant_count,
        'eligible_for_fusion_review_count': eligible_count,
        'records': [
            {
                'log_number': record.get('log_number'),
                'status': record.get('status'),
            }
            for record in records
        ],
    }

    (root / 'vision_extraction_v1_3_3_summary.json').write_text(
        json.dumps(summary, indent=2), encoding='utf-8'
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            'Nova DRL Traveler Reader v1.3.3 - region-specific vision extraction'
        )
    )
    parser.add_argument(
        'form_output_root',
        help='The v1.3.1 local output folder for one serial number.',
    )
    parser.add_argument(
        '--model', default=DEFAULT_MODEL, help='Ollama vision model.'
    )
    parser.add_argument(
        '--log',
        action='append',
        default=[],
        help='Nine-digit log number. Repeat for multiple logs.',
    )
    parser.add_argument(
        '--all-logs',
        action='store_true',
        help='Process every v1.3.1 log folder. Use cautiously.',
    )
    parser.add_argument(
        '--timeout', type=int, default=300, help='Seconds per vision request.'
    )
    args = parser.parse_args()

    if not args.log and not args.all_logs:
        print(
            'ERROR: Specify --log=######### or use --all-logs.',
            file=sys.stderr,
        )
        return 2

    for log_number in args.log:
        if not re.fullmatch(r'\d{9}', log_number):
            print(
                'ERROR: Invalid log number: {}'.format(log_number),
                file=sys.stderr,
            )
            return 2

    try:
        require_pillow()
    except Exception as exc:
        print('ERROR: {}'.format(exc), file=sys.stderr)
        return 2

    resolved_model = resolve_model(args.model)
    if not resolved_model:
        print(
            'ERROR: Ollama model not found: {}'.format(args.model),
            file=sys.stderr,
        )
        print('Run: ollama list', file=sys.stderr)
        return 2

    root = Path(args.form_output_root).expanduser().resolve()
    selected_logs = [] if args.all_logs else args.log

    try:
        summary = run_serial(
            root, selected_logs, resolved_model, args.timeout
        )
    except Exception as exc:
        print('ERROR: {}'.format(exc), file=sys.stderr)
        return 2

    print()
    print('Nova DRL Traveler Reader v{}'.format(VERSION))
    print('=' * 58)
    print('Model:                    {}'.format(summary['model']))
    print('Logs found:               {}'.format(summary['log_count']))
    print('Logs processed:           {}'.format(summary['logs_ok']))
    print('Subregions:               {}'.format(summary['subregion_count']))
    print(
        'Successful vision calls: {}'.format(
            summary['successful_vision_count']
        )
    )
    print(
        'Prompt noncompliance:     {}'.format(
            summary['prompt_noncompliance_count']
        )
    )
    print(
        'Eligible for fusion review: {}'.format(
            summary['eligible_for_fusion_review_count']
        )
    )
    print()
    print('REGION-SPECIFIC VISION EXTRACTION COMPLETE.')
    print('No DRL source files were changed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
