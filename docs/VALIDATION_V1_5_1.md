# Validation v1.5.1

Validated locally:
- Python compilation PASS.
- Unit tests PASS.
- 100% deterministic membership selects every folder.
- `MSR 56889` is classified as a Mouser procurement reference and excluded from manufacturer PN parts.
- Unsupported `DGK52102` with evidence `DigiKey 55516` is not accepted; visible `55516` is recovered instead.
- RMA and Customer PO remain separate literal tracking fields.
- Reused v1.4.7 customer PO rows are recovered out of old procurement metadata.

Live-server status/plan required before full ingestion.
