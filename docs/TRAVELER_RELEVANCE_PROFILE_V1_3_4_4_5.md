# Traveler Relevance Profile v1.3.4.4.5

The relevance map is based on the human-highlighted DRL Traveler supplied during validation.

## Knowledge / review sections

1. Identity/header fields needed to anchor the event and unit.
2. Special Notes, with printed boilerplate separated from event-specific content.
3. The complete Repairs/Replacements table.

## Repairs/Replacements boundary rule

The complete printed table outline in the original Traveler defines the evidence region. The reader uses repeated printed horizontal-rule endpoints to establish the table's left/right/top/bottom boundaries.

The final evidence boundary is **not** set by:

- handwriting extent,
- OCR text extent,
- a fixed left expansion,
- a fixed right expansion,
- Repaired/Replaced marks.

After the entire printed table box is captured, internal printed vertical rules are used to resolve:

`Repaired | Replaced | Description | Initials | Date`

Meaningful filled description content is retained for human review whether or not a disposition mark is present. Repaired/Replaced marks are attributes of the row, not prerequisites for preserving it.

## Audit-only Traveler areas

Unhighlighted Traveler areas remain available in raw/source evidence but are not promoted as Traveler repair knowledge. `Hours in Final Testing` remains globally ignored as knowledge or association evidence.

## Safety

- detect-only validation
- no automatic repair facts
- no DRL source mutation
- no Qdrant writes
