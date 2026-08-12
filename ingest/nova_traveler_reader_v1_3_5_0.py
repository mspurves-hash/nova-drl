#!/usr/bin/env python3
"""
Nova DRL Whole Traveler Evidence Reader v1.3.5.0

Architecture change from the v1.3.4.x geometry experiments:
- The ORIGINAL FULL Traveler image is the evidence object.
- No relevance boxes, repair-table crops, row starts, internal columns, or mark gates.
- A full-page orientation-normalized derivative is created only for machine reading.
- In transcription mode the vision model reads the entire Traveler page and returns
  literal visible text in visual reading order, including printed, typed, stamped,
  and handwritten content.
- No content is promoted to a repair fact here. Relevance, template repetition,
  parts/actions, terminology, and diagnostic meaning are downstream/corpus tasks.
- Raw machine response is preserved.
- Source files are never modified and Qdrant writes are disabled.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageOps

VERSION = "1.3.5.0"
OUTPUT_DIR_NAME = "whole_traveler_evidence_v1_3_5_0"
DEFAULT_MODEL = "minicpm-v:latest"
OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write("\n")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for v in value.values():
            yield from walk_values(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk_values(v)
    else:
        yield value


def is_source_candidate(path: Path) -> bool:
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return False
    lower = str(path).lower()
    if "/output/" in lower:
        return False
    if any(token in path.name.lower() for token in ("crop", "debug", "enhanced", "entry_")):
        return False
    return True


def find_source_image(log_dir: Path, explicit: Optional[str] = None) -> Optional[Path]:
    """Resolve the original full Traveler image without relying on a crop."""
    if explicit:
        p = Path(explicit)
        return p if p.exists() and is_source_candidate(p) else None

    candidates: List[Path] = []

    # Prefer explicit paths already preserved in earlier machine-readable artifacts.
    for jp in sorted(log_dir.rglob("*.json")):
        try:
            data = load_json(jp)
        except Exception:
            continue
        for v in walk_values(data):
            if not isinstance(v, str):
                continue
            p = Path(v)
            if p.exists() and is_source_candidate(p):
                candidates.append(p)

    # Fallback for paths embedded in text reports.
    if not candidates:
        path_re = re.compile(r"(/mnt/drl/[^\n\r\"']+\.(?:jpg|jpeg|png|tif|tiff|bmp|webp))", re.I)
        for tp in sorted(list(log_dir.rglob("*.txt")) + list(log_dir.rglob("*.json"))):
            try:
                text = tp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for match in path_re.findall(text):
                p = Path(match)
                if p.exists() and is_source_candidate(p):
                    candidates.append(p)

    # Score for the primary Traveler rather than receiving/packaging photos.
    log_number = log_dir.name
    scored: List[Tuple[int, Path]] = []
    seen = set()
    for p in candidates:
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        name = p.name.lower()
        score = 0
        if log_number in p.name:
            score += 25
        if "line card" in name:
            score += 50
        if "warranty" in name or "original" in name:
            score += 10
        if "/mnt/drl/" in str(p):
            score += 10
        if "receiving" in name or "shipment" in name or "packaging" in name:
            score -= 50
        scored.append((score, p))

    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], str(x[1])), reverse=True)
    return scored[0][1]


def normalize_full_page(image: Image.Image) -> Tuple[Image.Image, str]:
    """EXIF-normalize and place the complete Traveler in landscape orientation."""
    page = ImageOps.exif_transpose(image).convert("RGB")
    if page.height > page.width:
        page = page.rotate(90, expand=True)
        return page, "rotated_90_ccw_portrait_to_landscape"
    return page, "already_landscape"


def whole_page_prompt() -> str:
    return """You are transcribing ONE COMPLETE Direct Repair Laboratories Traveler page as raw evidence.

CRITICAL RULES:
- Read the ENTIRE Traveler image. Do not crop, isolate, localize, box, or focus on only one form section.
- Do not decide what is important, relevant, repeated boilerplate, garbage, a repair action, a part, a diagnosis, testing, or administration. Those decisions happen later after many Travelers are compared.
- Preserve visible printed, typed, stamped, and handwritten text. Repeated printed form text is intentionally included.
- Preserve literal wording, spelling, abbreviations, quantities, numbers, punctuation, and DRL shop slang. Do not normalize, expand, correct, summarize, or infer missing words.
- Follow the page in natural visual reading order as well as possible. Keep separate visible entries as separate raw_lines.
- If text cannot be read reliably, use unreadable_fragments rather than guessing.
- Do not infer a quantity from words such as several, many, or some.
- Return ONLY one JSON object with exactly these keys:
{
  \"raw_lines\": [string, ...],
  \"unreadable_fragments\": [string, ...]
}
"""


def call_ollama_vision(image_path: Path, model: str) -> str:
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {
        "model": model,
        "prompt": whole_page_prompt(),
        "images": [encoded],
        "stream": False,
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=600) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data.get("response") or "").strip()


def parse_vision_response(response: str) -> Optional[Dict[str, List[str]]]:
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


def prior_hash_matches(input_root: Path, current_log: str, source_sha256: str) -> List[Dict[str, str]]:
    """Audit-only duplicate scan signal; never suppresses the current evidence record."""
    matches: List[Dict[str, str]] = []
    for path in sorted(input_root.glob(f"*/{OUTPUT_DIR_NAME}/whole_traveler_evidence_v1_3_5_0.json")):
        try:
            data = load_json(path)
        except Exception:
            continue
        if str(data.get("log_number")) == str(current_log):
            continue
        if data.get("source_sha256") == source_sha256:
            matches.append({
                "log_number": str(data.get("log_number")),
                "report_path": str(path),
                "source_traveler": str(data.get("source_traveler")),
            })
    return matches


def build_report(
    input_root: Path,
    log_number: str,
    detect_only: bool,
    source_arg: Optional[str],
    model: str,
) -> Dict[str, Any]:
    log_dir = input_root / log_number
    if not log_dir.exists():
        raise FileNotFoundError(f"Log directory not found: {log_dir}")

    source_path = find_source_image(log_dir, source_arg)
    out_dir = log_dir / OUTPUT_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)

    if not source_path:
        return {
            "reader_version": VERSION,
            "log_number": log_number,
            "detect_only": detect_only,
            "model": model,
            "status": "review_required_source_traveler_not_found",
            "source_traveler": None,
            "source_sha256": None,
            "source_modified": False,
            "whole_page_derivative": None,
            "whole_page_transcription": None,
            "duplicate_source_hash_matches": [],
            "automatic_fact_acceptance": False,
            "accepted_as_repair_fact_count": 0,
            "qdrant_entry_created": False,
        }

    source_sha = sha256_file(source_path)
    original = Image.open(source_path)
    page, orientation = normalize_full_page(original)
    derivative = out_dir / "whole_traveler_evidence.png"
    page.save(derivative)
    derivative_sha = sha256_file(derivative)

    duplicate_matches = prior_hash_matches(input_root, log_number, source_sha)

    if detect_only:
        transcription = {
            "attempted": False,
            "model": model,
            "status": "not_run_detect_only",
            "raw_response": None,
            "parsed": None,
        }
        status = "review_ready_whole_traveler_capture"
    else:
        transcription = {
            "attempted": True,
            "model": model,
            "status": "not_run",
            "raw_response": None,
            "parsed": None,
        }
        try:
            raw = call_ollama_vision(derivative, model)
            parsed = parse_vision_response(raw)
            transcription.update({
                "status": "ok" if parsed is not None else "response_not_json",
                "raw_response": raw,
                "parsed": parsed,
            })
        except Exception as exc:
            transcription.update({"status": "vision_error", "error": str(exc)})
        status = (
            "review_ready_whole_traveler_transcription"
            if transcription.get("status") == "ok"
            else "review_required_whole_traveler_transcription"
        )

    return {
        "reader_version": VERSION,
        "architecture": "whole_traveler_full_page_evidence_first",
        "log_number": log_number,
        "detect_only": detect_only,
        "model": model,
        "status": status,
        "source_traveler": str(source_path),
        "source_sha256": source_sha,
        "source_modified": False,
        "source_file_size_bytes": source_path.stat().st_size,
        "source_image_size": list(Image.open(source_path).size),
        "orientation_normalization": orientation,
        "whole_page_derivative": str(derivative),
        "whole_page_derivative_sha256": derivative_sha,
        "whole_page_image_size": list(page.size),
        "whole_page_crop_used": False,
        "relevance_boxes_used": False,
        "row_or_column_geometry_used": False,
        "mark_gating_used": False,
        "whole_page_transcription": transcription,
        "duplicate_source_hash_matches": duplicate_matches,
        "duplicate_source_hash_match_count": len(duplicate_matches),
        "corpus_policy": {
            "collect_full_page_before_relevance_filtering": True,
            "retain_repeated_template_text_for_later_comparison": True,
            "retain_ambiguous_text_without_guessing": True,
            "do_not_infer_unstated_quantities": True,
            "classify_actions_parts_diagnostics_downstream": True,
            "deduplicate_for_frequency_counts_downstream": True,
        },
        "automatic_fact_acceptance": False,
        "accepted_as_repair_fact_count": 0,
        "qdrant_entry_created": False,
    }


def render_text_report(report: Dict[str, Any]) -> str:
    tx = report.get("whole_page_transcription") or {}
    parsed = tx.get("parsed") or {}
    lines: List[str] = []
    lines.append(f"# Nova DRL Whole Traveler Evidence Reader v{VERSION}")
    lines.append("")
    lines.append(f"Log:                           {report.get('log_number')}")
    lines.append("Architecture:                  whole Traveler / evidence first")
    lines.append(f"Detect only:                   {'YES' if report.get('detect_only') else 'NO'}")
    lines.append(f"Source Traveler:               {'FOUND' if report.get('source_traveler') else 'NOT FOUND'}")
    lines.append(f"Whole-page crop used:          {'YES' if report.get('whole_page_crop_used') else 'NO'}")
    lines.append(f"Relevance boxes used:          {'YES' if report.get('relevance_boxes_used') else 'NO'}")
    lines.append(f"Row/column geometry used:      {'YES' if report.get('row_or_column_geometry_used') else 'NO'}")
    lines.append(f"Mark gating used:              {'YES' if report.get('mark_gating_used') else 'NO'}")
    lines.append(f"Accepted as repair facts:      {report.get('accepted_as_repair_fact_count', 0)}")
    lines.append(f"Qdrant entries created:        {1 if report.get('qdrant_entry_created') else 0}")
    lines.append("")

    if report.get("source_traveler"):
        lines.append("WHOLE TRAVELER EVIDENCE")
        lines.append("-----------------------")
        lines.append(f"Original source: {report.get('source_traveler')}")
        lines.append(f"Source SHA-256: {report.get('source_sha256')}")
        lines.append(f"Original image size: {report.get('source_image_size')}")
        lines.append(f"Orientation: {report.get('orientation_normalization')}")
        lines.append(f"Whole-page derivative: {report.get('whole_page_derivative')}")
        lines.append(f"Derivative image size: {report.get('whole_page_image_size')}")
        lines.append(f"Duplicate source-hash matches: {report.get('duplicate_source_hash_match_count', 0)}")
        for match in report.get("duplicate_source_hash_matches") or []:
            lines.append(f"  - log {match.get('log_number')}: {match.get('source_traveler')}")
        lines.append("")

    lines.append("RAW WHOLE-PAGE MACHINE TRANSCRIPTION")
    lines.append("------------------------------------")
    if report.get("detect_only"):
        lines.append("Not run in detect-only mode.")
    else:
        lines.append(f"Vision status: {tx.get('status')}")
        raw_lines = parsed.get("raw_lines") or []
        unreadable = parsed.get("unreadable_fragments") or []
        if raw_lines:
            for i, text in enumerate(raw_lines, 1):
                lines.append(f"{i:03d}. {text}")
        elif tx.get("status") == "ok":
            lines.append("No lines returned.")
        if unreadable:
            lines.append("")
            lines.append("Unreadable fragments:")
            for text in unreadable:
                lines.append(f"- {text}")
    lines.append("")

    lines.append("CORPUS POLICY")
    lines.append("-------------")
    lines.append("Collect full Traveler before deciding relevance: YES")
    lines.append("Preserve repeated printed template text for later corpus comparison: YES")
    lines.append("Ambiguous text guessed/corrected by reader: NO")
    lines.append("Unstated quantities inferred: NO")
    lines.append("Actions/parts/diagnostics classified by this reader: NO")
    lines.append("Duplicate scan suppression performed by this reader: NO (hash signal only)")
    lines.append("Duplicate scans excluded from future frequency counts: POLICY YES")
    lines.append("Automatic repair fact acceptance: NO")
    lines.append("No DRL source files were changed.")
    lines.append("No Qdrant entry created.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Nova DRL Whole Traveler Evidence Reader v{VERSION}")
    parser.add_argument("input_root", help="Traveler Reader v1.3.1 serial output directory")
    parser.add_argument("--log", required=True, help="DRL log number")
    parser.add_argument("--detect-only", action="store_true", help="Capture the complete Traveler only; do not run vision")
    parser.add_argument("--source", help="Optional explicit original full Traveler image path")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama vision model (default: {DEFAULT_MODEL})")
    args = parser.parse_args()

    input_root = Path(args.input_root)
    report = build_report(input_root, args.log, args.detect_only, args.source, args.model)
    out_dir = input_root / args.log / OUTPUT_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    save_json(out_dir / "whole_traveler_evidence_v1_3_5_0.json", report)
    text = render_text_report(report)
    (out_dir / "whole_traveler_evidence_v1_3_5_0.txt").write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"Reports: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
