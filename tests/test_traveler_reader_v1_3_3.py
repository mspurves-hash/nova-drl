#!/usr/bin/env python3
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'reader', str(ROOT/'ingest'/'nova_traveler_reader_v1_3_3.py')
)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)

assert len(reader.REPAIR_SUBREGIONS) == 4
assert len(reader.SPECIAL_NOTES_SUBREGIONS) == 3
assert reader.fractional_box_to_pixels(
    (0.10, 0.20, 0.50, 0.80), 1000, 500
) == (100, 100, 500, 400)

compliant = reader.prompt_compliance(
    'Rebuilt A1/A2 arms, new bearings | EF | 9/7/23'
)
assert compliant['prompt_noncompliance'] is False
assert compliant['eligible_for_fusion_review'] is True

noncompliant = reader.prompt_compliance(
    'Title: Repairs\nThe image shows several repair entries.'
)
assert noncompliant['prompt_noncompliance'] is True
assert noncompliant['eligible_for_fusion_review'] is False

assert 'DESCRIPTION | INITIALS | DATE' in reader.REPAIR_PROMPT
assert '[unclear]' in reader.REPAIR_PROMPT
assert 'one complete note per line' in reader.SPECIAL_PROMPT

print('PASS: Nova Traveler Reader v1.3.3 tests')
