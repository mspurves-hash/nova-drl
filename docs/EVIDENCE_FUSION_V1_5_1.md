# Nova DRL Evidence Fusion and Human Review v1.5.1

## Purpose

v1.5.1 adds **Repair Actions** to the validated v1.5 human-review workflow.

The primary source is a structured Traveler Reader `repair_entries_v1_3_4_x.json`
artifact. Each traveler repair row becomes a separate human-review item.

v1.5.1 deliberately does **not** turn raw whole-region Tesseract OCR into
repair-action facts. If the structured Traveler Reader row extraction has not
been run, v1.5.1 reports that the repair-action field is not ready.

## Evidence rules

- Traveler repair rows are the primary repair-action anchor.
- Literal repair wording is preserved.
- Only whitespace and terminal punctuation may be normalized automatically.
- Technician initials and dates remain provisional metadata.
- Event-specific Internal Checklist Notes may corroborate a traveler action
  when the text closely matches.
- Printed checklist procedure text is not repair-action evidence.
- A repair action does not automatically establish a part, root cause,
  test result, or final result.
- Every action is approved/rejected/held separately.
- No Qdrant API is called.

## Prior v1.5 approval

v1.5.1 automatically looks in:

`/opt/nova-drl/output/evidence_fusion_v1_5/.../events/<log>/`

for the existing v1.5 human-review decision log. This preserves an already
approved customer complaint without requiring it to be approved again.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_evidence_fusion_v1_5_1.py
```

Expected:

```text
PASS: Nova Evidence Fusion v1.5.1 tests
```

## Pilot readiness

For log `130813004`, first create structured traveler repair rows.

Detect-only pass:

```bash
python3 ingest/nova_traveler_reader_v1_3_4_2.py /opt/nova-drl/output/traveler_reader_v1_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH --log=130813004 --detect-only
```

Do not run vision until the detected row count and coverage look reasonable.

After the full Traveler Reader run, rerun collector v1.4.3.2 so the new
structured traveler artifact is attached to the Repair Evidence Bundle.

Then run v1.5.1:

```bash
python3 ingest/nova_evidence_fusion_v1_5_1.py /opt/nova-drl/output/repair_evidence_collector_v1_4_3_2/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH --log=130813004
```

## Review

```bash
less /opt/nova-drl/output/evidence_fusion_v1_5_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/events/130813004/fusion_review.txt
```

Each action is shown as:

```text
ACTION 1 [action-id]
Candidate: <literal traveler wording>
Confidence: low|medium|high
Human review: pending
Accepted as human-reviewed fact: NO
Future Qdrant eligible: NO
```

## Approve one action

After visually verifying the traveler row:

```bash
python3 ingest/nova_evidence_fusion_v1_5_1.py /opt/nova-drl/output/repair_evidence_collector_v1_4_3_2/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH --log=130813004 --decision=approve --field=repair_actions --action-number=1 --reviewer="Matt Purves" --note="Verified against the traveler repair row."
```

Approval marks that action eligible for a future ingestion stage.
v1.5.1 still creates zero Qdrant entries.
