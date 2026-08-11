#!/usr/bin/env python3
import importlib.util
import json
import os
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_traveler_reader_v1_3_4_4.py"

spec = importlib.util.spec_from_file_location("reader1344", str(TARGET))
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)

assert reader.VERSION == "1.3.4.4"


def synthetic_table(path, starts, rows=12, row_h=42):
    width = 900
    header_top = 30
    header_h = 120
    body_top = header_top + header_h
    table_left = 40
    repaired_div = 95
    desc_left = 150
    init_left = 720
    date_left = 805
    right = 880

    image = Image.new("RGB", (920, body_top + rows * row_h + 40), "white")
    draw = ImageDraw.Draw(image)

    # Table header.
    draw.rectangle(
        [table_left, header_top, right, body_top],
        outline=(30, 30, 30),
        width=2,
    )
    for x in [repaired_div, desc_left, init_left, date_left]:
        draw.line([x, header_top, x, body_top + rows * row_h], fill=(30,30,30), width=2)

    # Repair rows.
    for index in range(rows + 1):
        y = body_top + index * row_h
        draw.line([table_left, y, right, y], fill=(30,30,30), width=2)
    draw.line([table_left, header_top, table_left, body_top + rows * row_h], fill=(30,30,30), width=2)
    draw.line([right, header_top, right, body_top + rows * row_h], fill=(30,30,30), width=2)

    # Draw blue X start marks. Allow selected marks to spill into the next row.
    for row in starts:
        y = body_top + (row - 1) * row_h + 8
        x = table_left + 18
        draw.line([x, y, x + 25, y + 29], fill=(60,80,170), width=5)
        draw.line([x + 25, y, x, y + 29], fill=(60,80,170), width=5)

    image.save(path)


with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    case1 = root / "two_blocks.png"
    synthetic_table(case1, [1, 11], rows=12)

    image = Image.open(case1)
    layout = reader.detect_table_layout(image)
    assert layout["status"] == "ok"
    assert layout["physical_row_count"] == 12

    scores = reader.physical_row_mark_scores(image, layout)
    blocks = reader.build_blocks(image, layout, scores)
    assert len(blocks) == 2
    assert blocks[0]["start_physical_row"] == 1
    assert blocks[1]["start_physical_row"] == 11
    assert blocks[0]["physical_rows_spanned"] == 10
    assert blocks[1]["physical_rows_spanned"] == 2

    case2 = root / "five_blocks.png"
    synthetic_table(case2, [2, 4, 6, 8, 12], rows=13)
    image2 = Image.open(case2)
    layout2 = reader.detect_table_layout(image2)
    scores2 = reader.physical_row_mark_scores(image2, layout2)
    blocks2 = reader.build_blocks(image2, layout2, scores2)
    assert [b["start_physical_row"] for b in blocks2] == [2, 4, 6, 8, 12]

    # Full detect-only integration: no vision call and no fact acceptance.
    serial_root = root / "SERIAL"
    log_dir = serial_root / "130813004"
    crops = log_dir / "crops"
    crops.mkdir(parents=True)
    case1.replace(crops / "repairs_replacements.png")

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
    assert record["detect_only"] is True
    assert record["detected_start_marks"] == 2
    assert record["detected_repair_entries"] == 2
    assert record["evidence_comparison_eligible"] == 0
    assert record["accepted_as_facts"] == 0
    assert record["qdrant_created"] is False
    assert (log_dir / "repair_entries_v1_3_4_4.json").exists()

print("PASS: Nova Traveler Reader v1.3.4.4 tests")
