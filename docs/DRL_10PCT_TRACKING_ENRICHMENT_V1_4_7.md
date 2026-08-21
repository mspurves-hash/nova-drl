# Nova DRL v1.4.7 — 10% Tracking + Procurement Enrichment

v1.4.7 enriches the frozen v1.4.6 10% benchmark corpus. It does **not** resample the 932 folders, does not rerun the original repair-history/parts extraction, and does not modify v1.4.6.

## Added fields

- RMA number(s), linked to the repair event and source.
- Procurement/distributor order reference(s), supplier when visible/known, description, explicit quantity, and manufacturer PN only when the same source explicitly provides it.
- Enriched replacement mentions that reclassify known procurement refs out of the manufacturer-PN field.

## DRL-specific procurement rule

Historical order-style codes such as `DGK52102`, `MSR...`, `NWK56548`, and `DSK520117` are procurement/order references, not manufacturer PNs. `DGK` defaults to Digi-Key and `MSR` to Mouser when no supplier label is visible. NWK/DSK remain supplier-unknown unless the source identifies the supplier.

## Source behavior

The script reuses `repair_event_plan_v1_4_6.json` and reads the exact frozen Line Cards/Travelers from that plan. It includes both primary and supporting cards because tracking/order metadata can live on either. Each source is read only for RMA/procurement metadata. No 14B event reconstruction is repeated.

## Lookup

After enrichment:

```bash
python3 analysis/nova_drl_10pct_tracking_enrichment_v1_4_7.py --lookup-rma 53434
python3 analysis/nova_drl_10pct_tracking_enrichment_v1_4_7.py --lookup-order DGK52102
```

The local lookup database is:

`/opt/nova-drl/output/drl_10pct_tracking_enrichment_v1_4_7/tracking_lookup_v1_4_7.sqlite`
