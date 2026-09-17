#!/usr/bin/env python3
"""
NOVA DRL Identity Catalog Audit v2.0.0-a1

Read-only first milestone for the new Part-Number-First technician search.

This program does NOT create or approve a canonical identity catalog. It audits:
  * the currently installed NOVA launcher targets;
  * the existing SQLite product-family identity candidates;
  * exact equipment-family labels in the frozen v1.6.0 lossless corpus;
  * short/OEM/category identities, normalization collisions, unsupported
    candidates, and multi-family mappings;
  * a small 80/20 review queue plus critical DRL benchmark Part # values.

Existing index identities are candidates only, never authority. Frozen inputs are
opened read-only. No database/corpus/source writes. No Qdrant. No accepted facts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


VERSION = "2.0.0-a1"
SCHEMA = "nova-drl-identity-catalog-audit-v2"

DEFAULT_DB = Path("/opt/nova-drl/index/drl_knowledge_index.sqlite")
DEFAULT_LOSSLESS_EVENTS = Path(
    "/opt/nova-drl/output/drl_global_lossless_corpus_v1_6_0/"
    "repair_events_lossless_v1_6_0.jsonl"
)
DEFAULT_LAUNCHERS = (
    Path("/usr/local/bin/nova-drl"),
    Path("/opt/nova-drl/bin/nova-drl"),
    Path("/opt/nova-drl/bin/nova-drl-tech"),
)
DEFAULT_BENCHMARKS = (
    "BM23995",
    "XU-RCM7231",
    "MR-J2S-40A",
    "9800106841",
    "ESC-200",
    "RCL1A-1D-W3",
    "GB7",
    "GB7S",
    "GB4S",
    "OFH-4000Q",
    "EG-300B",
    "VHP",
    "3NX550B",
    "3NS511",
    "3NS411",
    "TS220",
)

PN_COLUMN_CANDIDATES = (
    "base_part_number",
    "drl_part_number",
    "part_number",
    "canonical_part_number",
)
FAMILY_COLUMN_CANDIDATES = (
    "equipment_family",
    "display_family",
    "canonical_family",
    "product_family",
    "family",
    "title",
)
FAMILY_JSON_COLUMN_CANDIDATES = (
    "families_json",
    "family_variants_json",
    "model_variants_json",
    "equipment_families_json",
    "variants_json",
)
COUNT_COLUMN_CANDIDATES = (
    "repair_event_count",
    "event_count",
    "indexed_repair_events",
    "repairs",
)


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", clean(value)).upper()
    return re.sub(r"[^A-Z0-9]+", "", text)


def int_value(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def parse_json(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return value
    if value in (None, ""):
        return None
    try:
        return json.loads(str(value))
    except Exception:
        return None


def sha256_file(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_metadata(path: Path, include_sha256: bool = False) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
    }
    if not path.exists():
        return result
    stat = path.stat()
    result.update(
        {
            "size_bytes": stat.st_size,
            "modified_at_utc": datetime.fromtimestamp(
                stat.st_mtime, timezone.utc
            ).replace(microsecond=0).isoformat(),
        }
    )
    if include_sha256:
        result["sha256"] = sha256_file(path)
    return result


def token_pairs(value: Any) -> List[Tuple[str, str]]:
    """Observed family tokens only; these are evidence, never PN extraction."""
    text = clean(value)
    out: List[Tuple[str, str]] = []
    for match in re.finditer(r"[A-Za-z0-9][A-Za-z0-9._/+-]*", text):
        raw = match.group(0).strip("._/+-")
        key = norm(raw)
        if key:
            out.append((raw, key))
    return out


def family_values(row: Mapping[str, Any]) -> List[str]:
    found: List[str] = []
    seen: Set[str] = set()

    for column in FAMILY_COLUMN_CANDIDATES:
        family = clean(row.get(column))
        if family and family.casefold() not in seen:
            seen.add(family.casefold())
            found.append(family)

    for column in FAMILY_JSON_COLUMN_CANDIDATES:
        parsed = parse_json(row.get(column))
        if not isinstance(parsed, list):
            continue
        for item in parsed:
            if isinstance(item, str):
                family = clean(item)
            elif isinstance(item, dict):
                family = clean(
                    item.get("equipment_family")
                    or item.get("display_family")
                    or item.get("family")
                    or item.get("value")
                )
            else:
                family = ""
            if family and family.casefold() not in seen:
                seen.add(family.casefold())
                found.append(family)
    return found


def inspect_launcher(path: Path) -> Dict[str, Any]:
    result = file_metadata(path, include_sha256=False)
    result.update(
        {
            "is_symlink": path.is_symlink(),
            "resolved_path": None,
            "version_mentions": [],
            "script_targets": [],
            "read_error": None,
        }
    )
    if not path.exists() and not path.is_symlink():
        return result

    try:
        result["resolved_path"] = str(path.resolve(strict=False))
    except Exception as exc:
        result["read_error"] = f"resolve failed: {exc}"

    try:
        target = path.resolve(strict=True)
        if target.is_file() and target.stat().st_size <= 2 * 1024 * 1024:
            text = target.read_text(encoding="utf-8", errors="replace")
            versions = set(
                re.findall(r"(?i)v(?:1|2)[.-]\d+(?:[.-]\d+){1,3}", text)
            )
            versions.update(
                match.replace("_", ".")
                for match in re.findall(r"(?i)v[12]_\d+(?:_\d+){1,3}", text)
            )
            targets = set(
                re.findall(
                    r"/opt/nova-drl/[A-Za-z0-9_./+-]+(?:\.py|/nova-drl[A-Za-z0-9_.-]*)",
                    text,
                )
            )
            result["version_mentions"] = sorted(versions, key=str.casefold)
            result["script_targets"] = sorted(targets, key=str.casefold)
            result["sha256"] = sha256_file(target)
    except Exception as exc:
        result["read_error"] = str(exc)
    return result


def audit_launchers(paths: Sequence[Path]) -> Dict[str, Any]:
    rows = [inspect_launcher(path) for path in paths]
    existing = [row for row in rows if row.get("exists") or row.get("is_symlink")]
    return {
        "paths": rows,
        "existing_count": len(existing),
        "status": "observed" if existing else "no_launcher_found",
    }


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def sqlite_table_names(conn: sqlite3.Connection) -> List[str]:
    return sorted(
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    )


def sqlite_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [
        str(row[1])
        for row in conn.execute(
            f"PRAGMA table_info({_quoted_identifier(table)})"
        ).fetchall()
    ]


def first_existing(candidates: Sequence[str], columns: Sequence[str]) -> Optional[str]:
    available = set(columns)
    return next((name for name in candidates if name in available), None)


def audit_sqlite(path: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "file": file_metadata(path, include_sha256=False),
        "open_mode": "read_only",
        "status": "missing",
        "tables": [],
        "table_counts": {},
        "product_families_columns": [],
        "selected_part_number_column": None,
        "selected_family_columns": [],
        "selected_count_column": None,
        "identity_candidate_rows": [],
        "error": None,
    }
    if not path.exists():
        return result

    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except Exception as exc:
        result["status"] = "unreadable"
        result["error"] = str(exc)
        return result

    try:
        tables = sqlite_table_names(conn)
        result["tables"] = tables
        for table in (
            "repair_events",
            "product_families",
            "product_parts",
            "replacement_mentions",
        ):
            if table in tables:
                try:
                    result["table_counts"][table] = int(
                        conn.execute(
                            f"SELECT COUNT(*) FROM {_quoted_identifier(table)}"
                        ).fetchone()[0]
                    )
                except Exception as exc:
                    result["table_counts"][table] = f"ERROR: {exc}"

        if "product_families" not in tables:
            result["status"] = "no_product_families_table"
            return result

        columns = sqlite_columns(conn, "product_families")
        result["product_families_columns"] = columns
        pn_column = first_existing(PN_COLUMN_CANDIDATES, columns)
        result["selected_part_number_column"] = pn_column
        if pn_column is None:
            result["status"] = "no_explicit_part_number_column"
            return result

        family_columns = [
            column
            for column in FAMILY_COLUMN_CANDIDATES + FAMILY_JSON_COLUMN_CANDIDATES
            if column in columns
        ]
        count_column = first_existing(COUNT_COLUMN_CANDIDATES, columns)
        result["selected_family_columns"] = family_columns
        result["selected_count_column"] = count_column

        selected = [pn_column] + family_columns
        if count_column:
            selected.append(count_column)
        selected = list(dict.fromkeys(selected))
        sql = ",".join(_quoted_identifier(name) for name in selected)
        rows = conn.execute(
            f"SELECT {sql} FROM {_quoted_identifier('product_families')}"
        ).fetchall()

        candidate_rows: List[Dict[str, Any]] = []
        for row0 in rows:
            row = dict(row0)
            part_number = clean(row.get(pn_column))
            if not part_number:
                continue
            candidate_rows.append(
                {
                    "part_number": part_number,
                    "families": family_values(row),
                    "repair_event_count": int_value(
                        row.get(count_column) if count_column else 0
                    ),
                }
            )
        result["identity_candidate_rows"] = candidate_rows
        result["status"] = "candidate_rows_available"
        return result
    except Exception as exc:
        result["status"] = "audit_error"
        result["error"] = str(exc)
        return result
    finally:
        conn.close()


def event_family_values(row: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    primary = clean(row.get("equipment_family"))
    if primary:
        seen.add(primary.casefold())
        out.append(primary)
    values = row.get("equipment_families")
    if isinstance(values, list):
        for value in values:
            family = clean(value)
            if family and family.casefold() not in seen:
                seen.add(family.casefold())
                out.append(family)
    return out


def audit_lossless_events(path: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "file": file_metadata(path, include_sha256=False),
        "status": "missing",
        "repair_event_rows": 0,
        "processed_fact_rows": 0,
        "missing_family_rows": 0,
        "invalid_json_rows": 0,
        "invalid_json_examples": [],
        "family_counts": {},
        "sha256": None,
    }
    if not path.exists():
        return result

    family_events: Dict[str, Set[str]] = defaultdict(set)
    family_row_counts: Counter[str] = Counter()
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, 1):
            digest.update(raw_line)
            if not raw_line.strip():
                continue
            result["repair_event_rows"] += 1
            try:
                row = json.loads(raw_line.decode("utf-8", errors="replace"))
            except Exception as exc:
                result["invalid_json_rows"] += 1
                if len(result["invalid_json_examples"]) < 5:
                    result["invalid_json_examples"].append(
                        {"line": line_number, "error": str(exc)}
                    )
                continue
            if not isinstance(row, dict):
                result["invalid_json_rows"] += 1
                continue
            if isinstance(row.get("facts"), dict):
                result["processed_fact_rows"] += 1
            families = event_family_values(row)
            if not families:
                result["missing_family_rows"] += 1
                continue
            event_id = clean(row.get("repair_event_id")) or f"line_{line_number}"
            for family in families:
                family_events[family].add(event_id)
                family_row_counts[family] += 1

    result["sha256"] = digest.hexdigest()
    result["family_counts"] = {
        family: len(event_ids) if event_ids else family_row_counts[family]
        for family, event_ids in sorted(
            family_events.items(), key=lambda item: item[0].casefold()
        )
    }
    result["status"] = (
        "complete_with_json_errors"
        if result["invalid_json_rows"]
        else "complete"
    )
    return result


def family_indexes(
    family_counts: Mapping[str, int],
) -> Dict[str, Any]:
    token_to_families: Dict[str, Set[str]] = defaultdict(set)
    category_to_families: Dict[str, Set[str]] = defaultdict(set)
    trailing_to_families: Dict[str, Set[str]] = defaultdict(set)

    for family in family_counts:
        pairs = token_pairs(family)
        for _, key in pairs:
            token_to_families[key].add(family)

        if " - " in family:
            category = clean(family.split(" - ", 1)[0])
            if category:
                category_to_families[norm(category)].add(family)
                for _, key in token_pairs(category):
                    category_to_families[key].add(family)

        token_keys = [key for _, key in pairs]
        if token_keys:
            trailing_to_families[token_keys[-1]].add(family)
        if len(token_keys) >= 2:
            trailing_to_families[token_keys[-2]].add(family)
            trailing_to_families[token_keys[-2] + token_keys[-1]].add(family)

    return {
        "token_to_families": token_to_families,
        "category_to_families": category_to_families,
        "trailing_to_families": trailing_to_families,
    }


def flag_identity(
    identity: Mapping[str, Any],
    indexes: Mapping[str, Any],
    family_counts: Mapping[str, int],
    oem_min_families: int,
) -> Dict[str, Any]:
    key = identity["identity_key"]
    raw_forms = identity["raw_part_numbers"]
    mapped_families = identity["mapped_families"]
    token_families = sorted(
        indexes["token_to_families"].get(key, set()),
        key=lambda family: (-family_counts.get(family, 0), family.casefold()),
    )
    category_families = indexes["category_to_families"].get(key, set())
    trailing_families = indexes["trailing_to_families"].get(key, set())

    flags: List[str] = []
    critical: List[str] = []
    review: List[str] = []

    if len(key) < 3:
        critical.append("identity_too_short")
    elif key.isalpha() and len(key) <= 3:
        review.append("short_alphabetic_identity")
    elif key.isalpha():
        review.append("alphabetic_only_identity")

    if len(raw_forms) > 1:
        critical.append("normalization_collision")
    if category_families:
        critical.append("category_like_identity")
    if len(trailing_families) >= oem_min_families:
        critical.append("trailing_oem_like_identity")
    if not token_families:
        critical.append("not_observed_as_exact_family_token_v1_6_0")
    if len(mapped_families) > 1:
        review.append("multi_family_mapping")

    if mapped_families:
        mapped_supported = [
            family
            for family in mapped_families
            if key in {token for _, token in token_pairs(family)}
        ]
        if not mapped_supported:
            critical.append("mapped_family_does_not_contain_exact_token")

    flags.extend(critical)
    flags.extend(review)

    event_weight = max(
        identity.get("legacy_repair_event_count", 0),
        max((family_counts.get(family, 0) for family in token_families), default=0),
    )
    score = int(round(10 * math.log10(event_weight + 1)))
    weights = {
        "normalization_collision": 120,
        "category_like_identity": 110,
        "trailing_oem_like_identity": 105,
        "identity_too_short": 100,
        "not_observed_as_exact_family_token_v1_6_0": 90,
        "mapped_family_does_not_contain_exact_token": 85,
        "multi_family_mapping": 45,
        "short_alphabetic_identity": 35,
        "alphabetic_only_identity": 20,
    }
    score += sum(weights.get(flag, 0) for flag in flags)

    if critical:
        disposition = "quarantine_candidate"
    elif review:
        disposition = "review_candidate"
    else:
        disposition = "clean_candidate_not_approved"

    result = dict(identity)
    result.update(
        {
            "observed_v1_6_0_families": token_families,
            "category_family_count": len(category_families),
            "trailing_oem_family_count": len(trailing_families),
            "observed_event_weight": event_weight,
            "flags": flags,
            "critical_flags": critical,
            "review_flags": review,
            "disposition": disposition,
            "review_score": score,
        }
    )
    return result


def audit_identities(
    sqlite_audit: Mapping[str, Any],
    lossless_audit: Mapping[str, Any],
    benchmarks: Sequence[str],
    oem_min_families: int,
) -> Dict[str, Any]:
    groups: Dict[str, Dict[str, Any]] = {}
    for row in sqlite_audit.get("identity_candidate_rows") or []:
        raw = clean(row.get("part_number"))
        key = norm(raw)
        if not key:
            continue
        group = groups.setdefault(
            key,
            {
                "identity_key": key,
                "raw_part_numbers": set(),
                "mapped_families": set(),
                "legacy_repair_event_count": 0,
                "legacy_row_count": 0,
            },
        )
        group["raw_part_numbers"].add(raw.upper())
        group["mapped_families"].update(row.get("families") or [])
        group["legacy_repair_event_count"] = max(
            group["legacy_repair_event_count"],
            int_value(row.get("repair_event_count")),
        )
        group["legacy_row_count"] += 1

    normalized_groups: List[Dict[str, Any]] = []
    for group0 in groups.values():
        group = dict(group0)
        group["raw_part_numbers"] = sorted(group["raw_part_numbers"], key=str.casefold)
        group["mapped_families"] = sorted(group["mapped_families"], key=str.casefold)
        normalized_groups.append(group)

    family_counts: Mapping[str, int] = lossless_audit.get("family_counts") or {}
    indexes = family_indexes(family_counts)
    flagged = [
        flag_identity(group, indexes, family_counts, oem_min_families)
        for group in normalized_groups
    ]
    flagged.sort(
        key=lambda row: (
            -row["review_score"],
            -row["observed_event_weight"],
            row["identity_key"],
        )
    )

    disposition_counts = Counter(row["disposition"] for row in flagged)
    flag_counts = Counter(flag for row in flagged for flag in row["flags"])
    collisions = [row for row in flagged if "normalization_collision" in row["flags"]]
    multi_family = [row for row in flagged if "multi_family_mapping" in row["flags"]]

    candidate_keys = set(groups)
    unmatched_families = []
    for family, count in family_counts.items():
        family_tokens = {key for _, key in token_pairs(family)}
        if not family_tokens.intersection(candidate_keys):
            unmatched_families.append(
                {"equipment_family": family, "repair_event_count": count}
            )
    unmatched_families.sort(
        key=lambda row: (-row["repair_event_count"], row["equipment_family"].casefold())
    )

    benchmark_rows = []
    by_key = {row["identity_key"]: row for row in flagged}
    for benchmark in benchmarks:
        key = norm(benchmark)
        candidate = by_key.get(key)
        observed = sorted(
            indexes["token_to_families"].get(key, set()),
            key=lambda family: (-family_counts.get(family, 0), family.casefold()),
        )
        if candidate is None and not observed:
            status = "absent"
        elif candidate is None:
            status = "observed_in_v1_6_0_but_missing_legacy_identity"
        elif candidate["critical_flags"]:
            status = "legacy_candidate_quarantined"
        elif candidate["review_flags"]:
            status = "legacy_candidate_needs_review"
        else:
            status = "clean_candidate_not_approved"
        benchmark_rows.append(
            {
                "part_number": benchmark,
                "identity_key": key,
                "status": status,
                "legacy_raw_forms": candidate["raw_part_numbers"] if candidate else [],
                "flags": candidate["flags"] if candidate else [],
                "observed_v1_6_0_families": observed,
            }
        )

    return {
        "legacy_identity_groups": len(flagged),
        "disposition_counts": dict(disposition_counts),
        "flag_counts": dict(flag_counts),
        "normalization_collisions": collisions,
        "multi_family_mappings": multi_family,
        "review_queue": [row for row in flagged if row["flags"]],
        "all_candidates": flagged,
        "unmatched_v1_6_0_families": unmatched_families,
        "benchmarks": benchmark_rows,
        "policy": {
            "legacy_identity_is_authority": False,
            "v1_6_0_family_label_is_corroborating_evidence": True,
            "candidate_auto_approval": False,
            "normalization_collision_auto_merge": False,
            "multi_family_auto_merge": False,
            "review_strategy": "all_collisions_plus_ranked_recurring_80_20",
        },
    }


def overall_status(
    launcher_audit: Mapping[str, Any],
    sqlite_audit: Mapping[str, Any],
    lossless_audit: Mapping[str, Any],
) -> str:
    if sqlite_audit.get("status") != "candidate_rows_available":
        return "blocked_identity_source_schema"
    if lossless_audit.get("status") not in {"complete", "complete_with_json_errors"}:
        return "blocked_lossless_family_source"
    if launcher_audit.get("status") != "observed":
        return "complete_with_launcher_warning"
    if lossless_audit.get("invalid_json_rows"):
        return "complete_with_lossless_json_warning"
    return "audit_complete_no_approvals"


def build_audit(
    knowledge_db: Path,
    lossless_events: Path,
    launcher_paths: Sequence[Path],
    benchmarks: Sequence[str],
    oem_min_families: int,
) -> Dict[str, Any]:
    launchers = audit_launchers(launcher_paths)
    sqlite_result = audit_sqlite(knowledge_db)
    lossless = audit_lossless_events(lossless_events)
    identities = audit_identities(
        sqlite_result,
        lossless,
        benchmarks,
        oem_min_families,
    )
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "built_at_utc": now_utc(),
        "status": overall_status(launchers, sqlite_result, lossless),
        "launchers": launchers,
        "sqlite": sqlite_result,
        "lossless_v1_6_0": lossless,
        "identity_audit": identities,
        "safety": {
            "source_files_opened_read_only": True,
            "database_writes": 0,
            "corpus_writes": 0,
            "source_traveler_writes": 0,
            "approved_identities_created": 0,
            "accepted_facts_changed": 0,
            "qdrant": "OFF",
            "production_launcher_changed": False,
            "v1_5_19_deployed": False,
        },
    }


def fmt_count(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) else str(value)


def render_summary(report: Mapping[str, Any], review_limit: int) -> str:
    launchers = report["launchers"]
    sqlite_result = report["sqlite"]
    lossless = report["lossless_v1_6_0"]
    identities = report["identity_audit"]

    lines = [
        "=" * 92,
        f"NOVA DRL IDENTITY CATALOG AUDIT  |  v{VERSION}",
        "=" * 92,
        f"Status: {report['status']}",
        "Mode: READ-ONLY AUDIT — ZERO IDENTITIES APPROVED",
        "",
        "CURRENT LAUNCHERS",
        "-----------------",
    ]
    for row in launchers["paths"]:
        state = "FOUND" if row.get("exists") or row.get("is_symlink") else "MISSING"
        lines.append(f"{state:7} {row['path']}")
        if state == "FOUND":
            lines.append(f"        resolved: {row.get('resolved_path') or '-'}")
            lines.append(
                "        versions: "
                + (", ".join(row.get("version_mentions") or []) or "none detected")
            )
            for target in row.get("script_targets") or []:
                lines.append(f"        target:   {target}")

    lines.extend(
        [
            "",
            "SOURCE INVENTORY",
            "----------------",
            f"Knowledge DB: {sqlite_result['file']['path']}",
            f"DB status: {sqlite_result['status']} | mode={sqlite_result['open_mode']}",
            "DB rows: "
            + ", ".join(
                f"{key}={fmt_count(value)}"
                for key, value in sqlite_result.get("table_counts", {}).items()
            ),
            "product_families columns: "
            + (", ".join(sqlite_result.get("product_families_columns") or []) or "none"),
            f"Selected explicit PN column: {sqlite_result.get('selected_part_number_column') or 'NONE'}",
            f"Lossless events: {lossless['file']['path']}",
            f"Lossless status: {lossless['status']}",
            f"Lossless repair-event rows: {fmt_count(lossless['repair_event_rows'])}",
            f"Distinct v1.6.0 family labels: {fmt_count(len(lossless.get('family_counts') or {}))}",
            f"Missing-family rows: {fmt_count(lossless['missing_family_rows'])}",
            f"Invalid JSON rows: {fmt_count(lossless['invalid_json_rows'])}",
            "",
            "IDENTITY CANDIDATE AUDIT",
            "------------------------",
            f"Legacy normalized identity groups: {fmt_count(identities['legacy_identity_groups'])}",
        ]
    )
    for key in (
        "clean_candidate_not_approved",
        "review_candidate",
        "quarantine_candidate",
    ):
        lines.append(
            f"{key:32} {fmt_count(identities['disposition_counts'].get(key, 0))}"
        )

    lines.extend(["", "FLAG COUNTS", "-----------"])
    for flag, count in sorted(
        identities["flag_counts"].items(), key=lambda item: (-item[1], item[0])
    ):
        lines.append(f"{flag:48} {count:,}")

    lines.extend(
        [
            "",
            "NORMALIZATION COLLISIONS — NEVER AUTO-MERGE",
            "---------------------------------------------",
        ]
    )
    collisions = identities["normalization_collisions"]
    if not collisions:
        lines.append("None observed.")
    else:
        for row in collisions[:50]:
            lines.append(
                f"{row['identity_key']}: " + " | ".join(row["raw_part_numbers"])
            )
        if len(collisions) > 50:
            lines.append(f"... {len(collisions)-50} additional collisions omitted from screen")

    lines.extend(
        [
            "",
            f"80/20 REVIEW QUEUE — TOP {review_limit}",
            "--------------------------------",
        ]
    )
    queue = identities["review_queue"]
    if not queue:
        lines.append("No flagged legacy identity candidates.")
    else:
        for index, row in enumerate(queue[:review_limit], 1):
            raw = " | ".join(row["raw_part_numbers"])
            flags = ", ".join(row["flags"])
            lines.append(
                f"{index:2}. {raw} | events={row['observed_event_weight']} "
                f"| {row['disposition']} | {flags}"
            )

    lines.extend(["", "CRITICAL DRL BENCHMARKS", "-----------------------"])
    for row in identities["benchmarks"]:
        families = row["observed_v1_6_0_families"]
        family_text = families[0] if families else "not observed"
        if len(families) > 1:
            family_text += f" (+{len(families)-1} more)"
        flags = ", ".join(row["flags"]) or "none"
        lines.append(
            f"{row['part_number']:<18} {row['status']:<46} "
            f"| flags={flags} | {family_text}"
        )

    unmatched = identities["unmatched_v1_6_0_families"]
    lines.extend(
        [
            "",
            "HIGH-VOLUME v1.6.0 FAMILIES WITHOUT A LEGACY IDENTITY TOKEN",
            "-------------------------------------------------------------",
            f"Total unmatched families: {len(unmatched):,}",
        ]
    )
    for row in unmatched[:20]:
        lines.append(
            f"{row['repair_event_count']:5}  {row['equipment_family']}"
        )

    lines.extend(
        [
            "",
            "SAFETY / DECISION",
            "-----------------",
            "Legacy identity candidates treated as authority: NO",
            "Canonical identities created: 0",
            "Normalization collisions auto-merged: 0",
            "Multi-family mappings auto-merged: 0",
            "Database/corpus/source writes: 0",
            "Accepted facts changed: 0",
            "Qdrant: OFF",
            "Production launcher changed: NO",
            "",
            "NEXT: review this audit before building any v2 identity catalog or resolver.",
        ]
    )
    return "\n".join(lines) + "\n"


def compact_json_report(report: Mapping[str, Any]) -> Dict[str, Any]:
    """Machine-readable output without duplicating every clean candidate row."""
    identities = report["identity_audit"]
    return {
        "schema": report["schema"],
        "version": report["version"],
        "built_at_utc": report["built_at_utc"],
        "status": report["status"],
        "launchers": report["launchers"],
        "sqlite": {
            key: value
            for key, value in report["sqlite"].items()
            if key != "identity_candidate_rows"
        },
        "lossless_v1_6_0": {
            key: value
            for key, value in report["lossless_v1_6_0"].items()
            if key != "family_counts"
        },
        "identity_audit": {
            key: value
            for key, value in identities.items()
            if key not in {"all_candidates"}
        },
        "safety": report["safety"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only NOVA DRL identity catalog audit v2.0.0-a1"
    )
    parser.add_argument("--knowledge-db", default=str(DEFAULT_DB))
    parser.add_argument("--lossless-events", default=str(DEFAULT_LOSSLESS_EVENTS))
    parser.add_argument(
        "--launcher",
        action="append",
        help="Launcher path to inspect; repeatable. Defaults to known NOVA launchers.",
    )
    parser.add_argument(
        "--benchmark",
        action="append",
        help="Critical DRL Part # to audit; repeatable. Defaults to the standard set.",
    )
    parser.add_argument("--review-limit", type=int, default=30)
    parser.add_argument("--oem-min-families", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.review_limit < 1:
        raise SystemExit("--review-limit must be >= 1")
    if args.oem_min_families < 2:
        raise SystemExit("--oem-min-families must be >= 2")

    launchers = (
        [Path(value) for value in args.launcher]
        if args.launcher
        else list(DEFAULT_LAUNCHERS)
    )
    benchmarks = args.benchmark if args.benchmark else list(DEFAULT_BENCHMARKS)
    report = build_audit(
        Path(args.knowledge_db),
        Path(args.lossless_events),
        launchers,
        benchmarks,
        args.oem_min_families,
    )

    if args.json:
        print(json.dumps(compact_json_report(report), indent=2, ensure_ascii=False))
    else:
        print(render_summary(report, args.review_limit), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
