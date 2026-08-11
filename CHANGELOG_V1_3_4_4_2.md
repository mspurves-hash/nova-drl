# v1.3.4.4.2 Changelog

- Corrected sparse horizontal-grid detection on the real 2798x2162
  Traveler Reader production crop.
- Added robust physical-row pitch estimation.
- Reconstructs only missing horizontal rules whose gaps are near integer
  multiples of the observed row pitch.
- Interpolates reconstructed rules locally to tolerate mild scan stretch.
- Preserves all originally detected line positions.
- Adds horizontal-line reconstruction diagnostics to block_detection_debug.json.
- Keeps the clipped-left table correction from v1.3.4.4.1.
- No DRL source modifications.
- No Qdrant writes.
