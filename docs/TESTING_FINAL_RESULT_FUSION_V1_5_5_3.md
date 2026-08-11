# Nova DRL Testing / Final Result Fusion v1.5.5.3

## Purpose

v1.5.5.3 addresses the remaining mark-to-field association errors from the
live `130813004` pilot.

v1.5.5.2 successfully reduced the testing review set, but still allowed:

```text
Customer Problem/Symptom Description -> TESTING_PERFORMED
printed final-result choices on supporting pages -> false final candidates
No Trouble Found + Passed All Tests -> both apparently selected
48+ hours -> associated with Final O.K.
```

v1.5.5.3 adds an authority gate based on **printed-label anchors**.

## Testing authority gate

The following are never testing evidence:

```text
Customer Problem/Symptom Description
Customer Complaint
Failure Description
RMA / serial / date / repair technician header fields
```

Even if whole-page MiniCPM associates a visible mark with them.

## Supporting-document final results

A supporting Checklist/Test Report final candidate now requires its
`basis_label` to be verified in the page's template OCR. Generic model-created
labels such as:

```text
Final Result / Test Result / Overall Result / Pass-Fail selection
Final Result Field Label
literal associated final-result label
```

cannot pass unless the actual printed page contains the asserted basis.

## Traveler anchor verification

For fixed traveler crops, whole-page MiniCPM is no longer trusted to associate
marks with final fields.

For each known field Nova performs:

```text
1. Tesseract TSV locates the printed label.
2. Nova creates a relative crop around the detected label.
3. MiniCPM-V examines only that local crop.
4. It answers selected / not_selected / ambiguous for that one target.
```

No absolute form coordinates are used.

Known `final_test.png` fields:

```text
Passed All Tests
No Trouble Found
Untestable, Inspection Only
```

These are mutually exclusive. Nova creates a final candidate only when:

```text
exactly one = selected
all siblings = not_selected
none = ambiguous
```

If two appear selected, or one is ambiguous, Nova records an
`ambiguous_group` and creates **no final-result candidate**.

## Shipping Final O.K.

`shipping_final_ok.png` is separately anchored on:

```text
Final O.K.
```

The nearby duration field is explicitly excluded. Values such as:

```text
48+
48+ hours
hours in final testing
```

cannot become the Final O.K. event mark.

## Cache

Whole-page analysis remains cached under:

```text
page_analysis_cache_v1_5_5_3/
```

Field-level verification is cached separately under:

```text
field_verification_cache_v1_5_5_3/
```

The second identical run should reuse both caches.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_testing_final_result_fusion_v1_5_5_3.py
```

Expected:

```text
PASS: Nova DRL Testing / Final Result Fusion v1.5.5.3 tests
```

## Live pilot

```bash
python3 ingest/nova_testing_final_result_fusion_v1_5_5_3.py /opt/nova-drl/output/evidence_fusion_v1_5_4/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004
```

Review:

```bash
cat /opt/nova-drl/output/evidence_fusion_v1_5_5_3/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004/testing_final_result_review.txt
```

Additional field-level audit:

```bash
cat /opt/nova-drl/output/evidence_fusion_v1_5_5_3/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004/testing_anchor_field_verifications.json
```

Do not approve anything until the first v1.5.5.3 review is inspected.

## Safety

- raw whole-page MiniCPM responses preserved
- raw anchor-specific MiniCPM responses preserved
- source traveler/checklist/test-report files untouched
- ambiguous mutually-exclusive selections produce no fact
- no final repair summary accepted
- no Qdrant writes
