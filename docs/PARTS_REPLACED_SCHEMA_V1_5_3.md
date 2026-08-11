# Parts Replaced Review Schema v1.5.3

Each replacement candidate preserves:

- deterministic `part_candidate_id`
- review-friendly `part_number`
- canonical normalized part
- raw mention from the human-approved repair action
- quantity and raw quantity text when present
- classification and confidence
- explicit install/replacement signals
- service signals
- DRL terminology annotation, when used
- source approved repair action and its review decision
- source primary traveler evidence
- installation/location context
- human review decision
- future Qdrant eligibility
- `qdrant.entry_created = false`

Referenced or serviced components are stored separately and cannot become
replaced parts without a human-approved replacement candidate.
