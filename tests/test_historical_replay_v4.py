#!/usr/bin/env python3
from pathlib import Path
import importlib.util
p=Path(__file__).resolve().parents[1]/'tools'/'pre200_historical_pipeline_replay_v4.py'
spec=importlib.util.spec_from_file_location('v4',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
assert m.DEFAULT_MODEL == 'qwen3-vl-drl:8b-q8-16k'
assert 'Transcribe this complete DRL Traveler image as faithfully as possible.' in m.TRANSCRIPTION_PROMPT
assert 'Do not decide which text is important' in m.TRANSCRIPTION_PROMPT
assert 'HIGH-RECALL PROSPECTOR' in m.PROSPECT_PROMPT
assert 'Do NOT approve facts' in m.PROSPECT_PROMPT
assert 'part_number_or_identifier' in m.ALLOWED_KINDS
print('PASS: PRE-200 v4 historical frozen-architecture replay benchmark invariants')
