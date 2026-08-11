# Windows -> GitHub -> Ubuntu

1. Extract this ZIP on Windows.
2. Merge it into the existing Nova DRL Git working directory.
3. Commit in GitHub Desktop:

`Correct production repair crop detection v1.3.4.4.1`

4. Push.

On Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_traveler_reader_v1_3_4_4_1.py
```
