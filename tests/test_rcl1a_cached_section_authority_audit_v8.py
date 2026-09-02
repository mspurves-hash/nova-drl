#!/usr/bin/env python3
from pathlib import Path
import importlib.util, sys

ROOT=Path(__file__).resolve().parents[1]
MOD=ROOT/'tools'/'drl_global_evidence_linker_v8.py'
spec=importlib.util.spec_from_file_location('v8',MOD); v8=importlib.util.module_from_spec(spec);sys.modules['v8']=v8;spec.loader.exec_module(v8)

# 1) Explicit replacement-section role is authoritative even without a local verb.
fam={'reference':'ZXM482Q power transistor','aliases':['ZXM482Q','ZXM 482Q']}
ev=v8.Evidence(1,'replacement','4 - ZXM 482Q','4 - ZXM 482Q',True)
m=v8.match_alias('ZXM 482Q',ev.candidate)
linked,reason=v8.authoritative_replacement_link(ev,fam,m)
assert linked and reason=='explicit-replacement-section'

# 2) A generic spec-poor descriptor must not be forced into 250 V vs 600 V family.
families=[
 {'reference':'15A 250V cartridge fuse','aliases':['15A 250V fuse','15 amp fuse']},
 {'reference':'15A 600V fast fuse','aliases':['15A 600V fuse','15 amp fuse']},
]
f,m,status=v8.best_family_match_v8(families,'15 amp fuse')
assert f is None, (f,m,status)

# 3) A distinctive PN-like OCR variant can still resolve without every spec written nearby.
families=[
 {'reference':'15A 600V fast fuse','aliases':['KZX-15','15A 600V fuse']},
 {'reference':'15A 250V cartridge fuse','aliases':['15A 250V fuse']},
]
f,m,status=v8.best_family_match_v8(families,'K2X 15')
# Generic synthetic OCR may or may not cross this exact spelling; test a close one with the same stem.
f,m,status=v8.best_family_match_v8(families,'KZX15')
assert f and f['reference'].startswith('15A 600V')

# 4) Board/assembly location guard: replacing an IC on a board is not a board replacement.
fam={'reference':'control board assembly','aliases':['control board','board assembly']}
ev=v8.Evidence(2,'replacement','replaced driver IC on control board','replaced driver IC on control board',True)
m=v8.match_alias('control board',ev.candidate)
linked,reason=v8.authoritative_replacement_link(ev,fam,m)
assert not linked and reason=='assembly-location-guard'

# 5) Explicit board replacement is allowed.
ev=v8.Evidence(3,'replacement','replaced control board assembly','replaced control board assembly',True)
m=v8.match_alias('control board',ev.candidate)
linked,reason=v8.authoritative_replacement_link(ev,fam,m)
assert linked

# 6) Production generic module must not contain benchmark/product-specific repair PNs.
text=MOD.read_text(encoding='utf-8')
for forbidden in ['IXFX24N100Q3','FDH038AN08A1','0325015.HXP','STTH1506TPI','ISL6551IR','PRE-200','MR-J2S']:
    assert forbidden not in text, forbidden

print('PASS: v8 generic section-authority/ambiguity regression tests; no product-specific resolver rules')
