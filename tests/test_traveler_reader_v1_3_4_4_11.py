#!/usr/bin/env python3
import importlib.util
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ingest" / "nova_traveler_reader_v1_3_4_4_11.py"
spec = importlib.util.spec_from_file_location("r", SCRIPT)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

im = Image.new("RGB", (1000, 1600), "white")
landscape, status = r.normalize_orientation(im)
assert landscape.size == (1600, 1000)
assert status.startswith("rotated_90")

identity = r.normalized_box_to_pixels((1600,1000), r.DEFAULT_RELEVANCE_MAP["identity_header"])
repairs = r.normalized_box_to_pixels((1600,1000), r.DEFAULT_RELEVANCE_MAP["repairs_replacements"])
special = r.normalized_box_to_pixels((1600,1000), r.DEFAULT_RELEVANCE_MAP["special_notes"])

assert identity == [0,0,992,560], identity
assert repairs == [544,50,1600,860], repairs
assert special == [0,300,1088,1000], special

# Oversized regions intentionally overlap so section-edge estimation cannot clip writing.
assert identity[2] > repairs[0]
assert special[2] > repairs[0]
assert identity[3] > special[1]

p = r.section_prompt("repairs_replacements")
assert "OVERSIZED" in p
assert "Do NOT crop, isolate, localize" in p
assert "generous margins" in p
assert "Do NOT use table columns" in p

print("PASS: Nova Traveler Reader v1.3.4.4.11 oversized-section whole-read tests")
