# Traveler Relevance Profile v1.3.4.4.6

The relevance map is based on the human-highlighted DRL Traveler supplied during validation.

## Knowledge / review sections

1. Identity/header fields needed to anchor the event and unit.
2. Special Notes, with printed boilerplate separated from event-specific content.
3. The complete Repairs/Replacements table.

## Repairs/Replacements boundary rule

The complete printed table grid in the original Traveler defines the evidence region. v1.3.4.4.6 resolves the six semantic vertical rules first:

`Repaired | Replaced | Description | Initials | Date`

Those rules establish the full left/right extent. The reader then measures combined horizontal-rule coverage across that span and selects the repeated row-rule run that overlaps the existing Repairs/Replacements seed. Horizontal strokes may be segmented by vertical rules or have slightly different contour endpoints; identical endpoints are not required.

The final evidence boundary is **not** set by:

- handwriting extent,
- OCR text extent,
- a fixed left expansion,
- a fixed right expansion,
- Repaired/Replaced marks.

After the complete printed table box is captured, meaningful filled description content is retained for human review whether or not a disposition mark is present. Repaired/Replaced marks are attributes of the row, not prerequisites for preserving it.

## Why v1.3.4.4.6 exists

v1.3.4.4.5 safely failed on log 150622005 because its repeated-horizontal-contour-family rule required consistent horizontal endpoints. The actual Traveler has a valid printed table, but the lines are effectively segmented by the form grid. v1.3.4.4.6 uses the full printed grid network instead of repeated contour endpoints.

## Audit-only Traveler areas

Unhighlighted Traveler areas remain available in raw/source evidence but are not promoted as Traveler repair knowledge. `Hours in Final Testing` remains globally ignored as knowledge or association evidence.

## Safety

- detect-only validation
- no automatic repair facts
- no DRL source mutation
- no Qdrant writes
