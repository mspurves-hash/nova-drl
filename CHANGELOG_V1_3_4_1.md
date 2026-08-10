# v1.3.4.1 Changelog

Patch release for the first live v1.3.4 anchor-detection test.

## Fixed

- Lowered the repair-anchor search floor so the first A1/A2 repair is detected.
- Detects the table-body top from the form's horizontal rule.
- Builds repair bands from actual horizontal form lines rather than equal or
  midpoint-derived bands.
- Detects the description/initials/date column rules dynamically.
- Reconstructs an expanded repairs crop from the original traveler so the
  handwritten date column is not clipped.
- Extends date and full-row crops to the complete right edge.
- Enforces `--expected-entries` as a processing safety interlock.
- Stops vision processing on anchor-count mismatch.
- Adds expected/detected/status information to the debug image and reports.

## Unchanged

- Source DRL archive remains read-only.
- MiniCPM-V output remains working evidence only.
- `accepted_as_fact` remains false.
- No Qdrant ingestion.
