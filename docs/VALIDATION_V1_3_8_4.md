# Validation — v1.3.8.4

Validated locally before packaging:

- Python compilation PASS.
- v1.3.8.4 deterministic composer tests PASS.
- v1.3.8.2 hybrid regression tests PASS.
- v1.3.8.1 Qdrant trial regression tests PASS.
- v1.3.8.0 deterministic query regression tests PASS.
- Normal rendered answer does not expose `rg_...` IDs.
- Normal rendered answer does not print representative Traveler evidence.
- `show_evidence=True` expands Traveler evidence without exposing internal group IDs in headings.
- Source-ID validation remains active internally.
