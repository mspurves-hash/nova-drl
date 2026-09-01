# PRE-200 benchmark v3 — lossless classification + PN-focused acquisition

This incremental benchmark assumes the v2 25-event benchmark assets and both cached 8B vision runs already exist.
It does not change production Nova data.

## 1. Instant cached pipeline comparison (NO model calls)

```bash
python3 tools/pre200_cached_pipeline_compare_v3.py --show-category-detail
```

This compares the old v1.5.2 structured corpus, raw 8B production/high-recall output, a deterministic heading-only/lossless field parser, the union of both vision prompts, and the cached 14B reason pass.

The key question is whether a lossless heading parser retains most of the 85.7% raw high-recall evidence. If it does, the 14B model should not be allowed to rewrite/drop evidence during primary ingestion.

## 2. Focused exact-PN visual pass

```bash
python3 tools/pre200_pn_focus_vision_benchmark.py --model qwen3-vl-drl:8b-q8-16k --show-misses
```

This is a narrow second visual pass intended to test whether exact reference strings can be recovered more reliably than the 37.5–50% achieved by the general prompts.
