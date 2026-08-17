# Nova DRL v1.3.8.5 — Minimal Technician Output

- Preserves v1.3.8.4 retrieval/composition behavior and all frozen upstream baselines.
- Removes serial/log recurrence counts from normal technician-facing answers, findings, checks, support lines, and fallback prose.
- Keeps internal recurring-group IDs hidden as in v1.3.8.4.
- Keeps recurrence counts in frozen JSON/provenance and in opt-in Traveler evidence only.
- Adds Python display sanitization so model-written count phrases are removed before normal rendering.
- `--show-evidence` and `:evidence on` expose counts, logs, serials, raw evidence, and source paths when needed.
- No Qdrant writes; accepted facts remain 0.
