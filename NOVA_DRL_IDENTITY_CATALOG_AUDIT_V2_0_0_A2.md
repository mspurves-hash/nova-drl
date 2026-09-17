# NOVA DRL Identity Catalog Audit v2.0.0-a2

## Decision behind a2

The live a1 audit proved that `product_families` has no explicit equipment Part
Number column. Schema review also proved that `product_parts.manufacturer_pn`
contains replacement-component identities, not equipment identities.

Review of the frozen v1.5.18 resolver found a separate hazard: its exact and
unique-prefix paths remove punctuation before deciding identity equality. Two
different raw model numbers can therefore collapse to the same normalized key.

Version a2 corrects the audit source and equality rule. It does not change the
production resolver.

## Purpose

This is a read-only candidate inventory before any v2 identity catalog or
resolver is built. It:

1. inventories the installed launcher versions;
2. inventories the current SQLite schema and row counts without using database
   rows as equipment-identity authority;
3. streams exact `equipment_family` labels from the frozen v1.6.0 lossless
   repair-event corpus;
4. extracts broad model-like raw tokens while preserving punctuation;
5. uses punctuation-stripped keys only to expose potential collisions;
6. ranks an 80/20 human-review queue and checks the critical DRL benchmarks.

Every candidate remains unapproved.

## Hard safety rules

- Raw `AB-12-3` and raw `AB1-23` remain separate candidate identities.
- A shared comparison key such as `AB123` raises a collision; it never merges.
- SQLite `product_families` and `product_parts` are not identity authorities.
- Alphabetic benchmark identities can be inventoried only when observed as an
  exact raw family token.
- Unique-prefix resolution is not built or enabled by this audit.
- No database, corpus, source Traveler, launcher, or Qdrant writes occur.
- No canonical identity, alias, mapping, or accepted fact is created.
- v1.5.18 remains frozen as the compatibility baseline.

## 80/20 review boundary

The report prints every normalization-collision group separately, followed by
the highest-risk/highest-recurrence candidate tokens. It also prints the
critical DRL benchmark status and the highest-volume family labels that lack a
candidate token. This focuses human review on collisions, important families,
and recurring identities rather than the full long tail.

## Install and run

Place these files in `/opt/nova-drl` through the normal Git workflow:

```text
nova_drl_identity_catalog_audit_v2_0_0_a2.py
test_nova_drl_identity_catalog_audit_v2_0_0_a2.py
NOVA_DRL_IDENTITY_CATALOG_AUDIT_V2_0_0_A2.md
```

On Ubuntu:

```bash
cd /opt/nova-drl
python3 test_nova_drl_identity_catalog_audit_v2_0_0_a2.py
python3 nova_drl_identity_catalog_audit_v2_0_0_a2.py
```

For a compact machine-readable report:

```bash
python3 nova_drl_identity_catalog_audit_v2_0_0_a2.py --json
```

Do not redirect a launcher, change the Windows client, or deploy a resolver
based only on this audit.

## Decision after the audit

Only after reviewing collisions, benchmark gaps, and the ranked recurring
candidates should NOVA create a separately frozen human-approved catalog. A
future resolver should use exact punctuation-preserving canonical identities
and approved aliases first. Unique-prefix matching may operate only across that
approved catalog and must return no result whenever more than one approved
identity remains possible.
