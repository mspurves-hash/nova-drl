#!/usr/bin/env python3
"""
Nova DRL File Index v1.4.2

Persistent, read-only metadata index for the mounted DRL share.

Design goals:
- One index for the entire DRL share, not per-model indexes.
- Everything-style AND token search across the FULL relative path, so a query
  such as "RCL1A LINE" can match RCL1A in a parent folder and LINE in the
  Line Card filename.
- Initial full crawl once, then safe refreshes that rewrite only new/changed
  rows and remove vanished rows only after an error-free completed scan.
- No file contents are read and no source files are modified.
- No whole-file hashing during indexing. Analysis pipelines may hash selected
  files later when they need evidence identity/deduplication.

Python standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

VERSION = "1.4.2"
SCHEMA_VERSION = 1
DEFAULT_SHARE_ROOT = "/mnt/drl"
DEFAULT_DB = "/opt/nova-drl/index/drl_file_index.sqlite"
BATCH_SIZE = 1000
ERROR_SAMPLE_LIMIT = 25

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff", ".webp"
}
DOC_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt",
    ".rtf", ".csv", ".md"
}

# DRL log format from the project: YYMMDD###.
_LOG_RE = re.compile(r"(?<!\d)(\d{9})(?!\d)")


@dataclass(frozen=True)
class FileMeta:
    relative_path: str
    filename: str
    parent_path: str
    extension: str
    size: int
    mtime_ns: int
    detected_log: Optional[str]
    search_text: str
    file_kind: str


@dataclass
class ScanStats:
    seen: int = 0
    added: int = 0
    changed: int = 0
    unchanged: int = 0
    deleted: int = 0
    errors: int = 0
    error_samples: List[str] = None

    def __post_init__(self) -> None:
        if self.error_samples is None:
            self.error_samples = []


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def human_bytes(n: Optional[int]) -> str:
    if n is None:
        return "n/a"
    value = float(n)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    for unit in units:
        if abs(value) < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{value:.1f} PB"


def normalize_path_text(value: str) -> str:
    # Casefold gives more predictable case-insensitive matching than lower().
    # Convert Windows-style separators too because historical filenames/paths
    # can include copied path-like text.
    return value.replace("\\", "/").casefold()


def detect_drl_log(text: str) -> Optional[str]:
    """Return the first plausible YYMMDD### DRL log number in text."""
    for match in _LOG_RE.finditer(text):
        token = match.group(1)
        yy, mm, dd = int(token[0:2]), int(token[2:4]), int(token[4:6])
        # DRL's corpus is modern. Date validation prevents arbitrary 9-digit
        # serial numbers from being labeled as logs.
        try:
            dt.date(2000 + yy, mm, dd)
        except ValueError:
            continue
        seq = int(token[6:9])
        if seq <= 0:
            continue
        return token
    return None


def classify_file(extension: str) -> str:
    ext = extension.casefold()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in DOC_EXTENSIONS:
        return "document"
    if ext in {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz"}:
        return "archive"
    return "other"


def connect_db(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA busy_timeout=30000")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            relative_path TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            filename_search TEXT NOT NULL,
            parent_path TEXT NOT NULL,
            parent_search TEXT NOT NULL,
            extension TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            detected_log TEXT,
            search_text TEXT NOT NULL,
            file_kind TEXT NOT NULL,
            first_seen_scan INTEGER NOT NULL,
            last_changed_scan INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_files_log ON files(detected_log);
        CREATE INDEX IF NOT EXISTS idx_files_ext ON files(extension);
        CREATE INDEX IF NOT EXISTS idx_files_kind ON files(file_kind);
        CREATE INDEX IF NOT EXISTS idx_files_filename ON files(filename_search);

        CREATE TABLE IF NOT EXISTS scan_runs (
            scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            share_root TEXT NOT NULL,
            status TEXT NOT NULL,
            seen_files INTEGER NOT NULL DEFAULT 0,
            added_files INTEGER NOT NULL DEFAULT 0,
            changed_files INTEGER NOT NULL DEFAULT 0,
            unchanged_files INTEGER NOT NULL DEFAULT 0,
            deleted_files INTEGER NOT NULL DEFAULT 0,
            errors INTEGER NOT NULL DEFAULT 0,
            error_samples_json TEXT NOT NULL DEFAULT '[]',
            duration_seconds REAL
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('software_version',?)",
        (VERSION,),
    )
    conn.commit()


def meta_get(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def meta_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))


def normalize_root(root: str) -> str:
    return str(Path(root).expanduser().resolve())


def check_root(root: str) -> Tuple[bool, str]:
    p = Path(root)
    if not p.exists():
        return False, "NOT FOUND"
    if not p.is_dir():
        return False, "NOT A DIRECTORY"
    if not os.access(str(p), os.R_OK | os.X_OK):
        return False, "NOT READABLE"
    return True, "FOUND"


def bind_root(conn: sqlite3.Connection, root: str, *, allow_new: bool = True) -> None:
    normalized = normalize_root(root)
    bound = meta_get(conn, "share_root")
    if bound and normalize_root(bound) != normalized:
        raise RuntimeError(
            f"Index is bound to share root {bound!r}, not {normalized!r}. "
            "Use a different --db or rebuild the index intentionally."
        )
    if not bound and allow_new:
        meta_set(conn, "share_root", normalized)
        conn.commit()


def filemeta_from_entry(entry: os.DirEntry, root: str) -> FileMeta:
    st = entry.stat(follow_symlinks=False)
    rel = os.path.relpath(entry.path, root).replace(os.sep, "/")
    parent = os.path.dirname(rel).replace(os.sep, "/")
    if parent == ".":
        parent = ""
    filename = entry.name
    ext = Path(filename).suffix.casefold()
    return FileMeta(
        relative_path=rel,
        filename=filename,
        parent_path=parent,
        extension=ext,
        size=int(st.st_size),
        mtime_ns=int(st.st_mtime_ns),
        detected_log=detect_drl_log(rel),
        search_text=normalize_path_text(rel),
        file_kind=classify_file(ext),
    )


def add_error(stats: ScanStats, message: str) -> None:
    stats.errors += 1
    if len(stats.error_samples) < ERROR_SAMPLE_LIMIT:
        stats.error_samples.append(message)


def walk_files(root: str, stats: ScanStats) -> Iterator[FileMeta]:
    """Iterative scandir walk. Source share is never modified."""
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            yield filemeta_from_entry(entry, root)
                        # Symlinks and special files are intentionally ignored.
                    except OSError as exc:
                        add_error(stats, f"{entry.path}: {exc}")
        except OSError as exc:
            add_error(stats, f"{current}: {exc}")


def load_existing(conn: sqlite3.Connection) -> Dict[str, Tuple[int, int, Optional[str], str, str, str]]:
    rows = conn.execute(
        "SELECT relative_path,size,mtime_ns,detected_log,filename,parent_path,extension FROM files"
    )
    return {
        row["relative_path"]: (
            int(row["size"]),
            int(row["mtime_ns"]),
            row["detected_log"],
            row["filename"],
            row["parent_path"],
            row["extension"],
        )
        for row in rows
    }


def flush_upserts(conn: sqlite3.Connection, rows: List[Tuple]) -> None:
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO files(
            relative_path,filename,filename_search,parent_path,parent_search,
            extension,size,mtime_ns,detected_log,search_text,file_kind,
            first_seen_scan,last_changed_scan,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(relative_path) DO UPDATE SET
            filename=excluded.filename,
            filename_search=excluded.filename_search,
            parent_path=excluded.parent_path,
            parent_search=excluded.parent_search,
            extension=excluded.extension,
            size=excluded.size,
            mtime_ns=excluded.mtime_ns,
            detected_log=excluded.detected_log,
            search_text=excluded.search_text,
            file_kind=excluded.file_kind,
            last_changed_scan=excluded.last_changed_scan,
            updated_at=excluded.updated_at
        """,
        rows,
    )
    conn.commit()
    rows.clear()


def begin_scan(conn: sqlite3.Connection, root: str) -> int:
    cur = conn.execute(
        "INSERT INTO scan_runs(started_at,share_root,status) VALUES(?,?,?)",
        (utc_now(), root, "running"),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_scan(
    conn: sqlite3.Connection,
    scan_id: int,
    status: str,
    stats: ScanStats,
    duration: float,
) -> None:
    conn.execute(
        """
        UPDATE scan_runs SET
            completed_at=?, status=?, seen_files=?, added_files=?, changed_files=?,
            unchanged_files=?, deleted_files=?, errors=?, error_samples_json=?,
            duration_seconds=?
        WHERE scan_id=?
        """,
        (
            utc_now(), status, stats.seen, stats.added, stats.changed,
            stats.unchanged, stats.deleted, stats.errors,
            json.dumps(stats.error_samples, ensure_ascii=False),
            duration, scan_id,
        ),
    )
    if status == "completed":
        meta_set(conn, "last_completed_scan", str(scan_id))
        meta_set(conn, "last_completed_scan_at", utc_now())
    meta_set(conn, "last_scan_status", status)
    meta_set(conn, "last_scan_id", str(scan_id))
    conn.commit()


def run_scan(conn: sqlite3.Connection, root: str, *, progress_every: int = 10000) -> ScanStats:
    ok, why = check_root(root)
    if not ok:
        raise RuntimeError(f"Share root {root!r}: {why}")
    bind_root(conn, root)
    root = normalize_root(root)

    existing = load_existing(conn)
    existing_paths: Set[str] = set(existing)
    seen_paths: Set[str] = set()
    stats = ScanStats()
    scan_id = begin_scan(conn, root)
    started = time.monotonic()
    pending: List[Tuple] = []

    try:
        for meta in walk_files(root, stats):
            stats.seen += 1
            seen_paths.add(meta.relative_path)
            old = existing.get(meta.relative_path)
            changed = (
                old is None
                or old[0] != meta.size
                or old[1] != meta.mtime_ns
                or old[2] != meta.detected_log
                or old[3] != meta.filename
                or old[4] != meta.parent_path
                or old[5] != meta.extension
            )
            if old is None:
                stats.added += 1
            elif changed:
                stats.changed += 1
            else:
                stats.unchanged += 1

            if changed:
                now = utc_now()
                pending.append(
                    (
                        meta.relative_path,
                        meta.filename,
                        normalize_path_text(meta.filename),
                        meta.parent_path,
                        normalize_path_text(meta.parent_path),
                        meta.extension,
                        meta.size,
                        meta.mtime_ns,
                        meta.detected_log,
                        meta.search_text,
                        meta.file_kind,
                        scan_id,
                        scan_id,
                        now,
                    )
                )
                if len(pending) >= BATCH_SIZE:
                    flush_upserts(conn, pending)

            if progress_every and stats.seen % progress_every == 0:
                elapsed = time.monotonic() - started
                rate = stats.seen / elapsed if elapsed > 0 else 0
                print(
                    f"[scan] seen={stats.seen:,} added={stats.added:,} "
                    f"changed={stats.changed:,} errors={stats.errors:,} rate={rate:,.0f}/s",
                    flush=True,
                )

        flush_upserts(conn, pending)

        # Safety rule: if any directory/file metadata errors occurred, do not
        # infer deletion from absence. A transient NAS permission/network issue
        # must never purge valid index entries.
        if stats.errors == 0:
            missing = sorted(existing_paths - seen_paths)
            stats.deleted = len(missing)
            if missing:
                for i in range(0, len(missing), BATCH_SIZE):
                    conn.executemany(
                        "DELETE FROM files WHERE relative_path=?",
                        [(p,) for p in missing[i:i + BATCH_SIZE]],
                    )
                    conn.commit()
            status = "completed"
        else:
            status = "completed_with_errors"

        duration = time.monotonic() - started
        finish_scan(conn, scan_id, status, stats, duration)
        return stats

    except KeyboardInterrupt:
        flush_upserts(conn, pending)
        duration = time.monotonic() - started
        finish_scan(conn, scan_id, "interrupted", stats, duration)
        raise
    except Exception:
        flush_upserts(conn, pending)
        duration = time.monotonic() - started
        finish_scan(conn, scan_id, "failed", stats, duration)
        raise


def tokenize_query(query: str) -> List[str]:
    """Everything-like whitespace/quote tokenization; tokens are ANDed."""
    try:
        tokens = shlex.split(query)
    except ValueError:
        tokens = query.split()
    return [normalize_path_text(t.strip()) for t in tokens if t.strip()]


def build_search_where(
    query: str,
    extensions: Optional[Sequence[str]] = None,
    kinds: Optional[Sequence[str]] = None,
) -> Tuple[str, List[str]]:
    tokens = tokenize_query(query)
    if not tokens:
        raise ValueError("Search query must contain at least one token")
    clauses: List[str] = []
    params: List[str] = []
    for token in tokens:
        # Default semantics deliberately search the full relative path. This is
        # what lets `RCL1A LINE` match RCL1A in a parent repair folder and LINE
        # in `... Line Card Original.jpg`.
        clauses.append("instr(search_text, ?) > 0")
        params.append(token)

    if extensions:
        cleaned = []
        for ext in extensions:
            ext = ext.casefold().strip()
            if ext and not ext.startswith("."):
                ext = "." + ext
            if ext:
                cleaned.append(ext)
        if cleaned:
            clauses.append("extension IN (%s)" % ",".join("?" * len(cleaned)))
            params.extend(cleaned)

    if kinds:
        cleaned_kinds = [k.casefold().strip() for k in kinds if k.strip()]
        if cleaned_kinds:
            clauses.append("file_kind IN (%s)" % ",".join("?" * len(cleaned_kinds)))
            params.extend(cleaned_kinds)

    return " AND ".join(clauses), params


def search_index(
    conn: sqlite3.Connection,
    query: str,
    *,
    extensions: Optional[Sequence[str]] = None,
    kinds: Optional[Sequence[str]] = None,
    limit: Optional[int] = 200,
) -> Tuple[int, List[sqlite3.Row]]:
    where, params = build_search_where(query, extensions=extensions, kinds=kinds)
    total = int(conn.execute(f"SELECT COUNT(*) FROM files WHERE {where}", params).fetchone()[0])
    sql = (
        "SELECT relative_path,filename,parent_path,extension,size,mtime_ns,"
        "detected_log,file_kind FROM files WHERE " + where +
        " ORDER BY search_text"
    )
    if limit is not None:
        sql += " LIMIT ?"
        rows = list(conn.execute(sql, params + [int(limit)]))
    else:
        rows = list(conn.execute(sql, params))
    return total, rows


def db_counts(conn: sqlite3.Connection) -> Tuple[int, int, int]:
    row = conn.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes, "
        "SUM(CASE WHEN detected_log IS NOT NULL THEN 1 ELSE 0 END) AS with_logs FROM files"
    ).fetchone()
    return int(row["n"]), int(row["bytes"]), int(row["with_logs"] or 0)


def latest_scan(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM scan_runs ORDER BY scan_id DESC LIMIT 1").fetchone()


def command_status(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    root = normalize_root(args.share_root)
    ok, root_state = check_root(root)
    print(f"# Nova DRL File Index Status v{VERSION}")
    print(f"Share root:       {root_state} | {root}")
    print(f"Index DB:         {'FOUND' if db_path.exists() else 'NOT BUILT'} | {db_path}")
    print("Source writes:    NONE | indexer is read-only to share")
    print("Content reads:    NONE | metadata/path index only")
    print("Whole-file hash:  OFF | deferred to analysis pipelines")
    if not db_path.exists():
        print("Indexed files:    0")
        print("Last scan:        NONE")
        return 0 if ok else 2

    conn = connect_db(str(db_path))
    bound = meta_get(conn, "share_root")
    n, total_bytes, with_logs = db_counts(conn)
    scan = latest_scan(conn)
    print(f"Bound share root: {bound or 'UNBOUND'}")
    print(f"Indexed files:    {n:,}")
    print(f"Indexed bytes:    {human_bytes(total_bytes)}")
    print(f"Files w/ DRL log: {with_logs:,}")
    if scan:
        dur = scan["duration_seconds"]
        dur_text = f"{dur:.1f}s" if dur is not None else "n/a"
        print(
            f"Last scan:        id={scan['scan_id']} status={scan['status']} "
            f"seen={scan['seen_files']:,} +{scan['added_files']:,} "
            f"~{scan['changed_files']:,} -{scan['deleted_files']:,} "
            f"errors={scan['errors']:,} duration={dur_text}"
        )
    else:
        print("Last scan:        NONE")
    if bound and normalize_root(bound) != root:
        print("WARNING: requested --share-root differs from DB binding")
        return 2
    return 0 if ok else 2


def command_scan(args: argparse.Namespace, *, is_build: bool) -> int:
    root = normalize_root(args.share_root)
    ok, root_state = check_root(root)
    if not ok:
        print(f"ERROR: share root {root}: {root_state}", file=sys.stderr)
        return 2

    db_path = Path(args.db)
    if is_build and db_path.exists() and not args.rebuild:
        conn0 = connect_db(str(db_path))
        n, _, _ = db_counts(conn0)
        if n > 0:
            print(
                f"ERROR: index already contains {n:,} files. Use refresh, or build --rebuild intentionally.",
                file=sys.stderr,
            )
            return 2
    if is_build and args.rebuild and db_path.exists():
        # Remove DB and SQLite sidecars only; never touch source data.
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                p.unlink()

    conn = connect_db(str(db_path))
    try:
        stats = run_scan(conn, root, progress_every=args.progress_every)
    except KeyboardInterrupt:
        print("\nScan interrupted. Partial additions/changes are safe; deletions were not inferred.")
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    scan = latest_scan(conn)
    status = scan["status"] if scan else "unknown"
    duration = scan["duration_seconds"] if scan else None
    print(f"# Nova DRL File Index v{VERSION} — {'BUILD' if is_build else 'REFRESH'} COMPLETE")
    print(f"Share root:  {root}")
    print(f"Index DB:    {db_path}")
    print(f"Status:      {status}")
    print(f"Seen:        {stats.seen:,}")
    print(f"Added:       {stats.added:,}")
    print(f"Changed:     {stats.changed:,}")
    print(f"Unchanged:   {stats.unchanged:,}")
    print(f"Deleted:     {stats.deleted:,}")
    print(f"Errors:      {stats.errors:,}")
    if duration is not None:
        print(f"Duration:    {duration:.1f}s")
    if stats.errors:
        print("Deletion policy: SKIPPED because scan had errors")
        print("Error samples:")
        for err in stats.error_samples:
            print(f"  - {err}")
        return 1
    print("Deletion policy: applied after clean completed scan")
    return 0


def command_search(args: argparse.Namespace) -> int:
    if not Path(args.db).exists():
        print(f"ERROR: index DB not built: {args.db}", file=sys.stderr)
        return 2
    conn = connect_db(args.db)
    root = meta_get(conn, "share_root") or normalize_root(args.share_root)
    extensions = args.ext or None
    kinds = args.kind or None
    limit = None if args.all else args.limit
    try:
        total, rows = search_index(
            conn,
            args.query,
            extensions=extensions,
            kinds=kinds,
            limit=limit,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        payload = {
            "version": VERSION,
            "query": args.query,
            "match_scope": "full_relative_path",
            "total_matches": total,
            "returned": len(rows),
            "share_root": root,
            "results": [],
        }
        for row in rows:
            full = str(Path(root) / Path(row["relative_path"]))
            payload["results"].append({
                "path": full,
                "relative_path": row["relative_path"],
                "filename": row["filename"],
                "parent_path": row["parent_path"],
                "extension": row["extension"],
                "size": row["size"],
                "mtime_ns": row["mtime_ns"],
                "drl_log": row["detected_log"],
                "file_kind": row["file_kind"],
            })
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f'# Nova DRL Index Search v{VERSION} | query="{args.query}"')
    print("Match scope: full indexed path | tokens=AND | case-insensitive")
    print(f"Matches:     {total:,}")
    if args.count_only:
        return 0
    for row in rows:
        full = str(Path(root) / Path(row["relative_path"]))
        log = f" | log={row['detected_log']}" if row["detected_log"] else ""
        print(f"{full}{log}")
    if len(rows) < total:
        print(f"-- showing {len(rows):,} of {total:,}; use --all or increase --limit --")
    return 0


def command_stats(args: argparse.Namespace) -> int:
    if not Path(args.db).exists():
        print(f"ERROR: index DB not built: {args.db}", file=sys.stderr)
        return 2
    conn = connect_db(args.db)
    n, total_bytes, with_logs = db_counts(conn)
    print(f"# Nova DRL File Index Stats v{VERSION}")
    print(f"Files:       {n:,}")
    print(f"Bytes:       {human_bytes(total_bytes)}")
    print(f"With DRL log:{with_logs:>10,}")
    print("\nTop extensions:")
    for row in conn.execute(
        "SELECT extension,COUNT(*) AS n FROM files GROUP BY extension ORDER BY n DESC LIMIT ?",
        (args.top,),
    ):
        label = row["extension"] or "[no extension]"
        print(f"  {label:16s} {row['n']:>10,}")
    print("\nFile kinds:")
    for row in conn.execute(
        "SELECT file_kind,COUNT(*) AS n FROM files GROUP BY file_kind ORDER BY n DESC"
    ):
        print(f"  {row['file_kind']:16s} {row['n']:>10,}")
    return 0


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=f"Nova DRL persistent share file index v{VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Everything-style example:\n"
            "  python3 tools/nova_drl_file_index_v1_4_2.py search \"RCL1A LINE\"\n\n"
            "Generic search terms are ANDed across the full indexed relative path,\n"
            "so one term may match a folder name and another the filename."
        ),
    )
    p.add_argument("--db", default=DEFAULT_DB, help=f"SQLite index path (default: {DEFAULT_DB})")
    p.add_argument("--share-root", default=DEFAULT_SHARE_ROOT, help=f"DRL share root (default: {DEFAULT_SHARE_ROOT})")

    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show share/index status")

    b = sub.add_parser("build", help="initial full crawl")
    b.add_argument("--rebuild", action="store_true", help="delete/recreate only the local index DB, never source files")
    b.add_argument("--progress-every", type=int, default=10000, help="progress line every N files; 0 disables")

    r = sub.add_parser("refresh", help="rescan metadata and apply only additions/changes/deletions")
    r.add_argument("--progress-every", type=int, default=10000, help="progress line every N files; 0 disables")

    s = sub.add_parser("search", help="Everything-style AND token search across full indexed path")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--all", action="store_true", help="return all matching rows")
    s.add_argument("--count-only", action="store_true")
    s.add_argument("--ext", action="append", help="restrict extension; repeatable, e.g. --ext jpg --ext jpeg")
    s.add_argument("--kind", action="append", choices=["image", "pdf", "document", "archive", "other"])
    s.add_argument("--json", action="store_true")

    st = sub.add_parser("stats", help="summary counts by extension/type")
    st.add_argument("--top", type=int, default=25)

    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = make_parser().parse_args(argv)
    if args.command == "status":
        return command_status(args)
    if args.command == "build":
        return command_scan(args, is_build=True)
    if args.command == "refresh":
        return command_scan(args, is_build=False)
    if args.command == "search":
        return command_search(args)
    if args.command == "stats":
        return command_stats(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
