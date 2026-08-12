# Changelog v1.3.5.1

- Replaced per-event interpretation with **corpus acquisition first, sorting later**.
- Primary vision path changed from MiniCPM-V to `qwen3-vl-drl:8b-q8-16k`.
- Qwen3-VL receives the complete original Traveler image directly.
- Removed JSON-format requirement from the vision model; raw plain-text response is the evidence output.
- Added recursive discovery of `Line Card Original` and `Line Card Warranty` images.
- Added source SHA-256, source/folder/log metadata, model/digest/context metadata, and prompt fingerprinting.
- Added per-Traveler durable outputs and corpus manifest/JSONL/summary.
- Added resumable collection keyed by source hash + model + prompt + context settings.
- Added exact duplicate-hash audit groups without suppressing sources during acquisition.
- Added audit-only runaway-repetition signal; raw response is never altered or rejected by it.
- Classification, normalization, automatic fact acceptance, final summaries, and Qdrant remain OFF.
