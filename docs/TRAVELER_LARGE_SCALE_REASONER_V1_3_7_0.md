# Nova DRL Large-Scale Batched Corpus Reasoner v1.3.7.0

## Purpose

v1.3.7.0 is the first reasoning stage designed for the full GB8 corpus rather than the original 10-Traveler pilot.

The current GB8 corpus contains 461 acquired Travelers. The completed v1.3.6.1 prospect-only pass contains 8,621 evidence-backed candidates. v1.3.7.0 does **not** re-run vision or the 8B prospector. It reasons only over that frozen evidence-backed candidate ledger.

## Architecture

```text
461 whole Travelers
        ↓
v1.3.5.1 raw Qwen3-VL corpus
        ↓
v1.3.6.1 evidence-backed 8B candidate ledger
        ↓
Python metadata/audit separation
        ↓
Qwen2.5 32B dynamic batch clustering
        ↓
Python candidate-ID validation + safe singleton fallback
        ↓
Qwen2.5 32B hierarchical cluster merging
        ↓
Python recurrence/provenance accounting
        ↓
provisional corpus patterns — NOT approved knowledge
```

## Why batching is required

Thousands of candidates cannot be placed reliably into one 16K prompt. v1.3.7.0 dynamically packs manageable batches, caches every result, then reasons over compact cluster summaries in additional merge rounds.

This lets the run be stopped and resumed by executing the same command again.

## Model versus Python responsibilities

Qwen2.5 32B may propose:

- which evidence candidates appear to express the same concept;
- provisional concept labels and keys;
- which provisional clusters appear to represent the same broader concept.

Python alone determines:

- which candidate IDs actually exist;
- whether every eligible candidate is preserved;
- distinct repair-log counts;
- distinct source-hash counts;
- distinct serial-number and unit-folder counts;
- whether a provisional cluster meets the minimum recurrence threshold;
- final deterministic ranking.

## Safety and evidence policy

No source image, raw Qwen3-VL transcription, or v1.3.6.1 raw evidence quote is rewritten. Model labels are organizational metadata only.

A recurring group must contain evidence from at least two distinct DRL logs and two distinct source hashes.

If a 32B batch produces invalid JSON after the configured retries, the run does not discard the candidates or stop the corpus. Those candidates become provisional singleton clusters and the failure remains in the batch audit directory.

Automatic fact approval remains OFF. Qdrant remains OFF.

## First run

Run the plan first:

```bash
python3 analysis/nova_traveler_large_scale_reasoner_v1_3_7_0.py --plan-only
```

Then unload Qwen3-VL if needed and start the reasoner:

```bash
ollama stop qwen3-vl-drl:8b-q8-16k
python3 analysis/nova_traveler_large_scale_reasoner_v1_3_7_0.py
```

The run may require many 32B calls. It is designed to be resumable. If interrupted, run the same command again.

## Review after completion

```bash
cat /opt/nova-drl/output/traveler_large_scale_reason_v1_3_7_0/large_scale_reasoning_summary_v1_3_7_0.txt
```

The summary ranks provisional patterns primarily by the number of distinct serials and then by the number of distinct repair logs. This helps distinguish a pattern recurring across many robots from repeated work on one troublesome unit.
