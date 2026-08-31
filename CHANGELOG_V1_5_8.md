# Nova DRL v1.5.8 — Structured-Event Parts Aggregation

## Purpose
Correct product-level TIMES REPLACED counts by using all explicit structured `parts_replaced` evidence, even when `part_number` is blank but the replacement text contains the recurring core PN.

## Hard 80/20 behavior
- Corpus recurrence owns all counts.
- No expert/user repair-kit projection.
- Counts are distinct repair events.
- One-off part strings remain preserved/searchable but are omitted from the normal product report.

## Key correction
v1.5.7 could undercount a recurring part when the event contained explicit replacement text such as `Replaced HCPL-7800...` but no populated manufacturer-PN field. v1.5.8 discovers component cores from both the PN field and the explicit `parts_replaced` text.

## Product view
- Base PN owns suffix variants.
- PARTS REPLACED: recurring core PN + TIMES REPLACED, right-aligned.
- REPORTED FAILURE: top recurring failure patterns + TIMES SEEN.
- Tracking/Project and Repair History remain omitted from normal product overview.

## Windows client
Windows Engineer Client now calls the stable `/usr/local/bin/nova-drl` server endpoint instead of a versioned Python filename. Future server presentation updates should therefore not require a Windows client reinstall unless the Windows-side behavior itself changes.

## No re-ingestion
No vision, LLM, Qdrant, or NAS rescan is required. Existing full v1.5.2 corpus and v1.5.3 knowledge DB are reused.
