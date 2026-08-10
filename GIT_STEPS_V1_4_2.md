# Windows -> GitHub -> Ubuntu

1. Extract this package on Windows.
2. Copy/merge into the existing Windows Nova DRL Git working directory.
3. Commit in GitHub Desktop:

```text
Add Nova Repair Evidence Collector v1.4.2 scanned PDF OCR
```

4. Push.

On Ubuntu:

```bash
cd /opt/nova-drl
git pull
python3 tests/test_repair_evidence_collector_v1_4_2.py
```

Then run only log `130813004` first:

```bash
python3 ingest/nova_repair_evidence_collector_v1_4_2.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH" --extract-log=130813004
```
