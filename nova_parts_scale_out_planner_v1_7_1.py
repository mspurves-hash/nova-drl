#!/usr/bin/env python3
"""
Nova DRL Clean Parts Scale-Out Planner v1.7.1

80/20 purpose
-------------
Use the CLEAN replacement-mention corpus that already feeds the unified DRL
knowledge index to choose where Parts development should go next.

This is deliberately NOT a full-corpus canonicalizer and NOT a literal
"process enough families to reach 80% no matter how many it takes" engine.

Instead it answers:
1) Which equipment families have repeated, clean replacement activity?
2) Where is the steep/high-value part of the distribution?
3) Which small cross-family cohort should validate generalization?

Inputs default to the v1.4.7 enriched 10% corpus used by the unified index:
- repair_events_enriched_v1_4_7.jsonl
- replacement_mentions_enriched_v1_4_7.jsonl

The source is a representative clean corpus, not the full 13,166-event lossless
v1.6.0 corpus. The full lossless corpus remains preserved for later scale-up.

No LLM calls. No web. No Qdrant. No accepted facts. No evidence mutation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.7.1"
SCHEMA = "nova-drl-clean-parts-scale-out-planner-v1"

DEFAULT_ROOT = Path("/opt/nova-drl/output/drl_10pct_tracking_enrichment_v1_4_7")
DEFAULT_EVENTS = DEFAULT_ROOT / "repair_events_enriched_v1_4_7.jsonl"
DEFAULT_REPLACEMENTS = DEFAULT_ROOT / "replacement_mentions_enriched_v1_4_7.jsonl"
DEFAULT_OUTPUT = Path("/opt/nova-drl/output/parts_scale_out_v1_7_1")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized_ws(v: Any) -> str:
    return " ".join(str(v or "").split()).strip()


def sha256_file(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "\n".join(str(x) for x in parts)
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"Missing input JSONL: {path}")
    out = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception as exc:
                raise RuntimeError(f"Invalid JSONL {path}:{n}: {exc}") from exc
            if isinstance(row, dict):
                out.append(row)
    return out


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def first_value(row: Dict[str, Any], keys: Sequence[str]) -> str:
    for k in keys:
        v = normalized_ws(row.get(k))
        if v:
            return v
    return ""


def event_id(row: Dict[str, Any]) -> str:
    return first_value(row, ("repair_event_id", "event_id", "repair_id", "log_event_id"))


def family_value(row: Dict[str, Any]) -> str:
    v = first_value(
        row,
        (
            "equipment_family",
            "product_family",
            "family",
            "equipment",
            "unit_family",
            "unit_type",
        ),
    )
    if v:
        return v
    vals = row.get("equipment_families")
    if isinstance(vals, list):
        for x in vals:
            x = normalized_ws(x)
            if x:
                return x
    return ""


def part_number(row: Dict[str, Any]) -> str:
    return first_value(
        row,
        (
            "part_number",
            "manufacturer_part_number",
            "canonical_part_number",
            "pn",
        ),
    )


def description(row: Dict[str, Any]) -> str:
    return first_value(row, ("description", "text", "raw_quote", "part_description"))


def quantity(row: Dict[str, Any]) -> Optional[int]:
    for k in ("quantity", "qty", "recorded_quantity"):
        if k not in row:
            continue
        v = row.get(k)
        if isinstance(v, bool):
            return None
        try:
            v = int(v)
        except Exception:
            return None
        if 0 < v <= 10000:
            return v
        return None
    return None


def row_is_usable(row: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Be schema-tolerant. Respect explicit negative eligibility if present.
    Otherwise keep the enriched replacement row. This source already represents
    extracted replacement mentions, so absence of a newer eligibility field is
    not grounds to throw the row away.
    """
    false_flags = (
        "product_part_eligible",
        "knowledge_eligible",
        "usable_as_part",
        "eligible_for_product_parts",
    )
    for k in false_flags:
        if k in row and row.get(k) is False:
            return False, f"{k}=false"

    if row.get("procurement_only") is True:
        return False, "procurement_only=true"
    if row.get("is_procurement_only") is True:
        return False, "is_procurement_only=true"

    role = normalized_ws(row.get("source_role") or row.get("role")).casefold()
    if role in {"procurement_only", "procurement-only"}:
        return False, "procurement-only role"

    return True, "enriched_replacement_mention"


def join_family(
    mention: Dict[str, Any],
    event_by_id: Dict[str, Dict[str, Any]],
) -> Tuple[str, str]:
    fam = family_value(mention)
    if fam:
        return fam, "mention"
    eid = event_id(mention)
    if eid and eid in event_by_id:
        fam = family_value(event_by_id[eid])
        if fam:
            return fam, "event_join"
    return "UNKNOWN_EQUIPMENT_FAMILY", "unknown"


def build_stats(
    events: Sequence[Dict[str, Any]],
    mentions: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    event_by_id = {event_id(r): r for r in events if event_id(r)}
    stats: Dict[str, Dict[str, Any]] = {}
    exclusions = defaultdict(int)
    family_source_counts = defaultdict(int)
    usable_rows = 0
    unknown_family_rows = 0

    for m in mentions:
        ok, reason = row_is_usable(m)
        if not ok:
            exclusions[reason] += 1
            continue

        eid = event_id(m)
        if not eid:
            exclusions["missing_repair_event_id"] += 1
            continue

        fam, fam_source = join_family(m, event_by_id)
        family_source_counts[fam_source] += 1
        if fam == "UNKNOWN_EQUIPMENT_FAMILY":
            unknown_family_rows += 1

        usable_rows += 1
        s = stats.setdefault(
            fam,
            {
                "family_id": stable_id("ef_", fam),
                "equipment_family": fam,
                "repair_event_ids": set(),
                "replacement_mentions": 0,
                "part_numbers": set(),
                "descriptions": set(),
                "recorded_pieces": 0,
                "quantity_unstated": 0,
            },
        )
        s["repair_event_ids"].add(eid)
        s["replacement_mentions"] += 1
        pn = part_number(m)
        if pn:
            s["part_numbers"].add(pn.upper())
        desc = description(m)
        if desc:
            s["descriptions"].add(desc.casefold())
        q = quantity(m)
        if q is None:
            s["quantity_unstated"] += 1
        else:
            s["recorded_pieces"] += q

    rows = []
    for fam, s in stats.items():
        events_count = len(s["repair_event_ids"])
        rows.append(
            {
                "family_id": s["family_id"],
                "equipment_family": fam,
                "replacement_repair_events": events_count,
                "replacement_mentions": s["replacement_mentions"],
                "distinct_part_numbers": len(s["part_numbers"]),
                "distinct_descriptions": len(s["descriptions"]),
                "recorded_pieces": s["recorded_pieces"],
                "quantity_unstated_mentions": s["quantity_unstated"],
                "replacement_mentions_per_event": round(
                    s["replacement_mentions"] / events_count, 3
                ) if events_count else 0.0,
                "repair_event_ids": sorted(s["repair_event_ids"]),
            }
        )

    rows.sort(
        key=lambda r: (
            -r["replacement_repair_events"],
            -r["replacement_mentions"],
            r["equipment_family"].casefold(),
        )
    )

    all_replacement_events = set()
    for r in rows:
        all_replacement_events.update(r["repair_event_ids"])

    meta = {
        "repair_event_rows": len(events),
        "replacement_mention_rows": len(mentions),
        "usable_replacement_rows": usable_rows,
        "distinct_replacement_events": len(all_replacement_events),
        "families": len(rows),
        "unknown_family_rows": unknown_family_rows,
        "exclusions": dict(sorted(exclusions.items())),
        "family_resolution": dict(sorted(family_source_counts.items())),
    }
    return rows, meta


def add_rank_and_coverage(rows: Sequence[Dict[str, Any]], total_events: int) -> List[Dict[str, Any]]:
    out = []
    cumulative = 0
    for idx, r0 in enumerate(rows, 1):
        r = dict(r0)
        cumulative += int(r["replacement_repair_events"])
        r["rank"] = idx
        r["event_share"] = round(r["replacement_repair_events"] / total_events, 6) if total_events else 0.0
        # Family event counts are not mutually exclusive in pathological multi-family
        # joins, so clamp display coverage at 1.0.
        r["cumulative_event_share"] = round(min(1.0, cumulative / total_events), 6) if total_events else 0.0
        out.append(r)
    return out


def coverage_checkpoint(ranked: Sequence[Dict[str, Any]], target: float) -> Dict[str, Any]:
    for r in ranked:
        if r["cumulative_event_share"] >= target:
            return {
                "target": target,
                "families_required": r["rank"],
                "coverage_achieved": r["cumulative_event_share"],
                "last_family": r["equipment_family"],
                "last_family_events": r["replacement_repair_events"],
            }
    return {
        "target": target,
        "families_required": len(ranked),
        "coverage_achieved": ranked[-1]["cumulative_event_share"] if ranked else 0.0,
        "last_family": ranked[-1]["equipment_family"] if ranked else None,
        "last_family_events": ranked[-1]["replacement_repair_events"] if ranked else 0,
    }


def choose_evenly(pool: Sequence[Dict[str, Any]], n: int, used: set[str]) -> List[Dict[str, Any]]:
    available = [r for r in pool if r["equipment_family"] not in used]
    if n <= 0 or not available:
        return []
    if len(available) <= n:
        return available
    if n == 1:
        return [available[len(available)//2]]
    picks = []
    for i in range(n):
        idx = round(i * (len(available) - 1) / (n - 1))
        r = available[idx]
        if r not in picks:
            picks.append(r)
    return picks


def build_development_cohort(
    ranked: Sequence[Dict[str, Any]],
    benchmark_family: Optional[str],
    high_n: int,
    mid_n: int,
    lower_n: int,
) -> List[Dict[str, Any]]:
    eligible = [
        r for r in ranked
        if not benchmark_family or r["equipment_family"] != benchmark_family
    ]
    used: set[str] = set()
    out = []

    # High-volume = top repeated repair lines. Require >=25 clean replacement events.
    high = [r for r in eligible if r["replacement_repair_events"] >= 25]
    for r in high[:high_n]:
        x = dict(r); x["validation_stratum"] = "high_volume_25plus"
        out.append(x); used.add(r["equipment_family"])

    # Mid-volume = enough history to expose recurring OCR/parts patterns but not
    # dominant enough to simply repeat the top-family behavior.
    mid = [r for r in eligible if 10 <= r["replacement_repair_events"] <= 24]
    for r in choose_evenly(mid, mid_n, used):
        x = dict(r); x["validation_stratum"] = "mid_volume_10_to_24"
        out.append(x); used.add(r["equipment_family"])

    # Lower recurring = deliberately not one-offs. Five to nine repairs is enough
    # to test whether conservative automation survives sparse families.
    lower = [r for r in eligible if 5 <= r["replacement_repair_events"] <= 9]
    for r in choose_evenly(lower, lower_n, used):
        x = dict(r); x["validation_stratum"] = "lower_recurring_5_to_9"
        out.append(x); used.add(r["equipment_family"])

    # If any stratum is sparse, fill from the highest remaining families with >=5
    # events. We still refuse to spend a validation slot on 1-2 event long-tail data.
    target_n = high_n + mid_n + lower_n
    for r in eligible:
        if len(out) >= target_n:
            break
        if r["replacement_repair_events"] < 5 or r["equipment_family"] in used:
            continue
        x = dict(r); x["validation_stratum"] = "fill_5plus"
        out.append(x); used.add(r["equipment_family"])

    return out


def build_operational_focus(ranked: Sequence[Dict[str, Any]], min_events: int, max_families: int) -> List[Dict[str, Any]]:
    """
    Practical 80/20 cohort: repeated families only, with a hard cap so the long
    tail can never turn into hundreds of custom development targets.
    """
    rows = [r for r in ranked if r["replacement_repair_events"] >= min_events]
    return [dict(r) for r in rows[:max_families]]


def render_summary(
    ranked: Sequence[Dict[str, Any]],
    focus: Sequence[Dict[str, Any]],
    sample: Sequence[Dict[str, Any]],
    checkpoints: Sequence[Dict[str, Any]],
    meta: Dict[str, Any],
    benchmark: Optional[str],
    min_focus_events: int,
    max_focus_families: int,
) -> str:
    total_events = int(meta["distinct_replacement_events"])
    focus_events = set()
    for r in focus:
        focus_events.update(r["repair_event_ids"])
    focus_share = len(focus_events) / total_events if total_events else 0.0

    lines = [
        "# Nova DRL Clean Parts Scale-Out Planner v1.7.1",
        "",
        "Source scope: CLEAN ENRICHED 10% REPLACEMENT CORPUS",
        f"Repair-event rows: {meta['repair_event_rows']:,}",
        f"Replacement mentions: {meta['replacement_mention_rows']:,}",
        f"Usable replacement mentions: {meta['usable_replacement_rows']:,}",
        f"Distinct repairs with replacement mentions: {total_events:,}",
        f"Equipment/product families represented: {meta['families']:,}",
        f"Rows without resolved family: {meta['unknown_family_rows']:,}",
        "",
        "COVERAGE CURVE (descriptive only — not a mandate to chase 80%)",
        "-------------------------------------------------------------",
    ]
    for c in checkpoints:
        lines.append(
            f"{c['target']:.0%}: {c['families_required']} families "
            f"| achieved={c['coverage_achieved']:.1%} "
            f"| cutoff={c['last_family_events']} repairs"
        )

    lines.extend([
        "",
        "PRACTICAL 80/20 DEVELOPMENT FOCUS",
        "---------------------------------",
        f"Rule: >= {min_focus_events} clean replacement repairs; hard cap {max_focus_families} families",
        f"Families selected: {len(focus)}",
        f"Distinct replacement repairs covered by selected families: {len(focus_events):,} ({focus_share:.1%})",
    ])
    for r in focus[:30]:
        lines.append(
            f"{r['rank']:3}. {r['equipment_family']} "
            f"| repairs={r['replacement_repair_events']} "
            f"| mentions={r['replacement_mentions']} "
            f"| PNs={r['distinct_part_numbers']}"
        )

    lines.extend([
        "",
        "GENERALIZATION SAMPLE",
        "---------------------",
    ])
    if benchmark:
        lines.append(f"Existing benchmark excluded: {benchmark}")
    for i, r in enumerate(sample, 1):
        lines.append(
            f"{i:2}. [{r['validation_stratum']}] {r['equipment_family']} "
            f"| repairs={r['replacement_repair_events']} | rank={r['rank']}"
        )

    lines.extend([
        "",
        "POLICY",
        "------",
        "Literal 80% family coverage is descriptive only, NOT the development target",
        "One-off and 2-event families are excluded from validation effort by default",
        "RCL1A-specific canonical/fuse rules applied globally: NO",
        "Part canonicalization performed: NO",
        "Frozen evidence modified: NO",
        "LLM calls: 0",
        "Web calls: 0",
        "Accepted facts: 0",
        "Qdrant: OFF",
        "80/20 rule: FIXED DEFAULT",
        "",
        "NEXT STEP",
        "---------",
        "Run Parts candidate/resolver evaluation only on the generated generalization sample.",
        "Promote a new GLOBAL cleanup rule only when it recurs across multiple families.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Nova DRL Clean Parts Scale-Out Planner v1.7.1")
    ap.add_argument("--events", default=str(DEFAULT_EVENTS))
    ap.add_argument("--replacements", default=str(DEFAULT_REPLACEMENTS))
    ap.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--benchmark-family", default="PS - RCL1A-1D-W3 RACAL")
    ap.add_argument("--focus-min-events", type=int, default=10)
    ap.add_argument("--focus-max-families", type=int, default=40)
    ap.add_argument("--sample-high", type=int, default=5)
    ap.add_argument("--sample-mid", type=int, default=3)
    ap.add_argument("--sample-lower", type=int, default=2)
    ap.add_argument("--plan-only", action="store_true")
    args = ap.parse_args()

    events_path = Path(args.events)
    replacements_path = Path(args.replacements)
    output_root = Path(args.output_root)

    events = read_jsonl(events_path)
    replacements = read_jsonl(replacements_path)
    rows0, meta = build_stats(events, replacements)
    ranked = add_rank_and_coverage(rows0, int(meta["distinct_replacement_events"]))

    checkpoints = [coverage_checkpoint(ranked, x) for x in (0.10, 0.20, 0.30, 0.50, 0.80)]
    focus = build_operational_focus(
        ranked,
        max(1, args.focus_min_events),
        max(1, args.focus_max_families),
    )
    sample = build_development_cohort(
        ranked,
        normalized_ws(args.benchmark_family) or None,
        max(0, args.sample_high),
        max(0, args.sample_mid),
        max(0, args.sample_lower),
    )

    summary = render_summary(
        ranked, focus, sample, checkpoints, meta,
        normalized_ws(args.benchmark_family) or None,
        max(1, args.focus_min_events),
        max(1, args.focus_max_families),
    )
    print(summary, end="")

    manifest = {
        "version": VERSION,
        "schema": SCHEMA,
        "built_at_utc": now_utc(),
        "input": {
            "events": str(events_path),
            "events_sha256": sha256_file(events_path),
            "replacements": str(replacements_path),
            "replacements_sha256": sha256_file(replacements_path),
            "scope": "clean_enriched_10pct_corpus",
        },
        "counts": meta,
        "settings": {
            "benchmark_family": normalized_ws(args.benchmark_family) or None,
            "focus_min_events": args.focus_min_events,
            "focus_max_families": args.focus_max_families,
            "sample_high": args.sample_high,
            "sample_mid": args.sample_mid,
            "sample_lower": args.sample_lower,
        },
        "coverage_checkpoints": checkpoints,
        "policy": {
            "literal_80pct_is_mandate": False,
            "canonicalization": False,
            "rcl1a_rules_global": False,
            "frozen_evidence_modified": False,
            "llm_calls": 0,
            "web_calls": 0,
            "accepted_facts": 0,
            "qdrant_entries": 0,
            "80_20_rule": "fixed default",
        },
    }

    if args.plan_only:
        print("PLAN ONLY: no output files written.")
        return 0

    output_root.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_root / "clean_family_volume_v1_7_1.jsonl", ranked)
    write_jsonl(output_root / "operational_focus_v1_7_1.jsonl", focus)
    write_jsonl(output_root / "generalization_sample_v1_7_1.jsonl", sample)
    write_json(output_root / "parts_scale_out_manifest_v1_7_1.json", manifest)
    (output_root / "parts_scale_out_summary_v1_7_1.txt").write_text(summary, encoding="utf-8")
    print(f"Outputs: {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
