# Nova DRL Unified Knowledge Search + Print v1.4.9

v1.4.9 is the engineer-facing presentation and printing layer over the proven v1.4.8 unified SQLite knowledge index.

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

## Cleaner presentation

Results are grouped into:

1. Tracking / Project - RMA and explicitly labeled Customer PO.
2. Procurement / Reorder - Digi-Key, Mouser, NWK/DSK and other actual order references.
3. Equipment / Product - indexed product knowledge and top recurring parts.
4. Indexed Parts - product-specific part usage.
5. Repair History - reported problem/history/test evidence.
6. Part Occurrences - individual replacement evidence.
7. Source Files - relevant indexed files.

`.picasa.ini` and `.picasaoriginals` are legacy Picasa metadata/backup content and are hidden from normal engineer results and reports.

## Printable PDF

After any search:

    NOVA-DRL> :pdf

v1.4.9 creates a Letter-size PDF under:

    /opt/nova-drl/reports

and automatically attempts to start a local PDF-only report server on port 8765. It prints a browser URL using the Nova server hostname/IP.

A report can also be made directly:

    NOVA-DRL> :pdf 1526990

or from the shell:

    python3 tools/nova_drl_unified_knowledge_index_v1_4_9.py --pdf "1526990"

The report generation path is deterministic and makes no AI/LLM call.

## Identifier searches

Identifier-like searches use strict linking. A direct RMA/order/PN/log match may pull the repair event and other records explicitly linked by repair-event ID or DRL log, but unrelated fuzzy results are not used to fill sections.

## AI boundary

Lookup question:

    NOVA-DRL> 1526990

is index retrieval.

Interpretive question such as:

    I have a GB8 drifting in the Y axis. What could be the cause?

belongs to the AI reasoning layer, which should retrieve relevant indexed DRL evidence first and then reason over it.
