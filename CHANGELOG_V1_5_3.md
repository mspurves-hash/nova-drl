# v1.5.3 Changelog

- Added Parts Replaced Fusion from human-approved repair actions.
- Added conservative DRL component lexicon.
- Added DRL terminology-aware part normalization.
- Added explicit install/replacement signal requirement.
- Added quantity extraction for x2, 2x, qty 2, and similar forms.
- Separates replaced/installed candidates from referenced or serviced components.
- Prevents `slipping Y belt` from becoming a belt replacement.
- Prevents `machined Comm's` from becoming a commutator replacement.
- Adds item-level approve/reject/hold review for part candidates.
- Preserves raw BERS wording while proposing normalized `bearings`.
- No raw OCR part sourcing.
- No root-cause inference.
- No Qdrant writes.
