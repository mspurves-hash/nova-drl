# Windows -> GitHub -> Ubuntu

1. Extract on Windows.
2. Copy/merge into the existing Windows Nova DRL Git directory.
3. Commit in GitHub Desktop: `Add Nova Repair Evidence Collector v1.4`
4. Push.

Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_repair_evidence_collector_v1_4.py
```

Run the first live pilot in `--inventory-only` mode before enabling document extraction.
