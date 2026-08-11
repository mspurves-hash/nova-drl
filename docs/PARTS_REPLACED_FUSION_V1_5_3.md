# Nova DRL Parts Replaced Fusion v1.5.3

## Purpose

v1.5.3 extracts conservative part candidates from **human-approved repair
actions**. It is terminology-aware, quantity-aware, and deliberately refuses
to treat every component mention as a replaced part.

## Pilot behavior

Approved repair action:

```text
Adjusted Y-FE from around 9000 down to around 3000
by slipping Y belt a few teeth
```

Result:

```text
belts -> referenced/serviced component
accepted as replaced part: NO
```

Approved repair action:

```text
Added Flanges BERS x2 to A1 + A2 upper link
```

DRL terminology:

```text
BERS -> bearings
```

Result:

```text
candidate part: bearings
quantity: 2
signal: Added
status: pending human review
```

The source action still remains exactly `BERS`; normalized part meaning is
stored separately.

## Safety rules

- Human-approved repair actions only.
- Raw OCR is never a part source.
- An explicit install/replacement signal is required.
- `slipped belt`, `machined Comm's`, `cleaned motor`, etc. are service or
  reference observations unless the approved action explicitly says the part
  was replaced/installed/new.
- No automatic part approval.
- No root-cause inference.
- No Qdrant write.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_parts_replaced_fusion_v1_5_3.py
```

Expected:

```text
PASS: Nova DRL Parts Replaced Fusion v1.5.3 tests
```

## Pilot run

```bash
python3 ingest/nova_parts_replaced_fusion_v1_5_3.py /opt/nova-drl/output/evidence_fusion_v1_5_2/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004
```

Expected pilot:

```text
Approved repair actions:     2
Replacement part candidates: 1
Referenced components:       1
Parts approved:              0
Parts pending:               1
Raw OCR used as part source: NO
Qdrant entries created:      0
```

## Review

```bash
cat /opt/nova-drl/output/evidence_fusion_v1_5_3/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004/parts_replaced_review.txt
```

## Approve the pilot candidate

After review:

```bash
python3 ingest/nova_parts_replaced_fusion_v1_5_3.py /opt/nova-drl/output/evidence_fusion_v1_5_2/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004 --decision=approve --part-number=1 --reviewer="Matt Purves" --note="Verified from the human-approved repair action; BERS is DRL shorthand for bearings."
```

Expected approval:

```text
Parts approved: 1
Parts pending:  0
Qdrant entries created: 0
```

The approved value is `bearings`, quantity `2`, while the historical raw term
`BERS` remains attached for traceability.
