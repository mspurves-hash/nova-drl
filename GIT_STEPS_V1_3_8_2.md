# Git / Install Steps — v1.3.8.2

1. Extract this FLAT ZIP into the Windows Nova-DRL-Starter Git working copy.
2. Commit/push with GitHub Desktop.
3. On Ubuntu:

```bash
cd /opt/nova-drl && git pull
```

4. Test:

```bash
python3 tests/test_gb8_hybrid_technician_search_v1_3_8_2.py
```

5. Validate frozen source without network:

```bash
python3 analysis/nova_gb8_hybrid_technician_search_v1_3_8_2.py --self-check
```

6. Validate Qdrant/Ollama/runtime state:

```bash
python3 analysis/nova_gb8_hybrid_technician_search_v1_3_8_2.py --status
```

7. First hybrid search:

```bash
python3 analysis/nova_gb8_hybrid_technician_search_v1_3_8_2.py "Y axis drifting"
```
