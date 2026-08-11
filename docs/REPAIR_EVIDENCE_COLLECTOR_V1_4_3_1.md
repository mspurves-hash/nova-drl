# Nova DRL Repair Evidence Collector v1.4.3.1

## Corrective Release

The initial v1.4.3 ZIP was only a prototype inventory shell. Do not use it for
the live scanned-document pilot.

v1.4.3.1 restores the complete working v1.4.2 collector and adds:

- Tesseract PSM 3, 4, 6, and 11
- semantic OCR selection
- DRL checklist and acceptance-report page classification
- OCR quality gates
- provisional serial/RMA/log/complaint candidates
- strict separation of template instructions from event evidence
- no Qdrant writes
- no accepted repair facts

## Dependency

The existing file must remain in `ingest/`:

`nova_repair_evidence_collector_v1_4_2.py`

## Test

```bash
cd /opt/nova-drl
python3 tests/test_repair_evidence_collector_v1_4_3_1.py
```

## Live pilot

```bash
python3 ingest/nova_repair_evidence_collector_v1_4_3_1.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH" --extract-log=130813004
```

## Review

```bash
less "/opt/nova-drl/output/repair_evidence_collector_v1_4_3_1/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/events/130813004/repair_evidence_bundle.txt"
```

Every extracted field remains provisional and human-review-required.
