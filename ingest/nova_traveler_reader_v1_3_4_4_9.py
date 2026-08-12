#!/usr/bin/env python3
"""
Nova DRL Traveler Reader v1.3.4.4.9

Human-defined Traveler relevance-map reader.

Design change from experimental .4-.8 passes:
  * do NOT detect the Repairs/Replacements outer box from internal grid lines
  * do NOT use handwriting extent, row starts, repaired/replaced marks, initials,
    dates, or semantic columns to define the evidence region
  * use the human-approved normalized relevance map for the Traveler page
  * capture complete relevance boxes with a small safety margin
  * detect-only saves the boxes and stops
  * transcription mode reads literal handwriting from the Repairs/Replacements
    relevance box only; human review remains required

Frozen v1.3.4.4.3 remains unchanged.
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

VERSION = "1.3.4.4.9"
BASE_VERSION = "1.3.4.4.3"
BASE_SCRIPT_NAME = "nova_traveler_reader_v1_3_4_4.py"
OUTPUT_DIR_NAME = "vision_extraction_v1_3_4_4_9"
DEFAULT_MODEL = "minicpm-v:latest"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

# Human-approved Traveler relevance map supplied 2026-08-12.
# Coordinates are fractions of an upright landscape Traveler page.
# The boxes intentionally include a small amount of surrounding whitespace so
# no handwritten content at the printed border is clipped.
DEFAULT_RELEVANCE_MAP = {
    "identity_header": [0.060, 0.050, 0.465, 0.335],
    "repairs_replacements": [0.455, 0.125, 0.845, 0.645],
    "special_notes": [0.060, 0.485, 0.465, 0.910],
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
    v = entries.get("status")
    return str(v) if v is not None else None


def run_base_detect_only(input_root: Path, log_number: str) -> Dict[str, Any]:
    base_script = Path(__file__).with_name(BASE_SCRIPT_NAME)
    if not base_script.exists():
        return {"status": "base_script_missing", "returncode": None, "stdout": "", "stderr": f"Missing {base_script}"}
    cmd = [sys.executable, str(base_script), str(input_root), f"--log={log_number}", "--detect-only"]
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return {"status": "ok" if proc.returncode == 0 else "base_run_failed", "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def normalize_orientation(image: Image.Image) -> Tuple[Image.Image, str]:
    """Normalize a Traveler page to landscape orientation only.

    Production scans are normally already landscape. A portrait input is
    rotated 90 degrees counter-clockwise. No perspective or content inference
    is performed here.
    """
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


def handwriting_prompt() -> str:
    return """You are transcribing the handwritten technician content inside ONE DRL Traveler Repairs/Replacements relevance box.

Return ONLY one JSON object with exactly these keys:
{
  "handwritten_lines": [string, ...],
  "unreadable_fragments": [string, ...]
}

Rules:
- Read ONLY handwriting. Ignore every printed form label, printed word, table line, checkbox outline, and other preprinted text.
- Transcribe the handwriting literally from top to bottom. Preserve spelling, abbreviations, punctuation, numbers, quantities, and shop slang exactly as seen.
- Do NOT correct, normalize, expand, summarize, interpret, or infer missing words.
- Do NOT classify anything as repaired, replaced, description, initials, or date.
- Do NOT use X/check marks as evidence gates.
- Standalone X/check marks may be omitted unless inseparable from handwritten text.
- If a handwritten fragment cannot be read reliably, put a short literal fragment in unreadable_fragments rather than guessing.
- Do not add commentary outside the JSON object.
"""


def call_ollama_vision(image_path: Path, model: str) -> str:
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {"model": model, "prompt": handwriting_prompt(), "images": [encoded], "stream": False, "options": {"temperature": 0}}
    request = urllib.request.Request(OLLAMA_URL, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=300) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data.get("response") or "").strip()


def parse_handwriting_response(response: str) -> Optional[Dict[str, Any]]:
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
    lines = value.get("handwritten_lines")
    unreadable = value.get("unreadable_fragments")
    if not isinstance(lines, list) or not isinstance(unreadable, list):
        return None
    return {"handwritten_lines": [str(x) for x in lines if x is not None], "unreadable_fragments": [str(x) for x in unreadable if x is not None]}


def build_report(input_root: Path, log_number: str, detect_only: bool, source_arg: Optional[str], model: str, skip_base_run: bool) -> Dict[str, Any]:
    log_dir = input_root / log_number
    if not log_dir.exists():
        raise FileNotFoundError(f"Log directory not found: {log_dir}")

    base_run = None if skip_base_run else run_base_detect_only(input_root, log_number)
    entries_path = find_base_entries(log_dir)
    entries = load_json(entries_path) if entries_path and entries_path.exists() else None
    source_path = find_source_image(log_dir, source_arg)
    out_dir = log_dir / OUTPUT_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)

    tx: Dict[str, Any] = {"attempted": False, "model": model, "status": "not_run_detect_only" if detect_only else "not_run_source_unavailable", "raw_response": None, "parsed": None}
    region_records: Dict[str, Any] = {}
    orientation = None
    normalized_size = None

    if source_path:
        source = Image.open(source_path).convert("RGB")
        page, orientation = normalize_orientation(source)
        normalized_size = list(page.size)
        for name, norm_box in DEFAULT_RELEVANCE_MAP.items():
            pixel_box = normalized_box_to_pixels(page.size, norm_box)
            crop_path = out_dir / f"{name}_relevance_box.png"
            page.crop(tuple(pixel_box)).save(crop_path)
            region_records[name] = {"normalized_box": norm_box, "pixel_box": pixel_box, "crop_path": str(crop_path), "status": "captured"}
        if not detect_only:
            tx["attempted"] = True
            repair_crop = Path(region_records["repairs_replacements"]["crop_path"])
            try:
                raw = call_ollama_vision(repair_crop, model)
                parsed = parse_handwriting_response(raw)
                tx.update({"status": "ok" if parsed is not None else "response_not_json", "raw_response": raw, "parsed": parsed})
            except Exception as exc:
                tx.update({"status": "vision_error", "error": str(exc)})

    source_ok = bool(source_path)
    if not source_ok:
        status = "review_required_source_not_found"
    elif detect_only:
        status = "review_ready_relevance_boxes"
    elif tx.get("status") == "ok":
        status = "review_ready_handwriting_transcription"
    else:
        status = "review_required_handwriting_transcription"

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
        "human_defined_relevance_map": DEFAULT_RELEVANCE_MAP,
        "relevance_regions": region_records,
        "handwriting_transcription": tx,
        "repair_content_policy": "human_defined_relevance_box_then_literal_handwriting_no_internal_form_semantics",
        "automatic_fact_acceptance": False,
        "accepted_as_repair_fact_count": 0,
        "qdrant_entry_created": False,
        "status": status,
    }


def render_text_report(report: Dict[str, Any]) -> str:
    tx = report.get("handwriting_transcription") or {}
    parsed = tx.get("parsed") or {}
    regions = report.get("relevance_regions") or {}
    lines: List[str] = []
    lines.append(f"# Nova DRL Traveler Reader v{VERSION}")
    lines.append("")
    lines.append(f"Log:                           {report.get('log_number')}")
    lines.append("Detection mode:                human-defined relevance boxes")
    lines.append(f"Detect only:                   {'YES' if report.get('detect_only') else 'NO'}")
    lines.append(f"Frozen base version:           {BASE_VERSION}")
    lines.append(f"Frozen base status:            {report.get('base_status')}")
    lines.append(f"Source traveler:               {'FOUND' if report.get('source_traveler') else 'NOT FOUND'}")
    lines.append(f"Page orientation:              {report.get('orientation_normalization')}")
    lines.append(f"Repairs relevance box:         {regions.get('repairs_replacements', {}).get('status', 'not_captured')}")
    lines.append("Internal semantic columns:     NOT USED")
    lines.append("Repaired/Replaced marks:       NOT USED AS GATES")
    lines.append(f"Handwriting transcription:     {tx.get('status')}")
    lines.append("Accepted as repair facts:      0")
    lines.append("Qdrant entries created:        0")
    lines.append("")
    lines.append("RELEVANCE BOXES")
    lines.append("---------------")
    for name in ("identity_header", "repairs_replacements", "special_notes"):
        r = regions.get(name) or {}
        lines.append(f"{name}: {r.get('status', 'not_captured')}")
        if r:
            lines.append(f"  normalized: {r.get('normalized_box')}")
            lines.append(f"  pixels:     {r.get('pixel_box')}")
            lines.append(f"  crop:       {r.get('crop_path')}")
    lines.append("")
    lines.append("HANDWRITTEN REPAIR CONTENT")
    lines.append("--------------------------")
    if report.get("detect_only"):
        lines.append("Not transcribed in detect-only mode.")
    elif tx.get("status") == "ok":
        hlines = parsed.get("handwritten_lines") or []
        unread = parsed.get("unreadable_fragments") or []
        if hlines:
            for i, text in enumerate(hlines, 1):
                lines.append(f"{i:02d}. {text}")
        else:
            lines.append("No handwritten lines returned.")
        if unread:
            lines.append("")
            lines.append("Unreadable fragments preserved for review:")
            for text in unread:
                lines.append(f"- {text}")
    else:
        lines.append(f"Transcription unavailable: {tx.get('status')}")
    lines.append("")
    lines.append("POLICY")
    lines.append("------")
    lines.append("Human-highlighted Traveler relevance map defines evidence regions: YES")
    lines.append("Printed-grid/outer-box inference required: NO")
    lines.append("Repaired/Replaced/Description/Initials/Date fields interpreted separately: NO")
    lines.append("Repaired/Replaced mark required for handwriting preservation: NO")
    lines.append("Printed form text accepted as repair fact: NO")
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
    parser.add_argument("--detect-only", action="store_true", help="Capture human-defined relevance boxes; do not run vision")
    parser.add_argument("--source", help="Optional explicit original Traveler image path")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama vision model (default: {DEFAULT_MODEL})")
    parser.add_argument("--skip-base-run", action="store_true", help="Do not rerun frozen base detect-only pass")
    args = parser.parse_args()

    input_root = Path(args.input_root)
    report = build_report(input_root, args.log, args.detect_only, args.source, args.model, args.skip_base_run)
    out_dir = input_root / args.log / OUTPUT_DIR_NAME
    save_json(out_dir / "traveler_relevance_review_v1_3_4_4_9.json", report)
    text = render_text_report(report)
    (out_dir / "traveler_relevance_review_v1_3_4_4_9.txt").write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"Reports: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
