# Nova DRL Repair Evidence Collector v1.4.2

## Purpose

v1.4.2 adds automatic page-by-page OCR for scanned/image-only supporting PDFs.

The first live GB8 dossier showed that the Robot Checklist and Robot Test Report were correctly assigned to repair event `130813004`, but both returned:

```text
no_text_layer via pdftotext
```

They are scanned PDFs. v1.4.2 now renders their pages locally and OCRs them without altering the NAS source files.

## Scanned-PDF pipeline

For a PDF with no embedded text layer:

1. Render pages locally with `pdftoppm` at 300 DPI.
2. Run Tesseract PSM 6 and PSM 11 on every page.
3. Select the more readable pass with a transparent heuristic.
4. Preserve both OCR passes.
5. Preserve each rendered page image.
6. Write selected page text and a combined OCR file.
7. Write a page-level OCR manifest.
8. Mark the document as requiring human review.

## Critical interpretation rule

A DRL checklist contains two different knowledge layers:

```text
Static printed procedure/template
Event-specific initials, checkmarks, values, and handwritten notes
```

Printed instructions such as “Check for general damage” are model/procedure knowledge. They are **not proof** that the step was completed during this repair event.

The collector records document profiles and guardrails:

- `robot_checklist` -> `template_plus_event_annotations`
- `robot_test_report` -> `test_form_plus_event_results`
- `failure_analysis_report` -> `event_specific_analysis_report`

v1.4.2 does not yet interpret handwritten completion annotations.

## Dependencies

Already expected on the Nova Ubuntu station:

```bash
which pdftoppm
which pdfinfo
which tesseract
```

These are provided by:

```bash
sudo apt install poppler-utils tesseract-ocr
```

## Test

```bash
cd /opt/nova-drl
python3 tests/test_repair_evidence_collector_v1_4_2.py
```

Expected:

```text
PASS: Nova Repair Evidence Collector v1.4.2 tests
```

## First live run: one repair event only

This inventories the entire serial folder but attempts document extraction only for log `130813004`:

```bash
python3 ingest/nova_repair_evidence_collector_v1_4_2.py "/mnt/drl/000 folder for tech scans/RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH" --extract-log=130813004
```

The output directory is:

```text
/opt/nova-drl/output/repair_evidence_collector_v1_4_2/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH
```

Inspect the event dossier:

```bash
less "/opt/nova-drl/output/repair_evidence_collector_v1_4_2/RBT_-_GB8-MT_GENMARK_SN_80010732_UTI_MICRON_ERICH/events/130813004/repair_evidence_bundle.txt"
```

Expected supporting-document results:

```text
Robot Checklist.PDF  -> ok via scanned_pdf_ocr
Robot Test Report.PDF -> ok via scanned_pdf_ocr
```

Each document will have local OCR artifacts beneath:

```text
events/130813004/document_artifacts/<evidence-id>/scanned_pdf_ocr/
```

## Options

```text
--extract-log LOG       Extract only selected log(s); repeatable
--pdf-dpi 300           Render DPI
--max-pdf-pages 50      Per-document safety limit
--no-scanned-pdf-ocr    Disable scanned PDF fallback
```

## Safety

- DRL NAS remains read-only.
- No original file is changed.
- No final repair conclusion is generated.
- No Qdrant entry is created.
