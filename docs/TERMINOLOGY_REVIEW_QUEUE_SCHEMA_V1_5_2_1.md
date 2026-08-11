# Terminology Review Queue Schema v1.5.2.1

Each unresolved term records:

- raw term
- raw occurrence count
- unique repair-event count
- unique serial count
- unique model count
- unique OEM count
- first/last year and year span
- field distribution
- examples with source paths
- priority score
- HIGH / MEDIUM / LOW priority
- OCR-only flag
- consequential-field flag
- intervention recommendation: `queue` or `ask_now`
- suggested scope
- human review decision

Priority is event-centered. Duplicate OCR hits in one event do not become
multiple historical repair events.

Human definitions are audit logged and emitted into a derived
`effective_glossary.json`.
