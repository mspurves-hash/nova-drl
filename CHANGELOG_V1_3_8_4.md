# Nova DRL v1.3.8.4 — Clean Technician Output

- Preserves v1.3.8.3 retrieval/composition behavior and all frozen upstream baselines.
- Hides internal `rg_...` recurring-group IDs from normal human-readable support lines.
- Keeps recurring-group IDs in JSON/provenance and model-support validation.
- Hides representative Traveler evidence by default.
- Adds `--show-evidence` for one-shot queries.
- Adds `:evidence on` / `:evidence off` in interactive mode.
- When expanded, evidence shows log, serial, source text, and source path when available, but not internal group IDs.
- No Qdrant writes; accepted facts remain 0.
