#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ingest" / "nova_traveler_reader_v1_3_5_0.py"
spec = importlib.util.spec_from_file_location("r", SCRIPT)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

# Whole-page orientation only: no cropping.
portrait = Image.new("RGB", (1000, 1600), "white")
page, status = r.normalize_full_page(portrait)
assert page.size == (1600, 1000)
assert status.startswith("rotated_90")

landscape = Image.new("RGB", (1600, 1000), "white")
page2, status2 = r.normalize_full_page(landscape)
assert page2.size == landscape.size
assert status2 == "already_landscape"

prompt = r.whole_page_prompt()
assert "ENTIRE Traveler image" in prompt
assert "Do not crop, isolate, localize, box" in prompt
assert "Do not decide what is important" in prompt
assert "Repeated printed form text is intentionally included" in prompt
assert "several, many, or some" in prompt

parsed = r.parse_vision_response(json.dumps({
    "raw_lines": ["Printed Label", "Handwritten R8ZZ x3"],
    "unreadable_fragments": ["? motor note"],
}))
assert parsed == {
    "raw_lines": ["Printed Label", "Handwritten R8ZZ x3"],
    "unreadable_fragments": ["? motor note"],
}

# End-to-end detect-only with an explicit whole source image.
with tempfile.TemporaryDirectory() as td:
    root = Path(td) / "serial"
    log_dir = root / "150622005"
    log_dir.mkdir(parents=True)
    source = Path(td) / "150622005 Line Card Original.jpg"
    Image.new("RGB", (900, 1400), "white").save(source)
    report = r.build_report(root, "150622005", True, str(source), "minicpm-v:latest")
    assert report["status"] == "review_ready_whole_traveler_capture"
    assert report["whole_page_crop_used"] is False
    assert report["relevance_boxes_used"] is False
    assert report["row_or_column_geometry_used"] is False
    assert report["mark_gating_used"] is False
    assert report["accepted_as_repair_fact_count"] == 0
    assert report["qdrant_entry_created"] is False
    derivative = Path(report["whole_page_derivative"])
    assert derivative.exists()
    with Image.open(derivative) as im:
        assert im.size == (1400, 900), im.size
    assert report["source_sha256"] == r.sha256_file(source)

print("PASS: Nova Whole Traveler Evidence Reader v1.3.5.0 tests")
