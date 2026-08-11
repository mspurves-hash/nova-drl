# v1.5.5.4 Changelog

- Freezes the v1.5.5.3 anchor-aware architecture.
- Adds human-directed global ignore policy for the neighboring final-testing-duration field.
- The ignored duration field remains in original source/audit evidence but cannot become repair knowledge.
- Masks the ignored duration OCR value before local `Final O.K.` vision.
- Tightens the `Final O.K.` relative crop without hard-coded absolute coordinates.
- Rejects schema-placeholder event marks such as `handwritten_value`.
- Validates technician initials as compact alphabetic initials; numeric values cannot be initials.
- Ignored-duration values cannot be used as event marks, technician initials, dates, association context, testing, final result, terminology knowledge, or future Qdrant knowledge.
- Keeps mutually-exclusive traveler final-disposition ambiguity behavior from v1.5.5.3.
- No source mutation.
- No automatic approval.
- No Qdrant writes.
