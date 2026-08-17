# v1.4.0 Blind Validation Procedure

1. Keep the old hosted power-supply parts reports closed during the run.
2. Run `--status` and `--plan-only` against the original source PDF.
3. Run the full v1.4.0 pipeline and allow it to complete/resume until the manifest and frequency CSV are frozen.
4. Record the source PDF SHA256 from the v1.4.0 manifest.
5. Save/commit the v1.4.0 code version used for the run.
6. Only then compare:
   - source page count,
   - duplicate-page count/grouping,
   - unique representative count,
   - top replacement-part order,
   - repairs containing each part,
   - total recorded pieces,
   - quantity-unstated cases.
7. Differences are investigation targets, not automatic errors. Check the original page evidence before amending either result.

The benchmark reports are intentionally not parsed or bundled into the runtime.
