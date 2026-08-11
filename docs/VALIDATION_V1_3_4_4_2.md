# Validation v1.3.4.4.2

Live Ubuntu diagnostic for log 130813004 reported these detected horizontal
rules:

214, 362, 512, 811, 961, 1112, 1260, 1411, 2031

The repeated physical row pitch is approximately 150 pixels. Four rules were
lost where handwriting/scan texture interrupted the high-resolution printed
line. v1.3.4.4.2 reconstructs those missing boundaries before selecting the
regular table run.

Expected live pilot:

- Physical rows: 12
- Logical start marks: 2
- Logical repair blocks: 2
- status=ok

Vision must remain disabled until those counts pass and both block crops are
visually reviewed.
