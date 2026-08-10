# Nova DRL Repair Evidence Collector v1.4

## Purpose

v1.4 stops treating the traveler as the entire repair record.

The traveler remains the primary anchor, while every other relevant file in the serial-number folder becomes part of the same Repair Evidence Bundle.

```text
Model
└── Serial Number
    ├── Repair Event (DRL log number)
    │   ├── Traveler
    │   ├── Robot Checklist
    │   ├── Robot Test Report
    │   ├── Failure Analysis Report
    │   ├── Receiving Photos
    │   ├── Return-Packaging Photos
    │   └── Existing Traveler Reader results
    └── Unit-Level Evidence
        ├── Floppy/configuration archive
        ├── Parameter files
        └── Evidence without a repair-log prefix
```

## v1.4 does not

- generate a final repair conclusion;
- call Ollama;
- modify source files;
- write to Qdrant;
- interpret ordinary repair photos.

## Supported document text extraction

- PDF through `pdftotext`
- DOCX through built-in XML extraction
- XLSX/XLSM through built-in XML extraction
- TXT, CSV, TSV, JSON, XML, MD and LOG
- RTF through basic cleanup
- Legacy DOC when `antiword` is installed

Photos, movies, parameter binaries and unsupported legacy formats remain inventoried and source-linked even when no text is extracted.

## Output

```text
output/repair_evidence_collector_v1_4/<serial-folder>/
├── serial_evidence_summary.json
├── serial_evidence_summary.txt
├── evidence_index.csv
├── events/
│   └── <log>/
│       ├── repair_evidence_bundle.json
│       ├── repair_evidence_bundle.txt
│       └── extracted_text/
├── unit_level/
│   └── unit_evidence_bundle.json
└── unresolved/
    └── unresolved_evidence.json
```

Every original source file must be in exactly one of these states:

1. Assigned to one repair event
2. Assigned to serial/unit level
3. Explicitly unresolved

Nothing is silently dropped.

## Ubuntu test

```bash
cd /opt/nova-drl
python3 tests/test_repair_evidence_collector_v1_4.py
```

Expected:

```text
PASS: Nova Repair Evidence Collector v1.4 tests
```

## First live run — inventory only

Use the rich GB8 history folder:

```bash
python3 ingest/nova_repair_evidence_collector_v1_4.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH" --inventory-only --expect-events=11 --expect-files=49 --expect-warranty-events=4 --expect-missing-travelers=1 --expect-unit-items=6
```

This verifies grouping and file accounting without extracting document contents.

## Full document extraction

After the inventory counts match:

```bash
python3 ingest/nova_repair_evidence_collector_v1_4.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH"
```

The DRL share remains read-only.
