# Repair Evidence Bundle Schema v1.4.2

Each original evidence record now includes:

```json
{
  "document_semantics": {
    "profile": "template_plus_event_annotations",
    "static_template_content_present": true,
    "event_annotations_require_review": true,
    "guardrails": []
  },
  "extraction": {
    "status": "ok",
    "method": "scanned_pdf_ocr",
    "text_path": ".../extracted_text/<evidence-id>.txt",
    "page_count": 8,
    "pages_processed": 8,
    "artifact_dir": ".../document_artifacts/<id>/scanned_pdf_ocr",
    "manifest_path": ".../scanned_pdf_ocr_manifest.json",
    "ocr_review_required": true,
    "page_records": []
  }
}
```

Scanned-PDF OCR is raw evidence only:

```text
accepted_as_repair_fact: false
interpretation_status: raw_ocr_only
```

Printed template instructions must remain distinct from event-specific annotations.
