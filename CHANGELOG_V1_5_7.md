# v1.5.7 CORRECTED — Hard 80/20 Invariant

- REMOVED the MR-J2S-40A expert-confirmed repair-kit override.
- REMOVED all standard-repair-kit projection code/configuration.
- Parts counts now come only from corpus-supported distinct repair events.
- Added code-level hard invariant: expert knowledge overrides disabled.
- Added regression gate `tests/test_drl_80_20_project_invariant.py`.
- Added `config/drl_nova_project_invariants.json`.
- Retains base-PN product resolution, component-core consolidation, recurring-only parts, right-aligned counts, Reported Failure, and minimal product view.
- No re-ingestion and no knowledge-index rebuild required.
