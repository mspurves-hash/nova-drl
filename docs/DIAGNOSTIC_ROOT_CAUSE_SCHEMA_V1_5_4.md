# Diagnostic / Root Cause Schema v1.5.4

Each candidate preserves:

- candidate ID and number
- candidate type
- raw machine-transcribed technician note
- linked human-approved repair action
- source traveler and crop
- terminology annotations
- uncertainty cues
- causal cues
- human review decision
- root-cause confirmation state
- future Qdrant eligibility

Supported human decisions:

```text
approve-hypothesis
confirm-root-cause
reject
hold
```

`confirm-root-cause` is rejected when uncertainty language caused the
candidate to be classified as a diagnostic hypothesis.
