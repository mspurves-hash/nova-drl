# Nova DRL Global Manufacturer-PN Validation Worklist v1.7.4

The full v1.7.3 plan at a 10-repair threshold reduced the expensive identity
queue from 789 to 160 candidates and reduced fuzzy audit pairs from 278 to 8.

That is small enough to stop tuning the pipeline.

v1.7.4 does only safe pre-validation classification and prioritization.

## Safe exclusions

Examples already visible in the v1.7.3 queue:

- `N/A` -> not a part identity
- `100uF cap` -> component specification/description
- strings matching Amazon ASIN form `B0xxxxxxxx` -> retail reference, not a
  manufacturer PN

These exclusions do not alter the source evidence.

## Validation tiers

Tier A:
- 20+ repair events
- 2+ equipment families

Tier B:
- 20+ repairs in one family
- OR 10-19 repairs across 3+ families

Tier C:
- remaining 10+ repair recurring manufacturer-PN candidates

Validate Tier A first. Do not manually work all 160 identities.

## Required upstream full write

First write the v1.7.3 gate outputs using the same thresholds that were
successfully plan-tested:

```bash
cd /opt/nova-drl

python3 nova_parts_full_corpus_gate_v1_7_3.py \
  --global-validation-min-events 10 \
  --fuzzy-min-events 10
```

Then:

```bash
python3 test_nova_parts_global_validation_worklist_v1_7_4.py

python3 nova_parts_global_validation_worklist_v1_7_4.py --plan-only
```

The next stage after that should validate Tier A manufacturer identities once
globally and write a reusable cache. Tier B/C remain queued until useful.
