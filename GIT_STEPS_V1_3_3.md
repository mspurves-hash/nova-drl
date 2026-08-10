# Windows -> GitHub -> Ubuntu

1. Extract this package on Windows.
2. Copy/merge into the existing Windows Nova DRL Git working directory.
3. Review the changes in GitHub Desktop.
4. Commit as: `Add Nova Traveler Reader v1.3.3 region vision`
5. Push.

On Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_traveler_reader_v1_3_3.py
```

Then run the first single-log test shown in the documentation.
