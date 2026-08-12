#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

SCRIPT = Path(__file__).resolve().parents[1] / "ingest" / "nova_traveler_reader_v1_3_4_4_8.py"
spec = importlib.util.spec_from_file_location("nova448", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def synthetic_table() -> tuple[Image.Image, list[int], list[int]]:
    W, H = 1800, 1200
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    # Full table box deliberately extends far beyond the frozen seed.
    left, right, top, bottom = 160, 1640, 260, 1030
    ys = list(range(top, bottom + 1, 70))
    if ys[-1] != bottom:
        ys.append(bottom)
    for y in ys:
        d.line((left, y, right, y), fill="black", width=3)
    # Internal vertical rules exist but v1.3.4.4.8 must not need them.
    for x in [left, 250, 350, 1320, 1470, right]:
        d.line((x, top, x, bottom), fill="black", width=3)
    # Handwriting-like strokes do not define the crop.
    d.line((430, 420, 980, 450), fill="black", width=6)
    d.line((410, 560, 1180, 600), fill="black", width=5)
    seed = [360, 330, 1280, 900]  # clipped left AND right
    expected = [left, top, right, bottom]
    return img, seed, expected


def test_outer_box_detection():
    img, seed, expected = synthetic_table()
    result = mod.find_outer_repairs_box(img, seed)
    assert result["status"] == "ok", result
    box = result["outline_box"]
    # Margin is allowed; complete printed box must be included.
    assert box[0] <= expected[0] + 15, box
    assert box[1] <= expected[1] + 15, box
    assert box[2] >= expected[2] - 15, box
    assert box[3] >= expected[3] - 15, box
    assert result["internal_columns_used"] is False
    assert result["handwriting_extent_used"] is False
    assert result["repaired_replaced_marks_used"] is False


def test_prompt_is_literal_and_column_agnostic():
    p = mod.handwriting_prompt().lower()
    assert "transcribe the handwriting literally" in p
    assert "do not classify anything as repaired, replaced, description, initials, or date" in p
    assert "do not group handwriting based on internal table columns" in p


def test_response_parser():
    raw = json.dumps({"handwritten_lines": ["Brushes Z x3", "Lower Belts A1 + A2"], "unreadable_fragments": []})
    parsed = mod.parse_handwriting_response(raw)
    assert parsed == {"handwritten_lines": ["Brushes Z x3", "Lower Belts A1 + A2"], "unreadable_fragments": []}


if __name__ == "__main__":
    test_outer_box_detection()
    test_prompt_is_literal_and_column_agnostic()
    test_response_parser()
    print("PASS: Nova Traveler Reader v1.3.4.4.8 outer-box handwriting tests")
