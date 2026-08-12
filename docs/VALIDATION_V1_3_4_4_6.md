# Validation — Traveler Reader v1.3.4.4.6

## Purpose

Validate printed-grid-network recovery of the complete Repairs/Replacements table without modifying frozen v1.3.4.4.3.

## Required behavior

- use original Traveler printed geometry, not handwriting extent
- recover all six semantic vertical rules
- recover repeated horizontal row rules by combined coverage even when segmented
- preserve meaningful unmarked repair content for review
- no MiniCPM/Ollama transcription in this validation pass
- accepted repair facts remain zero
- no DRL source changes
- no Qdrant writes

## Regression events

- 130813004 — previously successful marked traveler
- 130130006 — complex five-action traveler
- 150622005 — legacy mixed marked/unmarked traveler that exposed clipped-column and segmented-outline behavior

Do not add expected-entry hints while validating 150622005.
