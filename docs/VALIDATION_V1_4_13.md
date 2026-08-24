# Validation - v1.4.13

Validated in the build environment:

- Python compilation passes.
- Unified-index regression suite passes.
- v1.4.13 print-report assertions verify that Search time, Coverage, Customer PO, Indexed Parts, and product summary-count text are absent from the PDF.
- Equipment/Product, RMA Tracking, Repair History, Part Occurrences, and Source Files remain present.
- Generated PDF rendered to PNG using the PDF validation workflow; no clipping or overlap observed.
- Strict identifier/search behavior from v1.4.12 remains unchanged.
- Windows client continues to use SCP to the Windows-accessible report share and auto-opens reports.
- Windows installer statically validates PowerShell 5.1-safe SSH-key creation/test logic.

The already-working DRL Windows workstation remains the final Windows execution validation target after deployment.
