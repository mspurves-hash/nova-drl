# Windows -> GitHub -> Ubuntu

1. Extract this ZIP on Windows.
2. Merge it into the existing Nova DRL Git working directory.
3. Commit in GitHub Desktop:

`Harden Testing Final Result Fusion v1.5.5.1`

4. Push.

Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_testing_final_result_fusion_v1_5_5_1.py
```
