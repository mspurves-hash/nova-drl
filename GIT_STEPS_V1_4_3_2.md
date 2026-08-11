# Windows -> GitHub -> Ubuntu

1. Extract the package on Windows.
2. Copy/merge it into the existing Nova DRL Git working directory.
3. Review in GitHub Desktop.
4. Commit:

```text
Add Nova document inheritance and header extraction v1.4.3.2
```

5. Push.

On Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_repair_evidence_collector_v1_4_3_2.py
```
