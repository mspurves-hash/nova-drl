#!/usr/bin/env python3
"""
Nova DRL Parts Scale-Out Planner v1.7.0

Purpose
-------
Stop optimizing one repair family and identify where Parts work has the highest
return across the Nova DRL corpus.

This is an 80/20 PLANNING stage, not another canonicalizer.

It reads the frozen v1.6.0 repair-event corpus and ranks equipment families by
distinct repair events that contain `facts.parts_replaced` evidence.

It does NOT:
- change frozen evidence,
- canonicalize part numbers,
- apply RCL1A-specific rules,
- perform web calls,
- perform LLM calls,
- write Qdrant,
- create accepted facts.

The primary 80/20 metric is DISTINCT PARTS-BEARING REPAIR EVENTS, not raw OCR
line count. This prevents one verbose Traveler from dominating the ranking.

The planner also reports how much of the frozen event corpus has actually been
processed into facts. If v1.6.0 is still resumable/incomplete, all coverage
numbers are explicitly labeled as coverage of the CURRENTLY PROCESSED PREFIX.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.7.0"
SCHEMA = "nova-drl-parts-scale-out-planner-v1"

DEFAULT_EVENTS = Path(
    "/opt/nova-drl/output/drl_global_lossless_corpus_v1_6_0/"
    "repair_events_lossless_v1_6_0.jsonl"
)
DEFAULT_OUTPUT = Path("/opt/nova-drl/output/parts_scale_out_v1_7_0")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized_ws(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "\n".join(str(x) for x in parts)
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def sha256_file(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise RuntimeError(f"Missing input JSONL: {path}")
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception as exc:
                raise RuntimeError(f"Invalid JSONL {path}:{line_no}: {exc}") from exc
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


def family_name(row: Dict[str, Any]) -> str:
    fam = normalized_ws(row.get("equipment_family"))
    if fam:
        return fam
    for x in row.get("equipment_families") or []:
        x = normalized_ws(x)
        if x:
            return x
    return "UNKNOWN_EQUIPMENT_FAMILY"


def facts_dict(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    facts = row.get("facts")
    return facts if isinstance(facts, dict) else None


def event_is_processed(row: Dict[str, Any]) -> bool:
    """
    v1.6.0 is resumable. A frozen repair-event row may exist before the evidence
    passes have populated `facts`. Presence of a facts dictionary is our stable,
    conservative signal that the event reached the event-fact layer.
    """
    return isinstance(row.get("facts"), dict)


def parts_replaced(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    facts = facts_dict(row)
    if facts is None:
        return []
    vals = facts.get("parts_replaced")
    if not isinstance(vals, list):
        return []
    return [x for x in vals if isinstance(x, dict)]


def part_references(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    facts = facts_dict(row)
    if facts is None:
        return []
    vals = facts.get("part_references")
    if not isinstance(vals, list):
        return []
    return [x for x in vals if isinstance(x, dict)]


def source_record_ids(row: Dict[str, Any]) -> List[str]:
    vals = row.get("primary_source_record_ids") or row.get("source_record_ids") or []
    return sorted({str(x) for x in vals if str(x)})


def norm_replacement_text(item: Dict[str, Any]) -> str:
    return normalized_ws(
        item.get("text")
        or item.get("description")
        or item.get("evidence_quote")
        or ""
    ).casefold()


def norm_reference(item: Dict[str, Any]) -> str:
    return normalized_ws(
        item.get("reference")
        or item.get("part_number")
        or item.get("text")
        or ""
    ).upper()


def assign_event_family(
    row: Dict[str, Any],
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    A v1.6.0 event normally has one equipment_family. If it carries multiple
    family labels, keep the primary equipment_family and report the ambiguity.
    """
    primary = family_name(row)
    all_families = []
    if normalized_ws(row.get("equipment_family")):
        all_families.append(normalized_ws(row.get("equipment_family")))
    for x in row.get("equipment_families") or []:
        x = normalized_ws(x)
        if x and x not in all_families:
            all_families.append(x)

    if len(all_families) <= 1:
        return primary, None

    return primary, {
        "repair_event_id": str(row.get("repair_event_id") or ""),
        "selected_family": primary,
        "observed_families": all_families,
        "policy": "equipment_family primary field wins; ambiguity preserved",
    }


def build_family_stats(
    events: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    processed_events = 0
    parts_bearing_events = 0
    family_conflicts: List[Dict[str, Any]] = []

    stats: Dict[str, Dict[str, Any]] = {}

    for row in events:
        if not event_is_processed(row):
            continue

        processed_events += 1
        fam, conflict = assign_event_family(row)
        if conflict:
            family_conflicts.append(conflict)

        replaced = parts_replaced(row)
        refs = part_references(row)
        if not replaced:
            # Part references are useful for PN richness, but they are not proof
            # that a part was replaced. They must not drive the primary ranking.
            continue

        parts_bearing_events += 1
        event_id = str(row.get("repair_event_id") or "")
        s = stats.setdefault(
            fam,
            {
                "family_id": stable_id("ef_", fam),
                "equipment_family": fam,
                "parts_repair_event_ids": set(),
                "source_record_ids": set(),
                "parts_replaced_items": 0,
                "part_reference_items": 0,
                "eligible_part_reference_items": 0,
                "replacement_texts": set(),
                "part_references": set(),
            },
        )

        if event_id:
            s["parts_repair_event_ids"].add(event_id)
        s["source_record_ids"].update(source_record_ids(row))
        s["parts_replaced_items"] += len(replaced)
        s["part_reference_items"] += len(refs)
        s["eligible_part_reference_items"] += sum(
            1 for r in refs if r.get("eligible_component_reference") is True
        )

        for item in replaced:
            t = norm_replacement_text(item)
            if t:
                s["replacement_texts"].add(t)
        for ref in refs:
            t = norm_reference(ref)
            if t:
                s["part_references"].add(t)

    rows: List[Dict[str, Any]] = []
    for fam, s in stats.items():
        event_count = len(s["parts_repair_event_ids"])
        rows.append(
            {
                "family_id": s["family_id"],
                "equipment_family": fam,
                "parts_repair_events": event_count,
                "parts_replaced_items": s["parts_replaced_items"],
                "source_records": len(s["source_record_ids"]),
                "part_reference_items": s["part_reference_items"],
                "eligible_part_reference_items": s["eligible_part_reference_items"],
                "distinct_replacement_texts": len(s["replacement_texts"]),
                "distinct_part_references": len(s["part_references"]),
                "replacement_items_per_parts_event": round(
                    s["parts_replaced_items"] / event_count, 3
                )
                if event_count
                else 0.0,
                "parts_repair_event_ids": sorted(s["parts_repair_event_ids"]),
            }
        )

    rows.sort(
        key=lambda r: (
            -r["parts_repair_events"],
            -r["parts_replaced_items"],
            r["equipment_family"].casefold(),
        )
    )

    meta = {
        "frozen_event_rows": len(events),
        "processed_event_rows": processed_events,
        "parts_bearing_events": parts_bearing_events,
        "families_with_parts_replaced": len(rows),
        "family_ambiguities": family_conflicts,
    }
    return rows, meta


def apply_coverage(
    rows: Sequence[Dict[str, Any]],
    total_parts_events: int,
    target: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    cumulative = 0
    ranked: List[Dict[str, Any]] = []
    target_rows: List[Dict[str, Any]] = []

    for idx, r0 in enumerate(rows, 1):
        r = dict(r0)
        cumulative += int(r["parts_repair_events"])
        share = (r["parts_repair_events"] / total_parts_events) if total_parts_events else 0.0
        cumulative_share = cumulative / total_parts_events if total_parts_events else 0.0
        r["rank"] = idx
        r["event_share"] = round(share, 6)
        r["cumulative_event_share"] = round(cumulative_share, 6)
        r["in_80_20_target_set"] = cumulative - int(r["parts_repair_events"]) < target * total_parts_events
        ranked.append(r)
        if r["in_80_20_target_set"]:
            target_rows.append(r)

    return ranked, target_rows


def quantile_pick(
    pool: Sequence[Dict[str, Any]],
    q: float,
    used: set[str],
) -> Optional[Dict[str, Any]]:
    available = [r for r in pool if r["equipment_family"] not in used]
    if not available:
        return None
    idx = int(round((len(available) - 1) * q))
    idx = max(0, min(idx, len(available) - 1))
    return available[idx]


def build_validation_sample(
    ranked: Sequence[Dict[str, Any]],
    benchmark_family: Optional[str],
    top_n: int,
    mid_n: int,
    lower_n: int,
    min_events: int,
) -> List[Dict[str, Any]]:
    """
    Deliberately samples more than just the biggest families:
    - high-volume families test scale,
    - mid-volume families test generalization,
    - lower recurring families test robustness without wasting effort on one-offs.
    """
    eligible = [
        r
        for r in ranked
        if r["parts_repair_events"] >= min_events
        and (not benchmark_family or r["equipment_family"] != benchmark_family)
    ]
    used: set[str] = set()
    out: List[Dict[str, Any]] = []

    for r in eligible[:top_n]:
        x = dict(r)
        x["validation_stratum"] = "high_volume"
        out.append(x)
        used.add(r["equipment_family"])

    mids = [0.35, 0.50, 0.65]
    for q in mids[:mid_n]:
        r = quantile_pick(eligible, q, used)
        if r:
            x = dict(r)
            x["validation_stratum"] = "mid_volume"
            out.append(x)
            used.add(r["equipment_family"])

    lows = [0.82, 0.95]
    for q in lows[:lower_n]:
        r = quantile_pick(eligible, q, used)
        if r:
            x = dict(r)
            x["validation_stratum"] = "lower_recurring"
            out.append(x)
            used.add(r["equipment_family"])

    return out


def render_summary(
    ranked: Sequence[Dict[str, Any]],
    targets: Sequence[Dict[str, Any]],
    validation: Sequence[Dict[str, Any]],
    meta: Dict[str, Any],
    coverage_target: float,
    benchmark_family: Optional[str],
) -> str:
    frozen = int(meta["frozen_event_rows"])
    processed = int(meta["processed_event_rows"])
    parts_events = int(meta["parts_bearing_events"])
    processed_share = (processed / frozen) if frozen else 0.0
    target_coverage = (
        targets[-1]["cumulative_event_share"] if targets else 0.0
    )

    coverage_label = (
        "FULL FROZEN EVENT SET"
        if frozen and processed == frozen
        else "CURRENTLY PROCESSED v1.6.0 PREFIX/SUBSET"
    )

    lines = [
        "# Nova DRL Parts Scale-Out Planner v1.7.0",
        "",
        f"Evidence coverage mode: {coverage_label}",
        f"Frozen repair-event rows: {frozen:,}",
        f"Events with populated facts: {processed:,} ({processed_share:.1%})",
        f"Parts-bearing repair events: {parts_events:,}",
        f"Equipment families with parts-replaced evidence: {len(ranked):,}",
        f"Family-label ambiguities preserved: {len(meta['family_ambiguities']):,}",
        "",
        f"80/20 target requested: {coverage_target:.0%} of parts-bearing repair events",
        f"Families required: {len(targets):,}",
        f"Coverage achieved: {target_coverage:.1%}",
        "",
        "TOP FAMILIES BY DISTINCT PARTS-BEARING REPAIRS",
        "------------------------------------------------",
    ]

    for r in ranked[:30]:
        lines.append(
            f"{r['rank']:3}. {r['equipment_family']} "
            f"| repairs={r['parts_repair_events']} "
            f"| replacement-items={r['parts_replaced_items']} "
            f"| refs={r['distinct_part_references']} "
            f"| cumulative={r['cumulative_event_share']:.1%}"
        )

    lines.extend(
        [
            "",
            "RECOMMENDED GENERALIZATION SAMPLE",
            "---------------------------------",
        ]
    )
    if benchmark_family:
        lines.append(f"Existing benchmark excluded from sample: {benchmark_family}")
    for i, r in enumerate(validation, 1):
        lines.append(
            f"{i:2}. [{r['validation_stratum']}] {r['equipment_family']} "
            f"| repairs={r['parts_repair_events']} "
            f"| rank={r['rank']}"
        )

    lines.extend(
        [
            "",
            "POLICY",
            "------",
            "Primary rank metric: DISTINCT REPAIR EVENTS WITH parts_replaced evidence",
            "part_references are secondary context only; they do not prove replacement",
            "RCL1A-specific rules applied globally: NO",
            "Part canonicalization performed: NO",
            "Frozen evidence modified: NO",
            "LLM calls: 0",
            "Web calls: 0",
            "Accepted facts: 0",
            "Qdrant: OFF",
            "80/20 rule: FIXED DEFAULT",
            "",
            "NEXT DECISION",
            "-------------",
            "Use the ranked families and validation sample to test the Parts pipeline",
            "across representative repair lines before adding any new global rules.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Nova DRL Parts Scale-Out Planner v1.7.0"
    )
    ap.add_argument(
        "--events",
        default=str(DEFAULT_EVENTS),
        help="Frozen v1.6.0 repair_events_lossless_v1_6_0.jsonl",
    )
    ap.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT),
    )
    ap.add_argument(
        "--coverage",
        type=float,
        default=0.80,
        help="Cumulative parts-bearing repair-event coverage target; default 0.80",
    )
    ap.add_argument(
        "--benchmark-family",
        default="PS - RCL1A-1D-W3 RACAL",
        help="Existing benchmark family excluded from the diversity sample.",
    )
    ap.add_argument("--sample-top", type=int, default=5)
    ap.add_argument("--sample-mid", type=int, default=3)
    ap.add_argument("--sample-lower", type=int, default=2)
    ap.add_argument(
        "--sample-min-events",
        type=int,
        default=2,
        help="Do not waste validation slots on one-event long-tail families.",
    )
    ap.add_argument("--plan-only", action="store_true")
    args = ap.parse_args()

    if not (0.0 < args.coverage <= 1.0):
        raise SystemExit("--coverage must be >0 and <=1")

    events_path = Path(args.events)
    output_root = Path(args.output_root)
    events = read_jsonl(events_path)

    ranked0, meta = build_family_stats(events)
    ranked, targets = apply_coverage(
        ranked0,
        int(meta["parts_bearing_events"]),
        float(args.coverage),
    )
    validation = build_validation_sample(
        ranked,
        normalized_ws(args.benchmark_family) or None,
        max(0, args.sample_top),
        max(0, args.sample_mid),
        max(0, args.sample_lower),
        max(1, args.sample_min_events),
    )

    summary = render_summary(
        ranked,
        targets,
        validation,
        meta,
        float(args.coverage),
        normalized_ws(args.benchmark_family) or None,
    )
    print(summary, end="")

    manifest = {
        "version": VERSION,
        "schema": SCHEMA,
        "built_at_utc": now_utc(),
        "input": {
            "events_path": str(events_path),
            "events_sha256": sha256_file(events_path),
        },
        "counts": {
            "frozen_event_rows": meta["frozen_event_rows"],
            "processed_event_rows": meta["processed_event_rows"],
            "parts_bearing_events": meta["parts_bearing_events"],
            "families_with_parts_replaced": len(ranked),
            "coverage_target_families": len(targets),
            "validation_sample_families": len(validation),
            "family_label_ambiguities": len(meta["family_ambiguities"]),
        },
        "settings": {
            "coverage": args.coverage,
            "benchmark_family": normalized_ws(args.benchmark_family) or None,
            "sample_top": args.sample_top,
            "sample_mid": args.sample_mid,
            "sample_lower": args.sample_lower,
            "sample_min_events": args.sample_min_events,
        },
        "policy": {
            "primary_metric": "distinct repair events with facts.parts_replaced",
            "part_references_secondary_only": True,
            "rcl1a_specific_rules_global": False,
            "canonicalization": False,
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
    write_jsonl(output_root / "family_volume_v1_7_0.jsonl", ranked)
    write_jsonl(output_root / "scale_out_targets_80_20_v1_7_0.jsonl", targets)
    write_jsonl(output_root / "validation_sample_v1_7_0.jsonl", validation)
    write_jsonl(
        output_root / "family_label_ambiguities_v1_7_0.jsonl",
        meta["family_ambiguities"],
    )
    write_json(output_root / "parts_scale_out_manifest_v1_7_0.json", manifest)
    (output_root / "parts_scale_out_summary_v1_7_0.txt").write_text(
        summary, encoding="utf-8"
    )

    print(f"Outputs: {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
