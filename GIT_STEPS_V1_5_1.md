# Windows -> GitHub -> Ubuntu

1. Extract this ZIP on Windows.
2. Merge it into the existing Nova DRL Git working directory.
3. Commit in GitHub Desktop:

`Add Nova Repair Actions Fusion v1.5.1`

4. Push.

On Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_evidence_fusion_v1_5_1.py
```
