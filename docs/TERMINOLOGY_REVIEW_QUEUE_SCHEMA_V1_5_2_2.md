# Terminology Review Queue Schema v1.5.2.2

Each unresolved term includes:

- raw term
- raw occurrence count
- unique repair-event count
- unique serial count
- unique model/OEM counts
- first/last year and year span
- field distribution
- acronym-shape contribution
- OCR-only flag
- template-repetition assessment
- template priority penalty
- scope suggestion
- HIGH / MEDIUM / LOW priority
- `queue` or `ask_now` intervention recommendation
- source examples
- human Define / Defer / Ignore decision

Suppressed candidate occurrences are separately auditable by reason:

- `common_english_word`
- `known_metadata_identifier`
- `fixed_ignore_term`
- `ignore_pattern`
- `alpha_token_too_long_for_acronym`
- other structural filters

Known terminology and metadata are never silently discarded.
