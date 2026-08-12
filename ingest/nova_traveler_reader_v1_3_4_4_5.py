#!/usr/bin/env python3
"""
Nova DRL Traveler Reader v1.3.4.4.5

Outline-first Traveler relevance hardening.

Human-defined Traveler relevance map:
  * identity/header anchors
  * Special Notes
  * complete Repairs/Replacements table

Key change from experimental v1.3.4.4.4:
  The Repairs/Replacements evidence region is defined by the PRINTED TABLE
  OUTLINE in the original Traveler, not by handwriting extent and not by a
  fixed left/right expansion of an older crop.

Policy:
  * frozen v1.3.4.4.3 remains unchanged
  * detect-only validation pass; no MiniCPM/Ollama transcription
  * meaningful repair content is preserved even without Repaired/Replaced mark
  * Repaired/Replaced marks are row attributes, not admission gates
  * no repair facts accepted automatically
  * no DRL source modification
  * no Qdrant writes
"""

from __future__ import annotations

import argparse
import itertools
import json
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
except Exception:  # pragma: no cover
    cv2 = None
    np = None

VERSION = "1.3.4.4.5"
BASE_VERSION = "1.3.4.4.3"
BASE_SCRIPT_NAME = "nova_traveler_reader_v1_3_4_4.py"
OUTPUT_DIR_NAME = "vision_extraction_v1_3_4_4_5"
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
            if not v.lower().endswith(tuple(IMAGE_EXTENSIONS)):
                continue
            p = Path(v)
            if p.exists():
                candidates.append(p)

    if not candidates:
        path_re = re.compile(r"(/mnt/drl/[^\n\r\"']+\.(?:jpg|jpeg|png|tif|tiff|bmp|webp))", re.IGNORECASE)
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


def find_base_entries(log_dir: Path) -> Optional[Path]:
    preferred = log_dir / "vision_extraction_v1_3_4_4_3" / "repair_entries_v1_3_4_4.json"
    if preferred.exists():
        return preferred
    direct = log_dir / "repair_entries_v1_3_4_4.json"
    if direct.exists():
        return direct
    found = sorted(log_dir.rglob("repair_entries_v1_3_4_4.json"))
    return found[-1] if found else None


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


def extract_region(regions: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    if isinstance(regions.get(name), dict):
        return regions[name]
    nested = regions.get("regions")
    if isinstance(nested, dict) and isinstance(nested.get(name), dict):
        return nested[name]
    return None


def base_status(entries: Optional[Dict[str, Any]]) -> Optional[str]:
    if not entries:
        return None
    v = entries.get("status")
    return str(v) if v is not None else None


def _gray_array(image: Image.Image) -> "np.ndarray":
    if np is None:
        raise RuntimeError("NumPy/OpenCV required for v1.3.4.4.5 outline detection")
    return np.asarray(ImageOps.grayscale(image))


def _binary_inv(image: Image.Image) -> "np.ndarray":
    if cv2 is None or np is None:
        raise RuntimeError("OpenCV required for v1.3.4.4.5 outline detection")
    gray = _gray_array(image)
    _thr, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return binary


def _merge_axis_records(records: List[Dict[str, int]], axis: str, tolerance: int = 4) -> List[Dict[str, int]]:
    if not records:
        return []
    key = "y" if axis == "horizontal" else "x"
    records = sorted(records, key=lambda r: r[key])
    groups: List[List[Dict[str, int]]] = [[records[0]]]
    for rec in records[1:]:
        if abs(rec[key] - groups[-1][-1][key]) <= tolerance:
            groups[-1].append(rec)
        else:
            groups.append([rec])
    out: List[Dict[str, int]] = []
    for g in groups:
        if axis == "horizontal":
            y = int(round(sum(r["y"] for r in g) / len(g)))
            x0 = min(r["x0"] for r in g)
            x1 = max(r["x1"] for r in g)
            out.append({"y": y, "x0": x0, "x1": x1, "length": x1 - x0})
        else:
            x = int(round(sum(r["x"] for r in g) / len(g)))
            y0 = min(r["y0"] for r in g)
            y1 = max(r["y1"] for r in g)
            out.append({"x": x, "y0": y0, "y1": y1, "length": y1 - y0})
    return out


def horizontal_rule_segments(image: Image.Image, min_length: int) -> List[Dict[str, int]]:
    if cv2 is None or np is None:
        return []
    binary = _binary_inv(image)
    h, w = binary.shape
    kernel_w = max(30, min(w, int(max(min_length * 0.55, w * 0.12))))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, 1))
    lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    contours, _hier = cv2.findContours(lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    recs: List[Dict[str, int]] = []
    for c in contours:
        x, y, rw, rh = cv2.boundingRect(c)
        if rw < min_length:
            continue
        if rh > max(18, int(h * 0.035)):
            continue
        recs.append({"y": int(y + rh // 2), "x0": int(x), "x1": int(x + rw), "length": int(rw)})
    return _merge_axis_records(recs, "horizontal", tolerance=5)


def vertical_rule_segments(image: Image.Image, min_length: int) -> List[Dict[str, int]]:
    if cv2 is None or np is None:
        return []
    binary = _binary_inv(image)
    h, w = binary.shape
    kernel_h = max(30, min(h, int(max(min_length * 0.55, h * 0.15))))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, kernel_h))
    lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    contours, _hier = cv2.findContours(lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    recs: List[Dict[str, int]] = []
    for c in contours:
        x, y, rw, rh = cv2.boundingRect(c)
        if rh < min_length:
            continue
        if rw > max(18, int(w * 0.025)):
            continue
        recs.append({"x": int(x + rw // 2), "y0": int(y), "y1": int(y + rh), "length": int(rh)})
    return _merge_axis_records(recs, "vertical", tolerance=5)


def _median(values: Sequence[float]) -> float:
    vals = sorted(float(v) for v in values)
    if not vals:
        return 0.0
    n = len(vals)
    return vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0


def find_repairs_table_outline(source: Image.Image, seed_box: Sequence[int]) -> Dict[str, Any]:
    """
    Resolve the complete Repairs/Replacements table from printed horizontal rules.

    The seed box only tells us where to SEARCH.  It never becomes the final crop
    boundary.  Final left/right/top/bottom are derived from repeated printed-rule
    endpoints in the original Traveler.
    """
    sx0, sy0, sx1, sy1 = [int(v) for v in seed_box]
    sw = max(1, sx1 - sx0)
    sh = max(1, sy1 - sy0)
    W, H = source.size

    # Broad search neighborhood.  This is not the output crop; printed rules decide that.
    qx0 = max(0, int(round(sx0 - sw * 1.05)))
    qx1 = min(W, int(round(sx1 + sw * 1.05)))
    qy0 = max(0, int(round(sy0 - sh * 0.35)))
    qy1 = min(H, int(round(sy1 + sh * 0.35)))
    search = source.crop((qx0, qy0, qx1, qy1)).convert("RGB")

    min_h_len = max(180, int(sw * 0.65))
    hlines_local = horizontal_rule_segments(search, min_h_len)
    hlines: List[Dict[str, int]] = []
    for r in hlines_local:
        hlines.append({
            "y": r["y"] + qy0,
            "x0": r["x0"] + qx0,
            "x1": r["x1"] + qx0,
            "length": r["length"],
        })

    endpoint_tol = max(18, int(sw * 0.035))
    families: List[Dict[str, Any]] = []
    for anchor in hlines:
        members = [
            r for r in hlines
            if abs(r["x0"] - anchor["x0"]) <= endpoint_tol
            and abs(r["x1"] - anchor["x1"]) <= endpoint_tol
        ]
        if len(members) < 4:
            continue
        left = int(round(_median([r["x0"] for r in members])))
        right = int(round(_median([r["x1"] for r in members])))
        ys = sorted(set(int(r["y"]) for r in members))
        # Family must overlap the known seed vertically and substantially enclose it horizontally.
        vertical_overlap = any((sy0 - int(sh * 0.15)) <= y <= (sy1 + int(sh * 0.15)) for y in ys)
        encloses_seed = left <= sx0 + int(sw * 0.18) and right >= sx1 - int(sw * 0.10)
        if not vertical_overlap or not encloses_seed:
            continue
        span = right - left
        score = len(ys) * 100 + int(25 * span / max(1, sw))
        if left < sx0:
            score += 30
        if right > sx1:
            score += 30
        families.append({"left": left, "right": right, "ys": ys, "members": members, "score": score})

    # Deduplicate equivalent endpoint families.
    unique: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for f in families:
        key = (int(round(f["left"] / endpoint_tol)), int(round(f["right"] / endpoint_tol)))
        if key not in unique or f["score"] > unique[key]["score"]:
            unique[key] = f
    families = sorted(unique.values(), key=lambda f: f["score"], reverse=True)

    if not families:
        return {
            "status": "review_required_outline_not_resolved",
            "seed_box": list(map(int, seed_box)),
            "search_box": [qx0, qy0, qx1, qy1],
            "horizontal_rules": hlines,
            "reason": "no_repeated_printed_horizontal_rule_family_enclosing_seed",
        }

    best = families[0]
    left, right = int(best["left"]), int(best["right"])
    ys = sorted(best["ys"])

    # Keep the consecutive printed-rule run overlapping the seed.  Break only on a
    # clearly abnormal vertical gap so adjacent form sections are not absorbed.
    gaps = [b - a for a, b in zip(ys, ys[1:]) if b > a]
    typical_gap = _median([g for g in gaps if g > 8]) or float(sh / 8.0)
    max_gap = max(40.0, typical_gap * 2.6)
    runs: List[List[int]] = []
    current: List[int] = []
    for y in ys:
        if not current or y - current[-1] <= max_gap:
            current.append(y)
        else:
            runs.append(current)
            current = [y]
    if current:
        runs.append(current)

    seed_mid_y = (sy0 + sy1) / 2.0
    candidate_runs = [r for r in runs if len(r) >= 4 and min(r) <= seed_mid_y <= max(r)]
    if not candidate_runs:
        # Seed may start near table header; allow strong overlap with seed range.
        candidate_runs = [r for r in runs if len(r) >= 4 and max(r) >= sy0 and min(r) <= sy1]
    if not candidate_runs:
        return {
            "status": "review_required_outline_not_resolved",
            "seed_box": list(map(int, seed_box)),
            "search_box": [qx0, qy0, qx1, qy1],
            "horizontal_rules": hlines,
            "selected_family": {"left": left, "right": right, "ys": ys},
            "reason": "printed_rule_family_found_but_no_consecutive_table_run_overlaps_seed",
        }

    run = max(candidate_runs, key=lambda r: (len(r), max(r) - min(r)))
    top, bottom = int(min(run)), int(max(run))
    if right - left < int(sw * 0.90) or bottom - top < int(sh * 0.55):
        return {
            "status": "review_required_outline_not_resolved",
            "seed_box": list(map(int, seed_box)),
            "search_box": [qx0, qy0, qx1, qy1],
            "selected_family": {"left": left, "right": right, "ys": run},
            "reason": "resolved_outline_implausibly_small_relative_to_seed",
        }

    # Include a tiny margin so the detected printed border itself is visible.
    margin = 4
    box = [max(0, left - margin), max(0, top - margin), min(W, right + margin), min(H, bottom + margin)]
    return {
        "status": "ok",
        "seed_box": list(map(int, seed_box)),
        "search_box": [qx0, qy0, qx1, qy1],
        "outline_box": box,
        "printed_left": left,
        "printed_right": right,
        "printed_top": top,
        "printed_bottom": bottom,
        "horizontal_rule_y_source": run,
        "horizontal_rule_family_count": len(run),
        "horizontal_rule_endpoint_tolerance": endpoint_tol,
        "boundary_basis": "repeated_printed_table_rule_endpoints_in_original_traveler",
        "final_crop_uses_handwriting_extent": False,
        "final_crop_uses_fixed_expansion": False,
    }


def _score_six_vertical_rules(xs: Sequence[int], width: int) -> float:
    if len(xs) != 6:
        return -1e9
    gaps = [xs[i + 1] - xs[i] for i in range(5)]
    if min(gaps) <= 8:
        return -1e9
    g1, g2, g3, g4, g5 = [float(g) for g in gaps]
    narrow = _median([g1, g2, g4, g5]) or 1.0
    score = 0.0
    # Two disposition columns should be similarly narrow.
    score -= abs(g1 - g2) / narrow * 20.0
    # Description should dominate the table width.
    score += min(120.0, (g3 / narrow) * 20.0)
    if g3 >= 3.0 * narrow:
        score += 80.0
    # Initials/date must not be mistaken for description-sized columns.
    if g4 <= g3 * 0.35:
        score += 25.0
    if g5 <= g3 * 0.55:
        score += 20.0
    # Outer rules should hug the outline crop.
    score -= abs(xs[0] - 4) * 0.25
    score -= abs((width - 5) - xs[-1]) * 0.25
    return score


def resolve_semantic_columns(table: Image.Image) -> Dict[str, Any]:
    h = table.height
    lines = vertical_rule_segments(table, max(40, int(h * 0.45)))
    xs = sorted(set(int(r["x"]) for r in lines))

    # Ensure detected printed outer borders are eligible even if morphology clips a few pixels.
    candidates = xs[:]
    if not candidates or candidates[0] > 14:
        candidates.insert(0, 4)
    if candidates[-1] < table.width - 15:
        candidates.append(table.width - 5)
    candidates = sorted(set(candidates))

    best: Optional[Tuple[float, Tuple[int, ...]]] = None
    if len(candidates) >= 6:
        # Limit combinatorics by keeping plausible line rules nearest the crop; typical forms have <12.
        pool = candidates[:14]
        for combo in itertools.combinations(pool, 6):
            if combo[0] > table.width * 0.12 or combo[-1] < table.width * 0.88:
                continue
            score = _score_six_vertical_rules(combo, table.width)
            if best is None or score > best[0]:
                best = (score, combo)

    if best is None or best[0] < 25.0:
        return {
            "status": "review_required_semantic_columns_not_resolved",
            "vertical_rule_centers": xs,
            "candidate_rule_centers": candidates,
            "reason": "could_not_resolve_six_rule_repaired_replaced_description_initials_date_pattern",
        }

    x0, x1, x2, x3, x4, x5 = [int(v) for v in best[1]]
    return {
        "status": "ok",
        "vertical_rule_centers": xs,
        "selected_rule_centers": [x0, x1, x2, x3, x4, x5],
        "semantic_columns": {
            "repaired": [x0, x1],
            "replaced": [x1, x2],
            "description": [x2, x3],
            "initials": [x3, x4],
            "date": [x4, x5],
        },
        "selection_score": round(float(best[0]), 3),
    }


def resolve_body_rows(table: Image.Image) -> Dict[str, Any]:
    lines = horizontal_rule_segments(table, max(120, int(table.width * 0.68)))
    ys = sorted(set(int(r["y"]) for r in lines))
    if len(ys) < 4:
        return {
            "status": "review_required_row_grid_not_resolved",
            "horizontal_rule_centers": ys,
            "reason": "fewer_than_four_full_width_printed_horizontal_rules",
        }

    # The first interval is the printed table header.  Data rows begin after its bottom rule.
    body_boundaries = ys[1:]
    if len(body_boundaries) < 3:
        return {
            "status": "review_required_row_grid_not_resolved",
            "horizontal_rule_centers": ys,
            "reason": "insufficient_body_boundaries_after_header",
        }
    return {
        "status": "ok",
        "horizontal_rule_centers": ys,
        "header_bounds": [ys[0], ys[1]],
        "body_boundaries": body_boundaries,
        "physical_row_count": len(body_boundaries) - 1,
    }


def remove_grid_lines(gray: "np.ndarray") -> "np.ndarray":
    if cv2 is None or np is None:
        return gray
    _thr, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = binary.shape
    hk = cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, int(w * 0.35)), 1))
    vk = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, int(h * 0.55))))
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, hk)
    vert = cv2.morphologyEx(binary, cv2.MORPH_OPEN, vk)
    cleaned = cv2.subtract(binary, cv2.bitwise_or(horiz, vert))
    return cleaned


def meaningful_ink_features(cell: Image.Image) -> Dict[str, Any]:
    if np is None or cv2 is None:
        g = ImageOps.grayscale(cell)
        pixels = list(g.getdata())
        dark = sum(1 for p in pixels if p < 145)
        area = max(1, len(pixels))
        return {"ink_pixels": dark, "ink_fraction": dark / area, "component_count": None, "meaningful": dark >= max(150, int(area * 0.001))}

    gray = np.asarray(ImageOps.grayscale(cell))
    if gray.size == 0:
        return {"ink_pixels": 0, "ink_fraction": 0.0, "component_count": 0, "meaningful": False}
    cleaned = remove_grid_lines(gray)
    mask = (cleaned > 0).astype("uint8")
    ink_pixels = int(mask.sum())
    area = int(mask.size)
    n, _labels, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    component_count = 0
    component_area = 0
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a >= 6:
            component_count += 1
            component_area += a
    minimum = max(110, int(area * 0.00075))
    meaningful = ink_pixels >= minimum and component_count >= 1 and component_area >= minimum // 2
    return {
        "ink_pixels": ink_pixels,
        "ink_fraction": round(ink_pixels / max(1, area), 6),
        "component_count": component_count,
        "component_area": component_area,
        "threshold_pixels": minimum,
        "meaningful": bool(meaningful),
    }


def detect_content_rows(table: Image.Image, body_boundaries: Sequence[int], columns: Dict[str, Sequence[int]], out_dir: Path) -> List[Dict[str, Any]]:
    rx0, rx1 = [int(v) for v in columns["repaired"]]
    px0, px1 = [int(v) for v in columns["replaced"]]
    dx0, dx1 = [int(v) for v in columns["description"]]
    ix0, ix1 = [int(v) for v in columns["initials"]]
    tx0, tx1 = [int(v) for v in columns["date"]]
    row_dir = out_dir / "content_rows"
    row_dir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []

    def feat(x0: int, x1: int, y0: int, y1: int, pad: int) -> Dict[str, Any]:
        if x1 <= x0 + 4 or y1 <= y0 + 4:
            return {"ink_pixels": 0, "ink_fraction": 0.0, "component_count": 0, "meaningful": False}
        return meaningful_ink_features(table.crop((x0 + 2, y0 + pad, x1 - 2, y1 - pad)))

    for i in range(len(body_boundaries) - 1):
        y0, y1 = int(body_boundaries[i]), int(body_boundaries[i + 1])
        if y1 - y0 < 12:
            continue
        pad = max(3, min(10, (y1 - y0) // 12))
        desc = feat(dx0, dx1, y0, y1, pad)
        repaired = feat(rx0, rx1, y0, y1, pad)
        replaced = feat(px0, px1, y0, y1, pad)
        initials = feat(ix0, ix1, y0, y1, pad)
        date = feat(tx0, tx1, y0, y1, pad)

        full = table.crop((max(0, rx0), y0, min(table.width, tx1), y1))
        desc_img = table.crop((max(0, dx0), y0, min(table.width, dx1), y1))
        full_path = row_dir / f"row_{i+1:02d}_full.png"
        desc_path = row_dir / f"row_{i+1:02d}_description.png"
        full.save(full_path)
        desc_img.save(desc_path)

        rows.append({
            "physical_row": i + 1,
            "y_bounds": [y0, y1],
            "description_content": desc,
            "meaningful_description_content": bool(desc.get("meaningful")),
            "repaired_mark_present_provisional": bool(repaired.get("meaningful")),
            "replaced_mark_present_provisional": bool(replaced.get("meaningful")),
            "initials_present_provisional": bool(initials.get("meaningful")),
            "date_present_provisional": bool(date.get("meaningful")),
            "full_row_crop": str(full_path),
            "description_crop": str(desc_path),
            "accepted_as_repair_fact": False,
        })
    return rows


def copy_existing_relevance_regions(log_dir: Path, out_dir: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for name in ("identity", "special_notes"):
        src = log_dir / "crops" / f"{name}.png"
        if src.exists():
            dst = out_dir / f"{name}_relevance.png"
            shutil.copy2(src, dst)
            result[name] = {"available": True, "path": str(dst), "boundary_basis": "existing_frozen_region_for_detect_only_validation"}
        else:
            result[name] = {"available": False, "path": None}
    return result


def render_text_report(report: Dict[str, Any]) -> str:
    outline = report.get("repairs_table_outline") or {}
    cols = report.get("semantic_column_resolution") or {}
    grid = report.get("row_grid_resolution") or {}
    lines: List[str] = []
    lines.append(f"# Nova DRL Traveler Reader v{VERSION}")
    lines.append("")
    lines.append(f"Log:                           {report.get('log_number')}")
    lines.append("Detection mode:                printed-outline relevance map")
    lines.append("Detect only:                   YES")
    lines.append(f"Frozen base version:           {BASE_VERSION}")
    lines.append(f"Frozen base status:            {report.get('base_status')}")
    lines.append(f"Repairs table outline:         {outline.get('status')}")
    lines.append(f"Semantic columns:              {cols.get('status')}")
    lines.append(f"Row grid:                      {grid.get('status')}")
    lines.append(f"Physical repair rows:          {grid.get('physical_row_count', 0)}")
    lines.append(f"Meaningful repair rows:        {report.get('meaningful_repair_row_count', 0)}")
    lines.append(f"Rows with repaired mark:       {report.get('rows_with_repaired_mark', 0)}")
    lines.append(f"Rows with replaced mark:       {report.get('rows_with_replaced_mark', 0)}")
    lines.append(f"Unmarked meaningful rows:      {report.get('unmarked_meaningful_row_count', 0)}")
    lines.append("Accepted as repair facts:      0")
    lines.append("Qdrant entries created:        0")
    lines.append("")
    lines.append("REPAIRS/REPLACEMENTS OUTLINE")
    lines.append("----------------------------")
    if outline.get("status") == "ok":
        lines.append(f"Printed outline box: {outline.get('outline_box')}")
        lines.append(f"Boundary basis: {outline.get('boundary_basis')}")
        lines.append("Handwriting used to set outer crop boundary: NO")
        lines.append("Fixed left/right expansion used as final boundary: NO")
        lines.append(f"Full outline crop: {report.get('repairs_outline_crop_path')}")
    else:
        lines.append(f"Outline unresolved: {outline.get('reason')}")
    lines.append("")
    lines.append("RELEVANCE SECTIONS")
    lines.append("------------------")
    lines.append("IDENTITY_HEADER          knowledge/review")
    lines.append("SPECIAL_NOTES            knowledge/review; printed boilerplate is context only")
    lines.append("REPAIRS_REPLACEMENTS     knowledge/review; entire printed table box is captured")
    lines.append("ALL OTHER TRAVELER AREAS raw/audit only")
    lines.append("")
    lines.append("REPAIR CONTENT ROWS")
    lines.append("-------------------")
    for row in report.get("repair_rows", []):
        if not row.get("meaningful_description_content"):
            continue
        marks: List[str] = []
        if row.get("repaired_mark_present_provisional"):
            marks.append("repaired_mark")
        if row.get("replaced_mark_present_provisional"):
            marks.append("replaced_mark")
        mark_text = ",".join(marks) if marks else "none"
        lines.append(f"row {row['physical_row']:02d}: meaningful_content=YES marks={mark_text} review_required=YES fact=NO")
    if report.get("meaningful_repair_row_count", 0) == 0:
        lines.append("None")
    lines.append("")
    lines.append("POLICY")
    lines.append("------")
    lines.append("Printed form outline defines Repairs/Replacements evidence region: YES")
    lines.append("Repaired/Replaced mark required for content preservation: NO")
    lines.append("Printed template accepted as repair fact: NO")
    lines.append("Final-test/shipping fields accepted as Traveler repair knowledge: NO")
    lines.append("Hours in Final Testing used as knowledge: NO")
    lines.append("Automatic repair fact acceptance: NO")
    lines.append("No DRL source files were changed.")
    lines.append("No Qdrant entry created.")
    return "\n".join(lines) + "\n"


def build_report(input_root: Path, log_number: str, source_arg: Optional[str], skip_base_run: bool) -> Dict[str, Any]:
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
    relevance_regions = copy_existing_relevance_regions(log_dir, out_dir)

    outline: Dict[str, Any]
    columns: Dict[str, Any] = {"status": "not_run"}
    row_grid: Dict[str, Any] = {"status": "not_run"}
    rows: List[Dict[str, Any]] = []
    crop_path: Optional[Path] = None

    if not source_path:
        outline = {
            "status": "review_required_source_not_found",
            "seed_box": region.get("pixel_box"),
            "reason": "original_traveler_source_not_found",
        }
    else:
        source = Image.open(source_path).convert("RGB")
        outline = find_repairs_table_outline(source, region["pixel_box"])
        if outline.get("status") == "ok":
            crop_path = out_dir / "repairs_replacements_full_outline.png"
            source.crop(tuple(outline["outline_box"])).save(crop_path)
            table = Image.open(crop_path).convert("RGB")
            columns = resolve_semantic_columns(table)
            row_grid = resolve_body_rows(table)
            if columns.get("status") == "ok" and row_grid.get("status") == "ok":
                rows = detect_content_rows(table, row_grid["body_boundaries"], columns["semantic_columns"], out_dir)

    meaningful = [r for r in rows if r.get("meaningful_description_content")]
    repaired = [r for r in meaningful if r.get("repaired_mark_present_provisional")]
    replaced = [r for r in meaningful if r.get("replaced_mark_present_provisional")]
    unmarked = [r for r in meaningful if not r.get("repaired_mark_present_provisional") and not r.get("replaced_mark_present_provisional")]

    hard_statuses = [outline.get("status"), columns.get("status"), row_grid.get("status")]
    status = "review_ready_outline_complete" if all(s == "ok" for s in hard_statuses) else next((s for s in hard_statuses if s not in ("ok", "not_run")), "review_required")

    return {
        "reader_version": VERSION,
        "base_reader_version": BASE_VERSION,
        "log_number": log_number,
        "detect_only": True,
        "base_status": base_status(entries),
        "base_run": base_run,
        "source_traveler": str(source_path) if source_path else None,
        "source_modified": False,
        "regions_source": str(regions_path),
        "repairs_seed_pixel_box": region.get("pixel_box"),
        "repairs_table_outline": outline,
        "repairs_outline_crop_path": str(crop_path) if crop_path else None,
        "semantic_column_resolution": columns,
        "row_grid_resolution": row_grid,
        "relevance_regions": relevance_regions,
        "repair_rows": rows,
        "meaningful_repair_row_count": len(meaningful),
        "rows_with_repaired_mark": len(repaired),
        "rows_with_replaced_mark": len(replaced),
        "unmarked_meaningful_row_count": len(unmarked),
        "repair_segmentation_policy": "printed_outline_defines_region_content_first_marks_are_attributes_not_gates",
        "automatic_fact_acceptance": False,
        "accepted_as_repair_fact_count": 0,
        "qdrant_entry_created": False,
        "status": status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Nova DRL Traveler Reader v{VERSION}")
    parser.add_argument("input_root", help="Traveler Reader v1.3.1 serial output directory")
    parser.add_argument("--log", required=True, help="DRL log number")
    parser.add_argument("--detect-only", action="store_true", help="Required; no MiniCPM/Ollama transcription")
    parser.add_argument("--source", help="Optional explicit original Traveler image path")
    parser.add_argument("--skip-base-run", action="store_true", help="Use existing frozen v1.3.4.4.3 artifacts without rerunning base detect-only")
    args = parser.parse_args()

    if not args.detect_only:
        print("ERROR: v1.3.4.4.5 is detect-only during outline validation; rerun with --detect-only.", file=sys.stderr)
        return 2

    try:
        report = build_report(Path(args.input_root), args.log, args.source, args.skip_base_run)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    out_dir = Path(args.input_root) / args.log / OUTPUT_DIR_NAME
    save_json(out_dir / "traveler_relevance_review_v1_3_4_4_5.json", report)
    text = render_text_report(report)
    (out_dir / "traveler_relevance_review_v1_3_4_4_5.txt").write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"Reports: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
