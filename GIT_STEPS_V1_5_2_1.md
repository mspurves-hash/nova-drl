# Windows -> GitHub -> Ubuntu

1. Extract this ZIP on Windows.
2. Merge into the existing Nova DRL Git working directory.
3. Commit in GitHub Desktop:

`Add frequency-weighted terminology review queue v1.5.2.1`

4. Push.

Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_terminology_review_queue_v1_5_2_1.py
python3 tests/test_drl_terminology_v1_5_2_1.py
```
