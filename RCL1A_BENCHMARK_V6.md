# Nova DRL Benchmark v6 — RCL1A Cross-Family Global Validation

Purpose: test whether the two additive 8B passes that improved PRE-200 generalize to a very different, parts-heavy product family **before** they are promoted globally.

The source is the established `RCL1A-1D-W3 All Line Cards.pdf` benchmark. It contains 167 scanned pages; 11 duplicate scans are excluded, leaving 156 unique repairs.

This benchmark intentionally keeps the proven-baseline rule:

- the frozen v1.3.5.1/v1.3.6.1 evidence-first baseline remains authoritative;
- the high-recall and PN-focused passes are **additive candidates only**;
- no 14B rewrite stage is used;
- benchmark part names/counts are used only by the scorer **after** model extraction and are not included in either prompt;
- no production corpus/index is modified.

## Run

First verify no benchmark leakage:

```bash
python3 tests/test_rcl1a_global_additive_benchmark_v6.py
```

Smoke test on 5 pages:

```bash
python3 tools/rcl1a_global_additive_benchmark_v6.py --max-pages 5
```

Full blind run across 156 unique repairs:

```bash
python3 tools/rcl1a_global_additive_benchmark_v6.py
```

The full run makes two 8B vision calls per unique page and caches every result, so it can be resumed. Absolute count error is meaningful only on the full 156-page run.

The scorer reports, for the established benchmark components, expected repair-event count vs raw evidence discovery vs replacement-linked count, plus Top-6 ranking overlap. Quantity/pieces scoring is intentionally deferred; first prove that the additive architecture reproduces the **repair-frequency ordering** without benchmark leakage.
