# Nova DRL Validated Repair Event Knowledge Record v1.5.6

## Purpose

v1.5.6 is the event-level assembly stage.

It performs **no new OCR and no new vision**. It does not try to discover
additional repair facts. Instead, it assembles already-reviewed upstream
knowledge into one traceable repair-event record.

The expected upstream baseline is:

```text
v1.5.4   Diagnostic Hypothesis / Root Cause Fusion
v1.5.5.4 Testing Performed / Final Result Fusion
```

## Valid field states

Every knowledge field has one of four explicit states:

```text
approved
not_established
not_available
pending_review
```

`not_established` is a valid evidence state. It is not rewritten to `unknown`
and does not invite Nova to infer a missing value.

## Knowledge groups

The record currently carries:

```text
customer_complaint
repair_actions
parts_replaced
diagnostic_hypotheses
root_cause
testing_performed
final_result
```

For human-approved fields, the approved upstream objects are preserved rather
than reworded.

## Root-cause protection

An approved diagnostic hypothesis remains a hypothesis.

v1.5.6 will not promote it to root cause. A confirmed root-cause count without
an approved provenance object is treated as a consistency error, not as a fact.

## Source fingerprints

Each source artifact is recorded with:

```text
path
fusion version
SHA-256
```

The assembled knowledge fields also receive a deterministic field-state digest.

This makes record-level approval source-specific.

If an upstream approved value or source file changes after the event record has
been approved, the old approval is automatically treated as stale and the new
assembly returns to `pending`.

## Record-level human validation

The first run assembles the record but does not approve it.

Run:

```bash
python3 ingest/nova_validated_repair_event_record_v1_5_6.py /opt/nova-drl/output/evidence_fusion_v1_5_5_4/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004
```

Review:

```bash
cat /opt/nova-drl/output/evidence_fusion_v1_5_6/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004/validated_repair_event_record.txt
```

When the assembled record has been verified:

```bash
python3 ingest/nova_validated_repair_event_record_v1_5_6.py /opt/nova-drl/output/evidence_fusion_v1_5_5_4/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004 --decision=approve-record --reviewer="Matt Purves" --note="Verified the assembled repair-event record against the human-approved upstream fields and explicit not-established states."
```

Record approval is blocked if a hard consistency check fails.

## Outputs

```text
validated_repair_event_record.json
validated_repair_event_record.txt
record_validation_checks.json
record_source_manifest.json
human_record_review_decisions.json   # after a decision
```

## Qdrant

Qdrant remains disabled in v1.5.6.

Even a human-approved v1.5.6 record is **not** automatically eligible for
record-level Qdrant ingestion. Search/ingestion policy will be a later,
separate decision after cross-event validation.

## Safety

- no OCR
- no vision
- no source mutation
- no silent correction
- no new fact inference
- no hypothesis-to-root-cause promotion
- no forced completion of missing fields
- no final natural-language repair summary acceptance
- no Qdrant writes
