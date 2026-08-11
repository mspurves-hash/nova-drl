# Nova DRL Testing Performed / Final Result Fusion v1.5.5

## Purpose

v1.5.5 adds two distinct repair-event knowledge groups:

```text
TESTING_PERFORMED
FINAL_RESULT
```

It deliberately does **not** treat printed checklist instructions as proof that
the work happened.

## Evidence rule

```text
Printed checklist instruction
    != test performed

Printed Acceptance Test Report title
    != unit passed

Event-specific checkmark / initials / handwritten value / marked PASS
    = candidate evidence requiring human review
```

This protects Nova from turning a reusable DRL form into false event history.

## Sources

v1.5.5 uses the existing v1.4.3.2 Repair Evidence Bundle to locate:

- Robot Checklist rendered page images
- Robot / Acceptance Test Report rendered page images
- Traveler `final_test.png`
- Traveler `shipping_final_ok.png`

The v1.5.4 approved event remains the knowledge anchor.

## Vision

Local `minicpm-v:latest` is used only to identify visible event-specific marks
and their associated labels.

The prompt explicitly tells the model:

- printed text alone is not evidence
- do not infer completion from the document title
- omit unmarked printed steps
- preserve literal visible wording
- put ambiguous marks in `uncertain_marks`

Every vision finding remains pending until human review.

## Vision cache

The first live run may analyze multiple supporting pages.

Each page result is cached under:

```text
page_analysis/
```

Human-review reruns reuse the cache automatically. Use:

```text
--refresh-vision
```

only when you intentionally want to rerun MiniCPM.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_testing_final_result_fusion_v1_5_5.py
```

Expected:

```text
PASS: Nova DRL Testing / Final Result Fusion v1.5.5 tests
```

## Live pilot

```bash
python3 ingest/nova_testing_final_result_fusion_v1_5_5.py /opt/nova-drl/output/evidence_fusion_v1_5_4/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004
```

The script should automatically find the matching v1.4.3.2 bundle under:

```text
/opt/nova-drl/output/repair_evidence_collector_v1_4_3_2
```

If it cannot, provide the bundle explicitly:

```bash
python3 ingest/nova_testing_final_result_fusion_v1_5_5.py /opt/nova-drl/output/evidence_fusion_v1_5_4/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004 --evidence-bundle=/opt/nova-drl/output/repair_evidence_collector_v1_4_3_2/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/events/130813004/repair_evidence_bundle.json
```

## Review

```bash
cat /opt/nova-drl/output/evidence_fusion_v1_5_5/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004/testing_final_result_review.txt
```

Do not approve candidates until their source page/crop has been checked.

## Human decisions

Testing item:

```bash
python3 ingest/nova_testing_final_result_fusion_v1_5_5.py <V1.5.4_EVENT_DIR> --decision=approve-test --test-number=1 --reviewer="Matt Purves" --value="Human-verified testing statement." --note="Verified against the event-specific mark on the source page."
```

Final result:

```bash
python3 ingest/nova_testing_final_result_fusion_v1_5_5.py <V1.5.4_EVENT_DIR> --decision=approve-final --final-number=1 --reviewer="Matt Purves" --value="Human-verified final result." --note="Verified against explicit event-specific final result."
```

Reject / hold are also supported:

```text
reject-test
hold-test
reject-final
hold-final
```

## Outputs

```text
testing_final_result_review.txt
testing_final_result_review.json
testing_final_result_page_analyses.json
approved_repair_fields_with_testing_final.json
human_review_decisions.json
page_analysis/
```

## Safety

- printed form text is never treated as completion evidence
- Acceptance Test Report title is never treated as PASS
- machine vision never auto-approves a test/result
- approved v1.5.4 fields are not modified
- no final repair summary is accepted
- no Qdrant writes
