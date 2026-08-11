# v1.3.4.4 Changelog

- Added variable-height repair-block detection.
- Uses Repaired/Replaced-column handwriting marks as logical action starts.
- A logical repair action may span multiple printed rows.
- Initials/date are supporting evidence rather than the primary crop boundary.
- Added dynamic repair-table grid detection.
- Added physical-row mark scoring and adjacent-row spill consolidation.
- Added detect-only block crop generation.
- Added strict local MiniCPM-V JSON transcription for repair blocks.
- Separates action description from clearly separate explanatory notes.
- Preserves raw Tesseract text and raw MiniCPM-V response.
- Writes structured `repair_entries_v1_3_4_4.json` for v1.5.1.
- No automatic repair-fact acceptance.
- No Qdrant writes.
