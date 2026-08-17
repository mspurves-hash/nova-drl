# Git / Ubuntu Steps — v1.3.8.5

1. Extract this FLAT ZIP into the Windows Nova-DRL-Starter Git working copy.
2. Commit and push with GitHub Desktop.
3. On Ubuntu:

```bash
cd /opt/nova-drl && git pull
```

4. Test:

```bash
python3 tests/test_gb8_technician_answer_composer_v1_3_8_5.py
```

5. Normal minimal answer:

```bash
python3 analysis/nova_gb8_technician_answer_composer_v1_3_8_5.py "Y axis drifting"
```

6. Expand evidence/provenance only when needed:

```bash
python3 analysis/nova_gb8_technician_answer_composer_v1_3_8_5.py --show-evidence "Y axis drifting"
```

Interactive:

```text
:evidence on
:evidence off
```
