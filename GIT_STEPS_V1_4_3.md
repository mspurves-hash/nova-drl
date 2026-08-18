# Git steps — v1.4.3

1. Extract this FLAT ZIP into the Windows Nova-DRL-Starter working tree.
2. Commit/push with GitHub Desktop.
3. On Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_rcl1a_indexed_focused_recovery_v1_4_3.py
python3 analysis/nova_rcl1a_indexed_focused_recovery_v1_4_3.py --status
python3 analysis/nova_rcl1a_indexed_focused_recovery_v1_4_3.py --plan-only
```

Do not start the full model run until the status/plan selection counts look correct.
