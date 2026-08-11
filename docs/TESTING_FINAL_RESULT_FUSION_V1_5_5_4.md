# Nova DRL Testing / Final Result Fusion v1.5.5.4

## Purpose

v1.5.5.4 is a narrow refinement of the validated v1.5.5.3 anchor-aware
architecture.

The live v1.5.5.3 pilot correctly reduced testing candidates to zero and
reduced final-result review to one candidate, but the local `Final O.K.` vision
still associated a neighboring final-testing-duration value with that field.

The final-testing-duration field is now a **human-directed global ignore**.

## Global-ignore policy

The ignored field:

- remains in the original DRL source file
- remains available in machine audit evidence
- is not promoted as testing
- is not promoted as a final result
- is not used as a neighboring association clue
- is not accepted as technician initials or a date
- is not terminology knowledge
- is not future Qdrant knowledge

The policy is stored in:

```text
config/testing_global_ignore_v1_5_5_4.json
```

## Final O.K. isolation

`Final O.K.` is still located by printed-label OCR anchor. No absolute form
coordinates are used.

The local crop is now tighter relative to the detected label. Before MiniCPM-V
sees that crop, OCR evidence belonging to the globally ignored neighboring
duration value is masked.

The audit record retains:

```text
globally_ignored_regions_masked
ignored_regions
```

## Literal mark validation

A selected field must have a literal usable event mark.

Rejected examples:

```text
handwritten_value
event_mark
initials
8+
```

If the model says the mark type is `initials`, the visible initials must be
compact alphabetic initials. A numeric duration cannot become technician
initials.

Invalid or contaminated selections become:

```text
selection_status = ambiguous
```

and cannot create a final-result candidate.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_testing_final_result_fusion_v1_5_5_4.py
```

Expected:

```text
PASS: Nova DRL Testing / Final Result Fusion v1.5.5.4 tests
```

## Live pilot

```bash
python3 ingest/nova_testing_final_result_fusion_v1_5_5_4.py /opt/nova-drl/output/evidence_fusion_v1_5_4/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004
```

Review:

```bash
cat /opt/nova-drl/output/evidence_fusion_v1_5_5_4/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004/testing_final_result_review.txt
```

Field-level audit:

```bash
cat /opt/nova-drl/output/evidence_fusion_v1_5_5_4/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004/testing_anchor_field_verifications.json
```

Do not approve a final result until the isolated source mark has been visually
verified.

## Safety

- original DRL files remain read-only
- raw machine evidence is retained
- global-ignore evidence cannot be promoted to repair knowledge
- no final repair summary acceptance
- no automatic approvals
- no Qdrant writes
