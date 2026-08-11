# Nova DRL Testing / Final Result Fusion v1.5.5.1

## Purpose

v1.5.5.1 hardens the first live v1.5.5 pilot.

The v1.5.5 pilot found useful evidence, but it also created too much review
work:

```text
Testing candidates:      28
Final-result candidates:  6
```

The live output exposed several failure modes:

- MiniCPM echoed the entire mark-type enum instead of selecting one value.
- generic values such as `handwritten mark` were accepted as event evidence.
- a long printed instruction was accepted as an event mark.
- hours, dollars, Cleaned, Aligned, firmware and warranty-sticker fields were
  routed into TESTING_PERFORMED.
- the customer complaint was proposed as a final result.
- ordinary checklist instructions were proposed as final results.
- `No Trouble Found` appeared despite approved repair actions and a replaced
  part.
- the repeated-run cache needed an explicit deterministic validation layer.

v1.5.5.1 addresses all of those without deleting any raw vision evidence.

## Strict schema validation

MiniCPM must choose exactly one value.

Allowed `mark_type`:

```text
checkmark
x_mark
initials
handwritten_value
circle
pass_fail_mark
other
```

Allowed testing `result`:

```text
pass
fail
completed
recorded_value
unknown
```

Allowed final `result`:

```text
pass
fail
accepted
rejected
final_ok
other
```

Values such as:

```text
checkmark|x_mark|initials|...
completed|recorded_value
```

are rejected before human review.

## Sentinel and printed-instruction rejection

These do not qualify as event marks:

```text
none
not recorded
handwritten mark
handwritten_value
unknown
```

Long text that looks like a printed instruction is also rejected.

The rejected item remains in:

```text
testing_final_result_rejections.json
```

## Source-aware routing

### Supporting checklist / test-report pages

These may create `TESTING_PERFORMED` candidates when they have valid
event-specific marks.

### final_test.png / shipping_final_ok.png

These are NOT TESTING_PERFORMED sources.

Their event-specific content is routed into:

```text
final disposition
final condition
administrative observations
```

Examples that are no longer treated as tests:

```text
Ttl Time Spent (Hours)
Ttl Money Spent (Dollars)
Cleaned
Aligned
Adjusted
Latest Firmware Applied
All Screws Appearance
Warranty Sticker Applied
```

The raw observations are preserved in:

```text
testing_final_result_routed_observations.json
```

## Final-result restrictions

The following cannot create final-result candidates:

- customer complaint/problem/symptom fields
- document titles
- `Page X of Y` headers
- printed scanner/checklist instructions
- a supporting-document field that lacks explicit final-result context

Traveler dispositions such as:

```text
Passed All Tests
Final O.K.
48+ hours in Final Testing: FINAL OK.
```

remain reviewable when they have explicit event-specific marks.

## Conflict detection

`No Trouble Found` is not automatically rejected, because historical forms can
contain unusual combinations. It is instead flagged when it conflicts with
already approved evidence.

For the 130813004 pilot, the software will flag it against:

```text
approved repair actions
approved parts replaced
```

If mutually exclusive final options are detected on the same source, they are
also flagged.

A conflicted final result cannot be approved accidentally. Approval requires:

```text
--acknowledge-conflict
```

after the source has been visually verified.

## Deterministic page cache

v1.5.5.1 uses:

```text
page_analysis_cache_v1_5_5_1/
```

Each cache record has a signature over:

```text
source identity
image file size + modification time
vision prompt
model
max image dimension
vision/no-vision mode
```

An exact repeat reuses the cache.

A changed image, prompt, model, or mode invalidates the cache deliberately.

The cache manifest is:

```text
page_analysis_cache_v1_5_5_1/cache_manifest.json
```

## Tests

```bash
cd /opt/nova-drl
python3 tests/test_testing_final_result_fusion_v1_5_5_1.py
```

Expected:

```text
PASS: Nova DRL Testing / Final Result Fusion v1.5.5.1 tests
```

## Live pilot

```bash
python3 ingest/nova_testing_final_result_fusion_v1_5_5_1.py /opt/nova-drl/output/evidence_fusion_v1_5_4/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004
```

Because v1.5.5.1 uses a stricter prompt and a new cache schema, the first run
will perform new vision analysis.

Review:

```bash
cat /opt/nova-drl/output/evidence_fusion_v1_5_5_1/RBT_GB8-MT_GENMARK_SN_80010732_UTI_MICRON/events/130813004/testing_final_result_review.txt
```

Then run the same live command a second time. The second run should report
cached analyses reused instead of re-running MiniCPM.

## Expected qualitative pilot change

We do NOT require a specific candidate count because MiniCPM output can vary,
but the following live v1.5.5 false positives should no longer appear as normal
review candidates:

```text
generic "handwritten mark"
pipe-separated mark/result enums
Ttl Time Spent
Ttl Money Spent
Cleaned
Aligned
Adjusted
Latest Firmware Applied
Warranty Sticker Applied
customer complaint as final result
Scanner check with a printed instruction as its event mark
page/document title as final result
```

`No Trouble Found` may remain visible, but only as a conflict-review candidate.

## Safety

- raw MiniCPM output remains preserved
- rejected candidates remain auditable
- routed non-test observations remain auditable
- approved v1.5.4 values are not modified
- no final repair summary is accepted
- no DRL source files are modified
- no Qdrant writes
