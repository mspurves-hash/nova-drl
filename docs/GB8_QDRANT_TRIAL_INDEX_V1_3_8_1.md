# Nova DRL GB8 Qdrant Trial Index v1.3.8.1

## Purpose

Create the first disposable semantic-search trial over the frozen v1.3.7.3 GB8 technician-signal baseline.

Qdrant is **not** the knowledge authority. The v1.3.7.3 JSON and original evidence remain authoritative. This version may delete/rebuild only its guarded versioned trial collection.

## Trial scope

- Source recurring groups: 419
- Technician groups indexed: 314
- Reference/admin groups indexed: 0 (105 remain outside this first trial)
- Raw v1.3.6.1 candidates indexed: 0
- One Qdrant point per technician recurring group
- Local embedding model: `nomic-embed-text`
- Generative reasoning calls: 0
- Accepted facts: 0
- Knowledge state: provisional

Each point payload contains the recurring-group ID, lane, service areas, recurrence counts, log numbers, robot serial numbers, candidate IDs, source hashes, representative Traveler evidence, and explicit `approved=false` metadata.

## Safety model

The default collection is:

`nova_drl_gb8_trial_v1_3_8_1`

`--rebuild` and `--drop-trial` refuse destructive action unless the collection name starts with `nova_drl_gb8_trial_`.

The source v1.3.7.3 files are read-only inputs and are never modified.

## Commands

### Frozen-source self-check

```bash
python3 analysis/nova_gb8_qdrant_trial_index_v1_3_8_1.py --self-check
```

Expected frozen baseline:

`source=419 technician=314 reference=105 planned_points=314`

### Plan only — no network or Qdrant writes

```bash
python3 analysis/nova_gb8_qdrant_trial_index_v1_3_8_1.py --plan-only
```

### Check Qdrant and Ollama

```bash
python3 analysis/nova_gb8_qdrant_trial_index_v1_3_8_1.py --status
```

### First build

```bash
python3 analysis/nova_gb8_qdrant_trial_index_v1_3_8_1.py --build
```

The build auto-detects the embedding dimension from Ollama, creates a cosine-distance collection, embeds the 314 technician groups in batches, upserts deterministic point IDs, verifies the exact Qdrant point count, and writes a local audit manifest with vectors omitted.

### Rebuild from scratch

```bash
python3 analysis/nova_gb8_qdrant_trial_index_v1_3_8_1.py --rebuild
```

This deletes and recreates only the guarded trial collection. It does not alter Nova DRL evidence or knowledge JSON.

### Semantic search

```bash
python3 analysis/nova_gb8_qdrant_trial_index_v1_3_8_1.py --search "Y axis drifting"
```

```bash
python3 analysis/nova_gb8_qdrant_trial_index_v1_3_8_1.py --search "vacuum leak when arm extends"
```

### Side-by-side with deterministic v1.3.8.0

```bash
python3 analysis/nova_gb8_qdrant_trial_index_v1_3_8_1.py --compare "Y axis drifting"
```

The scores are from different systems and are not numerically comparable. The report shows top group IDs and overlap so humans can judge which retrieval is more useful.

### Interactive trial

```bash
python3 analysis/nova_gb8_qdrant_trial_index_v1_3_8_1.py --interactive
```

Commands inside the console:

- `:compare on`
- `:compare off`
- `:status`
- `:quit`

### Delete the trial index only

```bash
python3 analysis/nova_gb8_qdrant_trial_index_v1_3_8_1.py --drop-trial
```

## Local outputs

`/opt/nova-drl/output/gb8_qdrant_trial_v1_3_8_1/`

- `qdrant_trial_plan_v1_3_8_1.json`
- `qdrant_trial_manifest_v1_3_8_1.json`
- `indexed_points_audit_v1_3_8_1.json`

The audit intentionally excludes embedding vectors; Qdrant can always be rebuilt from the frozen source groups.

## Bundled comparison dependency

The FLAT ZIP includes the unchanged `analysis/nova_gb8_technician_query_engine_v1_3_8_0.py` so `--compare` works even if the prior v1.3.8.0 package was not merged separately. v1.3.8.1 does not modify that deterministic baseline.
