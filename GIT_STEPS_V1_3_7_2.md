# Git / Ubuntu Steps — v1.3.7.2

1. Extract this FLAT ZIP into the Windows `Nova-DRL-Starter` Git working copy.
2. Commit and push with GitHub Desktop.
3. On Ubuntu:

```bash
cd /opt/nova-drl && git pull
```

4. Run the deterministic test:

```bash
python3 tests/test_traveler_knowledge_distiller_v1_3_7_2.py
```

Expected:

`PASS: Nova DRL Provisional Knowledge Distiller v1.3.7.2 tests`

5. Run the distiller. This makes no LLM calls:

```bash
python3 analysis/nova_traveler_knowledge_distiller_v1_3_7_2.py
```

6. Read the technician report:

```bash
cat /opt/nova-drl/output/traveler_knowledge_distill_v1_3_7_2/gb8_provisional_knowledge_report_v1_3_7_2.txt
```

7. Optional quick views:

```bash
head -40 /opt/nova-drl/output/traveler_knowledge_distill_v1_3_7_2/ranked_recurring_patterns_v1_3_7_2.csv
```

```bash
cat /opt/nova-drl/output/traveler_knowledge_distill_v1_3_7_2/stocking_attention_v1_3_7_2.csv
```

v1.3.7.1 remains untouched. v1.3.7.2 makes zero LLM calls, accepts zero facts automatically, and writes nothing to Qdrant.
