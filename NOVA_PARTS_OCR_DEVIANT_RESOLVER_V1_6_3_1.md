# Nova DRL Parts Resolver v1.6.3.1 — Full-write bugfix

This bugfix preserves the v1.6.3 resolver logic and fixes two issues that only appeared after `--plan-only` was removed.

1. The supplier-alias display loop reused the variable name `target`, overwriting the loaded target review dictionary. The full run then crashed while building the summary. The display variable is now `alias_target_label`.
2. The final output-writing block still used stale `v1_6_1` filenames and did not write the supplier-alias and canonical-refinement ledgers. All output filenames now use `v1_6_3_1`, and all v1.6.3.1 output layers are written.

A new full-write regression test creates a synthetic review workspace, runs the resolver without `--plan-only`, and verifies that every expected output file exists.

Recommended RCL1A run:

```bash
cd /opt/nova-drl
python3 test_nova_parts_ocr_deviant_resolver_v1_6_3_1.py

REVIEW_ROOT="/opt/nova-drl/output/human_parts_review/rcl1a"

python3 nova_parts_ocr_deviant_resolver_v1_6_3_1.py \
  --candidate-root "$REVIEW_ROOT" \
  --training-review-root "$REVIEW_ROOT" \
  --output-root /opt/nova-drl/output/rcl1a_parts_resolver_v1_6_3_1 \
  --family "PS - RCL1A-1D-W3 RACAL" \
  --web-results ./rcl1a_web_validation_results_v1_6_3.jsonl \
  --canonical-overrides ./rcl1a_canonical_overrides_v1_6_3.jsonl
```

Use the new `rcl1a_parts_resolver_v1_6_3_1` output directory rather than the partially written v1.6.3 directory.
