# Testing / Final Result Hardening Schema v1.5.5.1

## Reviewable testing candidate

Must satisfy all of:

```text
source = supporting checklist/test-report page
valid exact mark_type enum
valid exact result enum
non-sentinel event mark
event mark does not look like printed instruction
confidence threshold met
step is a real test/inspection rather than admin/final-condition metadata
```

## Reviewable final-result candidate

Must satisfy:

```text
explicit event-specific result mark
valid exact final-result enum
not a customer complaint/problem field
not a document/page header
not an ordinary printed instruction
supporting documents must have explicit final-result context
```

## Conflict state

A final candidate can carry:

```text
conflict_flags
conflict_review_required
approval_requires_conflict_acknowledgement
```

Conflict acknowledgement is an explicit human action and is written into the
review audit log.

## Rejected machine candidates

Preserved in:

```text
testing_final_result_rejections.json
```

## Routed non-test observations

Preserved in:

```text
testing_final_result_routed_observations.json
```

## Cache

Stable source identity selects the cache file. A content/prompt/model signature
controls reuse versus invalidation.

## Invariants

```text
printed template != performed test
document title != final result
customer complaint != final result
admin metadata != testing performed
machine vision != approved repair knowledge
```
