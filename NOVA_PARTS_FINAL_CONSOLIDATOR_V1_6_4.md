# Nova DRL Final Parts Consolidator v1.6.4

This is a new downstream stage. It does **not** modify Parts Resolver v1.6.3.1.

## Why

The first full recurring list exposed a small number of cross-kind duplicates that
the conservative resolver intentionally did not collapse:

- `038AN08A1 mosfet` -> `FDH038AN08A1`
- `DXFX24N100Q3` -> `IXFX24N100Q3`
- `ISL6551R` -> `ISL6551IR`
- `IRP9952PBF` -> `IRF9952PBF`
- pigtail-fuse wording variants -> `15A 250V Pigtail Fuse`

Matt also confirmed two distinct RCL1A fuse families:

1. `KLK-15 / 0KLK015.T` = **15A 600V**
2. `15A 250V Pigtail Fuse` = separate pigtail family

The rules treat voltage/physical-style evidence as a hard domain distinction.

## Important behavior

The consolidator rebuilds recurrence from the original `source_candidate_ids`.
That means merged identities get a correct union of repair events rather than
merely renaming an existing 80/20 row.

It may recover a previously screened candidate only when an explicit rule has
`recover_unselected: true`. This is how the first human screen becomes useful
training/evidence without requiring a second exhaustive review.

## First run

```bash
cd /opt/nova-drl

python3 test_nova_parts_final_consolidator_v1_6_4.py

python3 nova_parts_final_consolidator_v1_6_4.py \
  --resolver-root /opt/nova-drl/output/rcl1a_parts_resolver_v1_6_3_1 \
  --review-root /opt/nova-drl/output/human_parts_review/rcl1a \
  --rules ./rcl1a_final_consolidation_rules_v1_6_4.jsonl \
  --output-root /opt/nova-drl/output/rcl1a_parts_final_v1_6_4 \
  --family "PS - RCL1A-1D-W3 RACAL" \
  --plan-only
```

Do not run the full write until the plan-only list is reviewed.

A healthy result should:
- have zero hard conflicts;
- reduce the 21 upstream recurring rows;
- combine the two pigtail wording rows;
- keep `0KLK015.T` and `15A 250V Pigtail Fuse` separate;
- merge the known residual OCR identities listed above.
