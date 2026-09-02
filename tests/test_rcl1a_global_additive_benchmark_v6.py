#!/usr/bin/env python3
from pathlib import Path
import importlib.util,json
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'rcl1a_global_additive_benchmark_v6.py'
spec=importlib.util.spec_from_file_location('m',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
bench=json.loads((ROOT/'config'/'rcl1a_benchmark_counts.json').read_text())
assert bench['source_pages']==167 and bench['unique_repairs']==156
assert len(bench['duplicate_pages_excluded'])==11
assert 90 in bench['duplicate_pages_excluded'] and 108 in bench['duplicate_pages_excluded']
# Critical leakage gate: scoring answers must not appear in model prompts.
for part in bench['parts']:
    for alias in part['aliases']:
        c=''.join(ch.lower() for ch in alias if ch.isalnum())
        if len(c)>=5:
            assert c not in ''.join(ch.lower() for ch in m.HIGH_RECALL_PROMPT if ch.isalnum()), f'benchmark leakage in high-recall prompt: {alias}'
            assert c not in ''.join(ch.lower() for ch in m.PN_PROMPT if ch.isalnum()), f'benchmark leakage in PN prompt: {alias}'
assert 'Do not invent a PN' in m.PN_PROMPT
assert 'RECALL FIRST' in m.HIGH_RECALL_PROMPT
print('PASS: RCL1A v6 blind global additive benchmark invariants; benchmark answers isolated from prompts')
