# v1.5.5.2 Changelog

- Added DRL form-aware mark semantics.
- X/check marks on ordinary checklist/test steps now mean completed, not FAIL.
- Preserves raw model result and records semantic correction separately.
- Added semantic_role, association_basis and selected_result vision fields.
- Routes setup/procedure steps away from TESTING_PERFORMED.
- Added known traveler final-disposition field-label gating.
- Blocks unrelated numeric/admin values from neighboring final options.
- Blocks document title Acceptance Test Report as a final-result basis.
- Blocks generic unresolved Pass/Fail as a final result.
- Requires explicit result-field association for supporting-document PASS/FAIL.
- Preserves No Trouble Found and mutually-exclusive disposition conflict checks.
- Adds v1.5.5.2 signed cache and form-profile config.
- No source modifications. No final summary acceptance. No Qdrant writes.
