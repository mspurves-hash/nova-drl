# Git / Ubuntu Steps — v1.3.7.1

1. Extract this FLAT ZIP into the Windows `Nova-DRL-Starter` Git working copy.
2. Commit and push with GitHub Desktop.
3. On the Ubuntu AI server:

```bash
cd /opt/nova-drl && git pull
```

4. Run the deterministic test:

```bash
python3 tests/test_traveler_large_scale_reasoner_v1_3_7_1.py
```

5. Confirm the real corpus plan without model calls:

```bash
python3 analysis/nova_traveler_large_scale_reasoner_v1_3_7_1.py --plan-only
```

Expected baseline remains 461 records / 8,621 candidates, with accepted facts 0 and Qdrant OFF.

6. Start the fast provisional run:

```bash
ollama stop qwen3-vl-drl:8b-q8-16k
```

```bash
python3 analysis/nova_traveler_large_scale_reasoner_v1_3_7_1.py
```

7. If interrupted, rerun the identical command; matching completed batches are reused.

8. After completion:

```bash
cat /opt/nova-drl/output/traveler_large_scale_reason_v1_3_7_1/large_scale_reasoning_summary_v1_3_7_1.txt
```

v1.3.7.0 remains untouched. No v1.3.7.1 command writes to Qdrant or modifies source Travelers.
