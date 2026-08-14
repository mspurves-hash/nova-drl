#!/usr/bin/env python3
"""Nova DRL Technician Signal Cleaner v1.3.7.3.

Presentation-only cleanup of the completed v1.3.7.2 knowledge distill.
No LLM calls, no fact approval, no source/evidence modification, no Qdrant writes.

80/20 intent:
- keep technician-facing signal centered on repairs, diagnostics, components, testing;
- move terminology/customer/admin material to reference-only outputs;
- assign service areas from the dominant group label, not incidental evidence words;
- make stocking attention more conservative by requiring component labels or explicit
  repair/rebuild/replace/new-part evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

VERSION = "1.3.7.3"
REQUIRED_DISTILLER_VERSION = "1.3.7.2"
DEFAULT_INPUT_ROOT = Path("/opt/nova-drl/output/traveler_knowledge_distill_v1_3_7_2")
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/traveler_technician_signal_v1_3_7_3")

TECHNICIAN_LANES = {"repairs", "components", "diagnostics", "testing_process"}
REFERENCE_LANES = {"terminology", "customer_requirements", "other"}
LANE_ORDER = ["repairs", "diagnostics", "components", "testing_process"]
REFERENCE_LANE_ORDER = ["customer_requirements", "terminology", "other"]

# Form/identity artifacts that can occur even inside otherwise technician-facing lanes.
# They are never deleted; they are routed to the reference/audit side.
TECHNICIAN_PRESENTATION_NOISE_PHRASES = (
    "repaired replaced detailed description of repairs/replacements",
    "detailed description of repairs/replacements (including any costs for new parts)",
    "repaired replaced inits. date",
    "repaired replaced date inits",
    "head crash fails seeks d.o.a.",
    "head crash ☐ fails seeks ☐ d.o.a. ☐",
    "hours of work",
)

IDENTITY_LABEL_PATTERNS = (
    re.compile(r"^gb8(?:-mt)?\s*(?:\(genmark\))?$", re.I),
    re.compile(r"^rbt\s*-?\s*gb8(?:-mt)?\s*(?:\(genmark\))?$", re.I),
)

# Label-first service area rules. Matching the concept label/key rather than all raw
# evidence prevents mixed groups from contaminating unrelated subsystem totals.
SERVICE_AREA_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    ("Belts & tension", ("belt", "belts", "tension", "retension")),
    ("A1/A2 arms & geometry", ("a1", "a2", "arm", "arms", "elbow", "wrist", "b/roll", "b/r", "sweep", "swoop", "linearity")),
    ("Motors & harmonics", ("motor", "motors", "harmonic", "harmonics", "brush", "brushes", "commutator", "comm life", "suck motors", "blew out motors")),
    ("Z-axis mechanics", ("lead screw", "lead screws", "leadscrew", "z-axis", "z axis", "z1", "z2", "z3", "z motor", "z motors", "z slide", "z rail")),
    ("Vacuum system", ("vacuum", "vac ", "vac_", "vac solenoid", "vac solenoids", "vac line", "vac lines", "vacuum line", "vacuum lines", "43um", "43 um")),
    ("Sensors / scanner / connectors", ("sensor", "sensors", "scanner", "connector", "connectors", "encoder", "encoders", "home flag", "home sensor", "protrusion")),
    ("Cleaning / lubrication", ("clean", "cleaned", "regreas", "grease", "lub", "sucked", "blew out", "contact cleaner")),
    ("Refurbishment / rebuild", ("refurb", "refurbish", "rebuild", "rebuilt", "refreshed", "complete refurb")),
    ("Servo / drift / homing", ("servo", "drift", "drifting", "home", "homing", "maximum axis", "max axis")),
]

STOCKING_RULES: List[Tuple[str, Tuple[str, ...], str]] = [
    ("Belts", ("belt", "belts"), "wear item"),
    ("Bearings", ("bearing", "bearings", " bears", "bers", "r8zz", "r6zz", "nmb"), "rebuild item"),
    ("Motor brushes", ("brush", "brushes"), "motor rebuild item"),
    ("Vacuum lines / tubing", ("vac line", "vac lines", "vacuum line", "vacuum lines", "vac lanes"), "wear/service item"),
    ("Vacuum solenoids", ("vac solenoid", "vac solenoids", "vacuum solenoid", "vacuum solenoids"), "vacuum component"),
    ("Vacuum filters", ("43 um", "43um", "vac filter", "vacuum filter"), "vacuum component"),
    ("Lubricants / grease", ("grease", "regreas", "lubric"), "service consumable"),
    ("Shims / spacers", ("shim", "shims", "spacer", "spacers"), "mechanical setup item"),
    ("Sensors", ("sensor", "sensors", "scanner", "protrusion"), "electromechanical component"),
    ("Connector pins / contacts", ("connector pin", "connector pins", "connectors", " pin ", "pins"), "electrical service item"),
    ("Encoders", ("encoder", "encoders"), "motion component"),
    ("Motor assemblies / rebuild parts", ("motor", "motors", "harmonic", "harmonics"), "major rebuild attention"),
]

ACTION_CUES = (
    "replace", "replaced", "new ", "rebuilt", "rebuild", "refurb", "changed", "installed",
    "machined", "renewed", "cleaned", "regreased", "relubed", "serviced", "repaired",
)


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
    return [r for r in (group.get("raw_variants") or []) if isinstance(r, dict)]


def label_text(group: Dict[str, Any]) -> str:
    return " ".join(
        x for x in (
            normalized_ws(group.get("concept_label")),
            normalized_ws(group.get("concept_key")),
        ) if x
    ).lower()


def variant_text(row: Dict[str, Any]) -> str:
    return normalized_lower(row.get("raw_source_text"))


def group_logs(group: Dict[str, Any]) -> Set[str]:
    logs = {str(x) for x in (group.get("logs") or []) if str(x)}
    if logs:
        return logs
    return {str(r.get("log_number")) for r in raw_variants(group) if r.get("log_number")}


def group_serials(group: Dict[str, Any]) -> Set[str]:
    serials = {str(x) for x in (group.get("serial_numbers") or []) if str(x) and str(x) != "?"}
    if serials:
        return serials
    return {
        str(r.get("serial_number")) for r in raw_variants(group)
        if r.get("serial_number") and str(r.get("serial_number")) != "?"
    }


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


def is_identity_label(group: Dict[str, Any]) -> bool:
    t = normalized_ws(group.get("concept_label"))
    return any(p.fullmatch(t) for p in IDENTITY_LABEL_PATTERNS)


def is_presentation_noise(group: Dict[str, Any]) -> Tuple[bool, str]:
    t = label_text(group)
    if is_identity_label(group):
        return True, "equipment_identity_not_service_signal"
    if any(p in t for p in TECHNICIAN_PRESENTATION_NOISE_PHRASES):
        return True, "form_or_presentation_noise"
    return False, ""


def route_group(group: Dict[str, Any]) -> Tuple[str, str]:
    lane = str(group.get("lane") or "")
    noise, reason = is_presentation_noise(group)
    if noise:
        return "reference", reason
    if lane in TECHNICIAN_LANES:
        return "technician", "technician_lane"
    return "reference", "reference_lane"


def matched_service_areas(group: Dict[str, Any]) -> List[str]:
    """Assign areas from the dominant label/key only; no incidental raw-evidence matching."""
    text = label_text(group)
    out: List[str] = []
    for area, keywords in SERVICE_AREA_RULES:
        if any(k in text for k in keywords):
            out.append(area)
    return out or ["Other technician signal"]


def annotate_group(group: Dict[str, Any]) -> Dict[str, Any]:
    g = dict(group)
    g["v1_3_7_3_service_areas"] = matched_service_areas(group)
    g["v1_3_7_3_rank_tuple"] = {
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
                "groups": [],
            })
            row["group_ids"].add(str(group.get("group_id")))
            row["logs"].update(group_logs(group))
            row["serials"].update(group_serials(group))
            row["candidate_count_sum"] += int(group.get("candidate_count") or 0)
            row["groups"].append(group)

    out: List[Dict[str, Any]] = []
    for area, row in state.items():
        top = sorted(row["groups"], key=group_rank_key)[:8]
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


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    return any(k in text for k in keywords)


def matching_action_evidence(group: Dict[str, Any], keywords: Sequence[str]) -> List[Dict[str, Any]]:
    hits: List[Dict[str, Any]] = []
    for row in raw_variants(group):
        text = variant_text(row)
        if contains_any(text, keywords) and contains_any(text, ACTION_CUES):
            hits.append(row)
    return hits


def stocking_match(group: Dict[str, Any], keywords: Sequence[str]) -> Tuple[bool, Set[str], Set[str], str]:
    """Conservative match: component/repair label, or repeated explicit action evidence.

    When evidence fallback is used, coverage is counted only from the matching rows,
    not from the entire mixed group.
    """
    lane = str(group.get("lane") or "")
    if lane not in {"components", "repairs"}:
        return False, set(), set(), ""

    label = label_text(group)
    if contains_any(label, keywords):
        return True, group_logs(group), group_serials(group), "label_match"

    if lane == "repairs":
        hits = matching_action_evidence(group, keywords)
        hit_logs = {str(r.get("log_number")) for r in hits if r.get("log_number")}
        hit_serials = {
            str(r.get("serial_number")) for r in hits
            if r.get("serial_number") and str(r.get("serial_number")) != "?"
        }
        # Two independent matching evidence rows/logs keeps one stray mixed example
        # from inflating a parts family.
        if len(hit_logs) >= 2:
            return True, hit_logs, hit_serials, "repeated_explicit_action_evidence"
    return False, set(), set(), ""


def build_stocking_attention(groups: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    state: Dict[str, Dict[str, Any]] = {}
    for group in groups:
        for item, keywords, item_type in STOCKING_RULES:
            matched, logs, serials, match_basis = stocking_match(group, keywords)
            if not matched:
                continue
            row = state.setdefault(item, {
                "item": item,
                "type": item_type,
                "group_ids": set(),
                "logs": set(),
                "serials": set(),
                "labels": [],
                "match_basis": Counter(),
            })
            row["group_ids"].add(str(group.get("group_id")))
            row["logs"].update(logs)
            row["serials"].update(serials)
            row["labels"].append(normalized_ws(group.get("concept_label")))
            row["match_basis"][match_basis] += 1

    out: List[Dict[str, Any]] = []
    for item, row in state.items():
        labels = [x for x in row["labels"] if x]
        out.append({
            "item": item,
            "type": row["type"],
            "distinct_serial_coverage": len(row["serials"]),
            "distinct_log_coverage": len(row["logs"]),
            "recurring_group_count": len(row["group_ids"]),
            "example_pattern_labels": [x for x, _ in Counter(labels).most_common(5)],
            "match_basis_counts": dict(row["match_basis"]),
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
            "recurring_group_count", "example_pattern_labels", "match_basis", "status",
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
                "match_basis": json.dumps(r.get("match_basis_counts") or {}, sort_keys=True),
                "status": r["status"],
            })


def render_group_block(group: Dict[str, Any], examples: int) -> List[str]:
    lines = [
        f"{group.get('group_id')} | serials={group.get('distinct_serial_count')} | logs={group.get('distinct_log_count')} | candidates={group.get('candidate_count')} | {group.get('concept_label')}",
    ]
    for ex in evidence_examples(group, examples):
        lines.append(f"  {ex.get('log_number') or '?'} | {ex.get('serial_number') or '?'} | {normalized_ws(ex.get('raw_source_text'))}")
    return lines


def render_technician_report(
    source_count: int,
    tech_groups: Sequence[Dict[str, Any]],
    reference_groups: Sequence[Dict[str, Any]],
    service_areas: Sequence[Dict[str, Any]],
    stocking: Sequence[Dict[str, Any]],
    top_overall: int,
    top_per_lane: int,
    examples: int,
) -> str:
    lines: List[str] = [
        "# Nova DRL GB8 Technician Signal Report v1.3.7.3",
        "",
        "Operating mode: FAST PROVISIONAL 80/20",
        "Method: Python-only signal cleanup of v1.3.7.2; no new reasoning",
        f"Source recurring groups preserved: {source_count}",
        f"Technician-view groups: {len(tech_groups)}",
        f"Reference/admin groups routed out of technician ranking: {len(reference_groups)}",
        "Accepted facts: 0",
        "Qdrant: OFF",
        "",
        "IMPORTANT",
        "---------",
        "This is provisional technician guidance, not an approved SOP, BOM, or final repair fact set.",
        "v1.3.7.3 does not change any recurring group. It only changes presentation/routing.",
        "Service-area assignment is label-first to avoid incidental evidence words contaminating subsystem totals.",
        "",
        "TECHNICIAN SERVICE AREAS",
        "------------------------",
    ]
    for idx, row in enumerate(service_areas, 1):
        lines.append(
            f"{idx:>2}. {row['service_area']} | serials={row['distinct_serial_coverage']} | logs={row['distinct_log_coverage']} | recurring_groups={row['recurring_group_count']}"
        )
        for p in row["top_patterns"][:4]:
            lines.append(f"    - {p['concept_label']} ({p['distinct_serial_count']} serials / {p['distinct_log_count']} logs)")

    lines.extend(["", "TOP TECHNICIAN PATTERNS", "-----------------------"])
    for idx, g in enumerate(tech_groups[:top_overall], 1):
        block = render_group_block(g, examples)
        lines.append(f"\n{idx:>2}. " + block[0])
        lines.extend(block[1:])

    for lane in LANE_ORDER:
        lane_groups = [g for g in tech_groups if str(g.get("lane")) == lane][:top_per_lane]
        if not lane_groups:
            continue
        title = f"TOP {lane.upper().replace('_', ' ')} PATTERNS"
        lines.extend(["", title, "-" * len(title)])
        for g in lane_groups:
            lines.extend(render_group_block(g, examples))

    lines.extend(["", "STOCKING / PARTS ATTENTION — PROVISIONAL", "----------------------------------------"])
    lines.append("Conservative attention list: component/repair labels or repeated explicit repair-action evidence only; not an approved stocking BOM.")
    for idx, row in enumerate(stocking[:15], 1):
        labels = "; ".join(row["example_pattern_labels"][:3])
        lines.append(
            f"{idx:>2}. {row['item']} | serials={row['distinct_serial_coverage']} | logs={row['distinct_log_coverage']} | groups={row['recurring_group_count']} | {labels}"
        )

    lines.extend(["", "FIRST-PASS TECHNICIAN ATTENTION — PROVISIONAL", "--------------------------------------------"])
    lines.append("For a GB8 entering DRL, start with the highest-coverage service areas below, then follow unit-specific evidence.")
    for row in service_areas[:8]:
        lines.append(f"- {row['service_area']} — {row['distinct_serial_coverage']} serials / {row['distinct_log_coverage']} logs")

    lines.extend(["", "REFERENCE DATA ROUTED OUT OF TECHNICIAN RANKING", "-----------------------------------------------"])
    lines.append(f"{len(reference_groups)} recurring groups remain preserved in reference_patterns_v1_3_7_3.json, including customer requirements, terminology, admin/shipping, and presentation noise.")

    lines.extend(["", "POLICY", "------"])
    lines.extend([
        "Original Travelers/raw transcriptions modified: NO",
        "v1.3.6.1 evidence modified/replaced: NO",
        "v1.3.7.1 recurring patterns modified: NO",
        "v1.3.7.2 outputs modified: NO",
        "New LLM calls: NO",
        "Model labels treated as approved facts: NO",
        "Automatic human approval: NO",
        "Qdrant writes: OFF",
        "Operating philosophy: FAST PROVISIONAL 80/20; amend later when useful",
    ])
    return "\n".join(lines) + "\n"


def render_reference_report(reference_groups: Sequence[Dict[str, Any]], top_per_lane: int, examples: int) -> str:
    lines = [
        "# Nova DRL GB8 Reference/Admin Patterns v1.3.7.3",
        "",
        "These recurring groups are preserved but intentionally excluded from technician service-area and stocking rankings.",
        "No evidence or recurring-group data was deleted.",
    ]
    for lane in REFERENCE_LANE_ORDER + ["presentation_noise"]:
        if lane == "presentation_noise":
            groups = [g for g in reference_groups if g.get("v1_3_7_3_route_reason") not in {"reference_lane"}]
            title = "PRESENTATION / IDENTITY NOISE"
        else:
            groups = [g for g in reference_groups if str(g.get("lane")) == lane]
            title = lane.upper().replace("_", " ")
        if not groups:
            continue
        lines.extend(["", title, "-" * len(title)])
        for g in groups[:top_per_lane]:
            lines.extend(render_group_block(g, examples))
    return "\n".join(lines) + "\n"


def load_v1_3_7_2(input_root: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    distilled_path = input_root / "distilled_recurring_patterns_v1_3_7_2.json"
    suppressed_path = input_root / "suppressed_template_noise_v1_3_7_2.json"
    manifest_path = input_root / "knowledge_distiller_manifest_v1_3_7_2.json"
    for p in (distilled_path, suppressed_path, manifest_path):
        if not p.exists():
            raise FileNotFoundError(f"Missing required v1.3.7.2 output: {p}")

    distilled = load_json(distilled_path)
    suppressed = load_json(suppressed_path)
    manifest = load_json(manifest_path)
    if str(distilled.get("distiller_version")) != REQUIRED_DISTILLER_VERSION:
        raise ValueError(f"Expected distiller_version {REQUIRED_DISTILLER_VERSION}")
    if str(manifest.get("distiller_version")) != REQUIRED_DISTILLER_VERSION:
        raise ValueError(f"Expected manifest distiller_version {REQUIRED_DISTILLER_VERSION}")
    if int(distilled.get("accepted_fact_count") or 0) != 0 or int(distilled.get("qdrant_entries_created") or 0) != 0:
        raise ValueError("Refusing v1.3.7.2 input with approved facts or Qdrant entries")
    if int(manifest.get("new_llm_calls") or 0) != 0:
        raise ValueError("Unexpected LLM calls reported by v1.3.7.2")

    kept = [g for g in (distilled.get("groups") or []) if isinstance(g, dict)]
    suppressed_groups = [g for g in (suppressed.get("groups") or []) if isinstance(g, dict)]
    all_groups = kept + suppressed_groups
    expected = int(manifest.get("source_recurring_group_count") or len(all_groups))
    if len(all_groups) != expected:
        raise ValueError(f"v1.3.7.2 accounting mismatch: reconstructed {len(all_groups)} groups, expected {expected}")
    return all_groups, distilled, manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nova DRL Technician Signal Cleaner v1.3.7.3 — Python-only presentation cleanup")
    parser.add_argument("--input-root", default=str(DEFAULT_INPUT_ROOT), help="Completed v1.3.7.2 distiller output root")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Writable v1.3.7.3 output root")
    parser.add_argument("--top-overall", type=int, default=40)
    parser.add_argument("--top-per-lane", type=int, default=25)
    parser.add_argument("--examples", type=int, default=4)
    args = parser.parse_args(argv)

    input_root = Path(args.input_root)
    output_root = Path(args.output_root)
    groups, distilled, source_manifest = load_v1_3_7_2(input_root)

    technician: List[Dict[str, Any]] = []
    reference: List[Dict[str, Any]] = []
    routing_audit: List[Dict[str, Any]] = []
    for original in groups:
        group = annotate_group(original)
        route, reason = route_group(group)
        group["v1_3_7_3_route"] = route
        group["v1_3_7_3_route_reason"] = reason
        routing_audit.append({
            "group_id": group.get("group_id"),
            "lane": group.get("lane"),
            "concept_label": group.get("concept_label"),
            "route": route,
            "reason": reason,
        })
        (technician if route == "technician" else reference).append(group)

    technician.sort(key=group_rank_key)
    reference.sort(key=group_rank_key)
    service_areas = build_service_area_rollup(technician)
    stocking = build_stocking_attention(technician)

    output_root.mkdir(parents=True, exist_ok=True)
    save_json(output_root / "technician_patterns_v1_3_7_3.json", {
        "signal_cleaner_version": VERSION,
        "source_distiller_version": REQUIRED_DISTILLER_VERSION,
        "source_recurring_group_count": len(groups),
        "technician_group_count": len(technician),
        "groups": technician,
        "accepted_fact_count": 0,
        "qdrant_entries_created": 0,
    })
    save_json(output_root / "reference_patterns_v1_3_7_3.json", {
        "signal_cleaner_version": VERSION,
        "reference_group_count": len(reference),
        "groups": reference,
        "note": "Preserved recurring groups routed out of technician ranking; nothing deleted.",
    })
    save_json(output_root / "routing_audit_v1_3_7_3.json", {
        "signal_cleaner_version": VERSION,
        "source_group_count": len(groups),
        "technician_group_count": len(technician),
        "reference_group_count": len(reference),
        "rows": routing_audit,
    })
    save_json(output_root / "service_area_rollup_v1_3_7_3.json", {
        "signal_cleaner_version": VERSION,
        "assignment_method": "concept_label_and_key_only",
        "areas": service_areas,
        "note": "Areas may overlap when the dominant group label explicitly names multiple subsystems.",
    })
    save_json(output_root / "stocking_attention_v1_3_7_3.json", {
        "signal_cleaner_version": VERSION,
        "status": "provisional_attention_not_approved_bom",
        "matching_policy": "components/repairs only; label match or repeated explicit action evidence",
        "items": stocking,
    })
    write_patterns_csv(output_root / "technician_ranked_patterns_v1_3_7_3.csv", technician)
    write_stocking_csv(output_root / "stocking_attention_v1_3_7_3.csv", stocking)

    tech_report = render_technician_report(
        len(groups), technician, reference, service_areas, stocking,
        max(1, args.top_overall), max(1, args.top_per_lane), max(1, args.examples),
    )
    ref_report = render_reference_report(reference, max(1, args.top_per_lane), max(1, args.examples))
    (output_root / "gb8_technician_signal_report_v1_3_7_3.txt").write_text(tech_report, encoding="utf-8")
    (output_root / "gb8_technician_signal_report_v1_3_7_3.md").write_text(tech_report, encoding="utf-8")
    (output_root / "gb8_reference_patterns_v1_3_7_3.txt").write_text(ref_report, encoding="utf-8")

    source_files = [
        input_root / "distilled_recurring_patterns_v1_3_7_2.json",
        input_root / "suppressed_template_noise_v1_3_7_2.json",
        input_root / "knowledge_distiller_manifest_v1_3_7_2.json",
    ]
    source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    manifest = {
        "signal_cleaner_version": VERSION,
        "status": "complete_technician_signal_cleanup_not_approved",
        "operating_mode": "fast_provisional_80_20",
        "source_distiller_version": REQUIRED_DISTILLER_VERSION,
        "source_hashes": source_hashes,
        "source_recurring_group_count": len(groups),
        "technician_group_count": len(technician),
        "reference_group_count": len(reference),
        "service_area_count": len(service_areas),
        "stocking_attention_item_count": len(stocking),
        "new_llm_calls": 0,
        "automatic_fact_acceptance": False,
        "accepted_fact_count": 0,
        "qdrant_entries_created": 0,
        "v1_3_7_2_source_manifest_sha256": hashlib.sha256(json.dumps(source_manifest, sort_keys=True).encode("utf-8")).hexdigest(),
        "outputs": [
            "gb8_technician_signal_report_v1_3_7_3.txt",
            "gb8_technician_signal_report_v1_3_7_3.md",
            "gb8_reference_patterns_v1_3_7_3.txt",
            "technician_patterns_v1_3_7_3.json",
            "reference_patterns_v1_3_7_3.json",
            "routing_audit_v1_3_7_3.json",
            "service_area_rollup_v1_3_7_3.json",
            "stocking_attention_v1_3_7_3.json",
            "stocking_attention_v1_3_7_3.csv",
            "technician_ranked_patterns_v1_3_7_3.csv",
        ],
    }
    save_json(output_root / "technician_signal_manifest_v1_3_7_3.json", manifest)

    print("# Nova DRL Technician Signal Cleaner v1.3.7.3")
    print("Operating mode:             FAST PROVISIONAL 80/20")
    print("New LLM calls:              0")
    print(f"Source recurring groups:    {len(groups)}")
    print(f"Technician-view groups:     {len(technician)}")
    print(f"Reference/admin groups:     {len(reference)}")
    print(f"Service areas:              {len(service_areas)}")
    print(f"Stocking attention items:   {len(stocking)}")
    print("Accepted facts:             0")
    print("Qdrant:                     OFF")
    print(f"Report:   {output_root / 'gb8_technician_signal_report_v1_3_7_3.txt'}")
    print(f"Manifest: {output_root / 'technician_signal_manifest_v1_3_7_3.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
