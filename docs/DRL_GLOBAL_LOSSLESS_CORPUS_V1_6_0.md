# Nova DRL v1.6.0 — Global Lossless Corpus

## Purpose
Re-ingest the existing frozen DRL repair corpus with the globally proven evidence-first Qwen3-VL 8B architecture, while preserving the hard 80/20 rule.

This release changes **evidence acquisition and preservation**, not corpus membership, technician UI, or Qdrant.

## Global production pipeline
Every selected primary Traveler / Line Card source record follows the same path:

1. **Frozen whole-page transcription** — exact v1.3.5.1 prompt; raw response preserved.
2. **Frozen high-recall prospector** — exact v1.3.6.1 prompt over a deterministically sanitized working view; raw source phrases only.
3. **Generic high-recall vision pass** — additive technical evidence with explicit evidence-role headings.
4. **Generic PN/reference vision pass** — additive identifier evidence only.
5. **Deterministic lossless merge** — Python creates an append-only evidence ledger and derived event views. No LLM rewrites primary evidence.

## Rules that remain above this release
- **80/20 is the hard governing rule.** Benchmarks are used only when they are high-leverage; they are not a mandate for exhaustive validation.
- **Proven-baseline rule.** The model/prompt/role lock cannot silently change during a corpus run.
- **Global-fix rule.** No product-specific resolver logic belongs in this ingestion path.
- Original DRL source files remain read-only.
- Accepted facts remain 0; Qdrant writes remain OFF.

## What v1.6.0 deliberately does not do
- It does not normalize component families during ingestion.
- It does not force ambiguous evidence into a specific part family.
- It does not use a 14B/32B model to rewrite event evidence.
- It does not replace the current technician search/index while ingestion is running.

## Main outputs
Under `/opt/nova-drl/output/drl_global_lossless_corpus_v1_6_0/`:

- `proven_pipeline_lock_v1_6_0.json`
- `source_records/` — immutable/cacheable per-source pass outputs
- `source_records_v1_6_0.jsonl`
- `raw_evidence_ledger_v1_6_0.jsonl`
- `repair_events_lossless_v1_6_0.jsonl`
- `rma_refs_v1_6_0.jsonl`
- `customer_po_refs_v1_6_0.jsonl`
- `procurement_refs_v1_6_0.jsonl`
- `pass_timing_v1_6_0.json`
- `summary_v1_6_0.json`

## Resume behavior
Run the same command again after interruption. A successful source/pass cache is reused only when source image SHA256, model digest, and prompt hash still match.

If the proven model digest or prompt/role set changes, the run fails closed rather than silently mixing baselines.

## Recommended rollout
The smoke run and the full run use the **same output root**, so smoke work is not wasted.

```bash
python3 analysis/nova_drl_global_lossless_corpus_ingester_v1_6_0.py --limit-events 50
```

If the plumbing/output is healthy, resume into the full frozen corpus:

```bash
python3 analysis/nova_drl_global_lossless_corpus_ingester_v1_6_0.py
```

Do not rebuild the technician index until the new corpus finishes and its summary is reviewed.
