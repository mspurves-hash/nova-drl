# NOVA DRL Editable DOCX Report v1.9.1

Small operational update to the validated v1.9.0 DOCX generator.

## Change

Default DOCX output directory is now:

```text
/mnt/drl-reports
```

That mount maps read/write to the Windows share folder:

```text
\\192.168.86.25\Public\NOVA_REPORTS
```

The historical source mount `/mnt/drl` remains read-only.

## Compatibility

The hidden baseline marker remains `NOVA_BASELINE_V1_9_0_B64:` intentionally, so the validated v1.9.1 review tool can review both existing v1.9.0 reports and new v1.9.1 reports.

`--out` still overrides the default destination.

## Usage

```bash
nova-drl-docx --search "MR-J2S-40A"
```

Expected output:

```text
/mnt/drl-reports/NOVA_MR-J2S-40A_Repair_Reference.docx
```
