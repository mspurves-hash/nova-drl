#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_traveler_reader_v1_3_4_4.py"

spec = importlib.util.spec_from_file_location("reader13441", str(TARGET))
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)

assert reader.VERSION == "1.3.4.4.1"


def make_partial_crop(path):
    # Mimics the production v1.3.1 crop: left table border is outside image,
    # first visible vertical is the Repaired/Replaced divider, row height ~110.
    width, height = 2048, 1582
    body_top, row_h, rows = 156, 110, 12
    div1, desc, init, date = 115, 259, 1719, 1905

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    for x in [div1, desc, init, date]:
        draw.line([x, 0, x, body_top + rows * row_h], fill=(25,25,25), width=4)

    # Horizontal lines run to crop edges because table borders are clipped.
    for i in range(rows + 1):
        y = body_top + i * row_h
        draw.line([0, y, width - 1, y], fill=(25,25,25), width=4)

    # Extra lower line, like the actual crop, should not be absorbed into grid.
    draw.line([0, 1541, width - 1, 1541], fill=(25,25,25), width=4)

    # Two logical action-start marks, with spill into following rows.
    for row in [1, 11]:
        y = body_top + (row - 1) * row_h + 12
        x = 30
        draw.line([x, y, x+55, y+72], fill=(45,70,165), width=8)
        draw.line([x+55, y, x, y+72], fill=(45,70,165), width=8)

    image.save(path)


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    crop = root / "repairs_replacements.png"
    make_partial_crop(crop)

    image = Image.open(crop)
    layout = reader.detect_table_layout(image)
    assert layout["status"] == "ok"
    assert layout["crop_left_clipped"] is True
    assert layout["physical_row_count"] == 12
    assert layout["table_left"] == 0
    assert layout["description_left"] > 200

    scores = reader.physical_row_mark_scores(image, layout)
    blocks = reader.build_blocks(image, layout, scores)
    assert len(blocks) == 2
    assert blocks[0]["start_physical_row"] == 1
    assert blocks[1]["start_physical_row"] == 11

    serial = root / "SERIAL"
    log_dir = serial / "130813004"
    (log_dir / "crops").mkdir(parents=True)
    crop.replace(log_dir / "crops" / "repairs_replacements.png")
    (log_dir / "traveler_regions.json").write_text(
        json.dumps({
            "source_path": "/mnt/drl/example/130813004 Line Card Warranty.JPG",
            "relative_path": "130813004 Line Card Warranty.JPG",
        }),
        encoding="utf-8",
    )

    record = reader.process_log(
        log_dir,
        "130813004",
        model="minicpm-v:latest",
        detect_only=True,
        expected_entries=2,
    )
    assert record["status"] == "ok"
    assert record["detected_start_marks"] == 2
    assert record["detected_repair_entries"] == 2
    assert record["accepted_as_facts"] == 0
    assert record["qdrant_created"] is False

print("PASS: Nova Traveler Reader v1.3.4.4.1 tests")
