# PRE-200 Benchmark v5 — Proven Baseline Additive Merge

Purpose: compare the now-proven frozen v1.3.5.1/v1.3.6.1 8B evidence-first baseline against additive cached evidence paths.

This benchmark makes **no model calls** and modifies no production data.

It compares:
1. Frozen historical transcription + prospector baseline.
2. Frozen baseline + PN-focused pass.
3. Current high-recall direct sections + PN-focused pass.
4. Frozen baseline + high-recall + PN-focused pass.

The decision rule is strict: the frozen baseline remains production authority unless an additive candidate materially improves it. Additive passes may never delete or replace baseline evidence.

Run:

```bash
python3 tests/test_proven_baseline_merge_v5.py
python3 tools/pre200_proven_baseline_merge_v5.py --detail
```

PN-focused extraction has proven recall on this benchmark but still requires a precision audit before global production deployment.
