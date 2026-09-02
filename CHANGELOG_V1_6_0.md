# CHANGELOG — Nova DRL v1.6.0

## Global Lossless Corpus

- Restores the quantitatively proven evidence-first Qwen3-VL 8B model role globally.
- Regression-locks the frozen v1.3.5.1 transcription prompt and v1.3.6.1 prospector prompt.
- Adds globally generic high-recall and PN/reference additive vision passes.
- Removes the later 14B/32B evidence-rewrite gate from primary corpus ingestion.
- Adds a persistent model/prompt/role lock so a proven baseline cannot silently drift mid-corpus.
- Preserves raw evidence and ambiguous evidence; deterministic Python derives views without deleting source evidence.
- Reuses the frozen v1.5.1 full-corpus membership and v1.5.2 source/event selection machinery.
- Re-ingests all selected primary source records; old v1.5.2 structured event evidence is not treated as a substitute for the new lossless evidence.
- Keeps component-family normalization downstream so ingestion remains global and evidence-first.
- Keeps Qdrant OFF and accepted facts at 0.
- Preserves the hard 80/20 rule as the governing project rule.
