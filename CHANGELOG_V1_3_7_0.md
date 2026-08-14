# Nova DRL v1.3.7.0 — Large-Scale Batched Corpus Reasoner

## Why this version exists

The GB8 large-scale acquisition produced 461 Travelers and the v1.3.6.1 prospect-only pass produced 8,621 evidence-backed candidates. A single 16K 32B prompt is no longer an appropriate reasoning architecture.

## Added

- New `analysis/nova_traveler_large_scale_reasoner_v1_3_7_0.py`.
- Requires a completed v1.3.6.1 `prospect_only_complete` candidate ledger.
- Dynamically packs candidates into 32B batches by semantic lane and input character budget.
- Sorts candidates lexically for batching only, improving the chance that similar raw phrases are presented together without changing their evidence.
- Stage 1 asks Qwen2.5 32B to create provisional concept clusters; singleton clusters are explicitly allowed.
- Python guarantees every reasoning-eligible candidate survives exactly once. Invalid/missing model assignments fall back to singletons rather than disappearing.
- Stage 1 and hierarchical merge calls are cached separately and are safe to resume by rerunning the same command.
- Ollama JSON mode plus retry handling for malformed model output. After all retries fail, the affected batch falls back safely instead of stopping the entire corpus run.
- Hierarchical merge rounds operate on compact cluster summaries rather than thousands of raw candidates at once.
- Adjacent merge batches overlap by a few cluster summaries; Python union-find combines compatible merge proposals across overlaps.
- Python owns final recurrence counts across distinct DRL logs, distinct source hashes, distinct serial numbers, and unit folders.
- Generic document/event identity metadata is excluded only from the 32B working set and retained in `reasoning_exclusion_audit_v1_3_7_0.json`.
- Full raw candidate provenance remains attached to every provisional recurring group.
- `--plan-only` reports the actual large-scale batch plan without making model calls.

## Still intentionally OFF

- Automatic fact approval.
- Qdrant ingestion.
- Silent normalization of spelling, abbreviations, shop language, part numbers, or OCR.
- Modification of source Travelers, v1.3.5.1 raw transcriptions, or v1.3.6.1 raw candidate evidence.

## Output

Primary outputs are written to:

`/opt/nova-drl/output/traveler_large_scale_reason_v1_3_7_0`

Important files:

- `large_scale_reasoning_summary_v1_3_7_0.txt`
- `recurring_patterns_v1_3_7_0.json`
- `final_cluster_ledger_v1_3_7_0.json`
- `reasoning_exclusion_audit_v1_3_7_0.json`
- `reasoner_manifest_v1_3_7_0.json`
- resumable `stage1_batches/` and `merge_rounds/` audit trees
