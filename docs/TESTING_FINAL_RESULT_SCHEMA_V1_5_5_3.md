# Testing / Final Result Anchor-Aware Schema v1.5.5.3

## Field verification

Each known fixed traveler field records:

```text
verification_id
profile
field_id
target_label
canonical_value
canonical_result
mutually_exclusive_group
source
anchor_ocr_status
anchor_match { matched_text, score, bbox }
anchor_crop { crop_box, crop_path }
vision_status
raw_vision_response
verification {
  selection_status: selected|not_selected|ambiguous
  event_mark
  mark_type
  technician_initials
  date
  confidence
}
cache_status
```

## Candidate authority

```text
whole-page model association
    != traveler final-result authority

printed label anchor + local target verification
    = candidate evidence requiring human review
```

## Mutually exclusive groups

```text
exactly one selected + all others not_selected -> one candidate
multiple selected -> ambiguity only
any ambiguous -> ambiguity only
```

## Supporting documents

A final-result `basis_label` must be verified in page template OCR before a
supporting-document candidate can be created.

## Invariants

```text
Customer Problem/Symptom != testing
48+ hours != Final O.K. mark
ambiguous group != final result
machine vision != approved repair knowledge
```
