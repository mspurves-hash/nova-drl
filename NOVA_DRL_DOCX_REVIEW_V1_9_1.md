# NOVA DRL DOCX Review v1.9.1

## Why v1.9.1

The first real Microsoft Word edit test exposed a packaging detail:

- v1.9.0 identified report tables primarily by `w:tblCaption`.
- Microsoft Word can remove those captions when saving the hand-built DOCX.
- The hidden NOVA baseline survives, but the reviewer then sees no current
  Parts / Failures / Actions tables and incorrectly reports every row deleted.

v1.9.1 fixes only the reviewer.

## New table identification

The reviewer now:

1. uses `w:tblCaption` when available;
2. otherwise identifies tables from their visible stable headers:
   - `Reference PN / Component | Times Replaced`
   - `Reported Failure | Times Seen`
   - `Recurring Repair Action | Times Seen`
3. identifies the metadata table from:
   - `Equipment Family`
   - `Base DRL Part #`
4. ignores the Technician Notes table.

The report generator remains v1.9.0.

## Expected real edit result

If a technician changes:

`7800` -> `HCPL-7800`

the reviewer should show only:

```text
PARTS
-----
- 7800 (88)
+ HCPL-7800 (88)
```

Technician Notes / formatting remain ignored.

No changes are ever promoted automatically.
