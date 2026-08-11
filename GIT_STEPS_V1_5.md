# Windows -> GitHub -> Ubuntu

1. Extract this package on Windows.
2. Merge it into the existing Windows Nova DRL Git directory.
3. Commit in GitHub Desktop:

`Add Nova Evidence Fusion and Human Review v1.5`

4. Push.

Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_evidence_fusion_v1_5.py
```
