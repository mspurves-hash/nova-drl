# Nova DRL Architecture Audit v4

## What the audit found

The original frozen evidence-first architecture was materially safer than the later full-corpus path:

### v1.3.5.1 Whole Traveler Corpus Collector
- Qwen3-VL 8B reads the complete Traveler.
- Raw model transcription is preserved unchanged.
- No repair/parts/failure/testing classification occurs during acquisition.
- The prompt explicitly says not to decide what is important.

### v1.3.6.1 High-Recall Prospector
- The same Qwen3-VL 8B works over a temporary sanitized view.
- It proposes verbatim source phrases, not rewritten facts.
- Python re-binds every candidate to immutable raw evidence and owns counting/provenance.
- The larger model was intended only for cross-record grouping of evidence IDs, not rewriting evidence.

### v1.4.6 -> v1.5.2 drift
Two architectural changes introduced a lossy gate:
1. Vision prompt changed to: `Do NOT attempt perfect OCR and do NOT transcribe the whole form.`
2. Qwen2.5 14B rewrote the vision evidence into a concise structured JSON event record.

The PRE-200 gold benchmark quantified the consequence:
- current v1.5.2 structured corpus: 59.3% useful-fact recall anywhere / 47.1% right field
- 8B production raw: 81.4%
- 8B high-recall raw: 85.7%
- 14B rewrite on high-recall: 74.3% anywhere / 63.6% right field
- focused 8B PN pass: 100% recall on the eight known PN/reference facts

## Hard rule carried forward

Once a model + prompt + role is quantitatively proven, it becomes the global baseline and stays frozen until a controlled benchmark proves a superior replacement. No silent model, prompt, or role substitutions.

A later reasoning model may add enrichment, but may not delete, replace, or become the sole authority for source evidence that a proven acquisition stage already captured.

## v4 replay purpose

`tools/pre200_historical_pipeline_replay_v4.py` runs the exact historical v1.3.5.1 acquisition prompt and v1.3.6.1 high-recall prospector prompt on the same 25-event PRE-200 gold benchmark. This settles whether the early 8B architecture itself was weak or whether later evaluation/pipeline stages masked its actual performance.
