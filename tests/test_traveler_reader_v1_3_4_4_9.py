#!/usr/bin/env python3
import importlib.util
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ingest" / "nova_traveler_reader_v1_3_4_4_9.py"
spec = importlib.util.spec_from_file_location("r", SCRIPT)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

# Portrait inputs normalize to landscape.
im = Image.new("RGB", (1000, 1600), "white")
landscape, status = r.normalize_orientation(im)
assert landscape.size == (1600, 1000)
assert status.startswith("rotated_90")

# Human-defined normalized Repairs/Replacements region is wide and includes
# the whole upper-right evidence area; no internal column/grid logic is used.
box = r.normalized_box_to_pixels((1600, 1000), r.DEFAULT_RELEVANCE_MAP["repairs_replacements"])
assert box == [728, 125, 1352, 645], box
assert box[2] - box[0] > 600
assert r.DEFAULT_RELEVANCE_MAP["identity_header"][0] < r.DEFAULT_RELEVANCE_MAP["repairs_replacements"][0]
assert r.DEFAULT_RELEVANCE_MAP["special_notes"][1] > r.DEFAULT_RELEVANCE_MAP["identity_header"][1]

prompt = r.handwriting_prompt()
assert "Read ONLY handwriting" in prompt
assert "Do NOT classify anything as repaired, replaced, description, initials, or date" in prompt
assert "Do NOT use X/check marks as evidence gates" in prompt

print("PASS: Nova Traveler Reader v1.3.4.4.9 human-defined relevance-map tests")
