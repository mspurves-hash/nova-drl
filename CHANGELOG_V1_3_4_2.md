# v1.3.4.2 Changelog

## Fixed

- Repair-entry bottoms no longer stop at the first grid line when handwriting
  continues below that line.
- Scanner-sensor and Z lead-screw entries receive complete row crops.
- Correct anchor count alone no longer produces a false `Status: OK`.
- Description-column handwriting is independently validated.
- Date text touching the source right edge is explicitly flagged.

## Added

- Description-ink row profile with form rules removed
- Handwriting-aware boundary advancement
- Anchor-to-description coverage assignments
- Boundary-crossing detection
- Description coverage ratio
- Orange handwriting-extent boxes in the debug image
- Row-coverage safety interlock
- Date-edge review metadata

## Unchanged

- Read-only DRL source access
- Literal transcription preservation
- User-confirmed DRL glossary
- `accepted_as_fact: false`
- No Qdrant ingestion
