# Nova DRL v1.5.6 - Component Core Resolver + Clean Failure/Repair View

## Purpose
Presentation/index-layer upgrade over the existing full v1.5.3 knowledge database. No Traveler re-ingestion, vision, LLM calls, NAS scan, or knowledge-index rebuild is required.

## DRL rules implemented
- **Base product PN remains the repair identity.** Suffixes remain metadata and do not split repair history.
- **Component core identity is volume-based within each product.** Recurring prefixes/suffixes and obvious OCR variants are consolidated for the technician Parts list while raw variants remain stored in the underlying full corpus/index.
- Example: `7800`, `7800A`, `HCPL7800`, `HCPL7800A`, `630-HCPL-7800A-300E`, `HPC-7800` resolve to core repair part `7800` when product-local volume supports that core.
- Capacitor-value spellings such as `33uF` and `33 µF` consolidate to `33uF` (same for other explicit uF values).
- **Times Replaced = distinct repair events** containing the resolved core part; multiple spellings in one repair count once.
- `Notes: FA - ...` is **Reported Failure / Customer Complaint** information.
- Technician **Repair History** is only work-performed text (replace/change/rebuild/repair/clean/lube/adjust/etc.).
- Database/admin/customer requirements, shipping/packaging, battery-return instructions, FA-report requirements, warranty boilerplate, and testing/outcome text are excluded from technician Repair History.
- Normal product view/PDF shows top repeated Reported Failures and top repeated Technician Repair History separately.

## 80/20 policy
- Fixed default remains in force.
- No perfect OCR target.
- No human cleanup of low-frequency variants unless they materially change technician value.
- Original source/evidence remains preserved underneath the presentation layer.
