# Windows -> GitHub -> Ubuntu

1. Extract this ZIP on Windows.
2. Merge into the existing Nova DRL Git working directory.
3. Commit in GitHub Desktop:

`Detect true traveler repair start marks v1.3.4.4.3`

4. Push.

Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_traveler_reader_v1_3_4_4_3.py
```
