# Changelog v1.3.6.1

v1.3.6.1 keeps the v1.3.5.1 acquisition corpus unchanged and hardens only the post-acquisition sort.

- Added a **deterministic sanitation pass** before the 8B prospector. Routine form/admin lines are removed only from a temporary working view; customer requirements and event-bearing text stay available.
- Added partial form-label stripping so event text attached to printed labels can survive. Example: `Power-on Tests Only TOP to Bottom Dual` becomes working-view evidence `TOP to Bottom Dual`; the raw transcription remains untouched.
- Changed global `Hours in Final Testing` handling so the field is removed from the working view without inserting a synthetic suppression marker that the model could accidentally prospect.
- Improved evidence matching to accept **exact**, **whitespace-only**, and **one model-added terminal punctuation** differences while still rejecting spelling, apostrophe, digit, abbreviation, or part-string changes.
- Fixed the `Sugar Cube test` failure mode: line wrapping plus harmless terminal punctuation can now bind back to the exact raw source slice.
- Added narrow deterministic customer-requirement kind overrides without rewriting evidence text.
- Added Python **group-type / candidate-kind compatibility** checks so packaging requirements cannot be verified as repair groups merely because the 32B model labels them that way.
- Added deterministic high-value backup surfacing so unusual diagnostics, shop terms, part identifiers, named tests, and quantity/partlike repair strings are not dependent on the 32B model selecting them.
- Added an **OCR recheck queue** for exact-character-risk identifiers, mixed alphanumeric strings, `[unclear]` evidence, and unsupported technical prospector quotes.
- Added per-record `prospecting_view.txt` and `sanitation_audit.json` outputs for transparent audit of what Python removed from the model view.
- No source mutation, no automatic approvals, and no Qdrant writes.
