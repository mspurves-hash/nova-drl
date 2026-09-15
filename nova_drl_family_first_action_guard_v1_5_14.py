#!/usr/bin/env python3
"""
Nova DRL Family-First + Direct-Action Guard v1.5.14

Restores/keeps:
    DRL PART # -> exact family -> family evidence

Adds:
    recurring repair actions must have direct action-object evidence in the
    same clause/event snippet. Keyword co-occurrence is not enough.

Examples rejected:
- "Motor tests worked okay."
- "Billed as repair ... Turned motor ... no parts changed."
- "Works great turning Motor with 220V AC input."
- "Fixed motor issue ... Replaced batteries."

Examples accepted:
- "Replaced motor."
- "Motor was replaced."
- "Repaired board."
- "Cleaned connector."
- "Adjusted belt."
- "Rebuilt bearing."
- "Lubricated lead screw."

Presentation-layer guard only. No DB/corpus mutation.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any, Dict, List

SEARCH_TOOL = Path("/opt/nova-drl/tools/nova_drl_unified_knowledge_index_v1_5_11.py")
FAMILY_FIRST = Path("/opt/nova-drl/nova_drl_family_first_search_v1_5_13.py")

ACTION_VERBS = {
    "REPLACE": [r"replac(?:e|ed|ing)", r"chang(?:e|ed|ing)", r"swapp?(?:ed|ing)?"],
    "REPAIR": [r"repair(?:ed|ing)?"],
    "CLEAN": [r"clean(?:ed|ing)?"],
    "ADJUST": [r"adjust(?:ed|ing)?"],
    "ALIGN": [r"align(?:ed|ing)?"],
    "REBUILD": [r"rebuild", r"rebuilt", r"rebuilding"],
    "LUBRICATE": [r"lubricat(?:e|ed|ing)"],
    "REMOVE": [r"remov(?:e|ed|ing)"],
    "INSTALL": [r"install(?:ed|ing)?", r"re-?install(?:ed|ing)?"],
}

CLAUSE_SPLIT_RE = re.compile(r"(?:[.;]\s+|\s+\|\s+|\s+-\s+|\n+)")
NEGATION_RE = re.compile(
    r"\b(?:no|not|never|without|did\s+not|didn't|have\s+not|has\s+not|"
    r"no\s+parts?\s+(?:changed|replaced)|not\s+changed|not\s+replaced)\b",
    re.I,
)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def norm_words(value: Any) -> str:
    s = str(value or "").upper()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return " ".join(s.split())


def split_action_label(label: str):
    s = " ".join(str(label or "").split())
    if not s:
        return None, None
    parts = s.split(None, 1)
    verb = parts[0].upper()
    obj = parts[1].strip() if len(parts) > 1 else ""
    if verb not in ACTION_VERBS or not obj:
        return None, None
    return verb, obj


def clause_has_direct_action(clause: str, action_label: str) -> bool:
    verb, obj = split_action_label(action_label)
    if not verb:
        # Unknown action type: preserve current behavior rather than invent rules.
        return True

    raw = " ".join(str(clause or "").split())
    if not raw:
        return False

    # Strong negation wins for this clause.
    if NEGATION_RE.search(raw):
        return False

    obj_words = norm_words(obj).split()
    if not obj_words:
        return False

    # Object matcher tolerates punctuation/hyphen/space differences.
    obj_pat = r"\b" + r"[\s\-_/.]*".join(re.escape(w) for w in obj_words) + r"\b"
    verb_pat = r"(?:" + "|".join(ACTION_VERBS[verb]) + r")"

    # Active voice: "replaced the motor", "replaced main motor".
    # Keep the gap small so "repair ... turned motor" cannot bind across concepts.
    active = re.compile(
        rf"\b{verb_pat}\b(?:\s+(?:the|a|an|their|this|that|main|old|bad|failed|"
        rf"damaged|defective|both|all|two|three|x\d+)){{0,4}}\s+{obj_pat}",
        re.I,
    )

    # Passive/reversed: "motor was replaced", "board repaired".
    passive = re.compile(
        rf"{obj_pat}(?:\s+(?:was|were|is|are|has\s+been|have\s+been))?\s+"
        rf"\b{verb_pat}\b",
        re.I,
    )

    return bool(active.search(raw) or passive.search(raw))


def snippet_supports_action(snippet: str, action_label: str) -> bool:
    # Evaluate clause-by-clause. This is the key protection against proximity errors.
    clauses = [x.strip() for x in CLAUSE_SPLIT_RE.split(str(snippet or "")) if x.strip()]
    return any(clause_has_direct_action(c, action_label) for c in clauses)


def event_supports_action(search_mod, event: Dict[str, Any], action_label: str) -> bool:
    try:
        snippets = search_mod._technician_repair_snippets(event)
    except Exception:
        snippets = []
    return any(snippet_supports_action(s, action_label) for s in snippets)


def make_guarded_action_aggregator(search_mod, original):
    def aggregate_repair_actions(events, limit=10):
        # Pull extra rows before filtering so false positives do not crowd out good rows.
        raw = original(events, limit=max(int(limit) * 4, 200))
        event_by_id = {
            str(e.get("repair_event_id") or ""): e
            for e in events
            if str(e.get("repair_event_id") or "")
        }

        out = []
        for row0 in raw:
            row = dict(row0)
            label = str(row.get("primary_value") or row.get("title") or "").strip()
            verb, obj = split_action_label(label)

            # Unknown action families are outside this guard's scope.
            if not verb:
                out.append(row)
                continue

            payload = dict(row.get("payload") or {})
            source_ids = [str(x) for x in (payload.get("event_ids") or []) if str(x)]
            kept_ids = [
                eid for eid in source_ids
                if eid in event_by_id and event_supports_action(search_mod, event_by_id[eid], label)
            ]

            # Normal recurring action view requires recurrence.
            if len(set(kept_ids)) < 2:
                continue

            kept_ids = sorted(set(kept_ids))
            payload["pre_direct_action_guard_repairs"] = payload.get("repairs")
            payload["pre_direct_action_guard_event_ids"] = source_ids
            payload["repairs"] = len(kept_ids)
            payload["event_ids"] = kept_ids
            payload["semantic_guard"] = "direct_action_object_same_clause_v1_5_14"
            row["payload"] = payload
            out.append(row)

        out.sort(
            key=lambda r: (
                -int((r.get("payload") or {}).get("repairs") or 0),
                str(r.get("primary_value") or "").casefold(),
            )
        )
        return out[:limit]

    return aggregate_repair_actions


def main() -> int:
    search = load_module(SEARCH_TOOL, "nova_search_v1511")
    family = load_module(FAMILY_FIRST, "nova_family_first_v1513")

    # Keep family-first resolver.
    search.resolve_base_product = family.make_family_first_resolver(search.resolve_base_product)

    # Add only the general direct-action semantic guard.
    search.aggregate_repair_actions = make_guarded_action_aggregator(
        search, search.aggregate_repair_actions
    )

    return int(search.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
