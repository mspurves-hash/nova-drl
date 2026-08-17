# Nova DRL v1.4.0 — Power Supply Corpus Pilot

## Purpose

This is the first non-GB8 generalization test of the Nova DRL architecture. It processes the original PS-RCL1A-1D-W3 repair-history PDF (or a directory of page images) **without reading the prior hosted frequency-analysis answers**.

The goal is to freeze an independent local result first, then compare that frozen result against the earlier report.

## Pipeline

1. Source PDF remains read-only.
2. PDF pages are rendered to disposable JPEG derivatives when necessary.
3. `qwen3-vl-drl:8b-q8-16k` creates literal page transcriptions.
4. Python generates high-similarity duplicate-page candidates.
5. `qwen25-drl:14b-q6-16k` adjudicates only those near-duplicate pairs.
6. Unique representative pages are passed to 14B for replacement-event extraction.
7. Every extracted replacement must bind back to an exact/raw transcription quote.
8. 14B provisionally groups obvious same-part / same-family descriptors.
9. Python counts distinct repair pages containing each family and sums recorded quantities.
10. No Qdrant writes and no automatic approval.

## Blind-validation rule

Do **not** feed the previous power-supply parts reports to this program and do not alter the runtime using remembered target counts. Freeze the v1.4.0 output first. Comparison happens afterward.

## Source placement

Default expected source:

```text
/opt/nova-drl/input/RCL1A-1D-W3 All Line Cards.pdf
```

The PDF should not be committed to Git. Create the input directory if needed:

```bash
mkdir -p /opt/nova-drl/input
```

If the PDF lives elsewhere, pass its full path:

```bash
python3 analysis/nova_power_supply_corpus_pilot_v1_4_0.py --source-pdf "/path/to/RCL1A-1D-W3 All Line Cards.pdf" --plan-only
```

A directory of pre-rendered images may be used instead:

```bash
python3 analysis/nova_power_supply_corpus_pilot_v1_4_0.py --source-images-root /path/to/page-images --plan-only
```

## Dependencies

For PDF input, Poppler command-line tools are required:

```text
pdfinfo
pdftoppm
```

If missing on Ubuntu:

```bash
sudo apt install poppler-utils
```

The normal Nova models must be installed:

```text
qwen3-vl-drl:8b-q8-16k
qwen25-drl:14b-q6-16k
```

## Commands

Status:

```bash
python3 analysis/nova_power_supply_corpus_pilot_v1_4_0.py --status
```

Plan only — no model calls:

```bash
python3 analysis/nova_power_supply_corpus_pilot_v1_4_0.py --plan-only
```

Acquisition only:

```bash
python3 analysis/nova_power_supply_corpus_pilot_v1_4_0.py --acquire-only
```

Full blind run:

```bash
python3 analysis/nova_power_supply_corpus_pilot_v1_4_0.py
```

The run is cache/resume aware. Re-running the same command reuses completed page transcriptions, duplicate adjudications, extraction results, and part-family mapping.

## Primary outputs

```text
/opt/nova-drl/output/power_supply_corpus_pilot_v1_4_0/page_manifest_v1_4_0.json
/opt/nova-drl/output/power_supply_corpus_pilot_v1_4_0/dedupe/duplicate_adjudication_v1_4_0.json
/opt/nova-drl/output/power_supply_corpus_pilot_v1_4_0/replacement_mentions_v1_4_0.jsonl
/opt/nova-drl/output/power_supply_corpus_pilot_v1_4_0/part_family_map_v1_4_0.json
/opt/nova-drl/output/power_supply_corpus_pilot_v1_4_0/part_frequency_v1_4_0.csv
/opt/nova-drl/output/power_supply_corpus_pilot_v1_4_0/part_frequency_v1_4_0.json
/opt/nova-drl/output/power_supply_corpus_pilot_v1_4_0/power_supply_pilot_summary_v1_4_0.txt
/opt/nova-drl/output/power_supply_corpus_pilot_v1_4_0/power_supply_pilot_manifest_v1_4_0.json
```

## Counting semantics

- `repairs_containing_part`: distinct deduplicated representative pages containing at least one accepted extraction for that part family.
- `recorded_pieces`: sum of numeric quantities that were explicit or unmistakably enumerated in source wording.
- `quantity_unstated_mentions`: supported replacement mentions for which no defensible numeric quantity was present.
- Cleaning, soldering, trace repair, testing, adjustment, and similar work are not parts.

All labels/families are provisional until human review.
