# Nova DRL Whole Traveler Evidence Reader v1.3.5.0

## Purpose

v1.3.5.0 changes the Traveler-ingestion architecture from form-geometry-first to **evidence-first**.

The original full Traveler image is the evidence object. Nova no longer tries to locate the Repairs box, identify Repaired/Replaced marks, segment repair rows, interpret initials/date columns, or decide which section matters before collection.

## Core flow

1. Resolve the original full Traveler image for the requested DRL log.
2. Hash the original source and never modify it.
3. EXIF/orientation-normalize the **complete page** without cropping.
4. In detect-only mode, save only the complete normalized page and audit metadata.
5. In transcription mode, send the complete page to the vision model.
6. Preserve all visible printed, typed, stamped, and handwritten text as raw machine evidence.
7. Preserve the raw model response and unreadable fragments.
8. Accept no repair facts automatically.
9. Perform no Qdrant writes.

## Why this architecture

This follows the successful DRL power-supply repair-history precedent: collect the repair records broadly first, then use a larger corpus to identify repeated form/template content, recurring parts, repair actions, terminology, and useful patterns.

The downstream corpus stage—not the page reader—will decide what is repeated boilerplate, pertinent repair knowledge, administrative noise, a part, an action, or a diagnostic statement.

## Power-supply precedent carried forward

- Duplicate scans may be identified by source hashes and must be excluded from future frequency counts.
- Ambiguous items remain separate rather than being guessed into a known family.
- Explicit quantities may be preserved; unstated quantities are not estimated.
- Repair actions such as cleaning, soldering, testing, resurfacing, and trace repair are not parts merely because they appear in repair history.
- Normalization belongs downstream and must preserve the raw source wording.

## Important non-goals

v1.3.5.0 does **not**:

- crop or box any form section;
- detect row starts;
- interpret Repaired/Replaced marks;
- locate handwriting separately;
- classify relevance;
- normalize shop terminology;
- identify replacement parts;
- infer root cause;
- establish testing/final result;
- create a final repair summary;
- write to Qdrant.
