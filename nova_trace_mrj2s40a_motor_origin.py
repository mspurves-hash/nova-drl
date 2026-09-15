#!/usr/bin/env python3
"""
Trace the origin of MOTOR-related recurring repair actions for MR-J2S-40A.

Diagnostic only. No writes to NOVA data, no Qdrant, no pipeline changes.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple

SEARCH_TOOL = Path("/opt/nova-drl/tools/nova_drl_unified_knowledge_index_v1_5_11.py")
FAMILY_FIRST = Path("/opt/nova-drl/nova_drl_family_first_search_v1_5_13.py")
FULL_EVENTS = Path("/opt/nova-drl/output/drl_full_corpus_v1_5_2/repair_events_v1_5_2.jsonl")
QUERY = "MR-J2S-40A"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def text(v: Any) -> str:
    return str(v or "")


def recursive_matches(obj: Any, pattern: re.Pattern, prefix: str = "") -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.extend(recursive_matches(v, pattern, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{prefix}[{i}]"
            out.extend(recursive_matches(v, pattern, p))
    else:
        s = text(obj)
        if s and pattern.search(s):
            out.append((prefix, s))
    return out


def source_like(obj: Any, prefix: str = "") -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            lk = str(k).casefold()
            if isinstance(v, (str, int, float)) and any(x in lk for x in ("source", "path", "file", "record")):
                out.append((p, text(v)))
            out.extend(source_like(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{prefix}[{i}]"
            out.extend(source_like(v, p))
    return out


def read_full_rows(event_ids: set[str]) -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    if not FULL_EVENTS.exists():
        return found
    with FULL_EVENTS.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            eid = str(row.get("repair_event_id") or "")
            if eid in event_ids:
                found[eid] = row
                if len(found) == len(event_ids):
                    break
    return found


def print_matches(title: str, matches: List[Tuple[str, str]], max_items: int = 80):
    print(title)
    print("-" * len(title))
    if not matches:
        print("  NONE")
        return
    seen = set()
    count = 0
    for path, value in matches:
        key = (path, value)
        if key in seen:
            continue
        seen.add(key)
        print(f"  {path}: {value}")
        count += 1
        if count >= max_items:
            print(f"  ... truncated after {max_items}")
            break


def main() -> int:
    search = load_module(SEARCH_TOOL, "nova_search_v1511")
    family = load_module(FAMILY_FIRST, "nova_family_first_v1513")

    db_path = Path(getattr(search, "DEFAULT_DB", "/opt/nova-drl/index/drl_knowledge_index.sqlite"))
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    families = family.exact_family_matches(conn, QUERY)
    print("# MR-J2S-40A MOTOR ORIGIN TRACE")
    print()
    print(f"DB: {db_path}")
    print(f"Exact family labels: {len(families)}")
    for f in families:
        print(f"  - {f}")

    events = search.product_event_rows(conn, families)
    print(f"Family-scoped repair events: {len(events)}")

    actions = search.aggregate_repair_actions(events, limit=999)
    motor_actions = [
        r for r in actions
        if "MOTOR" in str(r.get("primary_value") or r.get("title") or "").upper()
    ]

    print()
    print("MOTOR ACTIONS PRODUCED BY v1.5.11")
    print("----------------------------------")
    if not motor_actions:
        print("NONE")
        return 0

    culprit_ids: set[str] = set()
    for r in motor_actions:
        payload = r.get("payload") or {}
        ids = [str(x) for x in (payload.get("event_ids") or []) if str(x)]
        culprit_ids.update(ids)
        print(
            f"{r.get('primary_value')} | repairs={payload.get('repairs')}"
            f" | event_ids={ids}"
        )

    event_by_id = {
        str(e.get("repair_event_id") or ""): e
        for e in events
        if str(e.get("repair_event_id") or "") in culprit_ids
    }
    full_by_id = read_full_rows(culprit_ids)

    motor_re = re.compile(r"\bmotor\b", re.I)
    broad_re = re.compile(r"\b(?:motor|replace(?:d|ment)?|repair(?:ed)?|servo)\b", re.I)

    for eid in sorted(culprit_ids):
        print()
        print("=" * 90)
        print(f"EVENT {eid}")
        print("=" * 90)

        e = event_by_id.get(eid, {})
        print(f"DB family: {e.get('equipment_family')}")
        print(f"Log number: {e.get('log_number')}")

        if hasattr(search, "_technician_repair_snippets"):
            try:
                snippets = search._technician_repair_snippets(e)
            except Exception as exc:
                snippets = [f"<ERROR calling _technician_repair_snippets: {exc}>"]
            print()
            print("PARSER INPUT SNIPPETS")
            print("---------------------")
            for s in snippets:
                mark = "  <<< MOTOR" if motor_re.search(text(s)) else ""
                print(f"  {text(s)}{mark}")

        print()
        print_matches(
            "DB EVENT FIELDS CONTAINING 'motor'",
            recursive_matches(e, motor_re),
        )
        print()
        print_matches(
            "DB EVENT FIELDS WITH MOTOR / REPLACE / REPAIR / SERVO CONTEXT",
            recursive_matches(e, broad_re),
        )
        print()
        print_matches(
            "DB EVENT SOURCE/PATH FIELDS",
            source_like(e),
        )

        full = full_by_id.get(eid)
        if full:
            print()
            print_matches(
                "FULL v1.5.2 ROW FIELDS CONTAINING 'motor'",
                recursive_matches(full, motor_re),
            )
            print()
            print_matches(
                "FULL v1.5.2 MOTOR / REPLACE / REPAIR / SERVO CONTEXT",
                recursive_matches(full, broad_re),
            )
            print()
            print_matches(
                "FULL v1.5.2 SOURCE/PATH FIELDS",
                source_like(full),
            )
        else:
            print()
            print("FULL v1.5.2 row: NOT FOUND or file unavailable")

    conn.close()

    print()
    print("INTERPRETATION")
    print("--------------")
    print("The event_ids above are the exact source repairs that created MOTOR actions.")
    print("If the raw/frozen evidence does not actually describe repairing/replacing a motor,")
    print("the defect is in the repair-action extraction/parser, not in family resolution.")
    print("No files or database rows were modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
