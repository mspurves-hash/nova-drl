# NOVA DRL Editable DOCX v1.9.2 - Overwrite Protection

## Purpose

Protect technician-edited Word reports from accidental replacement.

## Default behavior

If the normal report does not exist:

```text
/mnt/drl-reports/NOVA_<PART>_Repair_Reference.docx
```

NOVA creates it normally.

If that report already exists, NOVA leaves it untouched and creates:

```text
NOVA_<PART>_Repair_Reference_NEW_YYYY-MM-DD_HHMMSS.docx
```

If the timestamped name already exists, `_2`, `_3`, etc. are appended.

## Explicit custom output

`--out` will not overwrite an existing file. To deliberately replace an existing DOCX, the user must explicitly add:

```text
--force
```

## Examples

Normal safe generation:

```bash
nova-drl-docx --search "BM23995"
```

Intentional overwrite only:

```bash
nova-drl-docx --search "BM23995" --out /path/report.docx --force
```

## Unchanged behavior

- Knowledge source remains frozen v1.5.16.
- DOCX layout and embedded baseline are unchanged from v1.9.1.
- Technician Notes remain report-only.
- v1.9.1 DOCX reviewer remains compatible.
