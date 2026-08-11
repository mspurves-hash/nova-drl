# Windows -> GitHub -> Ubuntu

1. Extract this ZIP on Windows.
2. Merge into the existing Nova DRL Git working directory.
3. Commit in GitHub Desktop:

`Reconstruct sparse traveler grid v1.3.4.4.2`

4. Push.

Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_traveler_reader_v1_3_4_4_2.py
```
