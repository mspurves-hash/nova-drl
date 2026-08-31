# Nova DRL v1.5.5 - Base-PN Product Resolver + Complete Product View

## Purpose
Fix product searches that were fragmented by suffixes, OEM spelling, customer text, or other folder metadata even though DRL repairs are assigned by the base product part number.

## DRL business rule
- The base product part number is the canonical repair identity.
- Product suffixes do not split repair history or parts-frequency counts.
- Exact suffix/model strings remain preserved as searchable metadata.
- The resolver uses the volume already stored in the full-corpus index to select the dominant base product.

## Engineer presentation
- Recognized product searches aggregate all base-PN and suffix variants before presenting results.
- Product view shows the full indexed repair-event count for the resolved base PN.
- Parts list is vertical: `PART NUMBER` + `TIMES REPLACED`, highest to lowest.
- Repair History shows only the top 10 recurring repair-history patterns, highest to lowest.
- Repair-history patterns must occur in at least two distinct repair events.
- Direct RMA, serial, DRL-log, PN, customer-PO, and procurement searches continue to return event-level detail.

## Data / architecture
- No Traveler/Line Card re-ingestion.
- No vision or LLM calls.
- No NAS rescan.
- Reuses the current full-corpus v1.5.3 knowledge database.
- Original v1.5.2 corpus and source files remain unchanged.
- 80/20 rule remains the fixed default.
