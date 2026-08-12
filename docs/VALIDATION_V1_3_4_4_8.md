# Validation — Traveler Reader v1.3.4.4.8

Target event: `150622005`, GB8-MT serial `80010732`.

First validation is detect-only and uses **no expected-entry hint**.

Success criteria:

- complete printed Repairs/Replacements outer box is captured;
- left and right edges are not clipped;
- no internal semantic-column resolution is attempted;
- no X/start mark is required;
- no vision runs in detect-only mode;
- accepted repair facts remain zero;
- no source mutation;
- no Qdrant writes.

After the outer box is visually confirmed, run without `--detect-only` to
produce a literal handwriting transcription for human review.
