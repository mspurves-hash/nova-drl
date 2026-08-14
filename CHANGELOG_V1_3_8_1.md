# Nova DRL v1.3.8.1

## GB8 Qdrant Trial Index

- Adds first controlled Qdrant write path.
- Qdrant is explicitly disposable and non-authoritative.
- Indexes only the 314 frozen v1.3.7.3 technician recurring groups.
- Uses local `nomic-embed-text` embeddings through Ollama.
- One deterministic UUID point per recurring group.
- Payload preserves recurrence and source provenance metadata.
- Adds `--plan-only`, `--status`, `--build`, `--rebuild`, `--drop-trial`, `--search`, `--compare`, and `--interactive`.
- `--rebuild` / `--drop-trial` guarded to `nova_drl_gb8_trial_` collection names only.
- No generative reasoning calls.
- No fact approval.
- No modification of v1.3.7.3 or earlier baselines.
- Bundles the unchanged v1.3.8.0 deterministic query script so `--compare` is self-contained even if v1.3.8.0 was not previously merged.
