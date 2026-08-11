# Nova DRL Testing / Final Result Fusion v1.5.5.2

## Purpose

v1.5.5.2 adds **form-aware mark semantics** after the live v1.5.5.1 pilot.
The v1.5.5.1 structural filters worked, but MiniCPM still interpreted some X
marks on DRL checklist/test-report rows as FAIL and occasionally associated a
mark with the wrong final-disposition field.

## Critical semantic rule

```text
X/checkmark beside a normal checklist/test step
    = checked / completed
    != FAIL
```

PASS or FAIL is accepted only when the mark is tied to an explicit result
field or selected result option.

## Form profiles

v1.5.5.2 adds `config/testing_form_profiles_v1_5_5_2.json` for:

- DRL Internal Checklist
- DRL Acceptance Test Report
- traveler `final_test.png`
- traveler `shipping_final_ok.png`

No pixel coordinates are invented. The profile uses known document family,
source crop, label, mark type, semantic role and association basis.

## Testing semantics

MiniCPM now returns:

```text
semantic_role: test | inspection | setup | procedure | unknown
association_basis: same_row | same_box | adjacent_label | selected_option | unknown
selected_result: pass | fail | null
```

Only test/inspection items remain in `TESTING_PERFORMED`. Setup/procedure items
are routed to the non-test observation audit.

If MiniCPM reports `fail` for an ordinary X/checkmark on a checklist step,
Nova preserves the raw model result but canonicalizes the candidate to:

```text
result: completed
semantic_correction: x_or_checkmark_on_checklist_step_means_completed_not_pass_fail
```

## Final-result semantics

For `final_test.png`, only known disposition labels are reviewable:

```text
Passed All Tests
No Trouble Found
Untestable, Inspection Only
```

An unrelated numeric value such as `4 Hours` cannot be associated with one of
those options.

For `shipping_final_ok.png`, `Final O.K.` / `Final OK` is a known final field.
Compact handwritten marks remain pending human review without guessing their
initial/date meaning.

For supporting checklist/test-report pages:

- document title `Acceptance Test Report` is never a result field
- generic `Pass/Fail` is not a result
- PASS/FAIL requires an explicit result field and unambiguous selected result

## Conflicts

`No Trouble Found` continues to be conflict-flagged when approved repair
actions or approved replaced parts exist. Mutually exclusive traveler final
options on the same source are also conflict-flagged.

## Cache

New prompt semantics create a new signed cache:

```text
page_analysis_cache_v1_5_5_2/
```

First run creates it. The second identical run should reuse it.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_testing_final_result_fusion_v1_5_5_2.py
```

## Live pilot

```bash
python3 ingest/nova_testing_final_result_fusion_v1_5_5_2.py /opt/nova-drl/output/evidence_fusion_v1_5_4/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004
```

Review:

```bash
cat /opt/nova-drl/output/evidence_fusion_v1_5_5_2/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004/testing_final_result_review.txt
```

Then run the same live command a second time to verify cache reuse.

## Safety

- raw MiniCPM result is preserved beside canonical semantics
- no X/check mark is promoted to FAIL without an explicit result field
- rejected associations remain auditable
- no hard-coded geometry is invented
- no approved v1.5.4 values are modified
- no final repair summary is accepted
- no DRL source files are modified
- no Qdrant writes
