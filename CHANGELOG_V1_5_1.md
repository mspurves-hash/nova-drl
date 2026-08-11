# v1.5.1 Changelog

- Added item-level Repair Actions fusion.
- Reads structured Traveler Reader v1.3.4.x repair-row JSON.
- Refuses to create repair-action facts from raw whole-region OCR alone.
- Preserves repair descriptions exactly except whitespace/terminal punctuation.
- Carries provisional technician initials and dates without accepting them as facts.
- Can corroborate a traveler action against event-specific Internal Checklist Notes.
- Imports prior v1.5 human-review decisions so approved complaint fields persist.
- Added per-action approve/reject/hold decisions.
- Added approved repair-action export with source traceability.
- Parts, root cause, testing, and final result remain separate/unestablished.
- No Qdrant writes.
