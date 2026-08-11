# Nova DRL Evidence Fusion and Human Review v1.5

## Purpose

v1.5 creates field-level repair fact candidates from evidence already
collected by v1.4.3.2. It performs no new OCR and does not call Qwen.

The first supported field is:

- `customer_complaint`

Sources may include:

- Primary traveler Special Notes region
- DRL internal Robot Checklist page 1
- DRL acceptance/Robot Test Report page 1

## Safety

- Raw source wording is preserved.
- A canonical candidate is created only when the meaningful word sequence
  agrees.
- Capitalization and terminal punctuation may be normalized.
- No field is approved automatically.
- No Qdrant API is called.
- Human decisions are audit logged.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_evidence_fusion_v1_5.py
```

Expected:

```text
PASS: Nova Evidence Fusion v1.5 tests
```

## Build the review package

```bash
python3 ingest/nova_evidence_fusion_v1_5.py /opt/nova-drl/output/repair_evidence_collector_v1_4_3_2/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH --log=130813004
```

Expected pilot:

```text
Customer complaint sources: 3
Complaint confidence: high
Complaint candidate: Y Axis needs to be fixed
Human review status: pending
Future Qdrant eligible: NO
Qdrant entries created: 0
```

## Review output

```bash
less /opt/nova-drl/output/evidence_fusion_v1_5/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/events/130813004/fusion_review.txt
```

## Record a human approval

Run only after reviewing the evidence:

```bash
python3 ingest/nova_evidence_fusion_v1_5.py /opt/nova-drl/output/repair_evidence_collector_v1_4_3_2/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH --log=130813004 --decision=approve --field=customer_complaint --reviewer="Matt Purves" --note="Verified against traveler, checklist, and test report."
```

Approval marks the field eligible for a future ingestion pipeline.
v1.5 still creates no Qdrant entry.
