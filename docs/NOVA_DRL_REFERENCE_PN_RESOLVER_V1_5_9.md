# NOVA DRL v1.5.9 — 80/20 Reference-PN Resolver

## Purpose
v1.5.9 keeps v1.5.8's structured-event replacement counting and improves only the technician-facing part reference label.

The DRL rule remains hard: corpus recurrence owns the answer. Expert/user comments do not create PN mappings, standard kits, or projected counts unless Matt explicitly requests promotion of a rule.

## Reference-PN compromise
For each resolved product, v1.5.9 clusters recurring PN spellings/OCR variants using only explicit `facts.parts_replaced` evidence and then chooses one **REFERENCE PN**:

1. Prefer a recurring complete alphanumeric PN when corpus support is meaningful.
2. Keep a stable numeric core when that core itself clearly dominates across the product (for example, a family commonly written simply as `7800`).
3. Preserve all observed raw PN variants underneath the reference PN for provenance/search.
4. Suppress one-off PN/OCR noise from the normal 80/20 product view.
5. Never reduce a cluster to a bare numeric fragment merely because the number is common when a recurring complete PN explains the same cluster.
6. No expert/user mapping table exists.

## Counting
`TIMES REPLACED` remains the union of distinct repair-event IDs from explicit structured `parts_replaced` evidence. No technician-history inference, kit projection, or multiplier is used.

## Presentation
Normal product view remains minimal:
- Equipment / Product
- REFERENCE PN + TIMES REPLACED
- Reported Failure + TIMES SEEN

Tracking, procurement, individual repair history, and source files remain searchable by direct identifiers but are omitted from normal product overview/PDF.

## Deployment
No corpus re-ingestion or knowledge DB rebuild is required. Update the server code and production launcher. Existing v1.5.8+ Windows clients use `/usr/local/bin/nova-drl`, so they do not need reinstalling for this server-side presentation change.
