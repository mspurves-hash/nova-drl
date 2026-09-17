#!/usr/bin/env python3
"""
Nova DRL HARD Part-Number-First + Unique Prefix + Semantic Veto + Structured Component Parts Gate v1.5.18

Hierarchy:
    DRL PART # -> exact family -> family evidence
               -> Parts / failures / actions only inside that family

v1.5.18 hardens the family fence and adds safe partial-Part-# support:
    - exact DRL Part # token match remains highest priority;
    - a partial Part # may resolve only when its normalized prefix identifies
      exactly ONE full DRL Part # token in repair_events;
    - ambiguous prefixes return no product repair-knowledge resolution;
    - category/manufacturer/broad-text queries never fall back to the legacy
      global product resolver;
    - unresolved/broad searches cannot borrow Parts/failures/actions from an
      unrelated family.

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
                "structured_replacement_component_3plus_v1_5_17"
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


def part_number_candidates(query: str) -> List[str]:
    """Extract likely DRL Part # strings from a technician query."""
    raw = " ".join(str(query or "").strip().split())
    if not raw:
        return []

    candidates: List[str] = []

    # Whole query first when it is itself a compact Part #-like string.
    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", raw)
        and any(c.isdigit() for c in raw)
    ):
        candidates.append(raw)

    # Also support category/full-family inputs, e.g. CNTL - 9800106841.
    for m in re.finditer(r"[A-Za-z0-9][A-Za-z0-9._/-]*", raw):
        token = m.group(0).strip("._/-")
        compact = re.sub(r"[^A-Za-z0-9]", "", token)
        if len(compact) < 4:
            continue
        if not any(c.isdigit() for c in token):
            continue
        candidates.append(token)

    unique: List[str] = []
    seen = set()
    for token in sorted(
        candidates,
        key=lambda x: (-len(re.sub(r"[^A-Za-z0-9]", "", x)), x.casefold()),
    ):
        key = re.sub(r"[^A-Z0-9]+", "", token.upper())
        if key and key not in seen:
            seen.add(key)
            unique.append(token)
    return unique


def _norm_part(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _family_rows(conn):
    rows = conn.execute(
        "SELECT DISTINCT equipment_family "
        "FROM repair_events "
        "WHERE equipment_family IS NOT NULL AND TRIM(equipment_family) <> ''"
    ).fetchall()
    return [str(r[0] or "").strip() for r in rows if str(r[0] or "").strip()]


def _family_event_count(conn, family: str) -> int:
    try:
        return int(
            conn.execute(
                "SELECT COUNT(DISTINCT repair_event_id) "
                "FROM repair_events WHERE equipment_family=?",
                (family,),
            ).fetchone()[0]
            or 0
        )
    except Exception:
        return 0


def unique_prefix_family_match(conn, family_module, candidate: str):
    """Resolve a partial Part # only when it maps to one full PN token.

    Returns None for no match OR ambiguity.  This deliberately prefers a
    false-negative over cross-family contamination.
    """
    qn = _norm_part(candidate)

    # Keep partial matching conservative. This still supports ELA-B014 (7 chars)
    # and useful numeric prefixes such as 980010 (6 chars).
    if len(qn) < 6 or not any(c.isdigit() for c in qn):
        return None

    hits = []
    for fam in _family_rows(conn):
        for raw, tn in family_module.family_tokens(fam):
            if not tn or tn == qn:
                continue
            if len(tn) <= len(qn):
                continue
            if not tn.startswith(qn):
                continue
            if not any(c.isdigit() for c in tn):
                continue
            hits.append((fam, raw, tn))

    if not hits:
        return None

    # The core safety rule: one normalized full Part # token only.
    token_norms = sorted({tn for _, _, tn in hits})
    if len(token_norms) != 1:
        return None

    full_norm = token_norms[0]
    same_pn_hits = [(fam, raw, tn) for fam, raw, tn in hits if tn == full_norm]
    families = sorted(
        {fam for fam, _, _ in same_pn_hits},
        key=lambda f: (-_family_event_count(conn, f), f.casefold()),
    )
    if not families:
        return None

    # Preserve the actual observed full PN spelling from the dominant family.
    dominant_family = families[0]
    full_raw = next(
        raw for fam, raw, tn in same_pn_hits
        if fam == dominant_family and tn == full_norm
    )

    return {
        "base_part_number": full_raw,
        "display_family": dominant_family,
        "families": families,
        "model_variants": families,
        "resolution_source": "unique_prefix_drl_part_number_family_first_v1_5_18",
        "query_part_prefix": candidate,
        "resolved_part_number": full_raw,
    }


def make_hard_part_number_first_resolver(family_module):
    """Exact PN first; then unique PN-prefix. Never use legacy fuzzy resolver."""
    def resolve_base_product(conn, query: str):
        for candidate in part_number_candidates(query):
            # 1) Exact DRL Part #.
            exact = family_module.exact_family_matches(conn, candidate)
            if exact:
                base = family_module.family_base_part(exact[0], candidate)
                return {
                    "base_part_number": base,
                    "display_family": exact[0],
                    "families": exact,
                    "model_variants": exact,
                    "resolution_source": (
                        "hard_exact_drl_part_number_family_first_v1_5_18"
                    ),
                }

            # 2) Safe partial Part #: only one full PN token may match.
            partial = unique_prefix_family_match(conn, family_module, candidate)
            if partial:
                return partial

        # Never fall back to global/fuzzy family resolution.
        return None

    return resolve_base_product


def main() -> int:
    search = load_module(SEARCH_TOOL, "nova_search_v1511")
    family = load_module(FAMILY_FIRST, "nova_family_first_v1513")
    veto = load_module(SEMANTIC_VETO, "nova_semantic_v1515")

    # 1) HARD DRL Part #-first fence.
    # Never call the legacy global resolver for technician repair knowledge.
    search.resolve_base_product = make_hard_part_number_first_resolver(family)

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
