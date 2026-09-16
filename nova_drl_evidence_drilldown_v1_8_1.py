#!/usr/bin/env python3
"""
Nova DRL Evidence Drill-Down v1.8.1

Read-only technician evidence view layered on the frozen v1.5.16 search stack.

Hierarchy:
    DRL Part # -> exact family -> recurring knowledge item -> supporting events
               -> evidence text -> original Traveler source path

Examples:
    python3 nova_drl_evidence_drilldown_v1_8_0.py \
        --search "MR-J2S-40A" --item "7800"

    python3 nova_drl_evidence_drilldown_v1_8_0.py \
        --search "XU-RCM7231" --item "REPLACE BEARING"

    python3 nova_drl_evidence_drilldown_v1_8_0.py \
        --search "MR-J2S-40A" --item "LOW VOLTAGE" --type failure

No DB writes. No corpus writes. No Qdrant.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SEARCH_TOOL = Path("/opt/nova-drl/tools/nova_drl_unified_knowledge_index_v1_5_11.py")
FAMILY_FIRST = Path("/opt/nova-drl/nova_drl_family_first_search_v1_5_13.py")
SEMANTIC_VETO = Path("/opt/nova-drl/nova_drl_family_first_semantic_veto_v1_5_15.py")
PARTS_GATE = Path("/opt/nova-drl/nova_drl_family_first_parts_gate_v1_5_16.py")

DEFAULT_LIMIT = 12
MAX_LIMIT = 100


def load_module(path: Path, name: str):
    if not path.exists():
        raise RuntimeError(f"Missing required NOVA module: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_stack():
    """Load v1.5.11 and apply the frozen v1.5.16 presentation stack."""
    search = load_module(SEARCH_TOOL, "nova_search_v1511_drill")
    family = load_module(FAMILY_FIRST, "nova_family_first_v1513_drill")
    veto = load_module(SEMANTIC_VETO, "nova_semantic_v1515_drill")
    gate = load_module(PARTS_GATE, "nova_parts_gate_v1516_drill")

    search.resolve_base_product = family.make_family_first_resolver(
        search.resolve_base_product
    )
    search.aggregate_repair_actions = veto.make_conservative_action_aggregator(
        search, search.aggregate_repair_actions
    )
    search.aggregate_product_parts = gate.make_structured_component_parts_gate(
        search.aggregate_product_parts
    )
    return search, family, gate, veto


def compact(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def normalized_words(value: Any) -> List[str]:
    raw = re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper())
    return [x for x in raw.split() if x]


def display_label(row: Dict[str, Any]) -> str:
    return str(row.get("primary_value") or row.get("title") or "").strip()


def payload(row: Dict[str, Any]) -> Dict[str, Any]:
    p = row.get("payload")
    return dict(p) if isinstance(p, dict) else {}


def row_event_ids(row: Dict[str, Any]) -> List[str]:
    return sorted({
        str(x) for x in (payload(row).get("event_ids") or [])
        if str(x)
    })


def get_rows_with_large_limit(func, events):
    """Call aggregators that may or may not expose a `limit` argument."""
    try:
        return func(events, limit=9999)
    except TypeError:
        return func(events)


def item_collections(search, conn, resolved, events):
    parts = search.aggregate_product_parts(
        conn, resolved["families"], resolved.get("base_part_number")
    )
    actions = get_rows_with_large_limit(search.aggregate_repair_actions, events)
    failures = get_rows_with_large_limit(search.aggregate_reported_failures, events)
    return {
        "part": list(parts or []),
        "action": list(actions or []),
        "failure": list(failures or []),
    }


def exact_item_matches(
    collections: Dict[str, List[Dict[str, Any]]],
    requested: str,
    requested_type: str = "auto",
) -> List[Tuple[str, Dict[str, Any]]]:
    q = compact(requested)
    if not q:
        return []

    types = ["part", "action", "failure"] if requested_type == "auto" else [requested_type]
    exact: List[Tuple[str, Dict[str, Any]]] = []
    for kind in types:
        for row in collections.get(kind, []):
            if compact(display_label(row)) == q:
                exact.append((kind, row))
    return exact


def fuzzy_item_hints(
    collections: Dict[str, List[Dict[str, Any]]],
    requested: str,
    requested_type: str = "auto",
    limit: int = 12,
) -> List[Tuple[str, str, int]]:
    q = compact(requested)
    if not q:
        return []
    types = ["part", "action", "failure"] if requested_type == "auto" else [requested_type]
    out = []
    for kind in types:
        for row in collections.get(kind, []):
            label = display_label(row)
            k = compact(label)
            if q in k or k in q:
                out.append((kind, label, len(row_event_ids(row))))
    out.sort(key=lambda x: (-x[2], x[0], x[1].casefold()))
    return out[:limit]


def parse_source_paths(event: Dict[str, Any]) -> List[str]:
    out: List[str] = []

    raw = event.get("source_paths_json")
    if raw:
        if isinstance(raw, list):
            vals = raw
        else:
            try:
                vals = json.loads(str(raw))
            except Exception:
                vals = [raw]
        if isinstance(vals, list):
            out.extend(str(x) for x in vals if str(x).strip())

    for key in ("source_path", "file_path", "path"):
        v = event.get(key)
        if v and str(v).strip():
            out.append(str(v).strip())

    return list(dict.fromkeys(out))


def first_nonempty(event: Dict[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        v = event.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def event_problem(event: Dict[str, Any]) -> str:
    return first_nonempty(
        event,
        (
            "reported_failure_text",
            "reported_problem_text",
            "reported_failure",
            "reported_problem",
            "customer_failure",
            "problem_text",
        ),
    )


def event_repair(event: Dict[str, Any]) -> str:
    return first_nonempty(
        event,
        (
            "repair_history_text",
            "repair_history",
            "repair_action_text",
            "repair_action",
        ),
    )


def event_test(event: Dict[str, Any]) -> str:
    return first_nonempty(
        event,
        (
            "test_outcome_text",
            "test_outcome",
            "explicit_test_outcome",
            "outcome_text",
        ),
    )


def event_sort_key(event: Dict[str, Any]) -> Tuple[int, str]:
    log = str(event.get("log_number") or "")
    digits = re.sub(r"\D", "", log)
    try:
        n = int(digits)
    except Exception:
        n = -1
    return (n, str(event.get("repair_event_id") or ""))


def fetch_events(conn, event_ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    ids = sorted({str(x) for x in event_ids if str(x)})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM repair_events WHERE repair_event_id IN ({marks})",
        ids,
    ).fetchall()
    return {
        str(r["repair_event_id"]): dict(r)
        for r in rows
        if str(r["repair_event_id"] or "")
    }


def fetch_replacement_mentions(conn, event_id: str) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT repair_event_id,manufacturer_pn,quantity,text,evidence_quote "
        "FROM replacement_mentions "
        "WHERE repair_event_id=? AND procurement_only_excluded=0",
        (event_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def part_row_matches(label: str, row: Dict[str, Any], gate) -> bool:
    pn = str(row.get("manufacturer_pn") or "")
    text = str(row.get("text") or "")
    quote = str(row.get("evidence_quote") or "")

    if pn and compact(pn) == compact(label):
        return True

    # Reuse the v1.5.16 generic-component matcher when possible.
    try:
        if gate.component_matches_text(label, text) or gate.component_matches_text(label, quote):
            return True
    except Exception:
        pass

    lk = compact(label)
    return bool(lk and (lk in compact(text) or lk in compact(quote)))


def relevant_part_evidence(
    conn,
    event_id: str,
    label: str,
    gate,
) -> List[Dict[str, Any]]:
    rows = fetch_replacement_mentions(conn, event_id)
    matched = [r for r in rows if part_row_matches(label, r, gate)]
    return matched


def event_action_texts(event: Dict[str, Any]) -> List[str]:
    """
    Text fields that can carry the evidence behind a recurring repair action.

    all_fact_text is important: some Travelers have action evidence there even
    when repair_history_text is empty in the SQLite presentation row.
    """
    keys = (
        "repair_history_text",
        "repair_history",
        "repair_action_text",
        "repair_action",
        "all_fact_text",
        "test_outcome_text",
    )
    out = []
    for key in keys:
        v = event.get(key)
        if v is None or not str(v).strip():
            continue
        s = str(v).strip()
        if s not in out:
            out.append(s)
    return out


def action_evidence(
    conn,
    event: Dict[str, Any],
    action_label: str,
    gate,
    semantic,
) -> List[Tuple[str, str]]:
    """
    Explain why one event supports a recurring action without changing the
    frozen v1.5.15/v1.5.16 support set.

    Evidence priority:
      1) matching structured replacement_mentions for REPLACE actions;
      2) direct action-object clauses;
      3) coordinated action/object clauses;
      4) object-bearing repair context as a last-resort explanation.
    """
    eid = str(event.get("repair_event_id") or "")
    verb, obj = semantic.split_action_label(action_label)
    if not verb or not obj:
        return []

    found: List[Tuple[str, str]] = []

    def add(kind: str, value: Any) -> None:
        s = " ".join(str(value or "").split())
        if not s:
            return
        if any(existing == s for _, existing in found):
            return
        found.append((kind, s))

    # Strongest evidence for REPLACE actions.
    if verb == "REPLACE" and eid:
        for r in relevant_part_evidence(conn, eid, obj, gate):
            quote = str(r.get("evidence_quote") or r.get("text") or "").strip()
            pn = str(r.get("manufacturer_pn") or "").strip()
            qty = r.get("quantity")
            meta = []
            if pn:
                meta.append(f"PN {pn}")
            if qty not in (None, ""):
                meta.append(f"qty {qty}")
            prefix = "Structured replacement"
            if meta:
                prefix += f" ({', '.join(meta)})"
            add(prefix, quote)

    texts = event_action_texts(event)
    for source_text in texts:
        try:
            clauses = semantic.split_clauses(source_text)
        except Exception:
            clauses = [source_text]

        for clause in clauses:
            try:
                if semantic.direct_action_on_object(clause, action_label):
                    add("Direct repair clause", clause)
                    continue
            except Exception:
                pass

            try:
                contains_obj = semantic.clause_contains_object(clause, obj)
            except Exception:
                contains_obj = compact(obj) in compact(clause)

            if not contains_obj:
                continue

            # Coordinated wording such as "Replaced bearings and belts" can
            # support both REPLACE BEARING and REPLACE BELT.
            if verb == "REPLACE":
                try:
                    if semantic.REPLACE_VERB_RE.search(clause):
                        add("Replacement clause", clause)
                        continue
                except Exception:
                    pass

            try:
                pattern = semantic.ACTION_PATTERNS.get(verb)
            except Exception:
                pattern = None
            if pattern and re.search(pattern, clause, re.I):
                add("Repair clause", clause)

    if found:
        return found[:10]

    # Last resort: explain the retained support event rather than showing no
    # evidence at all.
    for source_text in texts:
        try:
            clauses = semantic.split_clauses(source_text)
        except Exception:
            clauses = [source_text]
        for clause in clauses:
            try:
                matches = semantic.clause_contains_object(clause, obj)
            except Exception:
                matches = compact(obj) in compact(clause)
            if matches:
                add("Supporting repair context", clause)

    return found[:6]



def print_value(prefix: str, value: str, indent: str = "   "):
    v = " ".join(str(value or "").split())
    if v:
        print(f"{indent}{prefix}: {v}")


def render_event(
    kind: str,
    label: str,
    event: Dict[str, Any],
    conn,
    search,
    gate,
    semantic,
):
    eid = str(event.get("repair_event_id") or "")
    log = str(event.get("log_number") or eid)
    family = str(event.get("equipment_family") or "")

    print(f"DRL log {log}  |  {eid}")
    if family:
        print(f"   Family: {family}")

    if kind == "part":
        evidence = relevant_part_evidence(conn, eid, label, gate)
        if evidence:
            for r in evidence[:8]:
                pn = str(r.get("manufacturer_pn") or "").strip()
                qty = r.get("quantity")
                quote = str(r.get("evidence_quote") or r.get("text") or "").strip()
                meta = []
                if pn:
                    meta.append(f"PN {pn}")
                if qty not in (None, ""):
                    meta.append(f"qty {qty}")
                tag = f" ({', '.join(meta)})" if meta else ""
                print(f"   Replacement evidence{tag}: {quote}")
        else:
            print("   Replacement evidence: no direct replacement_mentions row matched;")
            print("      support came from the aggregated event evidence retained by v1.5.16.")

    elif kind == "action":
        evidence = action_evidence(conn, event, label, gate, semantic)
        if evidence:
            for evidence_kind, evidence_text in evidence:
                print(f"   {evidence_kind}: {evidence_text}")
        else:
            print("   Repair evidence: supporting event retained by v1.5.16,")
            print("      but no direct action clause could be reconstructed from the SQLite row.")

    elif kind == "failure":
        p = event_problem(event)
        if p:
            print_value("Reported failure", p)

    # Context is useful for all three kinds.
    repair = event_repair(event)
    test = event_test(event)
    problem = event_problem(event)

    if kind != "failure" and problem:
        print_value("Reported problem", problem)
    if kind != "action" and repair:
        print_value("Repair history", repair)
    elif kind == "action" and repair and not action_evidence(conn, event, label, gate, semantic):
        print_value("Repair history", repair)
    if test:
        print_value("Test/outcome", test)

    paths = parse_source_paths(event)
    if paths:
        print("   Source Traveler:")
        for p in paths[:5]:
            print(f"      {p}")
    else:
        print("   Source Traveler: path unavailable in repair_events row")


def render_header(
    search_query: str,
    resolved: Dict[str, Any],
    kind: str,
    row: Dict[str, Any],
    total: int,
    offset: int,
    shown: int,
):
    print("=" * 92)
    print("NOVA DRL EVIDENCE DRILL-DOWN  |  v1.8.1")
    print("=" * 92)
    print(f"Search:          {search_query}")
    print(f"Family:          {resolved.get('display_family')}")
    print(f"Base DRL Part #: {resolved.get('base_part_number')}")
    print(f"Evidence type:   {kind.upper()}")
    print(f"Knowledge item:  {display_label(row)}")
    print(f"Supporting repairs: {total}")
    if total:
        start = min(offset + 1, total)
        end = min(offset + shown, total)
        print(f"Showing:         {start}-{end} of {total}  |  newest repairs first")
    print()


def run(args) -> int:
    search, family, gate, semantic = build_stack()

    db_path = Path(getattr(search, "DEFAULT_DB", "/opt/nova-drl/index/drl_knowledge_index.sqlite"))
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    resolved = search.resolve_base_product(conn, args.search)
    if not resolved:
        print(f"ERROR: Could not resolve a family for search: {args.search}")
        conn.close()
        return 2

    events = search.product_event_rows(conn, resolved["families"])
    if not events:
        print(f"ERROR: Resolved family has no repair events: {resolved.get('display_family')}")
        conn.close()
        return 2

    collections = item_collections(search, conn, resolved, events)
    matches = exact_item_matches(collections, args.item, args.type)

    if not matches:
        print(f"No exact displayed knowledge item matched: {args.item!r}")
        hints = fuzzy_item_hints(collections, args.item, args.type)
        if hints:
            print("Closest displayed items:")
            for kind, label, count in hints:
                print(f"   {kind:<7} {label:<48} repairs={count}")
        conn.close()
        return 1

    if len(matches) > 1 and args.type == "auto":
        # Usually this means the same label appears in more than one section.
        print(f"More than one exact item matched {args.item!r}:")
        for kind, row in matches:
            print(f"   --type {kind:<7} {display_label(row)} | repairs={len(row_event_ids(row))}")
        print("Re-run with --type part|action|failure.")
        conn.close()
        return 1

    kind, row = matches[0]
    ids = row_event_ids(row)
    by_id = fetch_events(conn, ids)

    ordered = sorted(
        [by_id[eid] for eid in ids if eid in by_id],
        key=event_sort_key,
        reverse=True,
    )

    offset = max(0, int(args.offset))
    limit = min(MAX_LIMIT, max(1, int(args.limit)))
    page = ordered[offset:offset + limit]

    render_header(args.search, resolved, kind, row, len(ordered), offset, len(page))

    if not page:
        print("No supporting repairs in this page.")
        conn.close()
        return 0

    for i, event in enumerate(page, offset + 1):
        print("-" * 92)
        print(f"[{i}/{len(ordered)}]")
        render_event(kind, display_label(row), event, conn, search, gate, semantic)

    if offset + limit < len(ordered):
        print()
        print(
            f"More evidence available. Next page: "
            f"--offset {offset + limit} --limit {limit}"
        )

    print()
    print("READ-ONLY: no database, corpus, accepted facts, or Qdrant entries were changed.")
    conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Nova DRL v1.8.1 family-scoped evidence drill-down"
    )
    ap.add_argument("--search", required=True, help="DRL Part # / product search")
    ap.add_argument("--item", required=True, help="Exact displayed Part, action, or failure")
    ap.add_argument(
        "--type",
        choices=("auto", "part", "action", "failure"),
        default="auto",
        help="Knowledge section; auto exact-matches all three",
    )
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--offset", type=int, default=0)
    return run(ap.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
