# Validation — Nova DRL v1.3.8.1

Validated locally before packaging:

- Python syntax compilation.
- Deterministic UUID point IDs.
- Provisional payload metadata and provenance preservation.
- Duplicate recurring-group ID rejection.
- Guarded collection deletion: non-`nova_drl_gb8_trial_` names are rejected.
- Synthetic frozen-baseline accounting.
- Plan construction with zero Qdrant writes.
- Semantic result rendering.
- ZIP extraction and SHA256 verification.

Network integration with the user's live Qdrant/Ollama services is intentionally performed on the Nova DRL host with `--status`, `--build`, and exact post-build point-count verification.
