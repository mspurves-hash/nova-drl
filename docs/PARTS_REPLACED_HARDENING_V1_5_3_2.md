# Parts Replaced Fusion v1.5.3.2

## Source authority
Only human-approved repair actions are eligible part sources. Raw OCR, machine-only transcription, and unapproved action candidates are not part sources.

## New structured evidence

- `quantity_source` distinguishes explicit total / x-quantity / numeric prefix / not established.
- `quantity_breakdown` preserves explicit distributions such as `4 for A1 + A2, 2 for R + T, 3 for Z`.
- `quantity_breakdown_verified` is true only when the distribution sum equals an independently explicit written total.
- `identified_part_number` preserves a conservative adjacent alphanumeric token from the human-approved action, such as `R8ZZ`.

## Service guardrails
`resurfaced` and `vacuumed` are service verbs. A commutator that was resurfaced, or a motor mentioned while vacuuming brush dust, is not a replaced part unless a separate explicit replacement/install signal exists.

## Expected 130130006 candidates

1. Bearings — identified part number `R8ZZ`, quantity 3.
2. Belts — quantity 9; distribution 4 / 2 / 3, sum verified.
3. Special shims — raw terminology `Blue Schmoo's`, quantity 2.

Commutators and motors remain non-replacement observations.
