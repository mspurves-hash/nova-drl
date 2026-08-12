# Validation — Traveler Reader v1.3.4.4.5

## Trigger

Third-event validation on log `150622005` showed that the frozen v1.3.4.4.3 crop omitted part of the Repaired/Replaced table. Experimental v1.3.4.4.4 recovered the missing left side but could clip the right side because its recovery still began from an older partial crop.

Human review established that the correct reference is the **printed table outline itself**.

## v1.3.4.4.5 requirements

- Start from the original Traveler image.
- Use the prior repairs region only as a search seed, never as the final crop boundary.
- Resolve the full printed Repairs/Replacements table from repeated printed rule geometry.
- Capture both disposition columns, the complete description, initials, and date columns.
- Preserve meaningful unmarked repair content.
- Do not infer grouping or repair facts from geometry alone.
- Keep v1.3.4.4.3 frozen.
- Keep Qdrant disabled.

## Regression plan

After `150622005` outline validation, run detect-only regression against:

- `130813004`
- `130130006`

No downstream human approvals should be changed during this geometry validation.
