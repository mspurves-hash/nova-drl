# Traveler Oversized-Section Reader v1.3.4.4.11

This version deliberately stops trying to box or localize handwritten text.

The three human-approved Traveler areas are captured as oversized, overlapping page regions with generous margins:

- Identity/Header
- Repairs/Replacements
- Special Notes

The regions are intentionally larger than the printed sections. Overlap is allowed and preferred over clipping. Vision reads each entire oversized region and is instructed to return only the pertinent entered content for that section.

There is no row detection, handwriting bounding-box detection, repaired/replaced mark gating, internal column interpretation, or printed-grid reconstruction.

Machine transcription remains provisional. Human review is required before repair actions become approved knowledge. No Qdrant writes are permitted.
