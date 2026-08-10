# v1.3.3 Changelog

- Split repairs/replacements into four overlapping repair-row crops.
- Split Special Notes into three overlapping content groups.
- Added region-specific prompts.
- Added per-subcrop Tesseract OCR for comparison.
- Added prompt-noncompliance detection.
- Added `eligible_for_fusion_review` status.
- Preserved source paths, parent crop, coordinates, prompts, model metadata,
  Tesseract passes, vision response, and every generated crop.
- Requires `--log=#########` by default to prevent accidental batch runs.
