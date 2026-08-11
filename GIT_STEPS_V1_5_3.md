# Windows -> GitHub -> Ubuntu

1. Extract this ZIP on Windows.
2. Merge into the existing Nova DRL Git working directory.
3. Commit in GitHub Desktop:

`Add Parts Replaced Fusion v1.5.3`

4. Push.

Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_parts_replaced_fusion_v1_5_3.py
```
