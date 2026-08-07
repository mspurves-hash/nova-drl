#!/usr/bin/env python3
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("r",str(ROOT/"ingest"/"nova_traveler_reader_v1_3_1.py"))
r=importlib.util.module_from_spec(spec); spec.loader.exec_module(r)
assert r.fractional_box_to_pixels((0.1,0.2,0.5,0.8),1000,500)==(100,100,500,400)
assert r.ocr_quality_score("Log 230809002 Customer Micron Technology Serial GB8-MT-80050477") > r.ocr_quality_score("| | ; ; --")
assert len(r.REGIONS)==6
assert "repairs_replacements" in r.REGIONS
assert "special_notes" in r.REGIONS
print("PASS: Nova Traveler Reader v1.3.1 tests")
