# Testing / Final Result Review Schema v1.5.5

## Testing candidate

A testing candidate preserves:

- deterministic candidate ID
- test number
- literal step label
- literal event-specific mark
- mark type
- pass/fail/completed/recorded-value classification
- recorded value, if any
- technician initials, if visible
- date, if visible
- source document/page/crop
- confidence
- human review decision
- future Qdrant eligibility

## Final result candidate

A final-result candidate preserves:

- deterministic candidate ID
- final number
- literal result value
- associated label
- literal event-specific mark
- result type
- source document/page/crop
- confidence
- human review decision

## Non-promoted evidence

Printed template labels without marks are retained separately as:

```text
printed_template_only_observations
```

Ambiguous visual marks are retained separately as:

```text
uncertain_mark_observations
```

Neither becomes repair knowledge automatically.

## Safety invariant

```text
printed template != performed test
document title != final result
```
