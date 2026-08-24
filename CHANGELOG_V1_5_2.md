# Nova DRL v1.5.2 Changelog

## Full Corpus Ingester — Resilient Vision Hotfix

v1.5.2 preserves the already-frozen v1.5.1 full-corpus membership and fixes a production-scale failure mode discovered on the first new legacy Line Card.

### Why
The first v1.5.1 full-corpus vision call received HTTP 400 from Ollama for a JPEG that Pillow could read normally. A single unusual legacy scan must not abort ~12,000 otherwise usable vision records under DRL Nova's fixed 80/20 rule.

### Changes
- Original image is still tried first.
- If Ollama rejects it, v1.5.2 creates a local compatibility copy:
  - EXIF orientation applied.
  - standard RGB color mode.
  - baseline/non-progressive JPEG.
  - metadata stripped.
  - source image never modified.
- The normalized copy is retried once.
- If both attempts fail, a structured vision exception is written and corpus ingestion continues.
- HTTP error bodies are preserved in exception diagnostics when available.
- Events with no usable vision evidence skip the 14B event call instead of wasting inference.
- Successful vision cache records remain resumable.
- Failed vision records are retried on a later rerun rather than permanently cached as success.
- Strict RMA / Customer PO / Digi-Key / Mouser procurement rules remain unchanged.
- Qdrant remains OFF; accepted facts remain 0.

### Corpus membership
v1.5.2 deliberately reuses:
`/opt/nova-drl/corpus/drl_full_corpus_v1_5_1/full_corpus_manifest_v1_5_1.json`

This keeps the same frozen 9,315-folder production corpus rather than silently creating a new population.

## Hotfix: frozen v1.5.1 manifest compatibility
- v1.5.2 now explicitly accepts the already-frozen v1.5.1 full-corpus manifest.
- The manifest is treated as corpus-membership identity, not as a processing-version cache.
- Unknown manifest versions, non-100% manifests, seed mismatches, and tech-base mismatches still fail closed.
- `--force-corpus` is NOT required for the v1.5.1 -> v1.5.2 vision-resilience hotfix and should not be used unless corpus membership is intentionally being regenerated.
