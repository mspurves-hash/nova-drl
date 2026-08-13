# Nova DRL Traveler Corpus Prospector + Sorter v1.3.6.1

## Purpose

v1.3.5.1 remains the acquisition authority: collect the complete Traveler corpus first, preserve each raw Qwen3-VL transcription, source path, and source hash, and make no repair knowledge decisions during acquisition.

v1.3.6.1 improves only what happens **after** acquisition. The 10-Traveler pilot showed that the model split is useful but the v1.3.6.0 working ledger contained too much printed form/admin noise, and some valuable phrases such as `Sugar Cube test` could be seen by the 8B prospector yet fail evidence binding later.

## Pipeline

1. Read a completed v1.3.5.1 corpus manifest.
2. Re-verify every raw transcription SHA-256.
3. Build a temporary **prospecting view** for each Traveler:
   - remove `Hours in Final Testing` from the working view only;
   - remove routine form/admin identity/checklist lines from the working view only;
   - strip known printed prefixes when they share a line with event-bearing text, retaining the exact trailing source text;
   - keep customer requirements, handwritten repairs, diagnostics, test wording, parts, quantities, shop language, and unknown text.
4. Run Qwen3-VL 8B in text-only mode over that per-record working view as the high-recall prospector.
5. Bind each proposed quote back to the working view and then back to the immutable raw transcription.
6. Accept only these evidence-match modes:
   - exact;
   - whitespace/layout only;
   - whitespace/layout plus one model-added final `.`, `,`, `;`, or `:`.
7. Never normalize spelling, apostrophes, digits, abbreviations, or part strings.
8. Apply only narrow deterministic kind overrides for obvious customer requirements; keep the exact source wording unchanged.
9. Run Qwen2.5 32B across the compact evidence-backed candidate ledger.
10. Let Python enforce candidate-ID validity, group-type/kind compatibility, two-distinct-log recurrence, and two-distinct-source-hash recurrence.
11. Build deterministic high-value and OCR-recheck outputs.
12. Produce provisional results only: no fact approval and no Qdrant write.

## Why `Sugar Cube test` should survive now

A source transcription can contain:

```text
Tested Robot w/ Sugar
Cube test (Homing in Script Log?) over
Next Weekend
```

If the prospector returns:

```text
Tested Robot w/ Sugar Cube test (Homing in Script Log?) over Next Weekend.
```

v1.3.6.1 may accept that as `whitespace_terminal_punctuation`, but the candidate ledger stores the **actual source slice**, without the invented final period. Internal characters cannot change.

Therefore `Blue Schmoo` still cannot stand in for raw `Blue Schmoo's`, and a digit change in a part string still fails evidence binding.

## Sanitation is not deletion

Every per-record output now includes:

- `prospecting_view.txt` — exactly what the 8B model received;
- `sanitation_audit.json` — every raw line/field Python suppressed or prefix-stripped and the reason;
- the existing immutable v1.3.5.1 raw transcription path and SHA remain the evidence authority.

The sanitation stage is reversible/auditable and does not modify source files or the acquisition corpus.

## Deterministic kind safety

v1.3.6.0 allowed repeated packaging text to be verified as a `repair_or_service` group when the model misclassified it. v1.3.6.1 fixes this in two places:

- obvious requirement wording such as `This customer requires...`, `robot Fas are put inside packaging with unit..`, and `SHIPPING:` is deterministically typed as `customer_requirement` without changing its wording;
- a proposed recurring `repair_or_service` group may contain only `repair_or_service` candidates. A customer requirement cannot pass that group type.

## High-value backup surfacing

The 32B model is still useful for cross-record grouping, but it is not the sole gatekeeper for one-off important evidence. Python also surfaces provisional high-value candidates when they are evidence-backed and match narrow categories such as:

- customer requirements;
- diagnostic/failure evidence;
- nontrivial shop terms or abbreviations;
- part identifiers;
- distinctive test/process wording;
- repair/component strings with explicit quantity or partlike exact-character risk.

This is intended to keep phrases such as `Turkey fat is cause`, `Sugar Cube test`, and `2x Blue Schmoo's...` visible without approving or defining them.

## OCR recheck queue

`ocr_recheck_queue_v1_3_6_1.json` is a provisional queue for source-image reinspection. It can include:

- `[unclear]` evidence;
- part-number/identifier candidates;
- mixed alphanumeric strings such as an OCR-looking part number;
- strings such as `X.2` that deserve exact-character verification;
- technical prospector quotes that were rejected because they could not bind exactly enough to the transcription.

The queue does **not** correct anything automatically. The original Traveler JPG remains the evidence authority for later secondary vision or human review.

## Main outputs

- `candidate_ledger_v1_3_6_1.json` and `.jsonl`
- `rejected_prospector_candidates_v1_3_6_1.json`
- `repeated_line_inventory_v1_3_6_1.json`
- `sanitation_summary_v1_3_6_1.json`
- `ocr_recheck_queue_v1_3_6_1.json`
- per-record `prospecting_view.txt`
- per-record `sanitation_audit.json`
- `reasoning_raw_response_v1_3_6_1.txt`
- `reasoning_model_proposal_v1_3_6_1.json`
- `verified_recurring_groups_v1_3_6_1.json`
- `rejected_reasoning_groups_v1_3_6_1.json`
- `provisional_sort_v1_3_6_1.json`
- `provisional_sort_summary_v1_3_6_1.txt`
- `sort_manifest_v1_3_6_1.json`

## Scope

This remains a corpus-sorting experiment. v1.3.5.1 is still the acquisition baseline. v1.3.6.1 creates no approved repair knowledge and does not populate Qdrant.
