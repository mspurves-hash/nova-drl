# Windows -> GitHub -> Ubuntu

1. Extract this package on Windows.
2. Copy/merge its contents into the existing Windows Nova DRL Git directory.
3. Review the new files in GitHub Desktop.
4. Commit:

```text
Add Nova Traveler Reader v1.3.4.1 anchor fix
```

5. Push to GitHub.

On Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_traveler_reader_v1_3_4_1.py
```

Expected:

```text
PASS: Nova Traveler Reader v1.3.4.1 tests
```

Then run detection-only using the command in
`docs/TRAVELER_READER_V1_3_4_1.md`.
