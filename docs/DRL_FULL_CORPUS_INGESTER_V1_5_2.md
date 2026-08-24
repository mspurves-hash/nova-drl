# Nova DRL Full Corpus Ingester v1.5.2

Production-scale 80/20 ingestion with resilient vision handling.

## Standing behavior
- Uses the persistent DRL SQLite file index; no NAS-wide discovery scan.
- Uses the frozen v1.5.1 full-corpus manifest.
- Reuses exact-match events from the frozen 10% benchmark.
- Reads new Travelers/Line Cards for parts, basic repair history, RMA, Customer PO, and procurement refs.
- Does not target perfect OCR.
- Does not globally mix unrelated equipment families.

## Vision compatibility path
For each uncached source:
1. Send the original image to Qwen3-VL.
2. If rejected, locally re-encode to standard RGB JPEG and retry.
3. If retry also fails, preserve a `vision_exception` record and continue.

The original DRL share is never modified.

## Exception outputs
- `vision_exceptions_v1_5_2.jsonl`
- `vision_exception_summary_v1_5_2.json`
- per-record JSON under `vision_exceptions/`

## Resume
Run the same command again after an interruption. Successful vision/event cache work is reused. Vision exceptions are retried on later runs.
