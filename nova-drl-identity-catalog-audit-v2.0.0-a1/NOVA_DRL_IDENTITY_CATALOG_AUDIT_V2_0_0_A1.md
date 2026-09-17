# NOVA DRL Identity Catalog Audit v2.0.0-a1

## Purpose

This is the corrected first milestone for the Part-Number-First technician search.
It audits identity candidates before any v2 resolver or search index is built.

The audit is deliberately read-only:

- no identity is approved;
- no punctuation variant is merged;
- no family mapping is merged;
- no database, corpus, source Traveler, launcher, or Qdrant writes occur;
- v1.5.x production remains unchanged;
- v1.5.19 remains undeployed.

## What it compares

1. The actual installed `nova-drl` launcher paths and detected version targets.
2. Explicit DRL Part # candidates in the existing SQLite `product_families` table.
3. Exact equipment-family labels in the frozen v1.6.0 lossless repair-event corpus.

The existing index supplies **candidates only**. It is not treated as canonical
authority.

## What it flags

- one- and two-character identities;
- alphabetic-only identities requiring review;
- category-like and recurring trailing OEM-like words;
- punctuation/format variants that collapse to the same alphanumeric key;
- candidates not observed as an exact token in any v1.6.0 family label;
- mapped families that do not contain the candidate as an exact token;
- one candidate mapped to multiple family labels.

Every normalization collision is printed and must remain unmerged. The normal
review list is ranked by recurrence and risk so the long tail does not become a
manual cleanup project.

## Install and run

Copy these files into the repository root:

```text
nova_drl_identity_catalog_audit_v2_0_0_a1.py
test_nova_drl_identity_catalog_audit_v2_0_0_a1.py
NOVA_DRL_IDENTITY_CATALOG_AUDIT_V2_0_0_A1.md
```

On Ubuntu:

```bash
cd /opt/nova-drl
python3 test_nova_drl_identity_catalog_audit_v2_0_0_a1.py
python3 nova_drl_identity_catalog_audit_v2_0_0_a1.py
```

Copy the complete second-command output back to Nova.

For a compact machine-readable report instead:

```bash
python3 nova_drl_identity_catalog_audit_v2_0_0_a1.py --json
```

Do not redirect a new launcher, change the Windows client, or deploy anything
based only on this audit.

## Decision after the audit

The audit determines whether the existing explicit Part # column is usable as a
candidate source and identifies the small high-value/collision review set.

Only after that review should NOVA build:

1. a separately frozen, approved equipment-identity catalog;
2. a derived read-only v2 SQLite search index with frozen-input hashes;
3. the exact/approved-alias/unique-prefix resolver;
4. exact-family adapters to v1.6.0 evidence and the latest available screened
   knowledge;
5. a shadow comparison against the existing production search.
