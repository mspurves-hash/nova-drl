# Nova DRL Traveler Relevance Profile v1.3.4.4.8

## Simplified Repairs/Replacements rule

The complete printed **outer Repairs/Replacements box** is the evidence region.
Nova does not need to interpret the internal `Repaired`, `Replaced`, `Description`,
`Initials`, or `Date` columns.

Once the whole box is captured, Nova transcribes the technician handwriting
literally. Internal X/check marks and table columns do not determine whether
handwriting is preserved.

## Evidence flow

1. Use the frozen v1.3.1 Repairs/Replacements crop only as a search seed.
2. Find the complete printed outer box in the original Traveler.
3. Save the entire box without clipping left/right handwritten content.
4. In non-detect-only mode, send the whole box to MiniCPM-V.
5. Ask for literal handwriting only; ignore printed form text and grid lines.
6. Preserve the raw model response and parsed line list separately.
7. Human review remains required before any repair action becomes knowledge.

## Non-negotiables

- Frozen v1.3.4.4.3 is unchanged.
- Handwriting extent never defines the crop boundary.
- No internal column semantics are required.
- No start mark is required.
- Machine transcription is not a repair fact.
- No DRL source mutation.
- No Qdrant writes.
