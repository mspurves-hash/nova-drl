# Nova DRL GB8 Hybrid Technician Search v1.3.8.2

## Purpose

Combine the complementary strengths demonstrated in the v1.3.8.1 field trials:

- **Qdrant semantic retrieval** finds symptom meaning and related wording.
- **v1.3.8.0 deterministic retrieval** finds exact DRL terminology and broad literal historical coverage.

v1.3.8.2 does not rebuild or modify Qdrant. It reads the existing
`nova_drl_gb8_trial_v1_3_8_1` collection and the frozen v1.3.7.3 technician JSON.

## Fusion

Default retrieval:

1. Qdrant top 12.
2. Python deterministic top 12.
3. Deduplicate by recurring group ID.
4. Weighted Reciprocal Rank Fusion (RRF), equal engine weights, `k=60`.
5. Add a deliberately small recurrence-support bonus (`0.0003 * bounded_support`).
6. Return final top 10 with representative Traveler evidence from authoritative JSON.

Qdrant cosine scores and deterministic Python scores are shown for diagnostics but **never added together**.

## Commands

```bash
python3 analysis/nova_gb8_hybrid_technician_search_v1_3_8_2.py --self-check
python3 analysis/nova_gb8_hybrid_technician_search_v1_3_8_2.py --status
python3 analysis/nova_gb8_hybrid_technician_search_v1_3_8_2.py "Y axis drifting"
python3 analysis/nova_gb8_hybrid_technician_search_v1_3_8_2.py "upper arm vacuum leak"
python3 analysis/nova_gb8_hybrid_technician_search_v1_3_8_2.py --interactive
```

Specific serial/log, service-area, and stocking questions continue to use the deterministic v1.3.8.0 path because they are structured lookups rather than semantic retrieval tasks.

## Authority

- Original Travelers and raw transcription remain authoritative evidence.
- v1.3.7.3 recurring-group JSON is the authoritative structured search source.
- Qdrant is a disposable semantic index only.
- No generative reasoning is performed.
- No facts are automatically approved.
