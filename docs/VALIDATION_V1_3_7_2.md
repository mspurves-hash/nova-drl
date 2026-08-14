# Validation — v1.3.7.2

Validation goals:

1. Refuse inputs that are not v1.3.7.1 recurring-pattern output.
2. Refuse input reporting accepted facts or Qdrant entries.
3. Make zero LLM calls.
4. Keep source v1.3.7.1 files read-only.
5. Rank main-view patterns by distinct serials, then distinct logs, then candidates.
6. Retain clearly suppressed template/form noise in an audit JSON.
7. Preserve representative source provenance in the distilled JSON/report.
8. Mark stocking output as provisional attention only, not an approved BOM.
9. Keep accepted facts = 0 and Qdrant = OFF in the manifest.

Run:

```bash
python3 tests/test_traveler_knowledge_distiller_v1_3_7_2.py
```

Expected:

`PASS: Nova DRL Provisional Knowledge Distiller v1.3.7.2 tests`
