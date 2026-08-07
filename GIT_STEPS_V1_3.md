# Windows -> GitHub -> Ubuntu

1. Extract this package on Windows.
2. Copy/merge into the existing Windows Nova DRL Git working directory.
3. Review in GitHub Desktop.
4. Commit: `Add Nova Traveler Reader v1.3`
5. Push.

On Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_traveler_reader_v1_3.py
```

Expected:

`PASS: Nova Traveler Reader v1.3 tests`

Then check dependencies:

```bash
which tesseract
which pdftotext
```

First live run should be extraction-only, without `--ollama`.
