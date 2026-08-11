# Nova DRL Repair Evidence Collector v1.4.3.2

## Purpose

v1.4.3.2 corrects the live-pilot findings from v1.4.3.1:

- A PDF document family is established once and inherited by every page.
- Known page-one form regions are cropped for event-specific values.
- MiniCPM-V reads the handwritten header values when available.
- Folder and filename values validate OCR/vision candidates.
- Template OCR quality is separated from event-annotation quality.
- Complaints from multiple documents are compared without creating a canonical fact.

No extracted value is accepted as a repair fact. No Qdrant entry is created.

## Document family inheritance

For the pilot repair:

- all 8 pages of `Robot Checklist.PDF` should inherit `DRL_INTERNAL_CHECKLIST`;
- all 3 pages of `Robot Test Report.PDF` should inherit `DRL_ACCEPTANCE_TEST_REPORT`.

Pages receive section subtypes but do not rediscover their document family independently.

## Known-form page-one profiles

### DRL internal checklist

The page-one header crop contains:

- date
- customer
- serial number
- RMA number
- log number
- repair technicians
- Customer FA summary

### DRL acceptance test report

The page-one event crop contains:

- customer
- product type
- serial number
- controller serial number
- RMA number
- traveler/log number
- repair-type checkbox
- Customer Problem/Symptom Description

MiniCPM-V is asked for JSON only. Raw responses and crops are preserved.

## Anchor validation

The collector already knows the expected serial from the serial-folder name and the expected log from the document filename. Those values are validation anchors, not document-derived facts.

Examples:

- OCR value `Number` is rejected as a field label.
- OCR/vision serial `80010732` matches the folder anchor.
- OCR/vision log `130813004` matches the filename anchor.
- A different serial or log is retained as a mismatch requiring review.

## Separate quality gates

Each page reports:

- template OCR quality;
- event annotation quality;
- handwriting annotation quality;
- eligibility for evidence comparison;
- accepted as repair fact: NO.

Readable printed instructions do not make handwritten event fields reliable.

## Cross-document complaint comparison

Raw complaint candidates from the checklist and test report are compared. The collector may report possible or strong cross-document agreement, but it does not silently create a canonical sentence.

## Test

```bash
cd /opt/nova-drl
python3 tests/test_repair_evidence_collector_v1_4_3_2.py
```

Expected:

```text
PASS: Nova Repair Evidence Collector v1.4.3.2 tests
```

## Live pilot

```bash
python3 ingest/nova_repair_evidence_collector_v1_4_3_2.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH" --extract-log=130813004
```

The run performs two MiniCPM-V header reads and may take several minutes.

To test without vision:

```bash
python3 ingest/nova_repair_evidence_collector_v1_4_3_2.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH" --extract-log=130813004 --no-header-vision
```

## Review

```bash
grep -n -A 220 "SCANNED DOCUMENT FAMILY INHERITANCE" "/opt/nova-drl/output/repair_evidence_collector_v1_4_3_2/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/events/130813004/repair_evidence_bundle.txt"
```

Known-form crop artifacts are under each document's:

```text
document_artifacts/<evidence-id>/scanned_pdf_ocr/known_form_headers/page_001/
```

## Safety

- DRL NAS remains read-only.
- No accepted repair conclusion is created.
- No Qdrant entry is created.
- Every raw candidate remains traceable to its PDF, page, crop, OCR pass, and vision response.
