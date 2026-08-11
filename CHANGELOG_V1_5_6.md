# v1.5.6 Changelog

- Added Validated Repair Event Knowledge Record assembly.
- Performs no OCR and no vision.
- Requires the validated v1.5.5.4 testing/final-result layer.
- Reads v1.5.4 diagnostic/root-cause provenance.
- Adds explicit field states: approved, not_established, not_available, pending_review.
- Preserves human-approved upstream wording and objects.
- Preserves not_established without guessing.
- Keeps diagnostic hypotheses separate from root cause.
- Adds source SHA-256 fingerprints.
- Adds deterministic field-state and source digests.
- Adds record-level human validation.
- Record approval becomes stale automatically if upstream source content changes.
- Blocks record approval when hard consistency checks fail.
- Adds record source manifest and consistency-check reports.
- Does not accept a final natural-language repair summary.
- Record-level Qdrant eligibility remains disabled.
- No Qdrant writes.
