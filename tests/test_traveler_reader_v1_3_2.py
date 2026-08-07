#!/usr/bin/env python3
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "reader", str(ROOT / "ingest" / "nova_traveler_reader_v1_3_2.py")
)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)

assert reader.DEFAULT_MODEL == "minicpm-v:latest"
assert reader.DEFAULT_REGIONS == ["repairs_replacements", "special_notes"]
assert "[unclear]" in reader.VISION_PROMPT
assert "Do not summarize" in reader.VISION_PROMPT
assert "Do not infer intent" in reader.VISION_PROMPT
assert "repairs_replacements" in reader.ALL_REGIONS
assert "special_notes" in reader.ALL_REGIONS

print("PASS: Nova Traveler Reader v1.3.2 tests")
