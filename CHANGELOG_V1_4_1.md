# Nova DRL v1.4.1 Changelog

## Power Supply Focused Evidence Recovery

- Makes individual Line Card images the primary production input.
- Keeps the combined PDF only as a benchmark adapter.
- Adds 300-DPI PDF rendering for benchmark pages.
- Adds parts-focused Qwen3-VL reread rather than relying on general whole-page transcription.
- Optionally supplies an enlarged repair-region crop in the same vision call.
- May reuse matching v1.4.0 blind whole-page transcriptions as auxiliary evidence without modifying them.
- Strengthens duplicate candidate generation with exact image hash, optional perceptual hash, and lower-threshold focused-text similarity.
- Groups multiple legitimate images with the same 9-digit DRL log into one repair event for frequency counting.
- Replaces one-shot part normalization with deterministic exact consolidation + fuzzy candidate components + resumable 14B normalization batches.
- Keeps all part-family labels provisional.
- Python remains recurrence/quantity authority.
- Accepted facts remain 0; Qdrant remains OFF.
