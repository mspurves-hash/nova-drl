# Validation — v1.4.0

Validated before packaging:

- Python compilation passes.
- Deterministic unit tests pass.
- Runtime source contains no known benchmark-specific part numbers.
- Qdrant is absent from the execution path.
- Duplicate-failure behavior preserves pages as unique.
- Extracted replacement quotes must bind to raw transcription.
- Vague quantities remain unnumbered.
- Python owns repair-frequency and quantity totals.
- Package ZIP extraction and SHA256 verification pass.

Live source/model validation is performed on the Nova server with `--status` and `--plan-only`.
