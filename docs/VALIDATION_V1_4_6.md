# Validation v1.4.6

Synthetic validation covers:

- exact deterministic 10% sampling,
- reproducibility with the same seed,
- top-level folder enumeration from a SQLite DRL index,
- no-Traveler folder exception reporting,
- `.picasaoriginals` exclusion,
- Roger-only `(2)` primary / `(1)` supporting behavior,
- conservative equipment-family identity from folder names,
- evidence-bound PN and explicit quantity validation.

The test suite does not require Ollama or the live NAS.
