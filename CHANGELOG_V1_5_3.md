# Nova DRL v1.5.3 — Full-Corpus Unified Knowledge Index

## Purpose
Promote the completed v1.5.2 full repair corpus into the fast engineer-facing NOVA-DRL search index.

## Changes
- Replaces frozen-10% repair knowledge with the full v1.5.2 corpus.
- Keeps full DRL file/path coverage from the persistent file index.
- Indexes repair events, replacement parts, RMA, Customer PO, and procurement/order references.
- Adds Customer PO as a first-class search/result type.
- Preserves strict literal tracking for RMA/PO/DGK/MSR/NWK/DSK fields.
- Builds product-specific parts knowledge from all full-corpus replacement evidence.
- Retains the v1.4.12 Windows Engineer Client, PDF generation, auto-copy/open reports, and `nova-drl` workflow.
- No AI/vision calls and no NAS rescan during the knowledge-index build.
- Atomic database replacement preserves the previous good search index if interrupted.

## Normal engineer workflow
`nova-drl` → type any full or partial model/PN/serial/RMA/log/PO/order reference.

Interpretive troubleshooting remains a separate AI reasoning layer above this retrieval index.
