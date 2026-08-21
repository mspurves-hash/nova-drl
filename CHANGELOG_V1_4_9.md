# Nova DRL v1.4.9 - Engineer Search Presentation + Printable PDF

## Added
- Cleaner engineer-facing grouped search output.
- Customer PO is displayed separately from distributor procurement/order references when the source explicitly says `Cust PO` / `Customer PO`.
- Strict identifier linkage: RMA, DRL log, PN, serial/model and DGK/MSR/NWK/DSK searches no longer receive unrelated fuzzy filler records.
- `.picasa.ini` and `.picasaoriginals` are suppressed from normal engineer search output and printed reports.
- `:pdf` creates a print-ready PDF of the current search.
- Generated PDFs are served by a lightweight local report server and a browser URL is printed for the engineer.
- One-shot `--pdf "query"` report generation.

## Preserved
- v1.4.8 SQLite/FTS5 storage engine and instant local search.
- Full DRL file/path coverage plus current frozen 10% repair-knowledge coverage.
- Fixed DRL Nova 80/20 rule.
- No LLM call for ordinary lookup or report generation.
- Strict literal grounding for RMA and procurement identifiers.
