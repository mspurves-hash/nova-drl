# Nova DRL Base-PN Product View v1.5.5

For DRL repair purposes, suffixes are metadata, not separate repair products.

Example:

- `MR-J2S-40A`
- `MR-J2S-40A-S12`
- OEM spelling/customer/folder variants containing the same base product

are aggregated under the base repair identity `MR-J2S-40A` when the full-corpus volume supports that base.

The normal engineer product view is intentionally concise:

1. Base product and total indexed repair events.
2. Complete indexed replacement-PN ranking, highest repair frequency first.
3. Top 10 recurring repair-history patterns, highest frequency first.
4. Useful tracking/procurement/source-file context.

Individual event detail is still available through direct RMA, serial, DRL-log, PN, Customer PO, or order-reference searches.
