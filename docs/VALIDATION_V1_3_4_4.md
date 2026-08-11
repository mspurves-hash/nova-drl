# Validation Notes v1.3.4.4

## Current visual pilot

Two real 2013 traveler images were reviewed before this build:

- Log 130813004: variable-height, approximately 2 logical repair blocks.
- Log 130130006: mixed-height repair table with approximately 5 logical
  repair-start marks.

The detector was tested locally against cropped versions of both uploaded
traveler images before packaging:

- 130813004 -> 2 start blocks detected.
- 130130006 -> 5 start blocks detected.

The actual DRL server test must still use the existing v1.3.1
`repairs_replacements.png` crop because that is the production derived input.

## Acceptance gate for 130813004

Do not run vision unless detect-only reports:

- status=ok
- starts=2
- blocks=2

Then visually inspect both generated block crops before the full vision pass.
