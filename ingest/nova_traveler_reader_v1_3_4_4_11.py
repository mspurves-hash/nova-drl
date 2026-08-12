#!/usr/bin/env python3
"""
Nova DRL Traveler Reader v1.3.4.4.11

Oversized-section relevance reader.

Design:
- Keep frozen v1.3.4.4.3 untouched.
- Do not detect or box individual handwriting.
- Do not use repaired/replaced marks, row starts, initials, dates, or internal
  table columns as gates.
- Capture three deliberately OVERSIZED, overlapping human-approved Traveler sections:
    identity/header, repairs/replacements, special notes.
- In transcription mode, send each entire large section to vision as one image.
- Preserve raw machine output; human review remains required before facts.
- No source mutation and no Qdrant writes.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image

VERSION = "1.3.4.4.11"
BASE_VERSION = "1.3.4.4.3"
BASE_SCRIPT_NAME = "nova_traveler_reader_v1_3_4_4.py"
OUTPUT_DIR_NAME = "vision_extraction_v1_3_4_4_11"
DEFAULT_MODEL = "minicpm-v:latest"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

# Intentionally OVERSIZED section boxes. These are not handwriting boxes and are
# not intended to match the printed section borders tightly. They overlap on purpose
# so pertinent handwritten content cannot be clipped by section-boundary estimation.
# Coordinates are fractions of an upright landscape Traveler page.
DEFAULT_RELEVANCE_MAP = {
    "identity_header": [0.000, 0.000, 0.620, 0.560],
    "repairs_replacements": [0.340, 0.050, 1.000, 0.860],
    "special_notes": [0.000, 0.300, 0.680, 1.000],
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write("\n")


def walk_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for v in value.values():
            yield from walk_values(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk_values(v)
    else:
        yield value


def find_source_image(log_dir: Path, explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None

    candidates: List[Path] = []
    for jp in sorted(log_dir.rglob("*.json")):
        try:
            data = load_json(jp)
        except Exception:
            continue
        for v in walk_values(data):
            if not isinstance(v, str):
                continue
            if not any(v.lower().endswith(ext) for ext in IMAGE_EXTENSIONS):
                continue
            p = Path(v)
            if p.exists():
                candidates.append(p)

    if not candidates:
        path_re = re.compile(r"(/mnt/drl/[^\n\r\"']+\.(?:jpg|jpeg|png|tif|tiff|bmp|webp))", re.I)
        for tp in sorted(list(log_dir.rglob("*.txt")) + list(log_dir.rglob("*.json"))):
            try:
                text = tp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for match in path_re.findall(text):
                p = Path(match)
                if p.exists():
                    candidates.append(p)

    log_number = log_dir.name
    scored: List[Tuple[int, Path]] = []
    for p in candidates:
        name = p.name.lower()
        score = 0
        if log_number in p.name:
            score += 10
        if "line card" in name:
            score += 20
        if "/mnt/drl/" in str(p):
            score += 5
        if "/output/" in str(p).lower() or "crop" in str(p).lower():
            score -= 20
        scored.append((score, p))

    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], str(x[1])), reverse=True)
    return scored[0][1]


def find_base_entries(log_dir: Path) -> Optional[Path]:
    preferred = log_dir / "vision_extraction_v1_3_4_4_3" / "repair_entries_v1_3_4_4.json"
    if preferred.exists():
        return preferred
    direct = log_dir / "repair_entries_v1_3_4_4.json"
    if direct.exists():
        return direct
    found = sorted(log_dir.rglob("repair_entries_v1_3_4_4.json"))
    return found[-1] if found else None


def base_status(entries: Optional[Dict[str, Any]]) -> Optional[str]:
    if not entries:
        return None
    value = entries.get("status")
    return str(value) if value is not None else None


def run_base_detect_only(input_root: Path, log_number: str) -> Dict[str, Any]:
    base_script = Path(__file__).with_name(BASE_SCRIPT_NAME)
    if not base_script.exists():
        return {
            "status": "base_script_missing",
            "returncode": None,
            "stdout": "",
            "stderr": f"Missing {base_script}",
        }
    cmd = [sys.executable, str(base_script), str(input_root), f"--log={log_number}", "--detect-only"]
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return {
        "status": "ok" if proc.returncode == 0 else "base_run_failed",
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def normalize_orientation(image: Image.Image) -> Tuple[Image.Image, str]:
    if image.height > image.width:
        return image.rotate(90, expand=True), "rotated_90_ccw_portrait_to_landscape"
    return image.copy(), "already_landscape"


def normalized_box_to_pixels(size: Sequence[int], box: Sequence[float]) -> List[int]:
    W, H = int(size[0]), int(size[1])
    x0, y0, x1, y1 = [float(v) for v in box]
    return [
        max(0, min(W, int(round(x0 * W)))),
        max(0, min(H, int(round(y0 * H)))),
        max(0, min(W, int(round(x1 * W)))),
        max(0, min(H, int(round(y1 * H)))),
    ]


def section_prompt(section_name: str) -> str:
    common = """You are reading ONE OVERSIZED DRL Traveler section. The entire image is the evidence area. It intentionally includes generous surrounding margin.

Rules:
- Read the entire oversized section as a whole. Do NOT crop, isolate, localize, or create smaller text boxes around handwriting.
- Do NOT use table columns, check marks, X marks, initials, dates, or row boundaries as gates for whether content matters.
- Preserve literal wording, spelling, abbreviations, quantities, numbers, punctuation, and shop slang.
- Do NOT normalize, expand, summarize, correct, or infer missing words.
- If something cannot be read reliably, preserve a short fragment in unreadable_fragments rather than guessing.
- Return ONLY one JSON object with exactly these keys:
{
  \"raw_lines\": [string, ...],
  \"unreadable_fragments\": [string, ...]
}
"""
    if section_name == "repairs_replacements":
        return common + """
Section purpose: Repairs/Replacements.
Read ALL technician-entered repair content anywhere inside this oversized section from top to bottom. Ignore unrelated neighboring printed form areas that appear only because the section intentionally has generous margins. Ignore preprinted form labels and grid lines except as visual context. Do not classify content as repaired/replaced/description/initials/date.
"""
    if section_name == "special_notes":
        return common + """
Section purpose: Special Notes.
Read all populated note content inside this oversized section, including typed, printed, stamped, and handwritten event/customer-specific notes. The heading itself may be omitted. Preserve note order.
"""
    return common + """
Section purpose: Identity/Header.
Read the populated identity/header content inside this oversized section. Preserve visible label/value associations as literal lines when possible. Do not invent missing fields.
"""


def call_ollama_vision(image_path: Path, model: str, section_name: str) -> str:
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {
        "model": model,
        "prompt": section_prompt(section_name),
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


def parse_section_response(response: str) -> Optional[Dict[str, Any]]:
    text = str(response or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return None
        try:
            value = json.loads(m.group(0))
        except Exception:
            return None
    if not isinstance(value, dict):
        return None
    lines = value.get("raw_lines")
    unreadable = value.get("unreadable_fragments")
    if not isinstance(lines, list) or not isinstance(unreadable, list):
        return None
    return {
        "raw_lines": [str(x) for x in lines if x is not None],
        "unreadable_fragments": [str(x) for x in unreadable if x is not None],
    }


def build_report(
    input_root: Path,
    log_number: str,
    detect_only: bool,
    source_arg: Optional[str],
    model: str,
    skip_base_run: bool,
) -> Dict[str, Any]:
    log_dir = input_root / log_number
    if not log_dir.exists():
        raise FileNotFoundError(f"Log directory not found: {log_dir}")

    base_run = None if skip_base_run else run_base_detect_only(input_root, log_number)
    entries_path = find_base_entries(log_dir)
    entries = load_json(entries_path) if entries_path and entries_path.exists() else None
    source_path = find_source_image(log_dir, source_arg)
    out_dir = log_dir / OUTPUT_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)

    region_records: Dict[str, Any] = {}
    transcriptions: Dict[str, Any] = {}
    orientation = None
    normalized_size = None

    if source_path:
        source = Image.open(source_path).convert("RGB")
        page, orientation = normalize_orientation(source)
        normalized_size = list(page.size)

        for name, norm_box in DEFAULT_RELEVANCE_MAP.items():
            pixel_box = normalized_box_to_pixels(page.size, norm_box)
            crop_path = out_dir / f"{name}_large_box.png"
            page.crop(tuple(pixel_box)).save(crop_path)
            region_records[name] = {
                "normalized_box": norm_box,
                "pixel_box": pixel_box,
                "crop_path": str(crop_path),
                "status": "captured_large_section",
                "internal_subboxing_used": False,
            }

            if detect_only:
                transcriptions[name] = {
                    "attempted": False,
                    "model": model,
                    "status": "not_run_detect_only",
                    "raw_response": None,
                    "parsed": None,
                }
            else:
                tx = {
                    "attempted": True,
                    "model": model,
                    "status": "not_run",
                    "raw_response": None,
                    "parsed": None,
                }
                try:
                    raw = call_ollama_vision(crop_path, model, name)
                    parsed = parse_section_response(raw)
                    tx.update({
                        "status": "ok" if parsed is not None else "response_not_json",
                        "raw_response": raw,
                        "parsed": parsed,
                    })
                except Exception as exc:
                    tx.update({"status": "vision_error", "error": str(exc)})
                transcriptions[name] = tx

    if not source_path:
        status = "review_required_source_not_found"
    elif detect_only:
        status = "review_ready_large_sections"
    elif all((transcriptions.get(name) or {}).get("status") == "ok" for name in DEFAULT_RELEVANCE_MAP):
        status = "review_ready_large_section_transcription"
    else:
        status = "review_required_section_transcription"

    return {
        "reader_version": VERSION,
        "base_reader_version": BASE_VERSION,
        "log_number": log_number,
        "detect_only": detect_only,
        "model": model,
        "base_status": base_status(entries),
        "base_run": base_run,
        "source_traveler": str(source_path) if source_path else None,
        "source_modified": False,
        "orientation_normalization": orientation,
        "normalized_page_size": normalized_size,
        "human_defined_large_section_map": DEFAULT_RELEVANCE_MAP,
        "relevance_regions": region_records,
        "section_transcriptions": transcriptions,
        "repair_content_policy": "oversized_section_capture_then_whole_section_read_no_internal_subboxing",
        "automatic_fact_acceptance": False,
        "accepted_as_repair_fact_count": 0,
        "qdrant_entry_created": False,
        "status": status,
    }


def render_text_report(report: Dict[str, Any]) -> str:
    regions = report.get("relevance_regions") or {}
    txs = report.get("section_transcriptions") or {}
    lines: List[str] = []
    lines.append(f"# Nova DRL Traveler Reader v{VERSION}")
    lines.append("")
    lines.append(f"Log:                           {report.get('log_number')}")
    lines.append("Detection mode:                oversized section + whole-section reading")
    lines.append(f"Detect only:                   {'YES' if report.get('detect_only') else 'NO'}")
    lines.append(f"Frozen base version:           {BASE_VERSION}")
    lines.append(f"Frozen base status:            {report.get('base_status')}")
    lines.append(f"Source traveler:               {'FOUND' if report.get('source_traveler') else 'NOT FOUND'}")
    lines.append(f"Page orientation:              {report.get('orientation_normalization')}")
    lines.append("Internal text boxes:           NOT USED")
    lines.append("Internal table semantics:      NOT USED")
    lines.append("Repaired/Replaced marks:       NOT USED AS GATES")
    lines.append("Accepted as repair facts:      0")
    lines.append("Qdrant entries created:        0")
    lines.append("")
    lines.append("OVERSIZED RELEVANCE SECTIONS")
    lines.append("------------------------")
    for name in ("identity_header", "repairs_replacements", "special_notes"):
        r = regions.get(name) or {}
        lines.append(f"{name}: {r.get('status', 'not_captured')}")
        if r:
            lines.append(f"  normalized: {r.get('normalized_box')}")
            lines.append(f"  pixels:     {r.get('pixel_box')}")
            lines.append(f"  crop:       {r.get('crop_path')}")
            lines.append("  internal sub-boxes: NO")
    lines.append("")
    lines.append("SECTION CONTENT")
    lines.append("---------------")
    if report.get("detect_only"):
        lines.append("Not transcribed in detect-only mode.")
    else:
        for name in ("identity_header", "repairs_replacements", "special_notes"):
            tx = txs.get(name) or {}
            lines.append("")
            lines.append(f"[{name}] status={tx.get('status')}")
            parsed = tx.get("parsed") or {}
            raw_lines = parsed.get("raw_lines") or []
            unreadable = parsed.get("unreadable_fragments") or []
            if raw_lines:
                for i, text in enumerate(raw_lines, 1):
                    lines.append(f"  {i:02d}. {text}")
            elif tx.get("status") == "ok":
                lines.append("  No content lines returned.")
            if unreadable:
                lines.append("  Unreadable fragments:")
                for text in unreadable:
                    lines.append(f"  - {text}")
    lines.append("")
    lines.append("POLICY")
    lines.append("------")
    lines.append("Oversized highlighted Traveler sections define evidence regions: YES")
    lines.append("Handwriting/text internally boxed before reading: NO")
    lines.append("Printed-grid reconstruction required: NO")
    lines.append("Repaired/Replaced/Description/Initials/Date interpreted separately: NO")
    lines.append("Repaired/Replaced mark required for content preservation: NO")
    lines.append("Machine transcription accepted as repair fact: NO")
    lines.append("Human review required before repair actions: YES")
    lines.append("Approved wording automatically modified: NO")
    lines.append("Final-test/shipping areas used as Traveler repair knowledge: NO")
    lines.append("No DRL source files were changed.")
    lines.append("No Qdrant entry created.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Nova DRL Traveler Reader v{VERSION}")
    parser.add_argument("input_root", help="Traveler Reader v1.3.1 serial output directory")
    parser.add_argument("--log", required=True, help="DRL log number")
    parser.add_argument("--detect-only", action="store_true", help="Capture the large relevance sections only; do not run vision")
    parser.add_argument("--source", help="Optional explicit original Traveler image path")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama vision model (default: {DEFAULT_MODEL})")
    parser.add_argument("--skip-base-run", action="store_true", help="Do not rerun frozen base detect-only pass")
    args = parser.parse_args()

    input_root = Path(args.input_root)
    report = build_report(input_root, args.log, args.detect_only, args.source, args.model, args.skip_base_run)
    out_dir = input_root / args.log / OUTPUT_DIR_NAME
    save_json(out_dir / "traveler_relevance_review_v1_3_4_4_11.json", report)
    text = render_text_report(report)
    (out_dir / "traveler_relevance_review_v1_3_4_4_11.txt").write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"Reports: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
