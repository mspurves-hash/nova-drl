# Nova DRL CHANGELOG — v1.3.8.3

## GB8 Technician Answer Composer

- Added `analysis/nova_gb8_technician_answer_composer_v1_3_8_3.py`.
- Keeps v1.3.8.2 Hybrid Technician Search unchanged as the evidence-selection baseline.
- Uses `qwen25-drl:14b-q6-16k` only after hybrid retrieval has selected the evidence set.
- Composer cannot search the corpus, modify recurrence counts, approve facts, or write Qdrant.
- Every model-composed finding and suggested check must cite one or more retrieved `recurring_group_id` values.
- Python drops unknown/unsupported group IDs before rendering.
- Added deterministic fallback when the 14B response is unavailable, malformed, or unusable.
- Added concise technician answer rendering with representative Traveler evidence and explicit provenance.
- Added exact composer-model status verification.
- Added interactive mode (`nova>`) with optional raw hybrid retrieval display.
- Added deterministic unit/integration tests.

Accepted facts remain `0`. Qdrant writes remain `0`.
