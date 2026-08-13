# Nova DRL Traveler Corpus Prospector + Sorter v1.3.6.0

## Why this build exists

v1.3.5.1 proved the acquisition-first architecture: collect every complete Traveler first with Qwen3-VL and preserve the raw response. The 10-record GB8 comparison then showed a useful model split:

- **Qwen3-VL 8B** had stronger recall for unusual DRL wording and one-off phrases.
- **Qwen2.5 32B** had better cross-record discipline and grouping.
- Neither model should be trusted to count recurrence or rewrite source evidence.

v1.3.6.0 therefore separates those jobs.

## Pipeline

1. Read a **completed v1.3.5.1 corpus manifest**.
2. Verify every selected raw transcription still matches its stored SHA-256.
3. Build a knowledge-sort view that suppresses only the globally excluded `Hours in Final Testing` field. The immutable raw transcript is unchanged.
4. Run **Qwen3-VL 8B in text-only mode per Traveler** as a high-recall prospector.
5. Verify every model `raw_quote` against the source transcription. Only exact or whitespace-only-supported quotes enter the candidate ledger. Unsupported model rewrites remain in an audit rejection file.
6. Run **Qwen2.5 32B** across the compact evidence-backed candidate ledger using candidate IDs.
7. Python validates every returned ID and enforces recurring groups as **>=2 distinct DRL logs and >=2 distinct source hashes**.
8. Preserve unusual/part-number/test/unclear candidates regardless of whether the 32B model selects them as unique high-value candidates.
9. Produce provisional outputs only. **No fact is approved and Qdrant remains OFF.**

## Important evidence behavior

A prospector output such as `Blue Schmoo` cannot silently replace raw `Blue Schmoo's`: punctuation/characters are not normalized by the evidence validator. Only whitespace differences may be tolerated, and the output ledger stores the actual source slice.

The 32B model does not receive authority to count. A group that it calls recurring but which contains only one log is rejected by Python and retained in the audit file.

## Main outputs

- `candidate_ledger_v1_3_6_0.json` / `.jsonl` — evidence-backed high-recall candidate ledger.
- `rejected_prospector_candidates_v1_3_6_0.json` — unsupported or invalid 8B proposals.
- `repeated_line_inventory_v1_3_6_0.json` — deterministic exact repeated-line inventory across logs.
- `reasoning_raw_response_v1_3_6_0.txt` — unmodified 32B reasoning response.
- `reasoning_model_proposal_v1_3_6_0.json` — parsed 32B proposal.
- `verified_recurring_groups_v1_3_6_0.json` — only Python-verified >=2-log groups.
- `rejected_reasoning_groups_v1_3_6_0.json` — invalid IDs, one-log groups, or other rejected groups.
- `provisional_sort_v1_3_6_0.json` — consolidated provisional sort.
- `provisional_sort_summary_v1_3_6_0.txt` — human-readable review summary.
- `sort_manifest_v1_3_6_0.json` — audit manifest.

## Scope

This is a **sorting experiment**, not a knowledge approval build. v1.3.5.1 remains the frozen acquisition source. v1.3.6.0 never modifies NAS source files or v1.3.5.1 raw transcriptions.
