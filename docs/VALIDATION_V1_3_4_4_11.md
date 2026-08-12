# Validation v1.3.4.4.11

Validation goal: verify that the three pertinent Traveler regions are intentionally oversized and cannot clip technician writing because of tight section-boundary estimates.

For 150622005, first use `--detect-only` and visually inspect the three saved oversized region images. Do not evaluate handwriting accuracy until the large-region coverage is accepted.

Regression requirement: frozen v1.3.4.4.3 remains unchanged. No source mutation, automatic repair facts, or Qdrant writes.
