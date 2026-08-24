# Nova DRL v1.4.12 - Windows Engineer Client + Auto-Open Reports

## Why this build
v1.4.11 proved that printable reports could be generated and reached from Windows, but classic Windows console hosts made hyperlink/copy behavior awkward. v1.4.12 removes that friction from the engineer workflow.

## Added
- Windows-native `NOVA DRL` Engineer Client in PowerShell.
- One-time dedicated SSH-key setup; no password stored.
- Engineer searches are sent over SSH to the existing local unified knowledge index.
- Base64-safe query transport so spaces, punctuation, PNs, RMAs, serials, etc. do not require shell quoting.
- `:pdf`: creates the report on Nova, copies it with SCP to `Z:\NOVA DRL Reports` (default), and opens it automatically with the Windows default PDF application.
- Automatic fallback to `Documents\NOVA DRL Reports` when the mapped DRL drive is unavailable.
- `:print`: attempts the Windows PDF handler's Print verb; falls back to opening the PDF for normal Ctrl+P printing.
- `:open`: reopens the most recently copied report.
- Server `--pdf-file-b64` mode creates a PDF only and emits a machine-readable report path; no HTTP server or CUPS required for the Windows workflow.
- Server `--search-b64` and `--no-actions` modes for the Windows client.

## Preserved
- v1.4.8 unified knowledge database schema and search behavior.
- v1.4.11 clean grouped presentation and strict tracking rules.
- Full DRL file-index coverage plus current frozen 10% repair-knowledge coverage.
- No AI/LLM calls for ordinary search or PDF generation.
- `.picasa.ini` and `.picasaoriginals` hidden from normal engineer results/reports.
- 80/20 rule remains fixed default.
