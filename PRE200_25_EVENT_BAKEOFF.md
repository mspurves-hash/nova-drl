# PRE-200 25-Event Multimodal Extraction Bake-Off

This is a diagnostic only. It does not change Nova production data, indexes, prompts, or reports.

## Source selection

25 unique PRE-200-family repair events from `pre-200.pdf`, pages 10–38. Duplicate scan pages 21 and 35 and PRE-201-CE pages 24 and 26 are excluded.

The ground truth is intentionally 80/20: only technician-useful facts clearly visible on the line card are scored—reported failures, explicit replaced components, meaningful repair actions, and part/reference numbers.

## Run on the Nova server

```bash
python3 tools/pre200_25_event_extraction_benchmark.py --show-misses
```

Default corpus:

`/opt/nova-drl/output/drl_full_corpus_v1_5_2/repair_events_v1_5_2.jsonl`

## What the two scores mean

- **ANYWHERE**: Did the current structured repair event capture evidence of the source fact anywhere?
- **RIGHT FIELD**: Did it put the fact in the field used by downstream aggregation?

If ANYWHERE is low, the primary loss is upstream acquisition/vision-model understanding. If ANYWHERE is much higher than RIGHT FIELD, the model saw the information but the schema/classification step lost it.
