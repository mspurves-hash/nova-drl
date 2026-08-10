# v1.3.4.2 Validation Record

The revised detector was smoke-tested against the live GB8 repair-table debug
crop used during development.

Observed result:

```text
Expected anchors: 4
Detected anchors: 4
Raw boundaries: 209, 424, 749, 1068, 1286
Description coverage: 1.000
Entries containing description ink: 4/4
Boundary crossings: 0
Unsafe boundaries: 0
```

The detector correctly skipped:

- grid line 960 because scanner/re-alignment handwriting continued below it;
- grid line 1176 because Z lead-screw handwriting continued below it.

The source date text touched the available image's right edge, so date fields
remain review-required. This does not block description extraction.
