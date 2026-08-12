# Nova DRL Whole Traveler Corpus Collector v1.3.5.1

## Purpose

v1.3.5.1 is an **acquisition-only** build. It intentionally stops interpreting individual Travelers during ingestion.

The successful power-supply workflow established the principle: collect the repair history broadly first, then sort repeated template text, useful repair content, parts, quantities, terminology, diagnostics, and noise from the larger corpus.

## Primary vision model

Default Ollama model:

`qwen3-vl-drl:8b-q8-16k`

The model receives the **original complete Traveler image bytes**. No semantic crop, repair box, row geometry, mark gate, or OCR intermediary is used.

## Collector flow

1. Recursively discover image files whose names contain a 9-digit DRL log number, `Line Card`, and `Original` or `Warranty`.
2. Preserve exact source path, relative path, unit folder, log, variant, file size, image metadata, and SHA-256.
3. Send the original complete image to Qwen3-VL with a literal-transcription-only prompt.
4. Save the raw model response exactly as returned.
5. Save audit-only transcription metrics such as `[unclear]` count and possible runaway repetition.
6. Flag exact source-hash duplicate groups without suppressing any source during acquisition.
7. Write/update the corpus manifest after every completed Traveler so interruption does not lose completed work.

## Explicit non-goals

The collector does **not** decide:

- which text is relevant;
- what is printed boilerplate;
- what is garbage/admin noise;
- what is a repair action;
- what is a replacement part;
- what a shop term means;
- what failed;
- what caused the failure;
- whether a test/final result is established;
- whether an ambiguous part string should be normalized;
- whether a duplicate should be excluded from the corpus.

Those are later corpus-analysis and human-review tasks.

## Duplicate policy

Exact SHA-256 duplicates are flagged during acquisition but are not discarded. Every source file receives a record. Later frequency analysis must exclude human/algorithm-approved duplicate scans before counting repair frequency.

## Resume behavior

By default the collector reuses an existing transcription only when all of these still match:

- source SHA-256;
- model name;
- transcription prompt SHA-256;
- context size;
- output-token limit.

Use `--force` to rerun vision.

## Safety

- DRL source files are never written or changed.
- Output path is rejected if it is inside the source tree.
- No repair facts are accepted automatically.
- Qdrant writes are disabled.
