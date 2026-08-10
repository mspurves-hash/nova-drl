# Windows -> GitHub -> Ubuntu

1. Extract this package on Windows.
2. Copy/merge it into the existing Windows Nova DRL Git working directory.
3. Review changes in GitHub Desktop.
4. Commit as:

```text
Add Nova Repair Evidence Collector v1.4.1 metadata accounting
```

5. Push.

On Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_repair_evidence_collector_v1_4_1.py
```
