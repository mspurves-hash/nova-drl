# Validation — v1.5.2

Synthetic validation covers:
- 100% corpus membership behavior.
- strict tracking/procurement separation.
- CMYK/legacy JPEG re-encoding to standard RGB JPEG.
- original-image failure followed by successful normalized retry.
- total vision failure producing an exception record without aborting ingestion.
- Python compilation.

### Frozen-manifest compatibility regression
The test suite verifies that a frozen manifest with `version: 1.5.1`, 100% membership, the v1.5.1 full-corpus seed, and the expected tech-scan base is accepted by v1.5.2 without `--force-corpus`.
