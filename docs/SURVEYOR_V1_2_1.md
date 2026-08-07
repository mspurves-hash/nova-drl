# Nova DRL Surveyor v1.2.1

Hardening release based on live GB8 history testing.

Improvements include broader traveler filename tolerance, more failure-analysis/report variations, incoming/receiving and return-packaging photo classification, floppy/configuration recognition, and explicit warnings for repair events with no traveler.

Core architecture remains: **Serial Number -> Repair Event -> Evidence**.

Ubuntu test:

```bash
cd /opt/nova-drl
python3 tests/test_surveyor_v1_2_1.py
```

Live run:

```bash
python3 ingest/nova_surveyor_v1_2_1.py "/mnt/drl/000 folder for tech scans/<GB8 serial folder>"
```
