# Git steps v1.4.5

Copy the FLAT package contents into the Windows Nova-DRL Git working directory, commit/push with GitHub Desktop, then on Ubuntu:

```bash
cd /opt/nova-drl
git pull
```

Run:

```bash
python3 tests/test_indexed_repair_intelligence_v1_4_5.py
python3 analysis/nova_indexed_repair_intelligence_v1_4_5.py --status
python3 analysis/nova_indexed_repair_intelligence_v1_4_5.py --plan-only
```

Do not start the full run until the status/plan source counts look sensible.
