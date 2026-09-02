# Nova DRL — RCL1A Cached Section-Authority / Ambiguity Audit v8

Purpose: one 80/20 cached diagnostic after v7 showed that fuzzy normalization helped, but the v7 replacement-link filter was too strict and degraded dominant-family ranking.

## What v8 changes

- **No model calls.** Reuses the completed v6 high-recall + PN-focus cache.
- **Replacement-section authority.** Evidence already emitted by the 8B under `EXPLICIT PARTS / COMPONENTS REPLACED...` is treated as replacement evidence without requiring a second nearby replacement verb.
- **Ambiguity abstention.** Generic evidence such as `15 amp fuse` is preserved but is not forced into 250 V vs 600 V when the evidence lacks a discriminator.
- **Relation guard.** `replaced IC on control board` does not become a control-board replacement.
- **Same-page additive fusion.** Reference-only PN evidence can inherit replacement role only when same-page replacement evidence names a compatible component class.
- **Evidence is never deleted.** Derived family/link classification may change; raw evidence remains preserved.
- **No RCL1A-specific resolver rules** exist in the generic helper module. Benchmark aliases/counts remain scorer configuration only.

## Run

```bash
python3 tests/test_rcl1a_cached_section_authority_audit_v8.py
python3 tools/rcl1a_cached_section_authority_audit_v8.py --show-changes --detail
```

## 80/20 stopping rule

Use this once to decide whether section-role + ambiguity-aware linking materially improves the dominant RCL1A families/ranking. If it does not, stop this audit layer rather than chasing low-frequency discrepancies.
