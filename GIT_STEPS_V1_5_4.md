# Windows -> GitHub -> Ubuntu

1. Extract this ZIP on Windows.
2. Merge into the existing Nova DRL Git working directory.
3. Commit in GitHub Desktop:

`Add Diagnostic Hypothesis Root Cause Fusion v1.5.4`

4. Push.

Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_diagnostic_root_cause_fusion_v1_5_4.py
```
