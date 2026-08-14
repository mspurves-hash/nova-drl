# Git / Ubuntu Steps — v1.3.7.0

1. Extract the FLAT ZIP into the Windows `Nova-DRL-Starter` Git working copy.
2. Commit and push with GitHub Desktop.
3. On the Ubuntu AI server:

```bash
cd /opt/nova-drl && git pull
```

4. Run the deterministic packaged test:

```bash
python3 tests/test_traveler_large_scale_reasoner_v1_3_7_0.py
```

5. Inspect the real 461-Traveler batch plan without model calls:

```bash
python3 analysis/nova_traveler_large_scale_reasoner_v1_3_7_0.py --plan-only
```

6. Start the resumable 32B large-scale reasoner:

```bash
ollama stop qwen3-vl-drl:8b-q8-16k
```

```bash
python3 analysis/nova_traveler_large_scale_reasoner_v1_3_7_0.py
```

7. If interrupted, run the identical command again. Completed matching batches are reused.

8. After completion:

```bash
cat /opt/nova-drl/output/traveler_large_scale_reason_v1_3_7_0/large_scale_reasoning_summary_v1_3_7_0.txt
```

No command in v1.3.7.0 writes to Qdrant or modifies the source Travelers.
