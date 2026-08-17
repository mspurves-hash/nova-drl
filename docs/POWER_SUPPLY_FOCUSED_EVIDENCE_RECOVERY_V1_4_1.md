# Nova DRL v1.4.1 — Power Supply Focused Evidence Recovery

## Purpose

v1.4.1 is the production-shaped follow-up to the v1.4.0 blind power-supply corpus pilot.

The combined RCL1A-1D-W3 PDF is **not** the normal Nova DRL source format. It is a benchmark container created for validation. Production operation is image-first: individual `Line Card ...jpg/JPG` files are discovered recursively inside repair folders.

## Architecture

```text
PRODUCTION
individual Line Card images
        ↓
source path/hash + DRL log/folder context
        ↓
Qwen3-VL 8B parts-focused visual reread
        ↓
exact/perceptual/text duplicate candidates
        ↓
14B duplicate adjudication where needed
        ↓
same DRL log grouped as one repair event
        ↓
14B replacement extraction
        ↓
Python exact punctuation/case consolidation
        ↓
Python fuzzy normalization candidates
        ↓
14B provisional corpus-level family grouping
        ↓
Python repair-frequency / explicit-piece counts
```

Benchmark PDF mode simply renders every PDF page to a 300-DPI image and feeds it into the same image-first path.

## Focused acquisition

The focused reader is intentionally different from a general whole-page transcription. It asks the vision model to preserve all visible component/assembly, part-number, quantity, fuse-rating, donor-part, and replacement-action evidence without normalizing characters.

When Pillow is installed, the same model call receives:

1. the complete source image, and
2. an enlarged repair/replacement-region crop.

If Pillow is unavailable, the complete image is still processed and the pipeline remains functional.

For the benchmark PDF only, a matching v1.4.0 blind whole-page transcription may be supplied as auxiliary evidence. It is reused only when the source PDF SHA256 matches. The hosted benchmark reports are never runtime inputs.

## Repair-event identity

- Exact/near duplicate scans are deduplicated as scan evidence.
- Multiple legitimate images with the same 9-digit DRL log number remain preserved but count as **one repair event** for parts-frequency calculations.
- When no log number is available, the deduplicated source record becomes the repair-event identity.

## Parts normalization

Raw mention strings are never rewritten.

v1.4.1 first deterministically consolidates trivial punctuation/case/spacing differences. It then creates fuzzy candidate descriptor components and lets the 14B model provisionally group likely OCR/handwriting variants. Unassigned descriptors remain independent.

Python owns all recurrence and explicit-quantity totals.

## Safety / evidence policy

- Original Line Card images: read-only.
- v1.4.0 blind baseline: unchanged.
- Focused transcription: immutable machine evidence after acquisition.
- Unstated quantities: never estimated.
- Hosted benchmark results: not read by runtime.
- Accepted facts: 0.
- Qdrant writes: OFF.
