# Nova DRL Repair Evidence Collector v1.4.1

## Purpose

v1.4.1 corrects evidence accounting discovered during the first live GB8 inventory.

The folder contained two incidental Picasa metadata files:

```text
.picasa.ini
131017001 Receiving Pictures/.picasa.ini
```

The nested file inherits repair log `131017001` from its parent folder, but neither file is repair evidence.

## Accounting model

Every original source file remains accounted for, but the collector now distinguishes:

```text
Repair-event evidence
Meaningful unit-level evidence
System/photo-manager metadata
Unresolved files
```

Known system metadata includes:

- `.picasa.ini`
- `Thumbs.db`
- `ehthumbs.db`
- `desktop.ini`
- `.DS_Store`
- AppleDouble `._*` files
- common system metadata directories

System metadata is:

- included in total source-file accounting;
- retained with its exact source path and inherited log scope;
- excluded from repair completeness scores;
- excluded from text extraction;
- excluded from Qdrant and technician-facing answers;
- written to a separate `system_metadata` bundle.

## Correct live GB8 pilot counts

```text
Repair events:                    11
Original source files:            49
Event-assigned files (all):       48
Meaningful event evidence files:  47
Event system-metadata files:       1
Meaningful unit-level items:       0
System-metadata files:             2
Unresolved files:                  0
Unaccounted files:                 0
Warranty events:                   4
Events missing traveler:           1
```

The directory `131017001 Receiving Pictures` is an event evidence container, not a unit-level item. Its four JPG files inherit log `131017001`. Its `.picasa.ini` is separately accounted as event-scoped system metadata.

## Ubuntu test

```bash
cd /opt/nova-drl
git pull
python3 tests/test_repair_evidence_collector_v1_4_1.py
```

Expected:

```text
PASS: Nova Repair Evidence Collector v1.4.1 tests
```

## First live run — inventory only

Use this single-line command:

```bash
python3 ingest/nova_repair_evidence_collector_v1_4_1.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH" --inventory-only --expect-events=11 --expect-files=49 --expect-event-assigned-files=48 --expect-event-evidence-files=47 --expect-warranty-events=4 --expect-missing-travelers=1 --expect-unit-items=0 --expect-system-metadata=2
```

All expectation checks should pass, and `Unaccounted files` must remain `0`.

## Full extraction

After inventory validation:

```bash
python3 ingest/nova_repair_evidence_collector_v1_4_1.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH"
```

The DRL NAS remains read-only. No Qdrant entry is created.
