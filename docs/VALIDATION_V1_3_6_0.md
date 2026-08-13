# Validation — v1.3.6.0

The packaged unit test validates the deterministic guardrails without requiring Ollama:

- completed v1.3.5.1 manifest is required;
- `Hours in Final Testing` is masked from the sort view;
- source text outside that field is retained;
- exact candidate quotes are accepted;
- whitespace-only quote differences are accepted while the actual source slice is stored;
- character rewrites are rejected (`Blue Schmoo` cannot stand in for `Blue Schmoo's`);
- one-log model groups are rejected as non-recurring;
- two-log/two-source-hash groups are accepted;
- no automatic fact approval or Qdrant behavior exists in the sorter.

The first live pilot should reuse the existing 10-log GB8 v1.3.5.1 corpus and compare the resulting provisional summary against the earlier manual 8B/32B prompt tests.
