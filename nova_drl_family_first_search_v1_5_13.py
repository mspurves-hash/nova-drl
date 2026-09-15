#!/usr/bin/env python3
"""
Nova DRL Family-First Search Wrapper v1.5.13

Restores the intended NOVA DRL hierarchy:

    DRL PART # -> exact equipment family -> family repair events
               -> parts / failures / repair actions inside that family only

This is a wrapper around the current v1.5.11 search implementation.
It does not rebuild the SQLite database and does not alter corpus data.
"""

from __future__ import annotations

import importlib.util
import re
import sqlite3
from pathlib import Path
from typing import Any, List, Tuple

TARGET = Path("/opt/nova-drl/tools/nova_drl_unified_knowledge_index_v1_5_11.py")


def norm(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def looks_like_drl_part_query(query: str) -> bool:
    n = norm(query)
    return len(n) >= 4 and any(c.isdigit() for c in n)


def family_tokens(family: str) -> List[Tuple[str, str]]:
    raw = " ".join(str(family or "").split())
    out = []
    for tok in raw.split():
        cleaned = tok.strip(",:;()[]{}")
        if cleaned == "-":
            continue
        # Legacy formatting example: "PS -00010-93076 AMAT"
        if cleaned.startswith("-") and len(cleaned) > 1:
            cleaned = cleaned[1:]
        n = norm(cleaned)
        if n:
            out.append((cleaned, n))
    return out


def exact_family_matches(conn: sqlite3.Connection, query: str) -> List[str]:
    # Exact DRL Part # resolution is deliberately not used for broad text searches.
    if not looks_like_drl_part_query(query):
        return []

    qn = norm(query)
    rows = conn.execute(
        "SELECT DISTINCT equipment_family "
        "FROM repair_events "
        "WHERE equipment_family IS NOT NULL AND TRIM(equipment_family) <> ''"
    ).fetchall()

    matches = []
    for row in rows:
        fam = str(row[0] or "").strip()
        if fam and any(tn == qn for _, tn in family_tokens(fam)):
            matches.append(fam)

    if not matches:
        return []

    counts = {}
    for fam in matches:
        try:
            count = conn.execute(
                "SELECT COUNT(DISTINCT repair_event_id) "
                "FROM repair_events WHERE equipment_family=?",
                (fam,),
            ).fetchone()[0]
        except Exception:
            count = 0
        counts[fam] = int(count or 0)

    return sorted(matches, key=lambda f: (-counts.get(f, 0), f.casefold()))


def family_base_part(family: str, query: str) -> str:
    qn = norm(query)
    for raw, tn in family_tokens(family):
        if tn == qn:
            return raw
    return str(query or "").strip()


def make_family_first_resolver(original_resolver):
    def resolve_base_product(conn, query: str):
        if looks_like_drl_part_query(query):
            exact = exact_family_matches(conn, query)
            if exact:
                base = family_base_part(exact[0], query)
                return {
                    "base_part_number": base,
                    "display_family": exact[0],
                    "families": exact,
                    "model_variants": exact,
                    "resolution_source": "exact_drl_part_number_family_first",
                }

        resolved = original_resolver(conn, query)
        if not resolved:
            return None

        # A specific DRL part-number-like query must never fall into an unrelated
        # generic family such as base part "M".
        if looks_like_drl_part_query(query):
            qn = norm(query)
            bn = norm(resolved.get("base_part_number"))
            families = list(resolved.get("families") or [])
            family_has_exact_part = any(
                any(tn == qn for _, tn in family_tokens(fam))
                for fam in families
            )
            if not family_has_exact_part and (len(bn) < 4 or bn != qn):
                return None

        return resolved

    return resolve_base_product


def load_target(path: Path):
    if not path.exists():
        raise RuntimeError(f"Missing current NOVA search tool: {path}")
    spec = importlib.util.spec_from_file_location("nova_drl_v1_5_11_family_first", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    mod = load_target(TARGET)
    if not hasattr(mod, "resolve_base_product"):
        raise RuntimeError("v1.5.11 does not expose resolve_base_product")
    if not hasattr(mod, "main"):
        raise RuntimeError("v1.5.11 does not expose main")

    mod.resolve_base_product = make_family_first_resolver(mod.resolve_base_product)
    return int(mod.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
