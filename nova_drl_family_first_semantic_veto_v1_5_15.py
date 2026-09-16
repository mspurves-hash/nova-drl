#!/usr/bin/env python3
"""
Nova DRL Family-First + Conservative Semantic Veto v1.5.15

Architecture:
    DRL PART # -> exact family -> family evidence -> existing v1.5.11 actions
                                             -> veto only clearly false semantics

This intentionally PRESERVES the original action parser unless source evidence
strongly contradicts the inferred action.

Strong vetoes:
1) explicit no-parts-changed / no-replacement language;
2) component is mentioned only as a test/load/operation target;
3) REPLACE <X> inferred when explicit replacement wording names another object
   in a different clause and no replacement clause even mentions X.

No database/corpus writes. No family-specific rules. No Qdrant.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any, Dict, List

SEARCH_TOOL = Path("/opt/nova-drl/tools/nova_drl_unified_knowledge_index_v1_5_11.py")
FAMILY_FIRST = Path("/opt/nova-drl/nova_drl_family_first_search_v1_5_13.py")

TEST_CONTEXT_RE = re.compile(
    r"\b(?:test(?:s|ed|ing)?|turn(?:ed|ing)?|run|runs|ran|running|"
    r"work(?:s|ed|ing)?|operat(?:e|ed|ing)|functional|load|loaded|"
    r"drive|driving|move|moves|moved|moving|hook\s*up|hookup|"
    r"me\s*server|meserver|input\s+voltage|ac\s+input)\b",
    re.I,
)

NO_PART_CHANGE_RE = re.compile(
    r"\b(?:"
    r"no\s+parts?\s+(?:were\s+)?(?:changed|replaced)"
    r"|have\s+not\s+changed\s+(?:any\s+)?parts?"
    r"|has\s+not\s+changed\s+(?:any\s+)?parts?"
    r"|did\s+not\s+(?:change|replace)\s+(?:any\s+)?parts?"
    r"|didn't\s+(?:change|replace)\s+(?:any\s+)?parts?"
    r"|no\s+parts?\s+changed"
    r"|no\s+parts?\s+replaced"
    r"|without\s+replacing\s+(?:any\s+)?parts?"
    r")\b",
    re.I,
)

REPLACE_VERB_RE = re.compile(
    r"\b(?:replac(?:e|ed|ing)|chang(?:e|ed|ing)|swapp?(?:ed|ing)?)\b",
    re.I,
)

ACTION_PATTERNS = {
    "REPLACE": r"(?:replac(?:e|ed|ing)|chang(?:e|ed|ing)|swapp?(?:ed|ing)?)",
    "REPAIR": r"(?:repair(?:ed|ing)?|fix(?:ed|ing)?)",
    "CLEAN": r"(?:clean(?:ed|ing)?)",
    "ADJUST": r"(?:adjust(?:ed|ing)?)",
    "ALIGN": r"(?:align(?:ed|ing)?)",
    "REBUILD": r"(?:rebuild|rebuilt|rebuilding)",
    "LUBRICATE": r"(?:lubricat(?:e|ed|ing))",
    "REMOVE": r"(?:remov(?:e|ed|ing))",
    "INSTALL": r"(?:install(?:ed|ing)?|re-?install(?:ed|ing)?)",
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def compact_words(v: Any) -> str:
    s = re.sub(r"[^A-Z0-9]+", " ", str(v or "").upper())
    return " ".join(s.split())


def singularish(word: str) -> str:
    w = word.upper()
    if len(w) > 4 and w.endswith("IES"):
        return w[:-3] + "Y"
    if len(w) > 3 and w.endswith("ES"):
        return w[:-2]
    if len(w) > 3 and w.endswith("S"):
        return w[:-1]
    return w


def object_tokens(obj: str) -> List[str]:
    return [singularish(x) for x in compact_words(obj).split() if x]


def clause_contains_object(clause: str, obj: str) -> bool:
    ct = [singularish(x) for x in compact_words(clause).split()]
    ot = object_tokens(obj)
    if not ot:
        return False
    return all(x in ct for x in ot)


def split_action_label(label: str):
    parts = str(label or "").strip().split(None, 1)
    if len(parts) != 2:
        return None, None
    verb = parts[0].upper()
    obj = parts[1].strip()
    if verb not in ACTION_PATTERNS or not obj:
        return None, None
    return verb, obj


def split_clauses(snippet: str) -> List[str]:
    vals = re.split(r"(?:[.;]\s+|\s+\|\s+|\n+|\s+-\s+)", str(snippet or ""))
    return [x.strip() for x in vals if x.strip()]


def direct_action_on_object(clause: str, action_label: str) -> bool:
    verb, obj = split_action_label(action_label)
    if not verb:
        return False

    ot = object_tokens(obj)
    if not ot:
        return False

    def tok_pat(tok: str) -> str:
        if tok.endswith("Y"):
            return re.escape(tok[:-1]) + r"(?:Y|IES)"
        return re.escape(tok) + r"(?:S|ES)?"

    obj_pat = r"\b" + r"[\s\-_/.]*".join(tok_pat(x) for x in ot) + r"\b"
    vp = ACTION_PATTERNS[verb]

    active = re.compile(
        rf"\b{vp}\b(?:\s+(?:the|a|an|their|this|that|main|old|bad|failed|"
        rf"damaged|defective|both|all|one|two|three|four|x\d+)){{0,4}}\s+{obj_pat}",
        re.I,
    )
    passive = re.compile(
        rf"{obj_pat}(?:\s+(?:was|were|is|are|has\s+been|have\s+been))?\s+\b{vp}\b",
        re.I,
    )
    return bool(active.search(clause) or passive.search(clause))


def event_should_be_vetoed(search_mod, event: Dict[str, Any], action_label: str) -> bool:
    verb, obj = split_action_label(action_label)
    if not verb:
        return False

    try:
        snippets = list(search_mod._technician_repair_snippets(event))
    except Exception:
        return False

    if not snippets:
        return False

    clauses = [c for snip in snippets for c in split_clauses(snip)]

    # If any clause directly supports the action-object pair, preserve it.
    if any(direct_action_on_object(c, action_label) for c in clauses):
        return False

    joined = " | ".join(str(x) for x in snippets)

    # Strong contradiction 1: explicit statement that no parts were changed.
    if verb in {"REPLACE", "INSTALL", "REMOVE"} and NO_PART_CHANGE_RE.search(joined):
        return True

    # Strong contradiction 2: object appears only in obvious test/load/operation clauses.
    obj_clauses = [c for c in clauses if clause_contains_object(c, obj)]
    if obj_clauses and all(TEST_CONTEXT_RE.search(c) for c in obj_clauses):
        return True

    # Strong contradiction 3: for REPLACE only, an explicit replacement happens
    # elsewhere, while no replacement clause even mentions the inferred object.
    #
    # This catches:
    #   "Fixed motor issue. Replaced batteries."
    #
    # But preserves:
    #   "Replaced bearings and belts."
    # because BELT is present in the same clause as REPLACED.
    if verb == "REPLACE":
        replacement_clauses = [c for c in clauses if REPLACE_VERB_RE.search(c)]
        if replacement_clauses:
            any_replacement_clause_mentions_obj = any(
                clause_contains_object(c, obj) for c in replacement_clauses
            )
            object_mentioned_elsewhere = any(clause_contains_object(c, obj) for c in clauses)
            if object_mentioned_elsewhere and not any_replacement_clause_mentions_obj:
                return True

    # Default 80/20 policy: keep original action parser result.
    return False


def make_conservative_action_aggregator(search_mod, original):
    def aggregate_repair_actions(events, limit=10):
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

            if not verb:
                out.append(row)
                continue

            payload = dict(row.get("payload") or {})
            original_ids = [str(x) for x in (payload.get("event_ids") or []) if str(x)]
            kept_ids = []
            vetoed_ids = []

            for eid in original_ids:
                ev = event_by_id.get(eid)
                if ev is not None and event_should_be_vetoed(search_mod, ev, label):
                    vetoed_ids.append(eid)
                else:
                    kept_ids.append(eid)

            kept_ids = sorted(set(kept_ids))
            vetoed_ids = sorted(set(vetoed_ids))

            if len(kept_ids) < 2:
                continue

            payload["pre_semantic_veto_repairs"] = payload.get("repairs")
            payload["pre_semantic_veto_event_ids"] = original_ids
            payload["semantic_vetoed_event_ids"] = vetoed_ids
            payload["repairs"] = len(kept_ids)
            payload["event_ids"] = kept_ids
            payload["semantic_guard"] = "conservative_contradiction_veto_v1_5_15"
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

    search.resolve_base_product = family.make_family_first_resolver(search.resolve_base_product)
    search.aggregate_repair_actions = make_conservative_action_aggregator(
        search, search.aggregate_repair_actions
    )
    return int(search.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
