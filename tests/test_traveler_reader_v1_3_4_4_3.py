#!/usr/bin/env python3
import importlib.util
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_traveler_reader_v1_3_4_4.py"

spec = importlib.util.spec_from_file_location("reader13443", str(TARGET))
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)

assert reader.VERSION == "1.3.4.4.3"

def synthetic_table(path):
    width, height = 2048, 1575
    top, row_h, rows = 155, 110, 12
    lines = [top + i * row_h for i in range(rows + 1)]
    div1, desc, init, date = 183, 259, 1719, 1905

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for x in [div1, desc, init, date]:
        draw.line([x, 0, x, lines[-1]], fill=(25,25,25), width=4)
    for y in lines:
        draw.line([0, y, width-1, y], fill=(25,25,25), width=4)

    for row in [1, 11]:
        y = lines[row-1] + 8
        x = 45
        draw.line([x, y, x+90, y+90], fill=(40,60,160), width=10)
        draw.line([x+90, y, x, y+90], fill=(40,60,160), width=10)

    # Sparse continuation writing must not create action starts.
    for row in [8, 9, 10]:
        y = lines[row-1] + 35
        draw.line([5, y, 160, y+4], fill=(70,70,70), width=3)
        draw.arc([30, y-20, 170, y+30], 185, 350, fill=(70,70,70), width=3)

    image.save(path)

with tempfile.TemporaryDirectory() as tmp:
    crop = Path(tmp) / "repairs.png"
    synthetic_table(crop)
    image = Image.open(crop).convert("RGB")
    layout = reader.detect_table_layout(image)
    assert layout["status"] == "ok"
    assert layout["physical_row_count"] == 12

    markers, _ = reader.start_mark_components(image, layout)
    assert [m["physical_row"] for m in markers] == [1, 11]

    scores = reader.physical_row_mark_scores(image, layout)
    blocks = reader.build_blocks(
        image, layout, row_scores=scores, start_markers=markers
    )
    assert len(blocks) == 2
    assert blocks[0]["start_physical_row"] == 1
    assert blocks[0]["end_physical_row"] == 10
    assert blocks[1]["start_physical_row"] == 11
    assert blocks[1]["end_physical_row"] == 12

print("PASS: Nova Traveler Reader v1.3.4.4.3 tests")
