# Nova DRL Parts Resolver v1.6.2 — Supplier-Aware Canonicalization

v1.6.2 inserts a supplier/distributor identity layer before OCR-deviant learning.

## Supplier rules currently taught by Matt

- Mouser: `511-` is a known supplier prefix in this DRL parts data.
  - Example: `511-STTH1506TPI`
  - Preserved supplier PN: `511-STTH1506TPI`
  - Manufacturer-body candidate: `STTH1506TPI`
  - Because Matt explicitly identified `511-` as a supplier wrapper, a human-selected
    Mouser row may seed the stripped manufacturer canonical when no better canonical
    already exists.
- DigiKey: supplier PNs end in `-ND`.
  - Example: `LM5110-2M/NOPB-ND`
  - Preserved supplier PN: `LM5110-2M/NOPB-ND`
  - Manufacturer-body candidate: `LM5110-2M/NOPB`
  - The `-ND` suffix by itself does **not** authorize a new manufacturer canonical.
    If the stripped body does not already match a trusted canonical, it goes to web
    validation for a manufacturer cross.

## Critical behavior change

Supplier wrappers are removed from OCR-training input.

That means Nova will not learn:
- `511-` as an OCR error;
- `-ND` as an OCR error.

Supplier PNs are written separately to:

` supplier_part_aliases_v1_6_2.jsonl `

True OCR deviations remain in:

` part_aliases_v1_6_2.jsonl `

## Recommended RCL1A plan-only run

```bash
cd /opt/nova-drl

REVIEW_ROOT="/opt/nova-drl/output/human_parts_review/rcl1a"

python3 test_nova_parts_ocr_deviant_resolver_v1_6_2.py

python3 nova_parts_ocr_deviant_resolver_v1_6_2.py \
  --candidate-root "$REVIEW_ROOT" \
  --training-review-root "$REVIEW_ROOT" \
  --output-root /opt/nova-drl/output/rcl1a_parts_resolver_v1_6_2 \
  --family "PS - RCL1A-1D-W3 RACAL" \
  --plan-only
```

Do not run the non-plan pass until the v1.6.2 counts and alias lists have been reviewed.

## Safeguards retained

- frozen evidence modified: NO
- accepted facts: 0
- Qdrant: OFF
- screened candidate promoted as new canonical without required authority: NO
- 80/20 rule: fixed default
