# Validation — v1.3.6.1

The packaged unit test is deterministic and does not require Ollama. It validates:

- completed v1.3.5.1 acquisition is required;
- `Hours in Final Testing` is absent from the working view while adjacent `Final O.K.` text is not accidentally consumed by the hours-field regex;
- routine form/admin identity and final checklist text is removed only from the working view;
- customer requirements and event-bearing text survive sanitation;
- printed final-test/time labels can be stripped while exact handwritten trailing text survives;
- `Sugar Cube test` can bind across line wraps plus one model-added terminal punctuation character;
- the stored candidate remains the exact raw source slice, not the model's reformatted quote;
- `Blue Schmoo` cannot replace `Blue Schmoo's`;
- digit changes fail evidence binding;
- obvious packaging/customer requirements can be deterministically retyped without changing evidence text;
- customer-requirement candidates cannot pass a `repair_or_service` recurring group;
- two-log/two-source-hash compatible groups pass Python recurrence accounting;
- one-log groups fail regardless of model claims;
- deterministic high-value backup logic preserves difficult pilot-style diagnostic/test/shop/part strings while suppressing trivial technician initials;
- OCR recheck queue catches part identifiers, `X.2`-style mixed strings, `[unclear]` repair text, and rejected technical prospector quotes;
- no automatic approval and no Qdrant behavior exists in the sorter.

Expected packaged test result:

```text
PASS: Nova DRL Traveler Corpus Prospector + Sorter v1.3.6.1 tests
```

The first live validation should run against the same 10-log GB8 corpus used for v1.3.6.0 and compare candidate count/noise, `Sugar Cube test` survival, requirement typing, recurring-group quality, and the OCR recheck queue.
