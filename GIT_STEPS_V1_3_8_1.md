# Git steps — Nova DRL v1.3.8.1

1. Extract this FLAT ZIP into the Windows Nova-DRL-Starter Git working directory.
2. Commit and push with GitHub Desktop.
3. On Ubuntu:

```bash
cd /opt/nova-drl && git pull
```

4. Test:

```bash
python3 tests/test_gb8_qdrant_trial_index_v1_3_8_1.py
```

5. Validate frozen source:

```bash
python3 analysis/nova_gb8_qdrant_trial_index_v1_3_8_1.py --self-check
```

6. Check services before any write:

```bash
python3 analysis/nova_gb8_qdrant_trial_index_v1_3_8_1.py --status
```

7. Plan with no network/write:

```bash
python3 analysis/nova_gb8_qdrant_trial_index_v1_3_8_1.py --plan-only
```

8. First trial build:

```bash
python3 analysis/nova_gb8_qdrant_trial_index_v1_3_8_1.py --build
```
