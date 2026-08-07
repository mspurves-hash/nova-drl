# Nova DRL Surveyor v1.2

v1.2 groups each serial-number folder into Repair Events using DRL's confirmed `YYMMDD###` log convention.

Example: `230809002` = August 9, 2023, second log created that day.

New capabilities:
- chronological repair-event grouping
- Original vs Warranty traveler recognition
- receiving-photo classification
- return-packaging-photo classification
- robot checklist/test report classification
- failure-analysis and internal checklist classification
- unit-level evidence for files/folders without a log prefix
- read-only operation

First live command:

```bash
cd /opt/nova-drl
python3 ingest/nova_surveyor_v1_2.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80050477 UTI MTV ERICH"
```

Reports are written under `/opt/nova-drl/output/<serial-folder>/`.
