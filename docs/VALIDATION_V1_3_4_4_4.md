# Validation v1.3.4.4.4

## Target events

- 130813004 — known-good marked traveler; frozen v1.3.4.4.3 geometry must remain authoritative.
- 130130006 — complex five-action traveler; frozen v1.3.4.4.3 geometry must remain authoritative.
- 150622005 — legacy/mixed traveler that exposed a clipped Repaired column and meaningful unmarked repair content.

## Required behavior for 150622005

- Detect legacy semantic column-role shift structurally.
- Recover the missing disposition column from the original traveler without changing the source.
- Preserve meaningful description content whether or not a Repaired/Replaced mark exists.
- Report marks only as provisional row attributes.
- Do not automatically promote any row to a repair fact.
- No MiniCPM/Ollama in `--detect-only`.
- No Qdrant writes.

## First validation run

Run only detect-only first.  Review the recovered full-table image and row crops before enabling any downstream fusion.
