# Testing / Final Result Field Isolation Schema v1.5.5.4

## Final O.K. verification

```text
printed label anchor
  -> relative local crop
  -> mask globally ignored neighboring duration evidence
  -> local vision
  -> literal mark validation
  -> human review candidate only if valid
```

No absolute form coordinates are encoded.

## Global ignored evidence

Audit properties may include:

```text
global_ignore_policy
global_ignore_contamination_detected
globally_ignored_regions_masked
ignored_regions
validation_reasons
```

Ignored evidence is never accepted as a repair fact and is never Qdrant
eligible.

## Initials validation

When `mark_type=initials`:

```text
valid:   MP
valid:   VT
valid:   VT/AM
invalid: 8+
invalid: 48+
invalid: handwritten_value
```

## Invariants

```text
ignored duration != testing
ignored duration != final result
ignored duration != technician initials
ignored duration != date
ignored duration != association context
schema placeholder != literal event mark
machine selection != human-approved fact
```
