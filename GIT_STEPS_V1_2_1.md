# Windows -> GitHub -> Ubuntu

1. Extract on Windows.
2. Copy/merge into the existing Windows Nova DRL Git working directory.
3. Commit in GitHub Desktop: `Add Nova Surveyor v1.2.1 hardening`
4. Push.

Then on Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_surveyor_v1_2_1.py
```
