# Repair Actions Human Review Schema v1.5.1

`repair_actions` is an item-level field.

Each candidate item preserves:

- deterministic `action_id`
- review-friendly `action_number`
- literal traveler repair description
- structured Traveler Reader version and artifact path
- primary traveler source path and repair-row number
- repair-row crop paths when available
- Tesseract full-row evidence when available
- raw MiniCPM-V response when available
- provisional initials/date and their validation states
- DRL glossary matches
- review reasons
- independent supporting source count
- optional matching Internal Checklist Notes
- confidence
- human review state
- future Qdrant eligibility
- `qdrant.entry_created = false`

Approvals are appended to `human_review_decisions.json`.
A prior v1.5 customer-complaint approval may be imported but is not rewritten.
