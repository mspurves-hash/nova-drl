#!/usr/bin/env python3
"""
Nova DRL Family-First + Semantic Veto + Structured Component Parts Gate v1.5.16

Hierarchy:
    DRL PART # -> exact family -> family evidence
               -> Parts / failures / actions only inside that family

Repair actions:
    Uses v1.5.15 conservative semantic veto.

Parts:
    Explicit PN/capacitance identities keep existing v1.5.11 behavior.
    Generic component labels (MOTOR, BELT, BEARING, RELAY, etc.) must:
      1) be supported by structured replacement_mentions rows; and
      2) recur in >= 3 independent repair events for normal 80/20 display.

This prevents narrative/test-load language from becoming a replacement part
while preserving strong recurring generic components in robots/mechanical units.

No DB rebuild. No corpus mutation. No family-specific rules. Qdrant OFF.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set

SEARCH_TOOL = Path("/opt/nova-drl/tools/nova_drl_unified_knowledge_index_v1_5_11.py")
FAMILY_FIRST = Path("/opt/nova-drl/nova_drl_family_first_search_v1_5_13.py")
SEMANTIC_VETO = Path("/opt/nova-drl/nova_drl_family_first_semantic_veto_v1_5_15.py")

GENERIC_COMPONENT_MIN_EVENTS = 3


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def norm_tokens(value: Any) -> List[str]:
    raw = re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper())
    out = []
    for tok in raw.split():
        # Light singularization only for generic-component matching.
        if len(tok) > 4 and tok.endswith("IES"):
            tok = tok[:-3] + "Y"
        elif len(tok) > 3 and tok.endswith("ES"):
            tok = tok[:-2]
        elif len(tok) > 3 and tok.endswith("S"):
            tok = tok[:-1]
        out.append(tok)
    return out


def component_matches_text(component: str, text: str) -> bool:
    ct = norm_tokens(component)
    tt = norm_tokens(text)
    if not ct or not tt:
        return False
    return all(tok in tt for tok in ct)


def structured_component_support_events(
    conn,
    event_ids: Sequence[str],
    component: str,
) -> List[str]:
    ids = sorted({str(x) for x in event_ids if str(x)})
    if not ids:
        return []

    marks = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT repair_event_id,text,evidence_quote "
        f"FROM replacement_mentions "
        f"WHERE repair_event_id IN ({marks}) "
        f"AND procurement_only_excluded=0",
        ids,
    ).fetchall()

    support: Set[str] = set()
    for r in rows:
        # sqlite Row or tuple compatibility
        try:
            eid = str(r["repair_event_id"] or "")
            text = str(r["text"] or "")
            quote = str(r["evidence_quote"] or "")
        except Exception:
            eid = str(r[0] or "")
            text = str(r[1] or "")
            quote = str(r[2] or "")

        if eid and (
            component_matches_text(component, text)
            or component_matches_text(component, quote)
        ):
            support.add(eid)

    return sorted(support)


def make_structured_component_parts_gate(original):
    def aggregate_product_parts(conn, families, base_part_number=None):
        rows = original(conn, families, base_part_number)
        out = []

        for row0 in rows:
            row = dict(row0)
            payload = dict(row.get("payload") or {})
            kind = str(payload.get("reference_kind") or "").strip().casefold()

            # Only generic component labels are tightened. PN/capacitance identities
            # retain existing v1.5.11 behavior.
            if kind != "component":
                out.append(row)
                continue

            label = str(
                payload.get("reference_pn")
                or payload.get("pn")
                or row.get("primary_value")
                or row.get("title")
                or ""
            ).strip()
            original_ids = [
                str(x) for x in (payload.get("event_ids") or []) if str(x)
            ]

            supported_ids = structured_component_support_events(
                conn, original_ids, label
            )

            # Normal 80/20 generic-component output requires three structured
            # replacement events. Low-count evidence remains preserved upstream.
            if len(supported_ids) < GENERIC_COMPONENT_MIN_EVENTS:
                continue

            payload["pre_structured_component_gate_repairs"] = payload.get("repairs")
            payload["pre_structured_component_gate_event_ids"] = original_ids
            payload["repairs"] = len(supported_ids)
            payload["explicit_repairs"] = len(supported_ids)
            payload["event_ids"] = supported_ids
            payload["parts_semantic_gate"] = (
                "structured_replacement_component_3plus_v1_5_16"
            )
            row["payload"] = payload
            out.append(row)

        out.sort(
            key=lambda r: (
                -int((r.get("payload") or {}).get("repairs") or 0),
                str(r.get("primary_value") or "").casefold(),
            )
        )
        return out

    return aggregate_product_parts


def main() -> int:
    search = load_module(SEARCH_TOOL, "nova_search_v1511")
    family = load_module(FAMILY_FIRST, "nova_family_first_v1513")
    veto = load_module(SEMANTIC_VETO, "nova_semantic_v1515")

    # 1) Family-first.
    search.resolve_base_product = family.make_family_first_resolver(
        search.resolve_base_product
    )

    # 2) Conservative repair-action semantic veto.
    search.aggregate_repair_actions = veto.make_conservative_action_aggregator(
        search, search.aggregate_repair_actions
    )

    # 3) Conservative generic-component Parts gate.
    search.aggregate_product_parts = make_structured_component_parts_gate(
        search.aggregate_product_parts
    )

    return int(search.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
