# Git Steps — v1.4.0

1. Download and extract the FLAT ZIP into the Windows Nova-DRL-Starter Git working copy.
2. Commit and push with GitHub Desktop.
3. On Ubuntu:

```bash
cd /opt/nova-drl && git pull
```

4. Run tests:

```bash
python3 tests/test_power_supply_corpus_pilot_v1_4_0.py
```

5. Make sure the original PDF is available outside Git at the default input path or use `--source-pdf` with its actual path.
6. Run status and plan-only before the full pilot.
