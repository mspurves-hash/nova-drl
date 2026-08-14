# Git / Run Steps — v1.3.7.3

1. Extract this FLAT ZIP into the Windows Nova-DRL-Starter Git working copy.
2. Commit and push with GitHub Desktop.
3. On Ubuntu:

```bash
cd /opt/nova-drl && git pull
```

4. Test:

```bash
python3 tests/test_traveler_technician_signal_cleaner_v1_3_7_3.py
```

5. Run:

```bash
python3 analysis/nova_traveler_technician_signal_cleaner_v1_3_7_3.py
```

6. View main report:

```bash
cat /opt/nova-drl/output/traveler_technician_signal_v1_3_7_3/gb8_technician_signal_report_v1_3_7_3.txt
```

7. Optional reference/admin report:

```bash
cat /opt/nova-drl/output/traveler_technician_signal_v1_3_7_3/gb8_reference_patterns_v1_3_7_3.txt
```
