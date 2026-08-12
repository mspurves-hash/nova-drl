#!/usr/bin/env python3
"""
Nova DRL Traveler Reader v1.3.4.4.4

Human-relevance hardening layer for DRL Travelers.

Design goals:
- Keep frozen v1.3.4.4.3 unchanged.
- Use the human-defined Traveler relevance map:
    * identity/header anchors
    * Special Notes
    * complete Repairs/Replacements table
- Treat Repaired/Replaced marks as attributes, not admission gates.
- Recover a clipped legacy Repaired column structurally when the frozen crop
  contains Replaced + Description + Initials/Date but omits Repaired.
- Detect meaningful filled-in repair rows without requiring marks.
- Detect-only performs deterministic image analysis only; no MiniCPM/Ollama.
- Never accept repair facts automatically and never write Qdrant.

This version is intentionally a validation/hardening layer.  It consumes the
frozen v1.3.4.4.3 artifacts and emits a new relevance-aware review artifact.
Downstream fusion remains human-gated.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageOps

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - server is expected to have OpenCV
    cv2 = None
    np = None

VERSION = "1.3.4.4.4"
BASE_VERSION = "1.3.4.4.3"
BASE_SCRIPT_NAME = "nova_traveler_reader_v1_3_4_4.py"
PROFILE_NAME = "traveler_relevance_profile_v1_3_4_4_4.json"
OUTPUT_DIR_NAME = "vision_extraction_v1_3_4_4_4"

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
            low = v.lower()
            if not low.endswith(tuple(IMAGE_EXTENSIONS)):
                continue
            p = Path(v)
            if p.exists():
                candidates.append(p)

    # Some frozen artifacts carry the source path only in rendered text.
    # Search derived text as a fallback; source is still opened read-only.
    if not candidates:
        path_re = re.compile(r"(/mnt/drl/[^\n\r\"']+\.(?:jpg|jpeg|png|tif|tiff|bmp|webp))", re.IGNORECASE)
        for tp in sorted(list(log_dir.rglob("*.txt")) + list(log_dir.rglob("*.json"))):
            try:
                text = tp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for match in path_re.findall(text):
                pp = Path(match)
                if pp.exists():
                    candidates.append(pp)

    # Prefer the actual Line Card source and current log number.
    log_number = log_dir.name
    scored: List[Tuple[int, Path]] = []
    for p in candidates:
        s = 0
        name = p.name.lower()
        if log_number in p.name:
            s += 10
        if "line card" in name:
            s += 20
        if "/mnt/drl/" in str(p):
            s += 5
        if "crop" in str(p).lower() or "/output/" in str(p).lower():
            s -= 20
        scored.append((s, p))
    if scored:
        scored.sort(key=lambda x: (x[0], str(x[1])), reverse=True)
        return scored[0][1]
    return None


def find_regions_json(log_dir: Path) -> Optional[Path]:
    direct = log_dir / "traveler_regions.json"
    if direct.exists():
        return direct
    matches = list(log_dir.rglob("traveler_regions.json"))
    return matches[0] if matches else None


def find_base_debug(log_dir: Path) -> Optional[Path]:
    preferred = log_dir / "vision_extraction_v1_3_4_4_3" / "block_detection_debug.json"
    if preferred.exists():
        return preferred
    matches = sorted(log_dir.glob("vision_extraction_v1_3_4_4_*/block_detection_debug.json"))
    return matches[-1] if matches else None


def find_base_entries(log_dir: Path) -> Optional[Path]:
    # Frozen reader also writes a compatibility artifact in the log directory.
    direct = log_dir / "repair_entries_v1_3_4_4.json"
    if direct.exists():
        return direct
    preferred = log_dir / "vision_extraction_v1_3_4_4_3" / "repair_entries_v1_3_4_4.json"
    if preferred.exists():
        return preferred
    matches = sorted(log_dir.rglob("repair_entries_v1_3_4_4.json"))
    return matches[-1] if matches else None


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


def extract_repair_region(regions: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # v1.3.1 schema uses either top-level key or nested regions map depending on age.
    if isinstance(regions.get("repairs_replacements"), dict):
        return regions["repairs_replacements"]
    nested = regions.get("regions")
    if isinstance(nested, dict) and isinstance(nested.get("repairs_replacements"), dict):
        return nested["repairs_replacements"]
    return None


def base_status(entries: Optional[Dict[str, Any]]) -> Optional[str]:
    if not entries:
        return None
    val = entries.get("status")
    return str(val) if val is not None else None


def base_blocks(debug: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not debug:
        return []
    blocks = debug.get("blocks")
    return blocks if isinstance(blocks, list) else []


def layout_widths(layout: Dict[str, Any]) -> Optional[Tuple[float, float, float]]:
    try:
        table_left = float(layout["table_left"])
        divider = float(layout["repaired_replaced_divider"])
        description_left = float(layout["description_left"])
        table_right = float(layout["table_right"])
    except Exception:
        return None
    a = divider - table_left
    b = description_left - divider
    c = table_right - description_left
    if min(a, b, c) <= 0:
        return None
    return a, b, c


def legacy_clip_suspected(layout: Dict[str, Any]) -> bool:
    """
    Detect the 150622005-style role shift without absolute coordinates.

    Frozen parser result there is:
      narrow column | very broad description | narrow initials column
    but labels are assigned one position to the right because Repaired is clipped.

    A valid v1.3.4.4.3 crop such as 130813004 does not exhibit this pattern.
    """
    widths = layout_widths(layout)
    if not widths:
        return False
    left_narrow, middle, right_narrow = widths
    narrow_ref = max(1.0, (left_narrow + right_narrow) / 2.0)
    return middle >= 4.0 * narrow_ref and 0.35 <= left_narrow / right_narrow <= 2.85


def robust_expand_pixels(layout: Dict[str, Any]) -> int:
    widths = layout_widths(layout)
    if not widths:
        return 0
    left_narrow, _middle, right_narrow = widths
    # Structural expansion: enough for one missing narrow disposition column plus margin.
    narrow_ref = (left_narrow + right_narrow) / 2.0
    return max(1, int(round(narrow_ref * 1.75)))


def vertical_line_centers(image: Image.Image, y0: int, y1: int) -> List[int]:
    if np is None:
        return []
    gray = np.asarray(ImageOps.grayscale(image))
    y0 = max(0, min(gray.shape[0] - 1, int(y0)))
    y1 = max(y0 + 1, min(gray.shape[0], int(y1)))
    roi = gray[y0:y1, :]
    dark = roi < 105
    counts = dark.sum(axis=0)
    h = roi.shape[0]
    # Printed vertical rules survive through a large fraction of the table body.
    idx = np.where(counts >= max(20, int(h * 0.42)))[0]
    if idx.size == 0:
        return []
    groups: List[List[int]] = [[int(idx[0])]]
    for x in idx[1:]:
        x = int(x)
        if x - groups[-1][-1] <= 3:
            groups[-1].append(x)
        else:
            groups.append([x])
    centers = [int(round(sum(g) / len(g))) for g in groups if len(g) >= 1]
    return centers


def nearest(values: Sequence[int], target: float, tolerance: float) -> Optional[int]:
    vals = [(abs(v - target), v) for v in values if abs(v - target) <= tolerance]
    if not vals:
        return None
    vals.sort()
    return vals[0][1]


def recover_full_table(
    source: Image.Image,
    base_box: Sequence[int],
    layout: Dict[str, Any],
    body_lines: Sequence[int],
    out_dir: Path,
) -> Dict[str, Any]:
    x0, y0, x1, y1 = [int(v) for v in base_box]
    expand = robust_expand_pixels(layout)
    rx0 = max(0, x0 - expand)
    recovered = source.crop((rx0, y0, x1, y1)).convert("RGB")
    out_path = out_dir / "repairs_replacements_full_relevance.png"
    recovered.save(out_path)

    shift = x0 - rx0
    old_left = int(layout["table_left"])
    old_divider = int(layout["repaired_replaced_divider"])
    old_description_left = int(layout["description_left"])
    old_table_right = int(layout["table_right"])

    # In the legacy clipped pattern, frozen names are shifted one semantic column right:
    # old table_left -> Replaced left
    # old divider -> Description left
    # old description_left -> Initials left
    # old table_right -> Date left
    replaced_left_est = old_left + shift
    description_left = old_divider + shift
    initials_left = old_description_left + shift
    date_left = old_table_right + shift

    body_y0 = int(body_lines[0]) if body_lines else 0
    body_y1 = int(body_lines[-1]) if body_lines else recovered.height
    lines = vertical_line_centers(recovered, body_y0, body_y1)

    widths = layout_widths(layout) or (200.0, 1500.0, 200.0)
    narrow_ref = max(20.0, (widths[0] + widths[2]) / 2.0)
    tol = max(20.0, narrow_ref * 0.65)

    replaced_left = nearest(lines, replaced_left_est, tol) or replaced_left_est
    repaired_left_candidates = [v for v in lines if v < replaced_left - narrow_ref * 0.35]
    repaired_left = max(repaired_left_candidates) if repaired_left_candidates else max(0, int(round(replaced_left - narrow_ref)))

    # Snap known downstream boundaries if line detector can confirm them.
    description_left = nearest(lines, description_left, tol) or description_left
    initials_left = nearest(lines, initials_left, tol) or initials_left
    date_left = nearest(lines, date_left, tol) or date_left

    return {
        "recovered": True,
        "source_pixel_box": [rx0, y0, x1, y1],
        "base_pixel_box": [x0, y0, x1, y1],
        "left_expansion_pixels": shift,
        "recovery_basis": "relative_narrow_column_width_plus_vertical_rule_confirmation",
        "recovered_crop_path": str(out_path),
        "vertical_rule_centers": lines,
        "semantic_columns": {
            "repaired": [repaired_left, replaced_left],
            "replaced": [replaced_left, description_left],
            "description": [description_left, initials_left],
            "initials": [initials_left, date_left],
            "date": [date_left, recovered.width],
        },
        "recovery_confidence": "high" if repaired_left_candidates else "medium",
    }


def remove_grid_lines(gray: "np.ndarray") -> "np.ndarray":
    if np is None:
        return gray
    binary = (gray < 145).astype("uint8") * 255
    if cv2 is None:
        return binary
    h, w = binary.shape
    # Remove long horizontal/vertical printed rules while retaining handwriting.
    horiz_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w // 5), 1))
    vert_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, h // 2)))
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, horiz_kernel)
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vert_kernel)
    cleaned = cv2.subtract(binary, cv2.bitwise_or(horiz, vert))
    return cleaned


def meaningful_ink_features(cell: Image.Image) -> Dict[str, Any]:
    if np is None:
        g = ImageOps.grayscale(cell)
        pixels = list(g.getdata())
        dark = sum(1 for p in pixels if p < 145)
        area = max(1, len(pixels))
        return {
            "ink_pixels": dark,
            "ink_fraction": dark / area,
            "component_count": None,
            "meaningful": dark >= max(150, int(area * 0.001)),
        }

    gray = np.asarray(ImageOps.grayscale(cell))
    if gray.size == 0:
        return {"ink_pixels": 0, "ink_fraction": 0.0, "component_count": 0, "meaningful": False}
    cleaned = remove_grid_lines(gray)
    ink_pixels = int((cleaned > 0).sum())
    area = int(cleaned.size)
    component_count = 0
    component_area = 0
    if cv2 is not None:
        n, _labels, stats, _cent = cv2.connectedComponentsWithStats((cleaned > 0).astype("uint8"), 8)
        for i in range(1, n):
            a = int(stats[i, cv2.CC_STAT_AREA])
            # Ignore isolated dust but keep punctuation and compact handwriting.
            if a >= 6:
                component_count += 1
                component_area += a
    else:
        component_count = 1 if ink_pixels else 0
        component_area = ink_pixels

    minimum = max(120, int(area * 0.0008))
    meaningful = ink_pixels >= minimum and component_count >= 1 and component_area >= minimum // 2
    return {
        "ink_pixels": ink_pixels,
        "ink_fraction": round(ink_pixels / max(1, area), 6),
        "component_count": component_count,
        "component_area": component_area,
        "threshold_pixels": minimum,
        "meaningful": bool(meaningful),
    }


def detect_content_rows(
    table_image: Image.Image,
    body_lines: Sequence[int],
    columns: Dict[str, Sequence[int]],
    out_dir: Path,
) -> List[Dict[str, Any]]:
    desc_x0, desc_x1 = [int(v) for v in columns["description"]]
    rep_x0, rep_x1 = [int(v) for v in columns["repaired"]]
    repl_x0, repl_x1 = [int(v) for v in columns["replaced"]]
    initials_x0, initials_x1 = [int(v) for v in columns["initials"]]
    date_x0, date_x1 = [int(v) for v in columns["date"]]
    rows: List[Dict[str, Any]] = []
    row_dir = out_dir / "content_rows"
    row_dir.mkdir(parents=True, exist_ok=True)

    for i in range(max(0, len(body_lines) - 1)):
        y0, y1 = int(body_lines[i]), int(body_lines[i + 1])
        if y1 - y0 < 10:
            continue
        pad_y = max(3, min(10, (y1 - y0) // 12))
        cell = table_image.crop((max(0, desc_x0 + 3), y0 + pad_y, min(table_image.width, desc_x1 - 3), y1 - pad_y))
        features = meaningful_ink_features(cell)

        # Disposition columns are attributes.  We preserve images for later review,
        # but never require a mark for the row to exist.
        full_row = table_image.crop((max(0, rep_x0), y0, min(table_image.width, date_x1), y1))
        full_path = row_dir / f"row_{i+1:02d}_full.png"
        desc_path = row_dir / f"row_{i+1:02d}_description.png"
        full_row.save(full_path)
        cell.save(desc_path)

        mark_repaired = meaningful_ink_features(table_image.crop((max(0, rep_x0 + 2), y0 + pad_y, min(table_image.width, rep_x1 - 2), y1 - pad_y)))
        mark_replaced = meaningful_ink_features(table_image.crop((max(0, repl_x0 + 2), y0 + pad_y, min(table_image.width, repl_x1 - 2), y1 - pad_y)))
        initials_features = meaningful_ink_features(table_image.crop((max(0, initials_x0 + 2), y0 + pad_y, min(table_image.width, initials_x1 - 2), y1 - pad_y)))
        date_features = meaningful_ink_features(table_image.crop((max(0, date_x0 + 2), y0 + pad_y, min(table_image.width, date_x1 - 2), y1 - pad_y)))

        row = {
            "physical_row": i + 1,
            "y_bounds": [y0, y1],
            "description_content": features,
            "meaningful_description_content": bool(features["meaningful"]),
            "repaired_mark_present_provisional": bool(mark_repaired["meaningful"]),
            "replaced_mark_present_provisional": bool(mark_replaced["meaningful"]),
            "initials_present_provisional": bool(initials_features["meaningful"]),
            "date_present_provisional": bool(date_features["meaningful"]),
            "full_row_crop": str(full_path),
            "description_crop": str(desc_path),
            "accepted_as_repair_fact": False,
        }
        rows.append(row)
    return rows


def copy_relevance_crops(log_dir: Path, out_dir: Path) -> Dict[str, Any]:
    crops_dir = log_dir / "crops"
    result: Dict[str, Any] = {}
    for name in ("identity", "special_notes"):
        src = crops_dir / f"{name}.png"
        if src.exists():
            dst = out_dir / f"{name}_relevance.png"
            shutil.copy2(src, dst)
            result[name] = {"available": True, "path": str(dst)}
        else:
            result[name] = {"available": False, "path": None}
    return result


def render_text_report(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"# Nova DRL Traveler Reader v{VERSION}")
    lines.append("")
    lines.append(f"Log:                           {report.get('log_number')}")
    lines.append("Detection mode:                human-relevance map + content-first fallback")
    lines.append(f"Detect only:                   {'YES' if report.get('detect_only') else 'NO'}")
    lines.append(f"Frozen base version:           {BASE_VERSION}")
    lines.append(f"Frozen base status:            {report.get('base_status')}")
    lines.append(f"Legacy crop recovery used:     {'YES' if report.get('legacy_crop_recovery_used') else 'NO'}")
    lines.append(f"Meaningful repair rows:        {report.get('meaningful_repair_row_count', 0)}")
    lines.append(f"Rows with repaired mark:       {report.get('rows_with_repaired_mark', 0)}")
    lines.append(f"Rows with replaced mark:       {report.get('rows_with_replaced_mark', 0)}")
    lines.append(f"Unmarked meaningful rows:      {report.get('unmarked_meaningful_row_count', 0)}")
    lines.append(f"Accepted as repair facts:      0")
    lines.append(f"Qdrant entries created:        0")
    lines.append("")
    lines.append("RELEVANCE SECTIONS")
    lines.append("------------------")
    lines.append("IDENTITY_HEADER          knowledge/review")
    lines.append("SPECIAL_NOTES            knowledge/review; template text is context only")
    lines.append("REPAIRS_REPLACEMENTS     knowledge/review; marks are attributes, not gates")
    lines.append("ALL OTHER TRAVELER AREAS raw/audit only")
    lines.append("")
    lines.append("REPAIR CONTENT ROWS")
    lines.append("-------------------")
    for row in report.get("repair_rows", []):
        if not row.get("meaningful_description_content"):
            continue
        mark_bits = []
        if row.get("repaired_mark_present_provisional"):
            mark_bits.append("repaired_mark")
        if row.get("replaced_mark_present_provisional"):
            mark_bits.append("replaced_mark")
        marks = ",".join(mark_bits) if mark_bits else "none"
        lines.append(
            f"row {row['physical_row']:02d}: meaningful_content=YES marks={marks} "
            f"review_required=YES fact=NO"
        )
    if not report.get("meaningful_repair_row_count"):
        lines.append("None")
    lines.append("")
    lines.append("POLICY")
    lines.append("------")
    lines.append("Repaired/Replaced mark required for content preservation: NO")
    lines.append("Printed template accepted as repair fact: NO")
    lines.append("Final-test/shipping fields accepted as Traveler repair knowledge: NO")
    lines.append("Hours in Final Testing used as knowledge: NO")
    lines.append("Automatic repair fact acceptance: NO")
    lines.append("No DRL source files were changed.")
    lines.append("No Qdrant entry created.")
    return "\n".join(lines) + "\n"


def build_report(input_root: Path, log_number: str, detect_only: bool, source_arg: Optional[str], skip_base_run: bool) -> Dict[str, Any]:
    log_dir = input_root / log_number
    if not log_dir.exists():
        raise FileNotFoundError(f"Log directory not found: {log_dir}")

    base_run = None
    if not skip_base_run:
        base_run = run_base_detect_only(input_root, log_number)

    regions_path = find_regions_json(log_dir)
    debug_path = find_base_debug(log_dir)
    entries_path = find_base_entries(log_dir)
    if not regions_path:
        raise FileNotFoundError(f"traveler_regions.json not found under {log_dir}")
    if not debug_path:
        raise FileNotFoundError(f"Frozen block_detection_debug.json not found under {log_dir}")

    regions = load_json(regions_path)
    debug = load_json(debug_path)
    entries = load_json(entries_path) if entries_path and entries_path.exists() else None
    region = extract_repair_region(regions)
    if not region or not isinstance(region.get("pixel_box"), list):
        raise RuntimeError("repairs_replacements pixel_box not found in traveler_regions.json")

    layout = debug.get("layout") if isinstance(debug.get("layout"), dict) else {}
    body_lines = layout.get("body_lines") if isinstance(layout.get("body_lines"), list) else []
    out_dir = log_dir / OUTPUT_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    relevance_crops = copy_relevance_crops(log_dir, out_dir)

    source_path = find_source_image(log_dir, source_arg)
    source_status = "found" if source_path else "not_found"
    recovery_needed = legacy_clip_suspected(layout)
    recovery: Optional[Dict[str, Any]] = None
    rows: List[Dict[str, Any]] = []

    if recovery_needed and source_path:
        source = Image.open(source_path).convert("RGB")
        recovery = recover_full_table(source, region["pixel_box"], layout, body_lines, out_dir)
        table_image = Image.open(recovery["recovered_crop_path"]).convert("RGB")
        rows = detect_content_rows(table_image, body_lines, recovery["semantic_columns"], out_dir)
    elif not recovery_needed:
        # Valid frozen geometry: preserve the frozen behavior and separately expose
        # relevance sections.  We intentionally do not reinterpret successful
        # v1.3.4.4.3 blocks in this hardening pass.
        base_crop = Path(region.get("crop_path", ""))
        if not base_crop.exists():
            base_crop = log_dir / "crops" / "repairs_replacements.png"
        if base_crop.exists() and body_lines and isinstance(layout, dict):
            # For successful layouts, only perform content-row detection when the
            # semantic roles are clearly ordered as two narrow columns followed by
            # a broad description. Otherwise leave rows to frozen segmentation.
            widths = layout_widths(layout)
            if widths and widths[1] < widths[2]:
                table_image = Image.open(base_crop).convert("RGB")
                columns = {
                    "repaired": [int(layout.get("table_left", 0)), int(layout.get("repaired_replaced_divider", 0))],
                    "replaced": [int(layout.get("repaired_replaced_divider", 0)), int(layout.get("description_left", 0))],
                    "description": [int(layout.get("description_left", 0)), int(layout.get("table_right", table_image.width))],
                    "initials": [int(layout.get("table_right", table_image.width)), table_image.width],
                    "date": [table_image.width, table_image.width],
                }
                # Avoid invalid empty date region; successful frozen segmentation is authoritative.
                rows = []

    meaningful = [r for r in rows if r.get("meaningful_description_content")]
    repaired_marked = [r for r in meaningful if r.get("repaired_mark_present_provisional")]
    replaced_marked = [r for r in meaningful if r.get("replaced_mark_present_provisional")]
    unmarked = [r for r in meaningful if not r.get("repaired_mark_present_provisional") and not r.get("replaced_mark_present_provisional")]

    report = {
        "reader_version": VERSION,
        "base_reader_version": BASE_VERSION,
        "log_number": log_number,
        "detect_only": detect_only,
        "base_status": base_status(entries),
        "base_block_count": len(base_blocks(debug)),
        "base_run": base_run,
        "source_traveler": str(source_path) if source_path else None,
        "source_status": source_status,
        "regions_source": str(regions_path),
        "base_debug_source": str(debug_path),
        "legacy_crop_recovery_required": recovery_needed,
        "legacy_crop_recovery_used": bool(recovery),
        "recovery": recovery,
        "relevance_crops": relevance_crops,
        "repair_rows": rows,
        "meaningful_repair_row_count": len(meaningful),
        "rows_with_repaired_mark": len(repaired_marked),
        "rows_with_replaced_mark": len(replaced_marked),
        "unmarked_meaningful_row_count": len(unmarked),
        "repair_segmentation_policy": "meaningful_content_first_marks_are_attributes_not_gates",
        "automatic_fact_acceptance": False,
        "accepted_as_repair_fact_count": 0,
        "qdrant_entry_created": False,
        "source_modified": False,
        "status": (
            "review_required_source_not_found"
            if recovery_needed and not source_path
            else "review_required_content_segmentation"
            if recovery_needed
            else "frozen_base_geometry_retained"
        ),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Nova DRL Traveler Reader v{VERSION}")
    parser.add_argument("input_root", help="Traveler Reader v1.3.1 serial output directory")
    parser.add_argument("--log", required=True, help="DRL log number")
    parser.add_argument("--detect-only", action="store_true", help="No MiniCPM/Ollama transcription; deterministic detection only")
    parser.add_argument("--source", help="Optional explicit original traveler image path")
    parser.add_argument("--skip-base-run", action="store_true", help="Use existing frozen v1.3.4.4.3 artifacts without rerunning base detect-only")
    args = parser.parse_args()

    if not args.detect_only:
        print("ERROR: v1.3.4.4.4 first validation pass is detect-only by design; rerun with --detect-only.", file=sys.stderr)
        return 2

    input_root = Path(args.input_root)
    try:
        report = build_report(input_root, args.log, args.detect_only, args.source, args.skip_base_run)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    out_dir = input_root / args.log / OUTPUT_DIR_NAME
    save_json(out_dir / "traveler_relevance_review_v1_3_4_4_4.json", report)
    text = render_text_report(report)
    (out_dir / "traveler_relevance_review_v1_3_4_4_4.txt").write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"Reports: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
