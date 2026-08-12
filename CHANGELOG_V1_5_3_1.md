# Nova DRL v1.5.3.1 — Parts Replaced Hardening

- Preserves the v1.5.3 approved-actions-only source policy.
- Parses explicit totals such as `(3 total)` and `(9 total)`.
- Preserves explicit `N for context` quantity distributions and verifies their arithmetic against an independently written total.
- Extracts a conservative adjacent alphanumeric part number from human-approved wording (pilot: `R8ZZ`).
- Adds **special shims** as a part class and accepts terminology-backed `special shim`.
- Adds `resurfaced` and `vacuumed` as service verbs so serviced components are not promoted to replacement parts.
- Raw OCR is never used as a part source.
- Human review remains mandatory. Qdrant remains disabled.
