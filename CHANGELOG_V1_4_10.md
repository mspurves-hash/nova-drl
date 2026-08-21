# Nova DRL v1.4.10 - Blue Actions + Clickable PDF Links

## Added
- Blue `:pdf` and `:print` action hints in interactive terminals.
- OSC-8 terminal hyperlink for generated PDF URLs; the visible URL is blue and clickable when the engineer terminal supports hyperlinks.
- Plain URL fallback when color/hyperlink output is unavailable (`NO_COLOR`, non-TTY, or basic terminal).
- `:print` command to create the current report and send it to the server's configured/default CUPS printer through `lp`/`lpr` when available.
- `:pdf <search>` and `:print <search>` remain available for direct report actions.
- Optional `--printer NAME` or `NOVA_DRL_PRINTER=NAME` selection.

## Preserved from v1.4.9
- Clean engineer-facing grouped search output.
- Customer PO separated from distributor procurement/order references.
- Strict identifier linkage: RMA, DRL log, PN, serial/model and DGK/MSR/NWK/DSK searches do not receive unrelated fuzzy filler records.
- `.picasa.ini` and `.picasaoriginals` suppressed from normal engineer search output and reports.
- Printable Letter PDF reports and local PDF-only HTTP report server.

## Preserved foundation
- v1.4.8 SQLite/FTS5 storage engine and instant local search.
- Existing `/opt/nova-drl/index/drl_knowledge_index.sqlite`; no knowledge rebuild required for presentation-only upgrade.
- Full DRL file/path coverage plus current frozen 10% repair-knowledge coverage.
- Fixed DRL Nova 80/20 rule.
- No LLM call for lookup, PDF generation, or direct printing.
