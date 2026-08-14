# Validation — v1.3.7.0

The packaged unit test validates the deterministic portions of the large-scale reasoner without requiring Ollama or the DRL NAS.

Covered behaviors:

- generic event-identity/admin candidates are preserved outside the 32B working set;
- repair/service candidates remain reasoning eligible;
- unclear OCR remains outside the automatic 32B recurrence path;
- GB8 serial extraction works for both `SN 80010732` and `SN GB8-MT-80110451` folder forms;
- dynamic batching preserves every candidate exactly once and does not mix semantic lanes;
- invalid/duplicate/missing stage-1 model assignments cannot cause candidate loss;
- specific matching provisional concept keys may consolidate while generic keys such as `repair` do not;
- merge proposals are validated against real cluster IDs;
- Python union-find combines accepted hierarchical merge proposals;
- recurrence is counted from distinct logs and distinct source hashes, with distinct serial counts computed separately;
- full raw evidence provenance remains attached to recurring groups;
- v1.3.6.1 input must be `prospect_only_complete` with zero accepted facts and zero Qdrant entries.

Expected result:

```text
PASS: Nova DRL Large-Scale Batched Corpus Reasoner v1.3.7.0 tests
```
