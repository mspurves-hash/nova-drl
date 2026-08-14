# Nova DRL Fast Provisional Corpus Reasoner v1.3.7.1

## Purpose

v1.3.7.1 is a forward-only performance tuning of v1.3.7.0. It does **not** replace or rewrite the frozen v1.3.5.1 acquisition baseline or the frozen v1.3.6.1 evidence-backed candidate ledger.

The operating philosophy is intentionally **80/20**: find the repair patterns that are useful most of the time, keep the original evidence available, and amend provisional interpretations later when a technician actually questions them.

Current GB8 baseline remains:

- 461 acquired Travelers
- 8,621 evidence-backed candidates
- 0 accepted facts
- Qdrant OFF

## Why 14B Q6

The local benchmark on the 22 GB RTX 2080 Ti showed:

- `qwen25-drl:32b-16k`: 24 GB loaded, 87% GPU / 13% CPU, 16K context
- `qwen25-drl:14b-q6-16k`: 14 GB loaded, 100% GPU, 16K context

On the identical first 175-candidate components batch, the successful 14B retry completed in about 43 seconds versus about 229 seconds for the 32B response. The original 14B prompt also produced an overlong malformed first response, so v1.3.7.1 changes the task rather than merely swapping models.

## What changed from v1.3.7.0

- Default bulk reasoner: `qwen25-drl:14b-q6-16k`.
- Stage-1 output budget: 2048 tokens instead of 6144.
- Merge output budget: 2048 tokens instead of 4096.
- JSON retries: one retry after the first failed response instead of two.
- Stage 1 no longer asks the model to return every candidate or any singleton clusters.
- The model emits only useful groups of 2+ candidates; Python automatically preserves every omitted candidate as a singleton.
- Stage-1 prompt explicitly prefers practical DRL-specific labels such as axis/subassembly/component/action combinations when obvious.
- Minor OCR, spelling, abbreviation, punctuation, and shop-term differences no longer require model hesitation.
- Merge reasoning is loosened from forensic equivalence to practical search/repair equivalence.
- Stage-1 batch size remains 22,000 input characters for throughput.
- Existing resumable per-batch cache, candidate-ID validation, recurrence accounting, provenance, and fallback behavior remain intact.
- Fixed a v1.3.7.0 summary-accounting typo that incremented a failed Stage-1 batch twice.

## What did NOT change

- Original Travelers remain read-only.
- v1.3.5.1 raw transcription remains untouched.
- v1.3.6.1 candidate raw evidence remains untouched.
- Python still validates real candidate and cluster IDs.
- Python still guarantees every eligible candidate survives exactly once.
- Recurrence still requires at least 2 distinct logs and 2 distinct source hashes.
- Model labels remain provisional, not approved facts.
- Automatic fact approval remains OFF.
- Qdrant remains OFF.

## Architecture

```text
461 whole Travelers
        ↓
frozen v1.3.5.1 acquisition
        ↓
frozen v1.3.6.1 candidate ledger — 8,621 candidates
        ↓
Python low-value audit separation
        ↓
Qwen2.5 14B Q6 fast provisional grouping
        ↓
Python ID validation + automatic singleton preservation
        ↓
Qwen2.5 14B Q6 practical hierarchical merging
        ↓
Python recurrence / provenance / ranking
        ↓
provisional corpus knowledge
        ↓
human amendment later when useful
```

## Default run

```bash
python3 analysis/nova_traveler_large_scale_reasoner_v1_3_7_1.py --plan-only
```

Then:

```bash
ollama stop qwen3-vl-drl:8b-q8-16k
```

```bash
python3 analysis/nova_traveler_large_scale_reasoner_v1_3_7_1.py
```

The output root is separate from v1.3.7.0:

`/opt/nova-drl/output/traveler_large_scale_reason_v1_3_7_1`

If interrupted, rerun the same command. Matching completed batches are reused.
