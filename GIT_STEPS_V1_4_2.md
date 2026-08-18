# Git / Ubuntu Steps — Nova DRL File Index v1.4.2

## Windows

1. Extract the FLAT ZIP into the Nova-DRL-Starter Git working copy.
2. Commit in GitHub Desktop.
3. Push origin.

The generated SQLite database is runtime state and must **not** be committed to Git.

Recommended `.gitignore` entry if not already covered:

```text
/index/*.sqlite*
```

## Ubuntu

```bash
cd /opt/nova-drl && git pull
```

Test:

```bash
python3 tests/test_nova_drl_file_index_v1_4_2.py
```

Expected:

```text
PASS: Nova DRL File Index v1.4.2 tests
```

Status before first build:

```bash
python3 tools/nova_drl_file_index_v1_4_2.py status
```

Initial DRL share index build:

```bash
python3 tools/nova_drl_file_index_v1_4_2.py build
```

After it finishes, validate against the manual Everything-style query:

```bash
python3 tools/nova_drl_file_index_v1_4_2.py search "RCL1A LINE" --count-only
```

Then show the matching paths:

```bash
python3 tools/nova_drl_file_index_v1_4_2.py search "RCL1A LINE" --all
```

Routine refresh:

```bash
python3 tools/nova_drl_file_index_v1_4_2.py refresh
```

## Optional periodic refresh

The package includes example systemd service/timer files under `systemd/`, configured for a 15-minute interval. Do **not** enable the timer until the initial build and at least one manual refresh have been timed on the real DRL share. We can choose the refresh interval from those measurements.
