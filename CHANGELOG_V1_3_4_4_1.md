# v1.3.4.4.1 Changelog

- Corrected variable-height grid detection for high-resolution v1.3.1 crops.
- Removed fixed 90-pixel repair-row height ceiling.
- Added resolution-adaptive horizontal-grid spacing.
- Added clipped-left table detection.
- Correctly handles crops beginning inside the Repaired column.
- Repair-mark mask now uses semantic table-left/description boundaries.
- Preserves stable `repair_entries_v1_3_4_4.json` downstream artifact name.
- No DRL source modifications.
- No Qdrant writes.
