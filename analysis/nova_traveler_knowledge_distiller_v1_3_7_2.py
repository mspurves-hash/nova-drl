#!/usr/bin/env python3
"""Nova DRL Provisional Knowledge Distiller v1.3.7.2.

Consumes completed v1.3.7.1 recurring-pattern output and produces technician-facing
80/20 reports. This script makes no LLM calls, does not approve facts, does not
modify source evidence, and never writes to Qdrant.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

VERSION = "1.3.7.2"
REQUIRED_REASONER_VERSION = "1.3.7.1"
DEFAULT_INPUT_ROOT = Path("/opt/nova-drl/output/traveler_large_scale_reason_v1_3_7_1")
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/traveler_knowledge_distill_v1_3_7_2")

LANE_ORDER = [
    "repairs",
    "components",
    "diagnostics",
    "testing_process",
    "terminology",
    "customer_requirements",
    "other",
]

TEMPLATE_NOISE_PHRASES = (
    "repaired replaced detailed description of repairs/replacements",
    "detailed description of repairs/replacements (including any costs for new parts)",
    "repaired replaced inits. date",
    "repaired replaced date inits",
    "repaired replaced date (m/d/yy)",
    "repaired replaced inits. date (m/d/yy)",
    "repaired replaced date inits. (m/d/yy)",
)

# Multi-tag service-area rules. They are intentionally simple and inspect both the
# provisional label and representative raw evidence. Areas may overlap.
SERVICE_AREA_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    ("A1/A2 arms & geometry", ("a1", "a2", "arm", "elbow", "wrist", "b/roll", "b/r", "sweep", "swoop", "linearity")),
    ("Belts & tension", ("belt", "belts", "belt tension", "retension")),
    ("Motors & harmonics", ("motor", "motors", "harmonic", "brush", "commutator", "blew out", "sucked")),
    ("Z-axis mechanics", ("lead screw", "leadscrew", "z1", "z2", "z3", "z axis", "z-axis", "z slide", "z rail")),
    ("Vacuum system", ("vac ", "vacuum", "vac line", "vac lines", "solenoid", "43 um", "43um", "filter")),
    ("Sensors / scanner / connectors", ("sensor", "scanner", "connector", "pins", "pin tightness", "encoder", "home sensor")),
    ("Cleaning / lubrication", ("clean", "regreas", "grease", "lub", "sucked", "blew out", "contact cleaner")),
    ("Refurbishment / rebuild", ("refurb", "rebuild", "rebuilt", "refreshed", "complete refurb")),
]

STOCKING_RULES: List[Tuple[str, Tuple[str, ...], str]] = [
    ("Belts", ("belt", "belts"), "wear item"),
    ("Bearings", ("bearing", "bearings", " bears", "bers", "r8zz", "r6zz", "nmb"), "rebuild item"),
    ("Motor brushes", ("brush", "brushes"), "motor rebuild item"),
    ("Vacuum lines / tubing", ("vac line", "vacuum line", "vac lanes"), "wear/service item"),
    ("Vacuum solenoids", ("solenoid", "solenoids"), "vacuum component"),
    ("Vacuum filters", ("43 um", "43um", "filter", "filters"), "vacuum component"),
    ("Lubricants / grease", ("grease", "regreas", "lubric"), "service consumable"),
    ("Shims / spacers", ("shim", "shims", "spacer", "spacers"), "mechanical setup item"),
    ("Sensors", ("sensor", "sensors", "scanner"), "electromechanical component"),
    ("Connector pins / contacts", ("connector", "connectors", " pin ", "pins"), "electrical service item"),
    ("Encoders", ("encoder", "encoders"), "motion component"),
    ("Motor assemblies / rebuild parts", ("motor", "motors", "harmonic"), "major rebuild attention"),
]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def normalized_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalized_lower(value: Any) -> str:
    return normalized_ws(value).lower()


def group_rank_key(g: Dict[str, Any]) -> Tuple[int, int, int, str, str]:
    return (
        -int(g.get("distinct_serial_count") or 0),
        -int(g.get("distinct_log_count") or 0),
        -int(g.get("candidate_count") or 0),
        str(g.get("lane") or ""),
        normalized_lower(g.get("concept_label")),
    )


def raw_variants(group: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = group.get("raw_variants") or []
    return [r for r in rows if isinstance(r, dict)]


def group_search_text(group: Dict[str, Any], max_variants: int = 12) -> str:
    parts = [normalized_ws(group.get("concept_label")), normalized_ws(group.get("concept_key"))]
    for row in raw_variants(group)[:max_variants]:
        parts.append(normalized_ws(row.get("raw_source_text")))
    return " ".join(x for x in parts if x).lower()


def looks_like_template_text(text: str) -> bool:
    t = normalized_lower(text)
    if not t:
        return False
    if any(p in t for p in TEMPLATE_NOISE_PHRASES):
        return True
    if t.startswith("repaired replaced") and ("inits" in t or "date" in t or "detailed description" in t):
        # Require that the line has no obvious repair payload after stripping common template words.
        stripped = re.sub(r"repaired replaced|detailed description of repairs/replacements|including any costs for new parts|date|inits|m/d/yy|\(|\)|\.|/", " ", t)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        return len(stripped) < 12
    return False


def is_clear_template_noise(group: Dict[str, Any]) -> Tuple[bool, str]:
    label = normalized_ws(group.get("concept_label"))
    if looks_like_template_text(label):
        return True, "template_like_label"
    variants = raw_variants(group)
    if not variants:
        return False, ""
    sample = variants[: min(8, len(variants))]
    template_hits = sum(1 for r in sample if looks_like_template_text(r.get("raw_source_text") or ""))
    if sample and template_hits == len(sample):
        return True, "all_sample_evidence_template_like"
    return False, ""


def matched_service_areas(group: Dict[str, Any]) -> List[str]:
    text = group_search_text(group)
    out: List[str] = []
    for area, keywords in SERVICE_AREA_RULES:
        if any(k in text for k in keywords):
            out.append(area)
    return out or ["Other / uncategorized"]


def group_logs(group: Dict[str, Any]) -> Set[str]:
    logs = {str(x) for x in (group.get("logs") or []) if str(x)}
    if logs:
        return logs
    return {str(r.get("log_number")) for r in raw_variants(group) if r.get("log_number")}


def group_serials(group: Dict[str, Any]) -> Set[str]:
    serials = {str(x) for x in (group.get("serial_numbers") or []) if str(x) and str(x) != "?"}
    if serials:
        return serials
    return {str(r.get("serial_number")) for r in raw_variants(group) if r.get("serial_number") and str(r.get("serial_number")) != "?"}


def evidence_examples(group: Dict[str, Any], limit: int = 4) -> List[Dict[str, Any]]:
    seen: Set[Tuple[str, str]] = set()
    out: List[Dict[str, Any]] = []
    for r in raw_variants(group):
        text = normalized_ws(r.get("raw_source_text"))
        log = str(r.get("log_number") or "")
        if not text:
            continue
        key = (log, text.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "log_number": r.get("log_number"),
            "serial_number": r.get("serial_number"),
            "raw_source_text": r.get("raw_source_text"),
            "candidate_id": r.get("candidate_id"),
            "source_sha256": r.get("source_sha256"),
            "source_path": r.get("source_path"),
            "raw_transcription_path": r.get("raw_transcription_path"),
        })
        if len(out) >= limit:
            break
    return out


def annotate_group(group: Dict[str, Any]) -> Dict[str, Any]:
    g = dict(group)
    g["service_areas"] = matched_service_areas(group)
    g["rank_tuple"] = {
        "distinct_serials": int(group.get("distinct_serial_count") or 0),
        "distinct_logs": int(group.get("distinct_log_count") or 0),
        "candidates": int(group.get("candidate_count") or 0),
    }
    return g


def build_service_area_rollup(groups: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    state: Dict[str, Dict[str, Any]] = {}
    for group in groups:
        for area in matched_service_areas(group):
            row = state.setdefault(area, {
                "service_area": area,
                "group_ids": set(),
                "logs": set(),
                "serials": set(),
                "candidate_count_sum": 0,
                "top_groups": [],
            })
            row["group_ids"].add(str(group.get("group_id")))
            row["logs"].update(group_logs(group))
            row["serials"].update(group_serials(group))
            row["candidate_count_sum"] += int(group.get("candidate_count") or 0)
            row["top_groups"].append(group)
    out: List[Dict[str, Any]] = []
    for area, row in state.items():
        top = sorted(row["top_groups"], key=group_rank_key)[:8]
        out.append({
            "service_area": area,
            "recurring_group_count": len(row["group_ids"]),
            "distinct_log_coverage": len(row["logs"]),
            "distinct_serial_coverage": len(row["serials"]),
            "candidate_count_sum_non_deduplicated": row["candidate_count_sum"],
            "top_patterns": [
                {
                    "group_id": g.get("group_id"),
                    "lane": g.get("lane"),
                    "concept_label": g.get("concept_label"),
                    "distinct_serial_count": g.get("distinct_serial_count"),
                    "distinct_log_count": g.get("distinct_log_count"),
                }
                for g in top
            ],
        })
    out.sort(key=lambda r: (-r["distinct_serial_coverage"], -r["distinct_log_coverage"], r["service_area"]))
    return out


def build_stocking_attention(groups: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    state: Dict[str, Dict[str, Any]] = {}
    for group in groups:
        if str(group.get("lane")) not in {"components", "repairs", "diagnostics"}:
            continue
        text = group_search_text(group)
        for item, keywords, item_type in STOCKING_RULES:
            if not any(k in text for k in keywords):
                continue
            row = state.setdefault(item, {
                "item": item,
                "type": item_type,
                "group_ids": set(),
                "logs": set(),
                "serials": set(),
                "labels": [],
            })
            row["group_ids"].add(str(group.get("group_id")))
            row["logs"].update(group_logs(group))
            row["serials"].update(group_serials(group))
            row["labels"].append(normalized_ws(group.get("concept_label")))
    out: List[Dict[str, Any]] = []
    for item, row in state.items():
        labels = [x for x in row["labels"] if x]
        top_labels = [x for x, _ in Counter(labels).most_common(5)]
        out.append({
            "item": item,
            "type": row["type"],
            "distinct_serial_coverage": len(row["serials"]),
            "distinct_log_coverage": len(row["logs"]),
            "recurring_group_count": len(row["group_ids"]),
            "example_pattern_labels": top_labels,
            "status": "provisional_stocking_attention_not_approved_bom",
        })
    out.sort(key=lambda r: (-r["distinct_serial_coverage"], -r["distinct_log_coverage"], r["item"]))
    return out


def write_patterns_csv(path: Path, groups: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "rank", "group_id", "lane", "concept_label", "distinct_serial_count",
            "distinct_log_count", "candidate_count", "service_areas", "status",
        ])
        writer.writeheader()
        for idx, g in enumerate(groups, 1):
            writer.writerow({
                "rank": idx,
                "group_id": g.get("group_id"),
                "lane": g.get("lane"),
                "concept_label": g.get("concept_label"),
                "distinct_serial_count": g.get("distinct_serial_count"),
                "distinct_log_count": g.get("distinct_log_count"),
                "candidate_count": g.get("candidate_count"),
                "service_areas": "; ".join(matched_service_areas(g)),
                "status": g.get("status"),
            })


def write_stocking_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "rank", "item", "type", "distinct_serial_coverage", "distinct_log_coverage",
            "recurring_group_count", "example_pattern_labels", "status",
        ])
        writer.writeheader()
        for idx, r in enumerate(rows, 1):
            writer.writerow({
                "rank": idx,
                "item": r["item"],
                "type": r["type"],
                "distinct_serial_coverage": r["distinct_serial_coverage"],
                "distinct_log_coverage": r["distinct_log_coverage"],
                "recurring_group_count": r["recurring_group_count"],
                "example_pattern_labels": "; ".join(r["example_pattern_labels"]),
                "status": r["status"],
            })


def render_group_block(group: Dict[str, Any], examples: int) -> List[str]:
    lines = [
        f"{group.get('group_id')} | serials={group.get('distinct_serial_count')} | logs={group.get('distinct_log_count')} | candidates={group.get('candidate_count')} | {group.get('concept_label')}",
    ]
    for ex in evidence_examples(group, examples):
        lines.append(f"  {ex.get('log_number') or '?'} | {ex.get('serial_number') or '?'} | {normalized_ws(ex.get('raw_source_text'))}")
    return lines


def render_report(
    input_data: Dict[str, Any],
    kept: Sequence[Dict[str, Any]],
    suppressed: Sequence[Dict[str, Any]],
    service_areas: Sequence[Dict[str, Any]],
    stocking: Sequence[Dict[str, Any]],
    top_overall: int,
    top_per_lane: int,
    examples: int,
) -> str:
    lines: List[str] = [
        "# Nova DRL GB8 Provisional Knowledge Distill v1.3.7.2",
        "",
        "Operating mode: FAST PROVISIONAL 80/20",
        "Method: Python-only post-processing of v1.3.7.1 recurring groups",
        f"Input recurring groups: {len(input_data.get('groups') or [])}",
        f"Main-view recurring groups: {len(kept)}",
        f"Clearly suppressed template/form-noise groups: {len(suppressed)}",
        "Accepted facts: 0",
        "Qdrant: OFF",
        "",
        "IMPORTANT",
        "---------",
        "This report is provisional technician guidance, not an approved SOP, BOM, or final repair fact set.",
        "Counts are inherited from Python-counted v1.3.7.1 recurrence. Original evidence/provenance remains authoritative.",
        "Service-area rollups can overlap because one recurring pattern may involve more than one subsystem.",
        "",
        "MOST COMMON SERVICE AREAS",
        "-------------------------",
    ]
    for idx, row in enumerate(service_areas[:12], 1):
        lines.append(
            f"{idx:>2}. {row['service_area']} | serials={row['distinct_serial_coverage']} | logs={row['distinct_log_coverage']} | recurring_groups={row['recurring_group_count']}"
        )
        for p in row["top_patterns"][:3]:
            lines.append(f"    - {p['concept_label']} ({p['distinct_serial_count']} serials / {p['distinct_log_count']} logs)")

    lines.extend(["", "TOP RECURRING PATTERNS — ALL LANES", "----------------------------------"])
    for idx, g in enumerate(kept[:top_overall], 1):
        lines.append(f"\n{idx:>2}. " + render_group_block(g, examples)[0])
        lines.extend(render_group_block(g, examples)[1:])

    for lane in LANE_ORDER:
        lane_groups = [g for g in kept if str(g.get("lane")) == lane][:top_per_lane]
        if not lane_groups:
            continue
        lines.extend(["", f"TOP {lane.upper().replace('_', ' ')} PATTERNS", "-" * (13 + len(lane))])
        for g in lane_groups:
            lines.extend(render_group_block(g, examples))

    lines.extend(["", "STOCKING / PARTS ATTENTION — PROVISIONAL", "----------------------------------------"])
    lines.append("These are repeated item families worth human parts-manager review; they are not an approved stocking BOM or quantity recommendation.")
    for idx, row in enumerate(stocking[:15], 1):
        labels = "; ".join(row["example_pattern_labels"][:3])
        lines.append(
            f"{idx:>2}. {row['item']} | serials={row['distinct_serial_coverage']} | logs={row['distinct_log_coverage']} | groups={row['recurring_group_count']} | {labels}"
        )

    lines.extend(["", "FIRST-PASS TECHNICIAN ATTENTION — PROVISIONAL", "--------------------------------------------"])
    lines.append("When inspecting a GB8, the corpus most strongly supports paying early attention to the highest-coverage service areas above.")
    for row in service_areas[:8]:
        lines.append(f"- {row['service_area']} — seen across {row['distinct_serial_coverage']} serials / {row['distinct_log_coverage']} logs")

    lines.extend(["", "SUPPRESSED FROM MAIN VIEW", "-------------------------"])
    lines.append(f"{len(suppressed)} clearly template/form-like recurring groups were retained in suppressed_template_noise_v1_3_7_2.json and omitted from the main ranking.")

    lines.extend(["", "POLICY", "------"])
    lines.extend([
        "Original Travelers/raw transcriptions modified: NO",
        "v1.3.6.1 evidence modified/replaced: NO",
        "v1.3.7.1 recurring-pattern input modified: NO",
        "New LLM calls: NO",
        "Model labels treated as approved facts: NO",
        "Automatic human approval: NO",
        "Qdrant writes: OFF",
        "Operating philosophy: FAST PROVISIONAL 80/20; amend later when useful",
    ])
    return "\n".join(lines) + "\n"


def validate_input(data: Dict[str, Any]) -> None:
    if str(data.get("reasoner_version")) != REQUIRED_REASONER_VERSION:
        raise ValueError(f"Expected reasoner_version {REQUIRED_REASONER_VERSION}, got {data.get('reasoner_version')!r}")
    if data.get("automatic_fact_acceptance") not in (False, None):
        raise ValueError("Refusing input with automatic_fact_acceptance enabled")
    if int(data.get("accepted_fact_count") or 0) != 0:
        raise ValueError("Refusing input with accepted facts")
    if int(data.get("qdrant_entries_created") or 0) != 0:
        raise ValueError("Refusing input that reports Qdrant entries")
    if not isinstance(data.get("groups"), list):
        raise ValueError("Input missing groups array")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nova DRL Provisional Knowledge Distiller v1.3.7.2 — Python-only 80/20 technician reporting")
    parser.add_argument("--input-root", default=str(DEFAULT_INPUT_ROOT), help="Completed v1.3.7.1 reasoner output root")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Writable v1.3.7.2 distiller output root")
    parser.add_argument("--top-overall", type=int, default=40, help="Top non-noise recurring patterns in main report")
    parser.add_argument("--top-per-lane", type=int, default=25, help="Top patterns shown per lane")
    parser.add_argument("--examples", type=int, default=4, help="Representative raw evidence examples per pattern")
    args = parser.parse_args(argv)

    input_root = Path(args.input_root)
    output_root = Path(args.output_root)
    source_path = input_root / "recurring_patterns_v1_3_7_1.json"
    if not source_path.exists():
        raise FileNotFoundError(f"Missing completed v1.3.7.1 recurring patterns: {source_path}")

    data = load_json(source_path)
    validate_input(data)
    groups = [g for g in data.get("groups") or [] if isinstance(g, dict)]

    kept: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []
    for g in groups:
        is_noise, reason = is_clear_template_noise(g)
        if is_noise:
            row = dict(g)
            row["v1_3_7_2_suppression_reason"] = reason
            suppressed.append(row)
        else:
            kept.append(annotate_group(g))
    kept.sort(key=group_rank_key)
    suppressed.sort(key=group_rank_key)

    service_areas = build_service_area_rollup(kept)
    stocking = build_stocking_attention(kept)

    output_root.mkdir(parents=True, exist_ok=True)
    save_json(output_root / "distilled_recurring_patterns_v1_3_7_2.json", {
        "distiller_version": VERSION,
        "source_reasoner_version": REQUIRED_REASONER_VERSION,
        "source_recurring_group_count": len(groups),
        "main_view_group_count": len(kept),
        "suppressed_template_noise_count": len(suppressed),
        "groups": kept,
        "automatic_fact_acceptance": False,
        "accepted_fact_count": 0,
        "qdrant_entries_created": 0,
    })
    save_json(output_root / "service_area_rollup_v1_3_7_2.json", {
        "distiller_version": VERSION,
        "areas": service_areas,
        "note": "Areas overlap; counts are unioned within each area from v1.3.7.1 recurring-group provenance.",
    })
    save_json(output_root / "stocking_attention_v1_3_7_2.json", {
        "distiller_version": VERSION,
        "status": "provisional_attention_not_approved_bom",
        "items": stocking,
    })
    save_json(output_root / "suppressed_template_noise_v1_3_7_2.json", {
        "distiller_version": VERSION,
        "suppressed_count": len(suppressed),
        "groups": suppressed,
        "note": "Retained for audit; only clearly template/form-like patterns are omitted from the main technician view.",
    })
    write_patterns_csv(output_root / "ranked_recurring_patterns_v1_3_7_2.csv", kept)
    write_stocking_csv(output_root / "stocking_attention_v1_3_7_2.csv", stocking)

    report = render_report(
        data, kept, suppressed, service_areas, stocking,
        max(1, int(args.top_overall)), max(1, int(args.top_per_lane)), max(1, int(args.examples)),
    )
    (output_root / "gb8_provisional_knowledge_report_v1_3_7_2.txt").write_text(report, encoding="utf-8")
    (output_root / "gb8_provisional_knowledge_report_v1_3_7_2.md").write_text(report, encoding="utf-8")

    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    manifest = {
        "distiller_version": VERSION,
        "status": "complete_provisional_knowledge_distill_not_approved",
        "operating_mode": "fast_provisional_80_20",
        "source_recurring_patterns": str(source_path),
        "source_sha256": source_sha,
        "source_reasoner_version": REQUIRED_REASONER_VERSION,
        "source_recurring_group_count": len(groups),
        "main_view_group_count": len(kept),
        "suppressed_template_noise_count": len(suppressed),
        "service_area_count": len(service_areas),
        "stocking_attention_item_count": len(stocking),
        "new_llm_calls": 0,
        "automatic_fact_acceptance": False,
        "accepted_fact_count": 0,
        "qdrant_entries_created": 0,
        "outputs": [
            "gb8_provisional_knowledge_report_v1_3_7_2.txt",
            "gb8_provisional_knowledge_report_v1_3_7_2.md",
            "distilled_recurring_patterns_v1_3_7_2.json",
            "service_area_rollup_v1_3_7_2.json",
            "stocking_attention_v1_3_7_2.json",
            "stocking_attention_v1_3_7_2.csv",
            "ranked_recurring_patterns_v1_3_7_2.csv",
            "suppressed_template_noise_v1_3_7_2.json",
        ],
    }
    save_json(output_root / "knowledge_distiller_manifest_v1_3_7_2.json", manifest)

    print("# Nova DRL Provisional Knowledge Distiller v1.3.7.2")
    print("Operating mode:             FAST PROVISIONAL 80/20")
    print("New LLM calls:              0")
    print(f"Input recurring groups:     {len(groups)}")
    print(f"Main-view groups:            {len(kept)}")
    print(f"Suppressed template noise:   {len(suppressed)}")
    print(f"Service areas:               {len(service_areas)}")
    print(f"Stocking attention items:    {len(stocking)}")
    print("Accepted facts:              0")
    print("Qdrant:                      OFF")
    print(f"Report:   {output_root / 'gb8_provisional_knowledge_report_v1_3_7_2.txt'}")
    print(f"Manifest: {output_root / 'knowledge_distiller_manifest_v1_3_7_2.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
