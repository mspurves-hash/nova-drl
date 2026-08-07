# Windows -> GitHub -> Ubuntu

1. Extract on Windows.
2. Copy/merge into the existing Windows Nova DRL Git working directory.
3. Commit in GitHub Desktop: `Add Nova Surveyor v1.2 repair event grouping`
4. Push.

On Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_surveyor_v1_2.py
```

Expected: `PASS: Nova Surveyor v1.2 tests`
