# Nova DRL OCR Deviant Resolver / Canonical Parts Gate v1.6.1

This is the first implementation of the revised 80/20 parts plan.

## What changes

The first human screen is treated as a labeled training sample, not as a permanent requirement to screen every part.

- Human-checked rows = trusted canonical vocabulary.
- Human-unchecked rows = not valid standalone canonical labels.
- Very strong OCR/format matches may be absorbed as aliases of a human-approved canonical.
- Recurring plausible unknown explicit part numbers go to `web_validation_queue_v1_6_1.jsonl`.
- Low-frequency unresolved candidates stay preserved but unpromoted.
- Clean recurring generic descriptions may auto-promote only when they were not explicitly screened out.
- Only the recurring canonical layer appears in the normal 80/20 output.
- Frozen v1.4.3 evidence is never modified.
- Qdrant remains OFF.
- Accepted facts remain 0.

## Why web lookup is separate

This version deliberately makes no network calls. It first measures how many candidates actually require online validation. That avoids building a large web-scraping/API system if only a small tail needs validation.

A later validator can consume `web_validation_queue_v1_6_1.jsonl` and write results such as:

```json
{"candidate_id":"pc_123","status":"valid","canonical_label":"STTH1506TPI","source":"Mouser","source_url":"https://...","manufacturer":"STMicroelectronics"}
```

or:

```json
{"candidate_id":"pc_456","status":"invalid","source":"web_search","notes":"No credible component/datasheet match; appears to be OCR noise."}
```

Then rerun the resolver with:

```bash
--web-results /path/to/web_validation_results.jsonl
```

Only `valid` / `confirmed` / `canonical` results can create a new canonical explicit part number.

## Recommended first run

Use the RCL1A human-review directory for both the target and training roots:

```bash
python ingest/nova_parts_ocr_deviant_resolver_v1_6_1.py \
  --candidate-root "$REVIEW_ROOT" \
  --training-review-root "$REVIEW_ROOT" \
  --output-root /opt/nova-drl/output/rcl1a_parts_resolver_v1_6_1 \
  --family "PS - RCL1A-1D-W3 RACAL" \
  --plan-only
```

The plan-only output is the important first measurement. It reports:

- human canonical vocabulary size,
- high-confidence OCR/format aliases recovered,
- web-validation queue size,
- preserved/unpromoted count,
- final 80/20 recurring part count.

If the numbers look sane, rerun without `--plan-only` to write outputs.

## Output files

- `canonical_parts_v1_6_1.jsonl`
- `part_aliases_v1_6_1.jsonl`
- `web_validation_queue_v1_6_1.jsonl`
- `preserved_unpromoted_v1_6_1.jsonl`
- `recurring_parts_80_20_v1_6_1.jsonl`
- `ocr_deviant_learning_v1_6_1.json`
- `parts_resolver_manifest_v1_6_1.json`
- `parts_resolver_summary_v1_6_1.txt`

## Default safeguards

- Alias similarity threshold: 0.92
- Minimum margin over second-best match: 0.08
- Unknown explicit PN must recur in at least 2 repair events before web validation.
- Generic description must recur in at least 3 repair events before non-human auto-promotion.
- Normal recurring output requires at least 2 repair events.
