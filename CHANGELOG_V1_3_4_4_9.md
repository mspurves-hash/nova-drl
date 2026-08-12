# Nova DRL Traveler Reader v1.3.4.4.9

- Removed experimental printed-grid / repeated-line outer-box detection.
- Uses the human-highlighted Traveler supplied 2026-08-12 as the canonical relevance map.
- Captures Identity/Header, Repairs/Replacements, and Special Notes by normalized page position.
- Repairs/Replacements internal columns, X marks, initials, dates, and row-start marks are not semantic gates.
- Detect-only saves the relevance boxes and stops.
- No automatic facts, source modification, or Qdrant writes.
- Frozen v1.3.4.4.3 remains unchanged.
