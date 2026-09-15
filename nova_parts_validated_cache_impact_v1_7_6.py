#!/usr/bin/env python3
"""
Nova DRL Validated Parts Cache Impact Audit v1.7.6

Purpose
-------
Measure what the first validated Tier-A cache already buys us before spending
effort validating more identities.

Read-only inputs:
- v1.7.3 full-corpus global recurring PN identities
- v1.7.3 family recurring Parts rows
- v1.7.3 family summary / manifest
- v1.7.5 validated global identity cache

This stage changes nothing. It only measures:
- distinct full-corpus repair events touched by validated identities,
- share of recurring-output repair events touched,
- equipment families benefiting,
- top validated identities by repair coverage,
- top families by validated recurring Parts coverage.

No LLM, web, Qdrant, accepted facts, or fuzzy merging.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set

VERSION = "1.7.6"

DEFAULT_GATE = Path("/opt/nova-drl/output/full_corpus_parts_gate_v1_7_3")
DEFAULT_GLOBAL = DEFAULT_GATE / "global_recurring_pn_identities_v1_7_3.jsonl"
DEFAULT_FAMILY_RECURRING = DEFAULT_GATE / "family_recurring_parts_v1_7_3.jsonl"
DEFAULT_FAMILY_SUMMARY = DEFAULT_GATE / "family_summary_v1_7_3.jsonl"
DEFAULT_MANIFEST = DEFAULT_GATE / "full_corpus_parts_gate_manifest_v1_7_3.json"
DEFAULT_CACHE = Path(
    "/opt/nova-drl/output/tier_a_validation_cache_v1_7_5/"
    "validated_global_identity_cache_v1_7_5.jsonl"
)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"Missing required JSONL: {path}")
    rows = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception as exc:
                raise RuntimeError(f"Invalid JSONL {path}:{n}: {exc}") from exc
            if isinstance(row, dict):
                rows.append(row)
    return rows


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"Missing required JSON: {path}")
    with path.open("r", encoding="utf-8", errors="replace") as f:
        obj = json.load(f)
    if not isinstance(obj, dict):
        raise RuntimeError(f"Expected JSON object: {path}")
    return obj


def pct(n: int, d: int) -> float:
    return (n / d) if d else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Nova DRL Validated Parts Cache Impact Audit v1.7.6"
    )
    ap.add_argument("--global-identities", default=str(DEFAULT_GLOBAL))
    ap.add_argument("--family-recurring", default=str(DEFAULT_FAMILY_RECURRING))
    ap.add_argument("--family-summary", default=str(DEFAULT_FAMILY_SUMMARY))
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--validated-cache", default=str(DEFAULT_CACHE))
    args = ap.parse_args()

    global_rows = read_jsonl(Path(args.global_identities))
    family_rows = read_jsonl(Path(args.family_recurring))
    family_summary = read_jsonl(Path(args.family_summary))
    manifest = read_json(Path(args.manifest))
    cache = read_jsonl(Path(args.validated_cache))

    global_by_id = {
        str(r.get("global_identity_id") or ""): r
        for r in global_rows
        if r.get("global_identity_id")
    }

    validated = []
    missing = []
    for c in cache:
        gid = str(c.get("global_identity_id") or "")
        g = global_by_id.get(gid)
        if not g:
            missing.append(c)
            continue
        validated.append((c, g))

    validated_ids = {str(c.get("global_identity_id")) for c, _ in validated}
    validated_keys = {
        str(g.get("identity_key") or "")
        for _, g in validated
        if g.get("identity_key")
    }

    touched_events: Set[str] = set()
    touched_families: Set[str] = set()
    identity_impact = []

    for c, g in validated:
        events = {str(x) for x in (g.get("repair_event_ids") or []) if str(x)}
        families = {str(x) for x in (g.get("families") or []) if str(x)}
        touched_events.update(events)
        touched_families.update(families)
        identity_impact.append({
            "observed": c.get("display_label"),
            "canonical": c.get("canonical_label"),
            "manufacturer": c.get("validated_manufacturer"),
            "repair_events": len(events),
            "families": len(families),
            "mentions": int(g.get("mention_count") or 0),
            "pieces": int(g.get("recorded_pieces") or 0),
            "confidence": c.get("validation_confidence"),
        })

    identity_impact.sort(
        key=lambda r: (-r["repair_events"], -r["families"], str(r["canonical"]))
    )

    summary_by_family = {
        str(r.get("family") or ""): r
        for r in family_summary
        if r.get("family")
    }

    fam_validated_events: Dict[str, Set[str]] = defaultdict(set)
    fam_validated_identities: Dict[str, Set[str]] = defaultdict(set)

    for r in family_rows:
        if r.get("candidate_kind") != "explicit_part_number":
            continue
        key = str(r.get("identity_key") or "")
        if key not in validated_keys:
            continue
        fam = str(r.get("family") or "")
        if not fam:
            continue
        fam_validated_events[fam].update(
            str(x) for x in (r.get("repair_event_ids") or []) if str(x)
        )
        fam_validated_identities[fam].add(key)

    family_impact = []
    for fam, evs in fam_validated_events.items():
        total = int(summary_by_family.get(fam, {}).get("replacement_repair_events") or 0)
        family_impact.append({
            "family": fam,
            "validated_identities": len(fam_validated_identities[fam]),
            "validated_repair_events": len(evs),
            "replacement_repair_events": total,
            "validated_event_coverage": pct(len(evs), total),
        })

    family_impact.sort(
        key=lambda r: (
            -r["validated_repair_events"],
            -r["validated_event_coverage"],
            r["family"].casefold(),
        )
    )

    counts = manifest.get("counts") or {}
    total_replacement_events = int(counts.get("replacement_repair_events") or 0)
    recurring_output_events = int(counts.get("recurring_output_repair_events") or 0)

    print("# Nova DRL Validated Parts Cache Impact Audit v1.7.6")
    print()
    print(f"Validated cache identities loaded:      {len(cache)}")
    print(f"Validated identities joined to v1.7.3: {len(validated)}")
    print(f"Missing cache identities:               {len(missing)}")
    print(f"Distinct repair events touched:         {len(touched_events):,}")
    print(
        f"Share of all replacement repairs:       "
        f"{pct(len(touched_events), total_replacement_events):.1%}"
    )
    print(
        f"Share of recurring-output repairs:      "
        f"{pct(len(touched_events), recurring_output_events):.1%}"
    )
    print(f"Equipment families touched:             {len(touched_families):,}")
    print("Frozen evidence modified:                NO")
    print("Additional validation performed:         NO")
    print("LLM calls:                               0")
    print("Web calls:                               0")
    print("Accepted facts:                          0")
    print("Qdrant:                                  OFF")

    print("\nVALIDATED IDENTITY IMPACT")
    print("-------------------------")
    for i, r in enumerate(identity_impact, 1):
        print(
            f"{i:2}. {r['observed']} -> {r['canonical']}"
            f" | repairs={r['repair_events']}"
            f" | families={r['families']}"
            f" | mentions={r['mentions']}"
        )

    print("\nTOP EQUIPMENT FAMILIES BENEFITING")
    print("---------------------------------")
    for i, r in enumerate(family_impact[:40], 1):
        print(
            f"{i:2}. {r['family']}"
            f" | validated-identities={r['validated_identities']}"
            f" | repairs-touched={r['validated_repair_events']}"
            f"/{r['replacement_repair_events']}"
            f" ({r['validated_event_coverage']:.1%})"
        )

    print("\n80/20 DECISION GUIDE")
    print("--------------------")
    print(
        "If these first validated identities already touch a useful share of recurring "
        "repair history, freeze the cache mechanism and move forward."
    )
    print(
        "Only validate more Tier-A identities when the added coverage/value is worth it; "
        "do not chase completion."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
