# CHANGELOG v1.3.4.4.4

- Added human-defined Traveler relevance profile.
- Limited Traveler knowledge/review focus to highlighted identity anchors, Special Notes, and full Repairs/Replacements table.
- Added structural detection of the 150622005 legacy clipped-column pattern.
- Added recovery of the missing Repaired column from the original traveler using relative column widths and vertical-rule confirmation.
- Added content-first row detection: meaningful filled-in repair content no longer requires a Repaired/Replaced mark to survive review.
- Preserved Repaired/Replaced marks as attributes rather than fact-admission gates.
- Kept frozen v1.3.4.4.3 unchanged.
- No automatic facts, source mutation, final summary, or Qdrant writes.
