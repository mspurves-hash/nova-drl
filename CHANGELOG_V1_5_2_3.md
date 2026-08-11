# v1.5.2.3 Changelog

- Added authority-aware terminology discovery.
- Human-approved repair actions shadow structured machine repair descriptions
  and raw Repairs/Replacements OCR for the same repair event.
- Preserves diagnostic notes and Special Notes as independent evidence.
- Added technician metadata:
  EF Erich Franke
  VT Victor Thomas
  SF Stefen Franke
  MP Matt Purves
  NP Nate Purves
  RB Roger Bingham
  AM Anthony Moulazimis
  MB Mary Ann Bingham
  BP Barbara Purves
- Preserved MTV -> Micron Technology Virginia as site metadata.
- Expanded common-word suppression.
- Added stronger repetition requirements for 2- and 3-character OCR-only
  fragments.
- Added authority-shadow and low-support suppression audit files.
- Preserved frequency weighting, serial-diversity weighting, template penalty,
  Define / Defer / Ignore and effective glossary behavior.
- No source modifications.
- No Qdrant writes.
