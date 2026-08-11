# Human Review Schema v1.5

Each field retains:

- Raw source candidates
- Source document, page, or traveler region
- Extraction method
- Evidence authority
- Similarity measurements
- Canonical candidate, when safely available
- Confidence
- Human decision status
- Future Qdrant eligibility
- `accepted_as_human_reviewed_fact`
- `qdrant.entry_created = false`

Human decisions are appended to `human_review_decisions.json`.
The latest decision controls current status; earlier decisions remain
in the audit history.
