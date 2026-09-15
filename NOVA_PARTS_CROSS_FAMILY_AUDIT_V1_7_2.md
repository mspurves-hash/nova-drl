# Nova DRL Cross-Family Parts Generalization Audit v1.7.2

This is the next step after the v1.7.1 10-family generalization cohort.

It does **not** canonicalize those families. It builds conservative candidate buckets and asks
whether the RCL1A-style OCR/format problems repeat across independent repair lines.

Default cohort:
- THERM ARRAY - 1957617006K PRESCOT
- RBT - GB8-MT GENMARK
- BRD - BM23995 EXEC CAR ASYST
- SVO MTR - 14204E239 PITTMAN
- PS -00010-93076 AMAT
- MTR - 0010-70264 AMAT
- HEAT EXCHANGER - ETN23A-SC-B ORION
- BRD - 3200-1000-09 ARM CNTL ASYST
- BRD - BM23994 CAR CHARGER ASYST
- SVO DRV - MR-J2S-20A MITSUBISHI

## Safety/80-20 rules

- same candidate bucketing semantics as the human-review workspace;
- no human review required;
- no RCL1A canonical vocabulary applied;
- no RCL1A fuse rules applied;
- no auto merges;
- numeric model/rating differences veto safe alias proposals;
- Mouser `511-` and DigiKey `-ND` are reported as supplier wrappers, not OCR errors;
- a potential global cleanup pattern must appear in at least 3 independent families before it is even labeled a global-rule candidate;
- family-specific oddities remain local and preserved.

## Run

```bash
cd /opt/nova-drl

python3 test_nova_parts_cross_family_audit_v1_7_2.py
python3 nova_parts_cross_family_audit_v1_7_2.py --plan-only
```

The next decision should be driven by:
1. recurring candidate event coverage across the 10 families;
2. how many safe OCR/format pair proposals exist;
3. whether the same pair pattern recurs in 3+ independent families.

If patterns do not recur cross-family, stop adding global OCR rules and keep the current conservative resolver.

## Eligibility-contract warning

Unified-index v1.4.8 historically reports 3,287 usable Parts rows from the
3,433 enriched replacement mentions. The exact 146-row exclusion contract is
not duplicated in this audit script.

v1.7.2 therefore compares its locally eligible global count with 3,287 and
prints an **ELIGIBILITY CONTRACT WARNING** if they differ.

This does not stop the generalization audit. It means candidate/event counts
should be treated as an upper-bound diagnostic until the exact v1.4.8
procurement-only exclusion helper is reused. Do not add pipeline rules based
only on a small count difference.
