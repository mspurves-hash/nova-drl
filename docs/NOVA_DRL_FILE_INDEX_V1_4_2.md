# Nova DRL v1.4.2 — File Index

## Purpose

v1.4.2 creates one persistent local index for the entire mounted DRL share. It is the file-discovery layer under future Nova DRL analysis pipelines.

The goal is to mimic the useful behavior of Everything: build the file catalog once, keep it refreshed, and answer queries such as `RCL1A LINE` immediately without recursively running a fresh NAS search for every analysis.

## Important search behavior

Search terms are case-insensitive, ANDed, and matched against the **full indexed relative path**, not just the basename.

That is intentional. A real Line Card may be named:

```text
130130006 Line Card Original.jpg
```

while `RCL1A` exists only in a parent repair folder. Therefore:

```text
RCL1A LINE
```

can match `RCL1A` in the directory path and `LINE` in the filename, the same way the production discovery workflow needs to behave.

## What is indexed

For every regular file:

- relative path
- filename
- parent path
- extension
- file size
- modified timestamp (nanoseconds when provided by the filesystem)
- detected DRL log number when a plausible `YYMMDD###` token exists
- lightweight file-kind classification
- normalized full-path search text

The index does **not** read file contents and does **not** SHA256 every file. Selected downstream evidence pipelines can hash or open files when needed.

## Database

Default:

```text
/opt/nova-drl/index/drl_file_index.sqlite
```

SQLite is local to the Nova server. The `/mnt/drl` source remains read-only.

## Initial build

```bash
python3 tools/nova_drl_file_index_v1_4_2.py status
python3 tools/nova_drl_file_index_v1_4_2.py build
```

The initial build metadata-crawls the entire `/mnt/drl` share.

## Search

```bash
python3 tools/nova_drl_file_index_v1_4_2.py search "RCL1A LINE"
```

Count only:

```bash
python3 tools/nova_drl_file_index_v1_4_2.py search "RCL1A LINE" --count-only
```

All matching rows:

```bash
python3 tools/nova_drl_file_index_v1_4_2.py search "RCL1A LINE" --all
```

JSON for another Nova pipeline:

```bash
python3 tools/nova_drl_file_index_v1_4_2.py search "RCL1A LINE" --all --json
```

Image-only example:

```bash
python3 tools/nova_drl_file_index_v1_4_2.py search "RCL1A LINE" --kind image --all
```

## Refresh

```bash
python3 tools/nova_drl_file_index_v1_4_2.py refresh
```

Refresh walks share metadata again but writes only new/changed records. Rows for vanished paths are deleted **only after a completely error-free scan**. If a NAS directory is temporarily unavailable or returns an error, stale deletion is skipped to protect the index.

A remote SMB/NFS mount cannot be assumed to expose a reliable Windows/NTFS change journal to Ubuntu. For this reason, periodic metadata refresh is the reliable v1.4.2 mechanism. A systemd timer example is included, but should be enabled only after measuring the first full scan/refresh time on the actual DRL share.

## Relationship to RCL1A

RCL1A analysis should no longer own NAS traversal. A later RCL1A version should ask this index for `RCL1A LINE`, receive the actual Line Card image paths, then perform vision/reasoning only on those selected files.

The combined 167-page RCL1A PDF remains a benchmark artifact and is not part of this file-index architecture.

## Safety

- `/mnt/drl`: read-only from the indexer's perspective.
- File contents: not opened by the indexer.
- Whole-file hashing: off.
- Index DB: disposable/rebuildable from the share.
- Accepted facts: 0.
- Qdrant: off.
