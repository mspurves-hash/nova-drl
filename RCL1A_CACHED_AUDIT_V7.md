# Nova DRL — RCL1A Cached Normalization / Replacement-Link Audit v7

Purpose: diagnose the full v6 RCL1A benchmark **without any additional model calls**.

This layer does **not** patch production and does **not** fit counts to the benchmark. It re-reads the cached high-recall and PN-focused outputs and applies a generic matcher/linker that:

- tolerates punctuation/spacing/OCR-like PN variants;
- rejects explicit electrical-spec conflicts (for example 250 V vs 600 V);
- prefers specific PN/spec evidence over generic descriptions;
- uses line-level replacement-object linkage so `replaced IC on daughter board` does not become a daughter-board replacement;
- preserves a page-level evidence ledger for review.

The established expected counts are used only after matching for scoring.

## Run

```bash
python3 tests/test_rcl1a_cached_normalization_link_audit_v7.py
python3 tools/rcl1a_cached_normalization_link_audit_v7.py --show-changes --detail
```

The tool expects the completed v6 cache at:

`/opt/nova-drl/output/rcl1a_global_additive_benchmark_v6/qwen3-vl-drl_8b-q8-16k`

Outputs:

- `/opt/nova-drl/output/rcl1a_cached_normalization_link_audit_v7/summary.json`
- `/opt/nova-drl/output/rcl1a_cached_normalization_link_audit_v7/rcl1a_v7_evidence_ledger.csv`

## Interpretation

A remaining count gap after v7 is **not automatically a vision miss**. It can still be an unrecognized shorthand/OCR variant. This layer only tells us what can be supported from the already-cached model text. A production global normalizer would require cross-family regression before deployment.
