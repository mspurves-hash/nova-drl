# Nova DRL Indexed Repair Event Intelligence v1.4.5

## Purpose
v1.4.5 is the first generic DRL repair-event intelligence branch. It is designed to be reused across repair families instead of creating a model-specific pipeline each time.

The first validation query is:

```bash
XU-RCM7231 LINE
```

## Standing 80/20 rule
The DRL Nova 80/20 rule is a fixed default until Matt explicitly changes it. The pipeline prioritizes high-volume recurrence, useful best-guess consolidation, and technician value over perfect OCR, exhaustive one-off cleanup, or forensic transcription. Original source paths/evidence remain preserved for human exception review.

## Production source flow

```text
/mnt/drl
   ↓
persistent DRL SQLite index
   ↓
Everything-style query
   ↓
Line Card source selector
   ↓
repair-event grouping
   ↓
primary evidence selection
   ↓
vision → event extraction → recurrence grouping
```

There is no recursive NAS walk during a v1.4.5 run.

## Roger-only paired Line Card rule
Roger is the only engineer for whom the paired-card shortcut is applied.

When a Roger repair event contains `(1)` and `(2)`:
- `(2)` is the primary typed repair narrative and gets the deep vision read.
- `(1)` remains in the repair-event manifest as supporting evidence and is not deep-read by default.
- `(3+)`, when present, remains primary/additional evidence.
- If `(2)` is absent, available Line Cards are read normally.

The shortcut is NOT generalized to other engineers.

## Repair intelligence categories
Each event is reduced to high-signal evidence in these lanes:
- reported symptoms
- diagnostics/findings
- repair actions
- parts/assemblies
- adjustments/calibration/teach
- testing/verification
- outcomes

Then 14B performs corpus-level grouping and Python counts distinct repair events.

## Safety / authority
- Original DRL source: read-only.
- Accepted facts: 0.
- Qdrant writes: OFF.
- No prior hosted benchmark data is read.
