# Authority-Aware Terminology Queue Schema v1.5.2.3

## Authority shadowing

Event-level human-approved `repair_actions` shadow these lower-authority fields
for terminology discovery:

- `structured_repair_action`
- `repairs_region_ocr`

They do not shadow:

- `diagnostic_note`
- `special_notes_ocr`
- approved complaint fields

Shadowed rows are recorded in `terminology_shadowed_evidence.json`.

## Metadata suppression

Human-confirmed technician initials and site codes are metadata, not
terminology. Suppression records preserve the metadata type/name.

## Low-support OCR gate

Unresolved OCR-only terms require:

- 2 characters: at least 3 unique repair events
- 3 characters: at least 2 unique repair events

Structured or human-approved occurrences are not removed by this gate.

## Safety

All decisions affect derived terminology discovery only.
