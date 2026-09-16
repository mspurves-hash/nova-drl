#!/usr/bin/env python3
"""
Trace the origin of MOTOR under PARTS REPLACED for MR-J2S-40A.

Diagnostic only. No DB/corpus writes.
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


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rec_matches(obj: Any, pattern: re.Pattern, prefix: str = "") -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out.extend(rec_matches(v, pattern, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(rec_matches(v, pattern, f"{prefix}[{i}]"))
    else:
        s = str(obj or "")
        if s and pattern.search(s):
            out.append((prefix, s))
    return out


def load_full(ids: set[str]) -> Dict[str, Dict[str, Any]]:
    out = {}
    if not FULL_EVENTS.exists():
        return out
    with FULL_EVENTS.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            eid = str(row.get("repair_event_id") or "")
            if eid in ids:
                out[eid] = row
                if len(out) == len(ids):
                    break
    return out


def main() -> int:
    s = load(SEARCH_TOOL, "s")
    ff = load(FAMILY_FIRST, "ff")

    db = Path(getattr(s, "DEFAULT_DB", "/opt/nova-drl/index/drl_knowledge_index.sqlite"))
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    families = ff.exact_family_matches(conn, QUERY)
    parts = s.aggregate_product_parts(conn, families, QUERY)

    motor_rows = [
        r for r in parts
        if str(r.get("primary_value") or r.get("title") or "").strip().upper() == "MOTOR"
    ]

    print("# MR-J2S-40A PARTS MOTOR ORIGIN TRACE")
    print(f"Families: {families}")
    print()

    if not motor_rows:
        print("No MOTOR Parts row found.")
        return 0

    culprit_ids = set()
    for r in motor_rows:
        p = r.get("payload") or {}
        ids = [str(x) for x in (p.get("event_ids") or []) if str(x)]
        culprit_ids.update(ids)
        print(f"MOTOR | repairs={p.get('repairs')} | event_ids={ids}")
        print(f"payload={json.dumps(p, ensure_ascii=False, indent=2)}")

    print()
    print("REPLACEMENT_MENTIONS FOR CULPRIT EVENTS")
    print("---------------------------------------")
    marks = ",".join("?" for _ in culprit_ids)
    rm = []
    if culprit_ids:
        rm = [
            dict(r) for r in conn.execute(
                f"SELECT * FROM replacement_mentions WHERE repair_event_id IN ({marks})",
                sorted(culprit_ids),
            ).fetchall()
        ]
    if not rm:
        print("NONE")
    else:
        for r in rm:
            print(json.dumps(r, ensure_ascii=False, default=str))

    events = {
        str(r["repair_event_id"]): dict(r)
        for r in conn.execute(
            f"SELECT * FROM repair_events WHERE repair_event_id IN ({marks})",
            sorted(culprit_ids),
        ).fetchall()
    } if culprit_ids else {}

    full = load_full(culprit_ids)
    motor_re = re.compile(r"\bmotor\b", re.I)
    replace_re = re.compile(r"\b(?:replace|replaced|parts?|motor|test|turn|turning)\b", re.I)

    for eid in sorted(culprit_ids):
        print()
        print("=" * 90)
        print(f"EVENT {eid}")
        print("=" * 90)
        e = events.get(eid, {})
        print(f"log={e.get('log_number')} family={e.get('equipment_family')}")

        try:
            snippets = s._technician_repair_snippets(e)
        except Exception as exc:
            snippets = [f"<snippet error: {exc}>"]

        print("TECHNICIAN SNIPPETS")
        for x in snippets:
            print(f"  - {x}")

        print("DB MOTOR FIELDS")
        for path, value in rec_matches(e, motor_re):
            print(f"  {path}: {value}")

        print("FULL v1.5.2 MOTOR / PART / TEST CONTEXT")
        row = full.get(eid, {})
        matches = rec_matches(row, replace_re)
        if not matches:
            print("  NONE")
        for path, value in matches[:100]:
            print(f"  {path}: {value}")

        facts = row.get("facts") if isinstance(row, dict) else None
        pr = facts.get("parts_replaced") if isinstance(facts, dict) else None
        print("FULL v1.5.2 parts_replaced")
        if not pr:
            print("  NONE")
        else:
            for item in pr:
                print("  " + json.dumps(item, ensure_ascii=False))

    conn.close()
    print()
    print("INTERPRETATION")
    print("--------------")
    print("If MOTOR has no corresponding parts_replaced record in these events, the")
    print("Parts row is being recovered from repair/test text and should be semantically vetoed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
