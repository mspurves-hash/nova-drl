# Validation — v1.3.8.2

Validated before packaging:

- Python compilation passes.
- v1.3.8.2 deterministic fusion tests pass.
- Bundled v1.3.8.1 Qdrant regression tests pass unchanged.
- Bundled v1.3.8.0 deterministic query regression tests pass unchanged.
- RRF test confirms raw Qdrant/Python score scales are not combined.
- Consensus groups outrank single-engine-only groups under equal RRF weights.
- Recurrence support is tuned to `0.0003` and remains a small tiebreaker; it does not override a one-rank relevance advantage in the validation case.
- Stale/unknown Qdrant group IDs are ignored unless they exist in frozen v1.3.7.3 JSON.
- Rendering exposes semantic rank, deterministic rank, raw diagnostic scores, recurrence support, and source evidence.
- End-to-end local integration passed using synthetic frozen JSON plus fake Ollama/Qdrant HTTP endpoints: status, 314-point count validation, embedding request, semantic search, deterministic search, fusion, and final evidence rendering.
- Package extraction and SHA256 verification pass.
