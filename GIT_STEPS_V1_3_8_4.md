# Git / Ubuntu Steps — v1.3.8.4

1. Extract this FLAT ZIP into the Windows Nova-DRL-Starter Git working copy.
2. Commit and push with GitHub Desktop.
3. On Ubuntu:

```bash
cd /opt/nova-drl && git pull
```

4. Test:

```bash
python3 tests/test_gb8_technician_answer_composer_v1_3_8_4.py
```

5. Self-check:

```bash
python3 analysis/nova_gb8_technician_answer_composer_v1_3_8_4.py --self-check
```

6. Normal clean answer:

```bash
python3 analysis/nova_gb8_technician_answer_composer_v1_3_8_4.py "Y axis drifting"
```

7. Expand Traveler evidence only when needed:

```bash
python3 analysis/nova_gb8_technician_answer_composer_v1_3_8_4.py --show-evidence "Y axis drifting"
```

Interactive evidence toggle:

```text
:evidence on
:evidence off
```
