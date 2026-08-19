# Validation — v1.4.5

Synthetic deterministic tests cover:

1. Everything-style indexed Line Card discovery.
2. `.picasaoriginals` exclusion.
3. Non-Line-Card filename exclusion.
4. Roger `(1)+(2)` event: `(2)` primary, `(1)` supporting.
5. Roger `(1)+(2)+(3)` event: `(2)+(3)` primary, `(1)` supporting.
6. Roger `(1)` without `(2)`: `(1)` remains primary.
7. Non-Roger `(1)+(2)`: both remain primary.
8. Legacy 10-digit shared filename token groups the pair without claiming a valid DRL 9-digit log.
9. Event-bound fact candidate provenance.
10. Python compilation.

Expected test result:

```text
PASS: Nova DRL Indexed Repair Event Intelligence v1.4.5 tests
```
