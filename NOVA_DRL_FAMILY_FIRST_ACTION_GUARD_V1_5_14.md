# Nova DRL v1.5.14 — Family-First + Direct-Action Semantic Guard

## Why

MR-J2S-40A exposed a general semantic error in v1.5.11 repair-action aggregation.

The word `motor` was genuine, but it described the external servo motor used to
test the drive:

- `Motor tests worked okay.`
- `works great turning Motor with 220V AC input`
- `on MeServer turned 400W motor`
- `Fixed motor issue ... Replaced batteries`

The old parser converted keyword proximity into:

- `REPAIR MOTOR`
- `REPLACE MOTOR`

That is not an OCR problem. It is an action/object binding problem.

## v1.5.14 rule

A recurring repair action survives only when the same clause contains a direct
action-object relationship.

Examples retained:

- `Replaced motor`
- `Motor was replaced`
- `Repaired board`
- `Cleaned connector`
- `Adjusted belt`
- `Rebuilt bearing`
- `Lubricated lead screw`

Examples rejected:

- `Motor tests worked okay`
- `Billed as repair ... Turned motor`
- `works great turning Motor`
- `Fixed motor issue ... Replaced batteries` -> may support battery replacement,
  but not motor replacement/repair
- negated statements such as `no parts replaced`

The guard is generic; there is no MR-J2S-40A-specific or servo-drive-specific rule.

## Architecture

v1.5.14 uses:

1. v1.5.13 family-first resolution
2. v1.5.11 existing evidence/aggregation
3. a conservative post-aggregation direct-action guard

No DB rebuild, no OCR rewrite, no Qdrant.

## Test

```bash
cd /opt/nova-drl

python3 test_nova_drl_family_first_action_guard_v1_5_14.py

python3 nova_drl_family_first_action_guard_v1_5_14.py --search "MR-J2S-40A"
python3 nova_drl_family_first_action_guard_v1_5_14.py --search "XU-RCM7231"
python3 nova_drl_family_first_action_guard_v1_5_14.py --search "BM23995"
```

For MR-J2S-40A, `REPAIR MOTOR` and `REPLACE MOTOR` should disappear.

For XU-RCM7231, legitimate direct mechanical actions such as `REPLACE BEARING`,
`REPLACE BELT`, `REPLACE LEAD SCREW`, etc. should remain when directly supported.

For BM23995, direct board/component actions should remain.

Do not change the launcher until these three checks look sensible.
