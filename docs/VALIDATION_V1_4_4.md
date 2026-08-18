# Validation — v1.4.4

Synthetic deterministic tests cover the architectural behaviors that matter for the 80/20 parts-intelligence layer:

- two different MOSFET PNs can share one functional `MOSFET Family` while remaining distinct PN usage groups;
- OCR variants of each MOSFET PN can be consolidated into a likely PN;
- an unsupported model-invented PN is rejected and replaced with grounded observed evidence;
- alternate 15 A / 600 V fuse PNs can share one functional fuse family while remaining separate PN groups;
- a 15 A / 250 V fuse remains separate from a 15 A / 600 V family;
- family frequency counts distinct repair events, not raw mention count;
- explicit quantities are summed by Python;
- unstated quantities remain unstated;
- description-only replacements such as a SMART board remain represented.

Expected test result:

```text
PASS: Nova DRL RCL1A 80/20 Parts Intelligence v1.4.4 tests
```
