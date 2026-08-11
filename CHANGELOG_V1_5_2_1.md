# v1.5.2.1 Changelog

- Added frequency-weighted DRL Terminology Review Queue.
- Ranks primarily by UNIQUE REPAIR EVENTS rather than raw hit count.
- Adds unique serial/model/OEM and year-span weighting.
- Adds technical field importance weighting.
- Penalizes OCR-only terms.
- Prevents duplicate-event inflation.
- Adds scope suggestions from OEM/model concentration.
- Adds `ask_now` only for HIGH-priority consequential unknown terms.
- Adds Define / Defer / Ignore audit decisions.
- Adds derived effective glossary generation.
- Adds retroactive annotation path via effective glossary.
- Added human-confirmed FE meaning scoped to Genmark robots.
- Preserves BERS, Comm's, KEAL and all historical raw wording.
- No DRL source modifications.
- No Qdrant writes.
