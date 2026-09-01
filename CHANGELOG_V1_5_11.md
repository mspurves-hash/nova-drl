# Nova DRL v1.5.11 — Global Evidence Rollup

## Why this release exists
v1.5.10 correctly resolved product suffix variants into one repair family, but testing PRE-200 exposed a downstream evidence gap: the normal Parts view still favored PN-like references, so recurring mechanical/robot components written without a manufacturer PN were suppressed. Raw Repair History was also intentionally hidden, which meant useful recurring work-performed patterns were not visible in the normal product report.

## Global fix
- Keeps the v1.5.10 global base-product resolver and full resolved event family.
- Parts Replaced now accepts **recurring explicit component/assembly names** when no PN/value reference exists.
- Explicit replacement/change/install/swap facts stored in structured technician Repair History can recover a missed Parts occurrence without rereading the source image.
- Non-replacement work such as cleaning/alignment/adjustment is not allowed to inflate Parts counts.
- Adds **RECURRING REPAIR ACTIONS**: only action + component/reference patterns seen in 2+ distinct repair events.
- Raw individual Repair History remains omitted from the normal product view.
- `REFERENCE PN` becomes `REFERENCE PN / COMPONENT` because both are legitimate DRL technician references.
- All counts remain distinct repair-event counts.
- No product-specific PN/component/model mappings were added.

## Hard invariant
A proven generic defect must be fixed globally. v1.5.11 regression tests reject product-specific recovery logic and require component-name recovery and recurring repair-action rollup to remain generic.

## Deployment
Query-time presentation layer only. No corpus re-ingestion, knowledge-index rebuild, Qdrant operation, LLM/vision run, or Windows-client reinstall is required.
