# Nova DRL v1.5.15 — Family-First + Conservative Semantic Veto

v1.5.14 proved the MR-J2S-40A motor false-positive cause, but it over-filtered
legitimate actions in XU-RCM7231 and BM23995.

v1.5.15 changes the philosophy:

> Preserve the existing v1.5.11 repair action unless the source evidence
> clearly contradicts it.

Only three strong vetoes are applied:

1. explicit no-parts-changed / no-replacement language;
2. inferred component appears only as a test/load/operation target;
3. `REPLACE X` is inferred while explicit replacement wording occurs in a
   different clause and no replacement clause mentions X.

There are no equipment-specific rules.

Important coordinated-action behavior:

`Replaced bearings and belts`

may support both:
- REPLACE BEARING
- REPLACE BELT

## Run

```bash
cd /opt/nova-drl

python3 test_nova_drl_family_first_semantic_veto_v1_5_15.py

python3 nova_drl_family_first_semantic_veto_v1_5_15.py --search "MR-J2S-40A"
python3 nova_drl_family_first_semantic_veto_v1_5_15.py --search "XU-RCM7231"
python3 nova_drl_family_first_semantic_veto_v1_5_15.py --search "BM23995"
```

Expected:
- MR-J2S-40A: REPAIR MOTOR / REPLACE MOTOR disappear.
- XU-RCM7231: legitimate mechanical actions return close to v1.5.13.
- BM23995: legitimate board/component actions return.
- family-first resolution remains unchanged.

Do not change the launcher until these checks look sensible.
