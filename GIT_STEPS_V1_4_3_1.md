# Windows -> GitHub -> Ubuntu

1. Extract on Windows.
2. Merge into the existing Nova DRL Git working directory.
3. Commit in GitHub Desktop:
   `Correct Nova scanned document reader v1.4.3.1`
4. Push.

Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_repair_evidence_collector_v1_4_3_1.py
```
