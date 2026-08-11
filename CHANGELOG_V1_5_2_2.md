# v1.5.2.2 Changelog

- Added common-English-word suppression.
- Added short acronym/shorthand shape filtering.
- Added confirmed FA -> Failure Analysis.
- Added confirmed RPT -> Report.
- Added confirmed FA RPT -> Failure Analysis Report.
- Added confirmed metadata site code MTV -> Micron Technology Virginia.
- Suppresses metadata identifiers from terminology review.
- Reads existing site_codes / technicians / oems project configs when present.
- Added suppression audit file and counts.
- Added single-serial OCR/template repetition detection.
- Added template repetition priority penalty.
- Increased serial-diversity weighting.
- Preserved unique repair-event frequency as the primary ranking signal.
- Preserved Define / Defer / Ignore and effective glossary workflow.
- No DRL source modifications.
- No Qdrant writes.
