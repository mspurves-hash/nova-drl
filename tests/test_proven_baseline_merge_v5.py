from pathlib import Path
p=Path(__file__).resolve().parents[1]/'tools'/'pre200_proven_baseline_merge_v5.py'
s=p.read_text()
assert 'urllib' not in s and 'ollama' not in s.lower(), 'v5 merge benchmark must make no model calls'
assert 'additive' in s.lower()
assert 'PN-focus must still pass a separate precision audit' in s
print('PASS: PRE-200 v5 proven-baseline additive merge benchmark invariants')
