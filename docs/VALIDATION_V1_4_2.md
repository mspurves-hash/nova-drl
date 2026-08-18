# Validation — Nova DRL File Index v1.4.2

Synthetic tests validate:

1. SQLite index creation without external dependencies.
2. `RCL1A LINE` matching across parent-folder + filename boundaries.
3. Case-insensitive AND token semantics.
4. DRL `YYMMDD###` log detection.
5. Multiple files with the same DRL log remain individually indexed.
6. Refresh detects added, changed and deleted files.
7. Index is bound to one share root to avoid mixing unrelated trees.
8. Source file contents are never modified by the test/index logic.

Run:

```bash
python3 tests/test_nova_drl_file_index_v1_4_2.py
```

Expected:

```text
PASS: Nova DRL File Index v1.4.2 tests
```
