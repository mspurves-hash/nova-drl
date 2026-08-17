# Git / Ubuntu Steps — v1.4.1

## Windows

1. Extract the FLAT ZIP into the Nova-DRL-Starter Git working copy.
2. Commit in GitHub Desktop.
3. Push origin.

Do not add the large benchmark PDF to Git.

## Ubuntu

```bash
cd /opt/nova-drl && git pull
```

Test:

```bash
python3 tests/test_power_supply_focused_evidence_recovery_v1_4_1.py
```

Expected:

```text
PASS: Nova DRL Power Supply Focused Evidence Recovery v1.4.1 tests
```

### Current benchmark PDF status

```bash
python3 analysis/nova_power_supply_focused_evidence_recovery_v1_4_1.py --source-pdf "/mnt/drl/input/RCL1A-1D-W3 All Line Cards.pdf" --status
```

Plan only:

```bash
python3 analysis/nova_power_supply_focused_evidence_recovery_v1_4_1.py --source-pdf "/mnt/drl/input/RCL1A-1D-W3 All Line Cards.pdf" --plan-only
```

Full benchmark run:

```bash
python3 analysis/nova_power_supply_focused_evidence_recovery_v1_4_1.py --source-pdf "/mnt/drl/input/RCL1A-1D-W3 All Line Cards.pdf"
```

### Future production image mode

Point `--source-images-root` at the power-supply repair-folder tree containing individual Line Card images:

```bash
python3 analysis/nova_power_supply_focused_evidence_recovery_v1_4_1.py --source-images-root "/mnt/drl/<power-supply-repair-root>" --status
```

The default filename filter is `line\s*card`; override it with `--image-name-regex` if a family uses another naming convention.
