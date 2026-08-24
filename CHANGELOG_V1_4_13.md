# Nova DRL v1.4.13 - Engineer Report Cleanup

## Why this build
Matt marked up the v1.4.12 printable 1526990 report to remove redundant screen-oriented details and make the printed project report faster for engineers to scan. v1.4.13 applies those amendments while preserving the underlying unified search/index behavior.

## Printable report changes
- Moves **EQUIPMENT / PRODUCT** to the top of the report.
- Prints only the equipment/product identity there; removes indexed-event/component counts and the duplicate top-parts sentence.
- Keeps the search query but removes **Search time** and **Coverage** from the PDF.
- Keeps **TRACKING / PROJECT** focused on RMA + DRL log + equipment; removes repeated source paths from this section.
- Customer PO remains searchable in NOVA-DRL but is omitted from the standard print report.
- Removes the redundant **INDEXED PARTS** section from the PDF.
- Keeps **PART OCCURRENCES** as the event-bound replacement-parts list.
- Keeps **REPAIR HISTORY** and **SOURCE FILES**.
- Console/unified-index search output is unchanged.

## Windows installer cleanup
- Fixes the two Windows PowerShell 5.1 issues found during the first v1.4.12 workstation install.
- Avoids the dropped empty `-N` ssh-keygen argument by using the normal interactive prompt (press Enter twice for no key passphrase).
- Makes the pre-authorization SSH key test tolerant of the expected first `Permission denied` result instead of terminating the installer.
- Existing `nova_drl_ed25519` keys are reused; no new password/key setup is required on already-configured workstations.

## Preserved
- v1.4.8 unified knowledge DB and search behavior.
- Full DRL file-index coverage + current frozen 10% knowledge coverage.
- Windows `:pdf` / `:print` auto-copy/open workflow to `Z:\NOVA DRL Reports`.
- Strict RMA/procurement tracking rules.
- `.picasa.ini` / `.picasaoriginals` suppression.
- No AI/LLM call for simple search or PDF generation.
- 80/20 rule remains fixed default.
