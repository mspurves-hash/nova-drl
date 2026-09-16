# NOVA DRL Technician Console v1.9.2

## Purpose

Make the editable DOCX report the normal technician-facing report action without
changing frozen v1.5.16 search semantics.

The console is a thin workflow wrapper only.

## Frozen components

- Search / family / Parts / failures / actions: v1.5.16
- Evidence audit: v1.8.1
- DOCX generator: v1.9.1
- DOCX edit reviewer: v1.9.1

## Workflow

```text
DRL Part # search
      |
      v
frozen v1.5.16 report
      |
      +-- :docx --> /mnt/drl-reports/NOVA_<PART>_Repair_Reference.docx
      |
      +-- :review --> compare Word edits with embedded NOVA baseline
      |
      +-- :new --> next DRL Part #
```

Technician Notes remain report-only.

## Installation

Repo root:

```text
nova_drl_technician_console_v1_9_2.py
test_nova_drl_technician_console_v1_9_2.py
```

Repo `bin/`:

```text
nova-drl-tech
```

Then:

```bash
cd /opt/nova-drl
chmod +x bin/nova-drl-tech
python3 test_nova_drl_technician_console_v1_9_2.py
```

## Validation

Do not replace `nova-drl` yet.

Run:

```bash
bin/nova-drl-tech --search "MR-J2S-40A"
```

After the report prints, type:

```text
:docx
```

The DOCX should appear directly in:

```text
/mnt/drl-reports
```

and therefore in Windows at:

```text
\\192.168.86.25\Public\NOVA_REPORTS
```

After editing/saving in Word, type `:review`.

If this workflow feels right at the bench, the Windows NOVA shortcut can later
be changed to launch `nova-drl-tech`. The underlying `nova-drl` command remains
available as the frozen v1.5.16 engine.
