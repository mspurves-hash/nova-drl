# Changelog — Nova DRL v1.4.4

## v1.4.4 — RCL1A 80/20 Parts Intelligence

- Added a post-processing-only RCL1A parts-intelligence layer over frozen v1.4.3 replacement mentions.
- Added separate **functional replacement family** and **likely actual PN usage** rankings.
- Preserves broad technician-useful families while keeping exact PN groups separately countable.
- Supports same-spec alternate/substitute fuse PNs sharing one functional family without erasing PN usage history.
- Added recurrence-based PN normalization with Python grounding checks to reject unsupported model inventions.
- Keeps every observed raw PN variant for provenance/human review.
- Uses v1.4.3 provisional family membership only as a candidate-block hint, not as a forced PN merge.
- Python owns repair-event and explicit-quantity counts.
- No new vision calls, NAS scans, source Line Card reads, Qdrant writes, benchmark reads, or accepted facts.
