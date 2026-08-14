# Validation — v1.3.7.1

The deterministic test suite carries forward the v1.3.7.0 safeguards and adds tests for the fast provisional mode.

Covered behaviors include:

- v1.3.6.1 input guardrails remain intact;
- metadata and unclear-OCR exclusions remain audit-preserved rather than consuming fast bulk reasoning time;
- dynamic batching preserves every eligible candidate exactly once;
- omitted Stage-1 candidates automatically become Python fallback singletons;
- duplicate/unknown model IDs cannot cause evidence loss;
- recurrence counts remain Python-owned;
- default reasoning model is `qwen25-drl:14b-q6-16k`;
- fast Stage-1 prompt emits useful groups rather than singleton exhaust;
- merge prompt uses the 80/20 provisional philosophy;
- the Stage-1 failed-batch counter increments once per failed batch;
- Qdrant and automatic fact approval remain OFF.

Expected result:

```text
PASS: Nova DRL Large-Scale Batched Corpus Reasoner v1.3.7.1 tests
```
