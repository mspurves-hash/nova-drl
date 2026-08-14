# Nova DRL v1.3.7.1 — Fast Provisional Corpus Reasoner

## Direction

v1.3.7.1 adopts the DRL 80/20 operating rule for provisional corpus reasoning: prioritize useful recurring repair knowledge and throughput, preserve evidence, and correct edge cases later when humans encounter them in real use.

## Changed

- 14B Q6 is now the default bulk reasoner.
- 16K context remains unchanged.
- Stage-1 prediction budget reduced from 6144 to 2048 tokens.
- Merge prediction budget reduced from 4096 to 2048 tokens.
- Malformed JSON retries reduced from two retries to one retry, then safe fallback.
- Stage-1 model output now includes only useful groups of 2+ candidates; no model-generated singleton exhaust.
- Python remains responsible for preserving all omitted candidates as singletons.
- Prompting now encourages useful DRL-specific grouping while allowing practical broad grouping.
- Minor OCR/shop-language differences no longer block provisional grouping.
- Hierarchical merge is tuned for practical search equivalence instead of forensic equivalence.
- Plan/summary wording is model-neutral and records the requested reasoning model/settings.
- Fixed duplicate Stage-1 failure-counter increment inherited from v1.3.7.0.

## Preserved

- v1.3.5.1 and v1.3.6.1 frozen baselines.
- Original/raw evidence.
- Candidate-ID validation and provenance.
- Resumable caches.
- Deterministic recurrence accounting.
- Accepted facts = 0.
- Qdrant = OFF.
