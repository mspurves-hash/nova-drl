# Windows -> GitHub -> Ubuntu

1. Extract this package on Windows.
2. Copy/merge the contents into the existing Windows Nova DRL Git directory.
3. Review the changes in GitHub Desktop.
4. Commit as:

   `Add Nova Traveler Reader v1.3.4.2 row coverage fix`

5. Push to GitHub.

On Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_traveler_reader_v1_3_4_2.py
```

Then run detection-only before any MiniCPM-V extraction.
