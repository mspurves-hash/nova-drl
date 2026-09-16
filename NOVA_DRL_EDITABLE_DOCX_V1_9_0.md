# NOVA DRL Editable DOCX Report v1.9.0

## Purpose

The DOCX becomes the primary technician report. Frozen `nova-drl` v1.5.16 remains the only knowledge source.

```text
DRL Part #
  -> frozen nova-drl v1.5.16 summary
  -> editable DOCX technician report
  -> optional edit review
  -> optional UNREVIEWED correction candidate
```

PDF remains optional for archival/final copies.

## Commands

Generate:

```bash
nova-drl-docx --search "MR-J2S-40A"
```

Default output:

```text
/opt/nova-drl/reports/NOVA_MR-J2S-40A_Repair_Reference.docx
```

Review after a technician edits the DOCX:

```bash
nova-drl-docx-review /opt/nova-drl/reports/NOVA_MR-J2S-40A_Repair_Reference.docx
```

## What can be edited freely

`Technician Notes / Current Repair` is intentionally excluded from knowledge review.
Formatting-only changes are ignored.

## What triggers the review screen

Changes in:
- equipment family / DRL Part # metadata
- Parts Replaced
- Reported Failures
- Recurring Repair Actions

The screen shows additions, removals, and count changes.

## Safety / evidence policy

An edited DOCX NEVER changes NOVA automatically.

Review choices:
1. Keep the edit in the report only.
2. Export an `unreviewed_candidate_only` JSON correction candidate.
3. Exit.

Candidate export is not accepted knowledge and does not modify the corpus, database, Qdrant, or accepted facts.

## Architecture choices

- Standard-library-only DOCX generation; no `python-docx` dependency on the NOVA server.
- The hidden baseline travels inside the DOCX.
- The report generator calls the existing frozen `nova-drl` command and formats its exact output. There is no second knowledge/extraction pipeline.
- Parts remain ranked most-used to least-used, including low-frequency exception history already surfaced by v1.5.16.

## Install

Place:

```text
nova_drl_docx_report_v1_9_0.py
nova_drl_docx_review_v1_9_0.py
bin/nova-drl-docx
bin/nova-drl-docx-review
```

Then:

```bash
cd /opt/nova-drl
chmod +x bin/nova-drl-docx bin/nova-drl-docx-review
git update-index --chmod=+x bin/nova-drl-docx bin/nova-drl-docx-review
```

Optional global links, matching the existing NOVA commands:

```bash
sudo ln -sf /opt/nova-drl/bin/nova-drl-docx /usr/local/bin/nova-drl-docx
sudo ln -sf /opt/nova-drl/bin/nova-drl-docx-review /usr/local/bin/nova-drl-docx-review
hash -r
```

## Initial validation

```bash
nova-drl-docx --search "MR-J2S-40A"
nova-drl-docx-review /opt/nova-drl/reports/NOVA_MR-J2S-40A_Repair_Reference.docx
```

The first review should say `NO KNOWLEDGE-AREA EDITS DETECTED`.
