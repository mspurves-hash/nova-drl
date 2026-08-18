# Git steps — v1.4.4

1. Extract the FLAT ZIP into the Windows Nova-DRL Git working directory.
2. Review/add the new v1.4.4 files in GitHub Desktop.
3. Commit and push.
4. On the Ubuntu Nova server:

```bash
cd /opt/nova-drl
git pull
```

5. Run the test:

```bash
python3 tests/test_rcl1a_parts_intelligence_v1_4_4.py
```

6. Check status and plan before the full run:

```bash
python3 analysis/nova_rcl1a_parts_intelligence_v1_4_4.py --status
python3 analysis/nova_rcl1a_parts_intelligence_v1_4_4.py --plan-only
```
