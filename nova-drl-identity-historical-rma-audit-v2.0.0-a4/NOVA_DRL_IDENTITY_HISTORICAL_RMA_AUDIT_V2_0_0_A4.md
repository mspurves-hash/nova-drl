# Nova DRL Historical RMA Identity Corroboration Audit v2.0.0-a4

## Purpose

This stage recovers the historical RMA-to-part relationship from the PDF-derived Excel report and compares conservative exact Part # tokens with the v2.0.0-a3 identity candidates.

The Excel conversion moved the part text between columns C through G. The RMA remains in column A on the same row. The extractor joins populated C:G cells in column order and preserves the original label.

This is a read-only audit and historical reference build. It is not a production identity catalog or resolver.

## Safety boundary

- Historical data is reference evidence, not identity authority.
- Exact normalized Part # comparison only.
- Equipment-type fences remain in place.
- No fuzzy matching.
- No completion of truncated prefixes or suffixes.
- No automatic cross-type merge.
- No canonical identity approvals.
- No SQLite, corpus, launcher, accepted-fact, or Qdrant writes.
- Qdrant remains OFF.

## Files

- `nova_drl_identity_historical_rma_audit_v2_0_0_a4.py` — dependency-free extractor and audit.
- `test_nova_drl_identity_historical_rma_audit_v2_0_0_a4.py` — focused regression tests.
- `nova_drl_identity_historical_rma_audit_v2_0_0_a4.json` — generated audit from the supplied historical workbook and a3 JSON.
- `nova_drl_historical_rma_part_reference_v1_0_0.jsonl` — generated deduplicated historical reference.

## Server placement

Recommended locations:

```text
/opt/nova-drl/nova_drl_identity_historical_rma_audit_v2_0_0_a4.py
/opt/nova-drl/test_nova_drl_identity_historical_rma_audit_v2_0_0_a4.py
/opt/nova-drl/input/00001 ReportGenReport.xlsx
/opt/nova-drl/output/nova_drl_identity_catalog_audit_v2_0_0_a3.json
```

The source workbook should stay outside the Git repository unless DRL deliberately decides otherwise.

## Test

From `/opt/nova-drl`:

```bash
python3 test_nova_drl_identity_historical_rma_audit_v2_0_0_a4.py
```

## Run

```bash
python3 nova_drl_identity_historical_rma_audit_v2_0_0_a4.py \
  --xlsx "/opt/nova-drl/input/00001 ReportGenReport.xlsx" \
  --a3 /opt/nova-drl/output/nova_drl_identity_catalog_audit_v2_0_0_a3.json \
  --output /opt/nova-drl/output/nova_drl_identity_historical_rma_audit_v2_0_0_a4.json \
  --reference-output /opt/nova-drl/output/nova_drl_historical_rma_part_reference_v1_0_0.jsonl
```

No extra Python package is required. The XLSX reader uses only `zipfile` and XML modules from the Python standard library.

## Output interpretation

### `corroborated_same_type_not_approved`

The historical report contains the same exact normalized Part # under the same equipment type as a3. This strengthens the candidate but does not approve it.

### `part_number_only_corroboration_not_approved`

The exact Part # is present, but the historical label has no explicit equipment type. It supports the Part # spelling only.

### `type_alias_corroboration_not_approved`

The exact Part # appears under the established canonical type while a3 contains a known spelling or punctuation alias such as `P_S`, `PREALGINER`, `GEAR BOS`, or `SVP DRV`. This supports correcting the type alias, but does not approve the identity.

### `mixed_type_historical_evidence_review`

The historical report contains the Part # under the candidate type and at least one other type. Keep identities type-scoped for human review.

### `historical_type_conflict_review`

The Part # exists historically under a different explicit type. Do not merge or correct automatically.

### `preserved_historical_only_not_promoted`

The historical Part # is absent from a3. It remains available in the reference JSONL but does not enter the identity catalog automatically.

## 80/20 review order

1. Review same-type corroboration with the most distinct RMAs.
2. Review cross-type evidence affecting established DRL types such as BRD, PS, CNTL, RBT, RBT ARM, SVO DRV, and PREALIGNER.
3. Preserve unmatched legacy disk, tape, and general computer hardware without promoting it into the semiconductor repair identity catalog.
4. Ignore one-off and truncated historical strings unless DRL supplies an exact type and Part #.
