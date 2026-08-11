#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_traveler_reader_v1_3_4_4.py"

spec = importlib.util.spec_from_file_location("reader13442", str(TARGET))
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)

assert reader.VERSION == "1.3.4.4.2"

# Exact horizontal lines reported on the live Ubuntu server for 130813004.
live_records = [
    {"y": 214, "x": 0, "width": 2798, "height": 1},
    {"y": 362, "x": 0, "width": 2798, "height": 1},
    {"y": 512, "x": 0, "width": 2798, "height": 1},
    {"y": 811, "x": 0, "width": 2798, "height": 1},
    {"y": 961, "x": 0, "width": 2798, "height": 1},
    {"y": 1112, "x": 0, "width": 2798, "height": 1},
    {"y": 1260, "x": 0, "width": 2798, "height": 1},
    {"y": 1411, "x": 0, "width": 2798, "height": 1},
    {"y": 2031, "x": 0, "width": 2798, "height": 1},
]

augmented, diag = reader.reconstruct_missing_horizontal_lines(live_records)
ys = [row["y"] for row in augmented]
assert diag["status"] == "ok"
assert diag["inserted_count"] == 4
assert len(ys) == 13
assert ys[:3] == [214, 362, 512]
assert 660 <= ys[3] <= 665
assert ys[-1] == 2031

body = reader.find_regular_body_run(augmented)
assert len(body) == 13
assert body[0] == 214
assert body[-1] == 2031

# Synthetic high-resolution partial crop with 12 physical rows but four
# horizontal rules deliberately erased. Detection must reconstruct the grid
# and still find two variable-height repair blocks.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    crop = root / "repairs_replacements.png"

    width, height = 2798, 2162
    body_lines = [
        214, 362, 512, 662, 811, 961, 1112,
        1260, 1411, 1566, 1721, 1876, 2031
    ]
    div1, desc, init, date = 250, 354, 2349, 2603

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    for x in [div1, desc, init, date]:
        draw.line([x, 0, x, body_lines[-1]], fill=(25,25,25), width=5)

    # Reproduce the live detector's missing rules by not drawing them.
    missing = {662, 1566, 1721, 1876}
    for y in body_lines:
        if y not in missing:
            draw.line([0, y, width - 1, y], fill=(25,25,25), width=5)

    # Two blue X start marks, each spilling into an adjacent physical row.
    for row in [1, 11]:
        top = body_lines[row - 1]
        y = top + 18
        x = 42
        draw.line([x, y, x + 74, y + 102], fill=(45,70,165), width=10)
        draw.line([x + 74, y, x, y + 102], fill=(45,70,165), width=10)

    image.save(crop)

    layout = reader.detect_table_layout(image)
    assert layout["status"] == "ok"
    assert layout["physical_row_count"] == 12
    assert layout["crop_left_clipped"] is True
    assert layout["horizontal_line_reconstruction"]["inserted_count"] >= 4

    scores = reader.physical_row_mark_scores(image, layout)
    blocks = reader.build_blocks(image, layout, scores)
    assert len(blocks) == 2
    assert blocks[0]["start_physical_row"] == 1
    assert blocks[1]["start_physical_row"] == 11

print("PASS: Nova Traveler Reader v1.3.4.4.2 tests")
