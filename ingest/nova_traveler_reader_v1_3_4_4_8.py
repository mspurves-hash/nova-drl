#!/usr/bin/env python3
"""
Nova DRL Traveler Reader v1.3.4.4.8

Simplified Traveler Repairs/Replacements relevance reader.

Human rule:
  The technician handwriting inside the complete printed Repairs/Replacements
  box is the evidence. Internal form columns are not semantic gates.

Behavior:
  * locate/capture the complete OUTER printed Repairs/Replacements box
  * do NOT resolve Repaired/Replaced/Description/Initials/Date columns
  * do NOT require X/check marks or row starts
  * in detect-only mode: save the complete box and stop
  * in transcription mode: ask MiniCPM-V for literal handwriting only from the
    complete box, preserving raw response and parsed line list separately
  * no automatic repair facts
  * no source modification
  * no Qdrant writes

Frozen v1.3.4.4.3 is not modified.
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

from PIL import Image, ImageOps

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None
    np = None

VERSION = "1.3.4.4.8"
BASE_VERSION = "1.3.4.4.3"
BASE_SCRIPT_NAME = "nova_traveler_reader_v1_3_4_4.py"
OUTPUT_DIR_NAME = "vision_extraction_v1_3_4_4_8"
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


def find_regions_json(log_dir: Path) -> Optional[Path]:
    direct = log_dir / "traveler_regions.json"
    if direct.exists():
        return direct
    found = list(log_dir.rglob("traveler_regions.json"))
    return found[0] if found else None


def extract_region(regions: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    if isinstance(regions.get(name), dict):
        return regions[name]
    nested = regions.get("regions")
    if isinstance(nested, dict) and isinstance(nested.get(name), dict):
        return nested[name]
    return None


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
    return {
        "status": "ok" if proc.returncode == 0 else "base_run_failed",
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _gray_array(image: Image.Image) -> "np.ndarray":
    if np is None:
        raise RuntimeError("NumPy/OpenCV required for v1.3.4.4.8 outline detection")
    return np.asarray(ImageOps.grayscale(image))


def _binary_inv(image: Image.Image) -> "np.ndarray":
    if cv2 is None or np is None:
        raise RuntimeError("OpenCV required for v1.3.4.4.8 outline detection")
    gray = _gray_array(image)
    _thr, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return binary


def _median(values: Sequence[float]) -> float:
    vals = sorted(float(v) for v in values)
    if not vals:
        return 0.0
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def _interval_overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def _cluster_indices(indices: List[int], max_gap: int = 3) -> List[List[int]]:
    if not indices:
        return []
    groups: List[List[int]] = [[indices[0]]]
    for i in indices[1:]:
        if i - groups[-1][-1] <= max_gap:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def horizontal_rule_records(image: Image.Image, search_box: Sequence[int]) -> List[Dict[str, Any]]:
    """Detect long printed horizontal rules without interpreting internal columns."""
    if cv2 is None or np is None:
        return []
    x0, y0, x1, y1 = [int(v) for v in search_box]
    crop = image.crop((x0, y0, x1, y1)).convert("RGB")
    binary = _binary_inv(crop)
    h, w = binary.shape

    # Keep horizontal print rules, remove most handwriting/text strokes.
    kernel_w = max(45, int(w * 0.035))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, 1))
    lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    strengths = np.count_nonzero(lines, axis=1)
    threshold = max(120, int(w * 0.16))
    ys = [int(i) for i, v in enumerate(strengths) if int(v) >= threshold]
    groups = _cluster_indices(ys, 4)
    out: List[Dict[str, Any]] = []
    for g in groups:
        cy = max(g, key=lambda yy: int(strengths[yy]))
        band = lines[max(0, g[0] - 2):min(h, g[-1] + 3), :]
        cols = np.where(np.any(band > 0, axis=0))[0]
        if len(cols) == 0:
            continue
        # Permit tiny scan gaps by taking overall printed-rule extent. We use a
        # repeated family of rows later, so one noisy line cannot set the box.
        lx = int(cols.min())
        rx = int(cols.max())
        if rx - lx < max(200, int(w * 0.22)):
            continue
        out.append({
            "y": int(cy + y0),
            "x0": int(lx + x0),
            "x1": int(rx + x0),
            "width": int(rx - lx),
            "strength": int(strengths[cy]),
        })
    return out


def _dominant_pitch(ys: Sequence[int]) -> Optional[float]:
    ys = sorted(set(int(y) for y in ys))
    if len(ys) < 4:
        return None
    gaps = [b - a for a, b in zip(ys, ys[1:]) if 18 <= b - a <= 450]
    if not gaps:
        return None
    best: List[int] = []
    for g in gaps:
        group = [h for h in gaps if abs(h - g) <= max(8, int(g * 0.16))]
        if len(group) > len(best):
            best = group
    return _median(best or gaps)


def _regular_runs(records: List[Dict[str, Any]], seed_box: Sequence[int]) -> List[Dict[str, Any]]:
    """Build regular horizontal-grid runs and score those enclosing the seed."""
    sx0, sy0, sx1, sy1 = [int(v) for v in seed_box]
    sw = max(1, sx1 - sx0)
    sh = max(1, sy1 - sy0)
    recs = sorted(records, key=lambda r: r["y"])
    ys = [r["y"] for r in recs]
    pitch = _dominant_pitch(ys)
    if not pitch:
        return []
    lo, hi = pitch * 0.48, pitch * 1.60
    dlo, dhi = pitch * 1.72, pitch * 2.35

    runs: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = [recs[0]] if recs else []
    for r in recs[1:]:
        gap = r["y"] - cur[-1]["y"]
        if lo <= gap <= hi or dlo <= gap <= dhi:
            cur.append(r)
        else:
            if len(cur) >= 4:
                runs.append(cur)
            cur = [r]
    if len(cur) >= 4:
        runs.append(cur)

    candidates: List[Dict[str, Any]] = []
    for run in runs:
        rys = [r["y"] for r in run]
        if max(rys) < sy0 - pitch or min(rys) > sy1 + pitch:
            continue
        # Repeated printed rows should substantially overlap the frozen seed.
        overlaps = [_interval_overlap(r["x0"], r["x1"], sx0, sx1) for r in run]
        if _median(overlaps) < sw * 0.35:
            continue
        lefts = [r["x0"] for r in run]
        rights = [r["x1"] for r in run]
        # Use robust outer extent. This captures the box without caring where
        # handwriting or internal form columns sit.
        left = int(round(_median(sorted(lefts)[:max(2, len(lefts)//2)])))
        right = int(round(_median(sorted(rights)[max(0, len(rights)//2):])))
        width = right - left
        if width < sw * 0.90:
            continue
        top = min(rys)
        bottom = max(rys)
        score = len(run) * 100.0
        score += min(180.0, width / sw * 55.0)
        if top <= sy0 <= bottom or top <= sy1 <= bottom:
            score += 120.0
        seed_mid = (sy0 + sy1) / 2.0
        score -= abs(((top + bottom) / 2.0) - seed_mid) / sh * 20.0
        candidates.append({
            "score": round(score, 3),
            "pitch": round(float(pitch), 3),
            "records": run,
            "left": left,
            "right": right,
            "top": top,
            "bottom": bottom,
        })
    return sorted(candidates, key=lambda c: c["score"], reverse=True)


def find_outer_repairs_box(source: Image.Image, seed_box: Sequence[int]) -> Dict[str, Any]:
    """
    Locate the complete printed Repairs/Replacements box.

    The frozen v1.3.1 crop is only a search seed. We intentionally ignore all
    internal columns and all handwriting when choosing the final evidence box.
    """
    sx0, sy0, sx1, sy1 = [int(v) for v in seed_box]
    sw = max(1, sx1 - sx0)
    sh = max(1, sy1 - sy0)
    W, H = source.size

    # Search the full Traveler width; the old seed can be clipped on either side.
    qx0, qx1 = 0, W
    qy0 = max(0, int(round(sy0 - sh * 0.65)))
    qy1 = min(H, int(round(sy1 + sh * 0.85)))
    search_box = [qx0, qy0, qx1, qy1]

    records = horizontal_rule_records(source, search_box)
    candidates = _regular_runs(records, seed_box)
    if not candidates:
        return {
            "status": "review_required_outer_box_not_resolved",
            "seed_box": list(map(int, seed_box)),
            "search_box": search_box,
            "horizontal_rule_count": len(records),
            "horizontal_rules": records,
            "reason": "no_regular_repeated_printed_horizontal_grid_enclosing_seed",
        }

    chosen = candidates[0]
    left, right, top, bottom = chosen["left"], chosen["right"], chosen["top"], chosen["bottom"]
    if right - left < int(sw * 0.90) or bottom - top < int(sh * 0.45):
        return {
            "status": "review_required_outer_box_not_resolved",
            "seed_box": list(map(int, seed_box)),
            "search_box": search_box,
            "selected_candidate": {k: v for k, v in chosen.items() if k != "records"},
            "reason": "resolved_grid_rectangle_implausibly_small",
        }

    margin = 8
    outline = [max(0, left - margin), max(0, top - margin), min(W, right + margin), min(H, bottom + margin)]
    return {
        "status": "ok",
        "seed_box": list(map(int, seed_box)),
        "search_box": search_box,
        "outline_box": outline,
        "printed_left": left,
        "printed_right": right,
        "printed_top": top,
        "printed_bottom": bottom,
        "horizontal_rule_pitch": chosen["pitch"],
        "horizontal_rule_count_in_selected_run": len(chosen["records"]),
        "selection_score": chosen["score"],
        "boundary_basis": "outer_printed_repairs_box_from_repeated_horizontal_grid_only",
        "internal_columns_used": False,
        "handwriting_extent_used": False,
        "repaired_replaced_marks_used": False,
    }


def handwriting_prompt() -> str:
    return """You are transcribing the handwritten technician content inside ONE DRL Traveler Repairs/Replacements box.

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
- Do NOT group handwriting based on internal table columns.
- Standalone X/check marks may be omitted unless they are inseparable from handwritten text.
- If a handwritten fragment is present but cannot be read reliably, put a short literal fragment in unreadable_fragments rather than guessing.
- Do not add commentary outside the JSON object.
"""


def call_ollama_vision(image_path: Path, model: str) -> str:
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {
        "model": model,
        "prompt": handwriting_prompt(),
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
    return {
        "handwritten_lines": [str(x) for x in lines if x is not None],
        "unreadable_fragments": [str(x) for x in unreadable if x is not None],
    }


def build_report(input_root: Path, log_number: str, detect_only: bool, source_arg: Optional[str], model: str, skip_base_run: bool) -> Dict[str, Any]:
    log_dir = input_root / log_number
    if not log_dir.exists():
        raise FileNotFoundError(f"Log directory not found: {log_dir}")

    base_run = None if skip_base_run else run_base_detect_only(input_root, log_number)
    regions_path = find_regions_json(log_dir)
    if not regions_path:
        raise FileNotFoundError(f"traveler_regions.json not found under {log_dir}")
    regions = load_json(regions_path)
    region = extract_region(regions, "repairs_replacements")
    if not region or not isinstance(region.get("pixel_box"), list):
        raise RuntimeError("repairs_replacements pixel_box not found in traveler_regions.json")

    entries_path = find_base_entries(log_dir)
    entries = load_json(entries_path) if entries_path and entries_path.exists() else None
    source_path = find_source_image(log_dir, source_arg)
    out_dir = log_dir / OUTPUT_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)

    outline: Dict[str, Any]
    crop_path: Optional[Path] = None
    transcription: Dict[str, Any] = {
        "attempted": False,
        "model": model,
        "status": "not_run_detect_only" if detect_only else "not_run_outline_unresolved",
        "raw_response": None,
        "parsed": None,
    }

    if not source_path:
        outline = {
            "status": "review_required_source_not_found",
            "seed_box": region.get("pixel_box"),
            "reason": "original_traveler_source_not_found",
        }
    else:
        source = Image.open(source_path).convert("RGB")
        outline = find_outer_repairs_box(source, region["pixel_box"])
        if outline.get("status") == "ok":
            crop_path = out_dir / "repairs_replacements_outer_box.png"
            source.crop(tuple(outline["outline_box"])).save(crop_path)
            if not detect_only:
                transcription["attempted"] = True
                try:
                    raw = call_ollama_vision(crop_path, model)
                    parsed = parse_handwriting_response(raw)
                    transcription.update({
                        "status": "ok" if parsed is not None else "response_not_json",
                        "raw_response": raw,
                        "parsed": parsed,
                    })
                except Exception as exc:
                    transcription.update({"status": "vision_error", "error": str(exc)})

    status = "review_ready_outer_box" if outline.get("status") == "ok" else outline.get("status", "review_required")
    if not detect_only and outline.get("status") == "ok":
        status = "review_ready_handwriting_transcription" if transcription.get("status") == "ok" else "review_required_handwriting_transcription"

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
        "regions_source": str(regions_path),
        "repairs_seed_pixel_box": region.get("pixel_box"),
        "repairs_outer_box": outline,
        "repairs_outer_box_crop_path": str(crop_path) if crop_path else None,
        "handwriting_transcription": transcription,
        "repair_content_policy": "complete_outer_box_then_literal_handwriting_only_no_internal_column_semantics",
        "automatic_fact_acceptance": False,
        "accepted_as_repair_fact_count": 0,
        "qdrant_entry_created": False,
        "status": status,
    }


def render_text_report(report: Dict[str, Any]) -> str:
    outline = report.get("repairs_outer_box") or {}
    tx = report.get("handwriting_transcription") or {}
    parsed = tx.get("parsed") or {}
    lines: List[str] = []
    lines.append(f"# Nova DRL Traveler Reader v{VERSION}")
    lines.append("")
    lines.append(f"Log:                           {report.get('log_number')}")
    lines.append("Detection mode:                outer-box + literal handwriting")
    lines.append(f"Detect only:                   {'YES' if report.get('detect_only') else 'NO'}")
    lines.append(f"Frozen base version:           {BASE_VERSION}")
    lines.append(f"Frozen base status:            {report.get('base_status')}")
    lines.append(f"Repairs outer box:             {outline.get('status')}")
    lines.append("Internal semantic columns:     NOT USED")
    lines.append("Repaired/Replaced marks:       NOT USED AS GATES")
    lines.append(f"Handwriting transcription:     {tx.get('status')}")
    lines.append("Accepted as repair facts:      0")
    lines.append("Qdrant entries created:        0")
    lines.append("")
    lines.append("REPAIRS/REPLACEMENTS OUTER BOX")
    lines.append("------------------------------")
    if outline.get("status") == "ok":
        lines.append(f"Printed outer box: {outline.get('outline_box')}")
        lines.append(f"Boundary basis: {outline.get('boundary_basis')}")
        lines.append(f"Full box crop: {report.get('repairs_outer_box_crop_path')}")
        lines.append("Internal column interpretation used: NO")
        lines.append("Handwriting extent used to set boundary: NO")
    else:
        lines.append(f"Outer box unresolved: {outline.get('reason')}")
    lines.append("")
    lines.append("HANDWRITTEN CONTENT")
    lines.append("-------------------")
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
    lines.append("Complete printed outer box defines the repair evidence region: YES")
    lines.append("Repaired/Replaced/Description/Initials/Date fields interpreted separately: NO")
    lines.append("Repaired/Replaced mark required for handwriting preservation: NO")
    lines.append("Printed form text accepted as repair fact: NO")
    lines.append("Machine transcription accepted as repair fact: NO")
    lines.append("Human review required before repair actions: YES")
    lines.append("Approved wording automatically modified: NO")
    lines.append("No DRL source files were changed.")
    lines.append("No Qdrant entry created.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Nova DRL Traveler Reader v{VERSION}")
    parser.add_argument("input_root", help="Traveler Reader v1.3.1 serial output directory")
    parser.add_argument("--log", required=True, help="DRL log number")
    parser.add_argument("--detect-only", action="store_true", help="Locate/save complete outer repair box; do not run vision")
    parser.add_argument("--source", help="Optional explicit original Traveler image path")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama vision model (default: {DEFAULT_MODEL})")
    parser.add_argument("--skip-base-run", action="store_true", help="Use existing frozen v1.3.4.4.3 artifacts without rerunning base detect-only")
    args = parser.parse_args()

    try:
        report = build_report(Path(args.input_root), args.log, args.detect_only, args.source, args.model, args.skip_base_run)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    out_dir = Path(args.input_root) / args.log / OUTPUT_DIR_NAME
    save_json(out_dir / "traveler_relevance_review_v1_3_4_4_8.json", report)
    text = render_text_report(report)
    (out_dir / "traveler_relevance_review_v1_3_4_4_8.txt").write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"Reports: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
