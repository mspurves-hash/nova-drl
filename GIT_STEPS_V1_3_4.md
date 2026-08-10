# Windows -> GitHub -> Ubuntu

1. Extract on Windows.
2. Copy/merge into the existing Windows Nova DRL Git folder.
3. Commit in GitHub Desktop: `Add Nova Traveler Reader v1.3.4 anchored entries`
4. Push.

Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_traveler_reader_v1_3_4.py
```

Run detection-only first before the vision pass.
