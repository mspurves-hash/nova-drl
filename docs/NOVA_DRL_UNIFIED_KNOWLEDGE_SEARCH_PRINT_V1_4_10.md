# Nova DRL Unified Knowledge Search + Print v1.4.10

v1.4.10 is an engineer-facing presentation update over the existing v1.4.8 unified SQLite knowledge index and v1.4.9 clean report layout.

## Engineer workflow

Start:

    nova-drl

Then type whatever is known:

    NOVA-DRL> 1526990
    NOVA-DRL> BRD-1526990
    NOVA-DRL> S07211
    NOVA-DRL> 53434
    NOVA-DRL> DGK52102
    NOVA-DRL> MSR 56889
    NOVA-DRL> IXFX24N100
    NOVA-DRL> RCL1A

Ordinary search is SQLite/FTS5 only. No LLM is invoked.

## Blue report actions

After each successful search, an interactive terminal shows blue action hints:

    Actions: :pdf create/open printable PDF   :print send current report to printer

The action words are blue visual hints; engineers still type `:pdf` or `:print` at the prompt.

### PDF

    NOVA-DRL> :pdf

creates a Letter-size report under `/opt/nova-drl/reports`, starts/reuses the PDF-only report server on port 8765, and prints the PDF URL in blue. When the terminal supports OSC-8 hyperlinks, the URL itself is clickable. On simpler terminals it remains a normal copyable URL.

Direct report:

    NOVA-DRL> :pdf 1526990

### Print

    NOVA-DRL> :print

creates the same PDF and then uses `lp` (or `lpr`) to queue it to the server's default printer when CUPS printing is available.

Direct print of another search:

    NOVA-DRL> :print 1526990

Optional printer selection:

    NOVA_DRL_PRINTER="Printer_Name" nova-drl

or shell mode:

    python3 tools/nova_drl_unified_knowledge_index_v1_4_10.py --printer "Printer_Name" --print "1526990"

If no server printer is configured, `:print` leaves the generated PDF available through the clickable link so the engineer can print from the workstation browser.

## Search presentation

Results remain grouped as:

1. Tracking / Project - RMA and explicitly labeled Customer PO.
2. Procurement / Reorder - Digi-Key, Mouser, NWK/DSK and other actual order references.
3. Equipment / Product - indexed product knowledge and top recurring parts.
4. Indexed Parts - product-specific part usage.
5. Repair History - reported problem/history/test evidence.
6. Part Occurrences - individual replacement evidence.
7. Source Files - relevant indexed files.

`.picasa.ini` and `.picasaoriginals` remain hidden from normal engineer results and reports.

## AI boundary

Lookup such as `1526990` is instant index retrieval.

Interpretive troubleshooting such as `I have a GB8 drifting in the Y axis. What could be the cause?` belongs to the AI reasoning layer, which retrieves indexed DRL evidence first and then reasons over it.
