# v1.5.5.3 Changelog

- Added anchor-aware field association for fixed DRL traveler final fields.
- Locates printed field labels with Tesseract TSV; no absolute pixel coordinates.
- Sends only a relative local crop around each target label to MiniCPM-V.
- Blocks Customer Problem/Symptom/Complaint and event-header fields from TESTING_PERFORMED.
- Supporting-document final-result basis must be verified in the page template OCR.
- Traveler `final_test.png` dispositions are evaluated independently by label anchor.
- Multiple or ambiguous mutually-exclusive selections create an ambiguity record, not final-result candidates.
- `48+` / hours values cannot become the `Final O.K.` mark.
- Added separate field-verification cache and audit output.
- Preserves raw whole-page vision, local field vision, rejected candidates, and routed observations.
- No DRL source modifications.
- No final repair summary acceptance.
- No Qdrant writes.
