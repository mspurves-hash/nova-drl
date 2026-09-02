#!/usr/bin/env python3
from pathlib import Path
import importlib.util, sys
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'drl_global_evidence_linker_v7.py'
spec=importlib.util.spec_from_file_location('g',P);g=importlib.util.module_from_spec(spec);sys.modules['g']=g;spec.loader.exec_module(g)

# Global, unrelated synthetic regression cases.
fams=[
 {'reference':'ZX-9010 12A 500V fuse','aliases':['ZX-9010','12A 500V fuse']},
 {'reference':'12A 250V standard fuse','aliases':['12A 250V standard fuse','12A 250V fuse']},
 {'reference':'ABX7700Q2','aliases':['ABX7700Q2','ABX 7700/Q2']},
 {'reference':'Control daughter-board assembly','aliases':['daughter board','daughter-board','control daughter board']},
 {'reference':'LMX4321 driver IC','aliases':['LMX4321','LMX 4321']},
]

# OCR/spacing PN variant should match generic family.
f,m=g.best_family_match(fams,'ABX 7700/Q2',threshold=.80)
assert f and f['reference']=='ABX7700Q2', (f,m)

# Explicit spec conflicts must not cross 500V into 250V family.
f,m=g.best_family_match(fams,'12A 500V fuse',threshold=.80)
assert f and f['reference']=='ZX-9010 12A 500V fuse', (f,m)

# Replacement-object relation: board as location must not count as board replacement.
assert g.explicit_replacement_object('Replaced LMX4321 IC on daughter board','daughter board') is False
assert g.explicit_replacement_object('Replaced daughter board assembly','daughter board') is True

# A part in explicit replacement context is linkable; a service-only context is not automatically replacement.
ev=g.Evidence(1,'pn_focus','LMX4321','replaced driver IC',True)
assert ev.explicit_replacement
assert not g.REPL_VERB_RE.search('cleaned and tested driver IC')

# Generic module must not contain benchmark-specific RCL1A identifiers.
src=P.read_text().upper()
for forbidden in ['IXFX24N100Q3','FDH038AN08A1','ISL6551IR','0325015.HXP']:
    assert forbidden not in src, forbidden
print('PASS: v7 generic normalization/linker regression tests; no RCL1A-specific rules in generic module')
