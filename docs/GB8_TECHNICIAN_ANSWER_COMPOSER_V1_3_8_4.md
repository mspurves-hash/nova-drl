# GB8 Technician Answer Composer v1.3.8.4

## Purpose

Make Nova DRL's technician-facing answer cleaner without changing retrieval, evidence, recurrence, or composition authority.

## Default output

Normal output intentionally hides internal recurring-group IDs and the long Traveler evidence section. Findings retain concise human-readable support labels and recurrence counts.

Example support display:

```text
support: Y Axis Drift (10 serials / 14 logs); Y Axis Motor (9 serials / 11 logs)
```

Internal `rg_...` IDs remain available in `--json` output and continue to be required for model-output validation.

## Traveler evidence

Traveler evidence is hidden by default. Use `--show-evidence` or `:evidence on` in interactive mode to expand it. Expanded evidence includes log number, serial number, raw source text, and source path when the frozen source record contains one.

This CLI deliberately does not invent web hyperlinks to NAS evidence. A future UI can render preserved source paths as clickable links without changing the underlying evidence model.

## Policy

- v1.3.8.3 and all earlier baselines remain unchanged.
- Hybrid retrieval remains v1.3.8.2.
- Composer remains `qwen25-drl:14b-q6-16k`.
- Qdrant writes: 0.
- Accepted facts: 0.
- Source evidence modification: NO.
