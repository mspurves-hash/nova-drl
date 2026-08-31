# Nova DRL Structured-Event Parts Aggregation v1.5.8

The technician product Parts view is derived from explicit structured `facts.parts_replaced` evidence only.

A replacement row is still explicit parts evidence even when its `part_number` field is blank. The text may contain the useful recurring identity, for example:

- `Replaced HCPL-7800 and capacitors`
- `Shotgunned 630-HCPL-7800A-300E`
- `Changed HCPL7800A`

When recurrence supports the core `7800`, all such rows contribute their unique repair-event IDs to `7800 — TIMES REPLACED`.

This is not inference from Repair History and does not use expert overrides. It is a more complete aggregation of the structured parts-replaced corpus already produced by ingestion.
