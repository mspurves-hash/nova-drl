# v1.4.2 Changelog

- Added scanned/image-only PDF detection.
- Added local 300-DPI page rendering with pdftoppm.
- Added per-page Tesseract PSM 6 and PSM 11 OCR.
- Added readability-based OCR pass selection.
- Added page images, per-pass text, selected text, combined text, and manifests.
- Added document-semantics profiles and interpretation guardrails.
- Added `--extract-log` for controlled pilot extraction.
- Added `--pdf-dpi`, `--max-pdf-pages`, and `--no-scanned-pdf-ocr`.
- Preserved all v1.4.1 evidence accounting and system-metadata exclusions.
- No Qdrant ingestion and no final repair conclusions.
