#!/usr/bin/env python3
"""
Nova DRL Traveler Reader v1.3.4.4.6

Outline-first Traveler relevance hardening.

Human-defined Traveler relevance map:
  * identity/header anchors
  * Special Notes
  * complete Repairs/Replacements table

Key change from v1.3.4.4.5:
  The Repairs/Replacements evidence region is defined by the PRINTED TABLE
  GRID NETWORK in the original Traveler. The full rectangle is recovered from
  semantic vertical rules plus repeated horizontal-rule coverage, so broken or
  segmented horizontal strokes do not need identical endpoints.

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

VERSION = "1.3.4.4.6"
BASE_VERSION = "1.3.4.4.3"
BASE_SCRIPT_NAME = "nova_traveler_reader_v1_3_4_4.py"
OUTPUT_DIR_NAME = "vision_extraction_v1_3_4_4_6"
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
        raise RuntimeError("NumPy/OpenCV required for v1.3.4.4.6 outline detection")
    return np.asarray(ImageOps.grayscale(image))


def _binary_inv(image: Image.Image) -> "np.ndarray":
    if cv2 is None or np is None:
        raise RuntimeError("OpenCV required for v1.3.4.4.6 outline detection")
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



def _projection_clusters(mask: "np.ndarray", axis: str, threshold: int, offset: int = 0) -> List[Dict[str, Any]]:
    """Cluster strong morphology projection peaks into printed-rule centers."""
    if np is None:
        return []
    if axis == "vertical":
        strengths = np.count_nonzero(mask, axis=0)
    else:
        strengths = np.count_nonzero(mask, axis=1)
    idx = [int(i) for i, v in enumerate(strengths) if int(v) >= threshold]
    if not idx:
        return []
    groups: List[List[int]] = [[idx[0]]]
    for i in idx[1:]:
        if i - groups[-1][-1] <= 2:
            groups[-1].append(i)
        else:
            groups.append([i])
    out: List[Dict[str, Any]] = []
    for g in groups:
        best = max(g, key=lambda i: int(strengths[i]))
        if axis == "vertical":
            band = mask[:, max(0, g[0]-1):min(mask.shape[1], g[-1]+2)]
            occupied = np.any(band > 0, axis=1)
        else:
            band = mask[max(0, g[0]-1):min(mask.shape[0], g[-1]+2), :]
            occupied = np.any(band > 0, axis=0)
        locs = np.where(occupied)[0]
        out.append({
            "center": int(best + offset),
            "strength": int(strengths[best]),
            "span0": int(locs.min()) if len(locs) else None,
            "span1": int(locs.max()) if len(locs) else None,
            "thickness": int(g[-1] - g[0] + 1),
        })
    return out


def vertical_rule_projection_records(image: Image.Image, min_fraction: float = 0.20) -> List[Dict[str, Any]]:
    if cv2 is None or np is None:
        return []
    binary = _binary_inv(image)
    h, w = binary.shape
    kernel_h = max(45, int(h * 0.18))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, kernel_h))
    lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    threshold = max(35, int(h * min_fraction))
    return _projection_clusters(lines, "vertical", threshold)


def horizontal_rule_projection_records(image: Image.Image, left: int, right: int, top: int = 0, bottom: Optional[int] = None, min_coverage: float = 0.38) -> List[Dict[str, Any]]:
    """
    Detect printed horizontal rules by combined coverage across a known table span.

    Unlike v1.3.4.4.5, this does not require every horizontal rule to be one
    contour with identical endpoints. Segments separated by vertical rules are
    accumulated in the projection and count as one printed row rule.
    """
    if cv2 is None or np is None:
        return []
    bottom = image.height if bottom is None else int(bottom)
    left = max(0, int(left)); right = min(image.width, int(right))
    top = max(0, int(top)); bottom = min(image.height, int(bottom))
    if right - left < 40 or bottom - top < 40:
        return []
    crop = image.crop((left, top, right, bottom)).convert("RGB")
    binary = _binary_inv(crop)
    h, w = binary.shape
    kernel_w = max(35, int(w * 0.055))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, 1))
    lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    threshold = max(45, int(w * min_coverage))
    records = _projection_clusters(lines, "horizontal", threshold, offset=top)
    for r in records:
        r["coverage_fraction"] = round(r["strength"] / max(1, w), 4)
    return records


def _score_six_vertical_rules(xs: Sequence[int], width: int) -> float:
    if len(xs) != 6:
        return -1e9
    gaps = [xs[i + 1] - xs[i] for i in range(5)]
    if min(gaps) <= 8:
        return -1e9
    g1, g2, g3, g4, g5 = [float(g) for g in gaps]
    narrow = _median([g1, g2, g4, g5]) or 1.0
    score = 0.0
    score -= abs(g1 - g2) / narrow * 22.0
    score += min(150.0, (g3 / narrow) * 22.0)
    if g3 >= 2.8 * narrow:
        score += 90.0
    if g4 <= g3 * 0.42:
        score += 28.0
    if g5 <= g3 * 0.55:
        score += 24.0
    # If evaluating a table-local crop, prefer outer rules near the crop edges.
    if width > 0:
        score -= min(50.0, abs(xs[0] - 4) * 0.18)
        score -= min(50.0, abs((width - 5) - xs[-1]) * 0.18)
    return score


def _select_semantic_six_source(records: List[Dict[str, Any]], seed_box: Sequence[int], search_left: int, search_right: int) -> Optional[Dict[str, Any]]:
    sx0, _sy0, sx1, _sy1 = [int(v) for v in seed_box]
    sw = max(1, sx1 - sx0)
    xs = sorted(set(int(r["center"]) for r in records))
    if len(xs) < 6:
        return None
    best: Optional[Tuple[float, Tuple[int, ...]]] = None
    # Forms normally contribute a modest number of long vertical rules. Keep
    # the strongest geometric candidates but do not assume absolute positions.
    for combo in itertools.combinations(xs[:18], 6):
        span = combo[-1] - combo[0]
        if span < sw * 0.95 or span > sw * 3.6:
            continue
        # The complete table should substantially bracket the older seed crop.
        if combo[0] > sx0 + int(sw * 0.22):
            continue
        if combo[-1] < sx1 - int(sw * 0.12):
            continue
        gaps = [combo[i+1] - combo[i] for i in range(5)]
        narrow = _median([gaps[0], gaps[1], gaps[3], gaps[4]]) or 1.0
        if gaps[2] < 2.2 * narrow:
            continue
        score = _score_six_vertical_rules(combo, 0)
        if combo[0] < sx0:
            score += 35.0
        if combo[-1] > sx1:
            score += 35.0
        # Prefer an outer span centered near the seed, without requiring the
        # seed to begin at any particular semantic column.
        seed_mid = (sx0 + sx1) / 2.0
        combo_mid = (combo[0] + combo[-1]) / 2.0
        score -= abs(combo_mid - seed_mid) / max(1.0, sw) * 25.0
        if search_left <= combo[0] and combo[-1] <= search_right:
            score += 5.0
        if best is None or score > best[0]:
            best = (score, combo)
    if best is None or best[0] < 65.0:
        return None
    combo = [int(v) for v in best[1]]
    return {"score": round(float(best[0]), 3), "rules": combo}


def _dominant_pitch(ys: Sequence[int]) -> Optional[float]:
    if len(ys) < 4:
        return None
    gaps = [b-a for a,b in zip(ys, ys[1:]) if 18 <= b-a <= 450]
    if not gaps:
        return None
    # Find the densest 12-pixel gap neighborhood rather than taking a median
    # across unrelated adjacent form sections.
    best_group: List[int] = []
    for g in gaps:
        group = [h for h in gaps if abs(h-g) <= max(8, int(g*0.14))]
        if len(group) > len(best_group):
            best_group = group
    return _median(best_group or gaps)


def _select_regular_horizontal_run(records: List[Dict[str, Any]], seed_box: Sequence[int]) -> Optional[Dict[str, Any]]:
    ys = sorted(set(int(r["center"]) for r in records))
    if len(ys) < 4:
        return None
    pitch = _dominant_pitch(ys)
    if not pitch or pitch < 18:
        return None
    lo = pitch * 0.52
    hi = pitch * 1.62
    runs: List[List[int]] = []
    cur: List[int] = [ys[0]]
    for y in ys[1:]:
        gap = y-cur[-1]
        # One missing printed rule is allowed; it is reconstructed below.
        if lo <= gap <= hi or (pitch*1.70 <= gap <= pitch*2.35):
            cur.append(y)
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = [y]
    if len(cur) >= 2:
        runs.append(cur)

    sy0, sy1 = int(seed_box[1]), int(seed_box[3])
    seed_mid = (sy0+sy1)/2.0
    candidates: List[Tuple[float,List[int]]] = []
    for r in runs:
        if len(r) < 5:
            continue
        if max(r) < sy0 - pitch or min(r) > sy1 + pitch:
            continue
        gaps = [b-a for a,b in zip(r,r[1:])]
        regular = sum(1 for g in gaps if lo <= g <= hi)
        score = len(r)*100 + regular*15 - abs(((min(r)+max(r))/2.0)-seed_mid)/max(1.0,pitch)
        candidates.append((score,r))
    if not candidates:
        return None
    _score, run = max(candidates, key=lambda x:x[0])

    # Reconstruct a single missing rule when a gap is approximately 2*pitch.
    rebuilt: List[int] = [run[0]]
    inserted: List[int] = []
    for a,b in zip(run,run[1:]):
        gap=b-a
        if pitch*1.70 <= gap <= pitch*2.35:
            y=int(round((a+b)/2.0))
            rebuilt.append(y); inserted.append(y)
        rebuilt.append(b)
    rebuilt=sorted(set(rebuilt))
    return {"ys":rebuilt,"pitch":round(float(pitch),3),"inserted_y":inserted}


def find_repairs_table_outline(source: Image.Image, seed_box: Sequence[int]) -> Dict[str, Any]:
    """
    Resolve the complete Repairs/Replacements rectangle from the printed grid.

    v1.3.4.4.6 intentionally resolves semantic vertical rules FIRST, then uses
    combined horizontal-rule coverage across that span. This tolerates printed
    horizontal rules that are broken into column segments or whose contour
    endpoints vary slightly. Handwriting never determines the outer boundary.
    """
    sx0, sy0, sx1, sy1 = [int(v) for v in seed_box]
    sw=max(1,sx1-sx0); sh=max(1,sy1-sy0)
    W,H=source.size
    qx0=max(0,int(round(sx0-sw*1.20)))
    qx1=min(W,int(round(sx1+sw*1.20)))
    qy0=max(0,int(round(sy0-sh*0.55)))
    qy1=min(H,int(round(sy1+sh*0.55)))
    search=source.crop((qx0,qy0,qx1,qy1)).convert("RGB")

    local_v=vertical_rule_projection_records(search, min_fraction=0.17)
    source_v=[]
    for r in local_v:
        rr=dict(r); rr["center"]=int(r["center"]+qx0)
        if rr.get("span0") is not None:
            rr["span0"]=int(rr["span0"]+qy0); rr["span1"]=int(rr["span1"]+qy0)
        source_v.append(rr)
    semantic=_select_semantic_six_source(source_v,seed_box,qx0,qx1)
    if semantic is None:
        return {
            "status":"review_required_outline_not_resolved",
            "seed_box":list(map(int,seed_box)),
            "search_box":[qx0,qy0,qx1,qy1],
            "vertical_rule_candidates":[r["center"] for r in source_v],
            "reason":"six_semantic_vertical_rules_not_resolved_from_printed_grid",
        }

    x0,x1,x2,x3,x4,x5=semantic["rules"]
    # The selected printed vertical rules also tell us the approximate table
    # top/bottom. Use their common span to reject neighboring form sections
    # before looking for horizontal rules.
    selected_v_records=[]
    for x in (x0,x1,x2,x3,x4,x5):
        nearest=min(source_v,key=lambda r:abs(int(r["center"])-x))
        selected_v_records.append(nearest)
    v_tops=[int(r["span0"]) for r in selected_v_records if r.get("span0") is not None]
    v_bottoms=[int(r["span1"]) for r in selected_v_records if r.get("span1") is not None]
    if v_tops and v_bottoms:
        vertical_top=int(round(_median(v_tops)))
        vertical_bottom=int(round(_median(v_bottoms)))
        vmargin=max(12,int(sh*0.08))
        hqy0=max(qy0,vertical_top-vmargin)
        hqy1=min(qy1,vertical_bottom+vmargin)
    else:
        vertical_top=None; vertical_bottom=None; hqy0=qy0; hqy1=qy1
    hrecords=horizontal_rule_projection_records(source,x0,x5,hqy0,hqy1,min_coverage=0.34)
    run=_select_regular_horizontal_run(hrecords,seed_box)
    if run is None:
        return {
            "status":"review_required_outline_not_resolved",
            "seed_box":list(map(int,seed_box)),
            "search_box":[qx0,qy0,qx1,qy1],
            "selected_vertical_rules":semantic,
            "selected_vertical_common_span":[vertical_top,vertical_bottom],
            "horizontal_search_y":[hqy0,hqy1],
            "horizontal_rule_candidates":[{"y":r["center"],"coverage_fraction":r.get("coverage_fraction")} for r in hrecords],
            "reason":"regular_horizontal_rule_run_not_resolved_across_semantic_table_span",
        }
    ys=run["ys"]
    top,bottom=int(min(ys)),int(max(ys))
    if bottom-top < sh*0.55 or x5-x0 < sw*0.95:
        return {
            "status":"review_required_outline_not_resolved",
            "seed_box":list(map(int,seed_box)),
            "search_box":[qx0,qy0,qx1,qy1],
            "selected_vertical_rules":semantic,
            "selected_horizontal_run":run,
            "reason":"resolved_printed_grid_rectangle_implausibly_small_relative_to_seed",
        }
    margin=4
    box=[max(0,x0-margin),max(0,top-margin),min(W,x5+margin),min(H,bottom+margin)]
    return {
        "status":"ok",
        "seed_box":list(map(int,seed_box)),
        "search_box":[qx0,qy0,qx1,qy1],
        "outline_box":box,
        "printed_left":x0,"printed_right":x5,"printed_top":top,"printed_bottom":bottom,
        "semantic_vertical_rules_source":[x0,x1,x2,x3,x4,x5],
        "semantic_vertical_selection_score":semantic["score"],
        "selected_vertical_common_span":[vertical_top,vertical_bottom],
        "horizontal_search_y":[hqy0,hqy1],
        "horizontal_rule_y_source":ys,
        "horizontal_rule_pitch":run["pitch"],
        "horizontal_rule_inserted_y":run["inserted_y"],
        "boundary_basis":"printed_grid_network_semantic_vertical_rules_plus_horizontal_projection_coverage",
        "final_crop_uses_handwriting_extent":False,
        "final_crop_uses_fixed_expansion":False,
    }


def resolve_semantic_columns(table: Image.Image) -> Dict[str, Any]:
    records=vertical_rule_projection_records(table,min_fraction=0.34)
    xs=sorted(set(int(r["center"]) for r in records))
    candidates=xs[:]
    if not candidates or candidates[0] > 14:
        candidates.insert(0,4)
    if not candidates or candidates[-1] < table.width-15:
        candidates.append(table.width-5)
    candidates=sorted(set(candidates))
    best:Optional[Tuple[float,Tuple[int,...]]]=None
    for combo in itertools.combinations(candidates[:16],6):
        if combo[0] > table.width*0.12 or combo[-1] < table.width*0.88:
            continue
        score=_score_six_vertical_rules(combo,table.width)
        if best is None or score>best[0]: best=(score,combo)
    if best is None or best[0] < 35.0:
        return {"status":"review_required_semantic_columns_not_resolved","vertical_rule_centers":xs,"candidate_rule_centers":candidates,"reason":"could_not_resolve_six_rule_repaired_replaced_description_initials_date_pattern"}
    x0,x1,x2,x3,x4,x5=[int(v) for v in best[1]]
    return {"status":"ok","vertical_rule_centers":xs,"selected_rule_centers":[x0,x1,x2,x3,x4,x5],"semantic_columns":{"repaired":[x0,x1],"replaced":[x1,x2],"description":[x2,x3],"initials":[x3,x4],"date":[x4,x5]},"selection_score":round(float(best[0]),3)}


def resolve_body_rows(table: Image.Image) -> Dict[str, Any]:
    # Because the crop is already the printed table box, projection coverage can
    # collect segmented rules without requiring one full-width contour.
    records=horizontal_rule_projection_records(table,4,max(5,table.width-4),0,table.height,min_coverage=0.34)
    ys=sorted(set(int(r["center"]) for r in records))
    if len(ys)<4:
        return {"status":"review_required_row_grid_not_resolved","horizontal_rule_centers":ys,"reason":"fewer_than_four_printed_horizontal_rules_by_projection"}
    pitch=_dominant_pitch(ys)
    inserted:List[int]=[]
    if pitch:
        rebuilt=[ys[0]]
        for a,b in zip(ys,ys[1:]):
            gap=b-a
            if pitch*1.70 <= gap <= pitch*2.35:
                y=int(round((a+b)/2)); rebuilt.append(y); inserted.append(y)
            rebuilt.append(b)
        ys=sorted(set(rebuilt))
    # Outer crop margin may place the first/last rule a few pixels from edge.
    if ys[0] > max(18,int(table.height*0.035)):
        return {"status":"review_required_row_grid_not_resolved","horizontal_rule_centers":ys,"reason":"top_printed_rule_not_near_outline_crop_edge"}
    if table.height-ys[-1] > max(18,int(table.height*0.035)):
        return {"status":"review_required_row_grid_not_resolved","horizontal_rule_centers":ys,"reason":"bottom_printed_rule_not_near_outline_crop_edge"}
    body_boundaries=ys[1:]
    if len(body_boundaries)<3:
        return {"status":"review_required_row_grid_not_resolved","horizontal_rule_centers":ys,"reason":"insufficient_body_boundaries_after_header"}
    return {"status":"ok","horizontal_rule_centers":ys,"header_bounds":[ys[0],ys[1]],"body_boundaries":body_boundaries,"physical_row_count":len(body_boundaries)-1,"pitch":round(float(pitch),3) if pitch else None,"inserted_y":inserted}


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
    lines.append("Detection mode:                printed-grid-network relevance map")
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
        print("ERROR: v1.3.4.4.6 is detect-only during outline validation; rerun with --detect-only.", file=sys.stderr)
        return 2

    try:
        report = build_report(Path(args.input_root), args.log, args.source, args.skip_base_run)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    out_dir = Path(args.input_root) / args.log / OUTPUT_DIR_NAME
    save_json(out_dir / "traveler_relevance_review_v1_3_4_4_6.json", report)
    text = render_text_report(report)
    (out_dir / "traveler_relevance_review_v1_3_4_4_6.txt").write_text(text, encoding="utf-8")
    print(text, end="")
    print(f"Reports: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
