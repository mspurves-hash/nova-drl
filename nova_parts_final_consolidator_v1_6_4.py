#!/usr/bin/env python3
"""
Nova DRL Final Parts Consolidator v1.6.4

Downstream-only consolidation layer for validated Parts Resolver outputs.

Why this stage exists
---------------------
The v1.6.3.1 resolver intentionally separates:
- human canonicals,
- supplier aliases,
- OCR aliases,
- web validation,
- authoritative canonical refinements.

That resolver is now treated as a frozen upstream result. This stage performs
the last 80/20 cleanup that requires DRL/domain rules across candidate kinds.

Key behaviors
-------------
- Reads v1.6.3.1 outputs; never edits them.
- Rebuilds final recurrence from source candidate IDs so cross-kind merges do
  not just rename a row; their event/count evidence follows the target family.
- Can recover explicitly rule-matched screened candidates from the original
  review candidate set.
- Uses an external JSONL rules file; family-specific knowledge does not need to
  be hard-coded into the generic consolidator.
- Supports hard evidence conflicts. Conflicting contributions are quarantined
  instead of guessed.
- Qdrant OFF. Accepted facts remain 0. Frozen source evidence unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.6.4"
SCHEMA = "nova-drl-final-parts-consolidator-v1"

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def normalized_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()

def stable_id(prefix: str, *parts: Any) -> str:
    s = "\n".join(str(x) for x in parts)
    return prefix + hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

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
        raise RuntimeError(f"Missing required JSONL: {path}")
    out = []
    with path.open("r", encoding="utf-8") as f:
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

def label_norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalized_ws(value).casefold())

def candidate_text(row: Dict[str, Any], base_label: Optional[str] = None) -> str:
    parts: List[str] = []
    if base_label:
        parts.append(str(base_label))
    for key in ("display_label", "bucket_key"):
        if row.get(key):
            parts.append(str(row[key]))
    for key in ("part_number_variants", "description_variants", "evidence_examples"):
        for value in row.get(key) or []:
            if value:
                parts.append(str(value))
    return "\n".join(parts)

def row_events(row: Dict[str, Any]) -> List[str]:
    return sorted({str(x) for x in (row.get("repair_event_ids") or []) if str(x)})

def int0(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0

def load_rules(path: Path, family: str) -> List[Dict[str, Any]]:
    rows = read_jsonl(path)
    out = []
    for idx, row in enumerate(rows, 1):
        rfam = normalized_ws(row.get("family"))
        if rfam and rfam != family:
            continue
        target_label = normalized_ws(row.get("target_label"))
        target_kind = normalized_ws(row.get("target_kind") or "explicit_part_number")
        if not target_label:
            raise RuntimeError(f"Rule {idx} missing target_label")
        r = dict(row)
        r["rule_id"] = normalized_ws(row.get("rule_id")) or stable_id("mr_", idx, target_label)
        r["priority"] = int0(row.get("priority"))
        r["target_label"] = target_label
        r["target_kind"] = target_kind
        r["recover_unselected"] = bool(row.get("recover_unselected"))
        r["match_labels_norm"] = {label_norm(x) for x in (row.get("match_labels") or []) if normalized_ws(x)}
        out.append(r)
    out.sort(key=lambda x: (-x["priority"], x["rule_id"]))
    return out

def rule_matches(rule: Dict[str, Any], observed_label: str, text: str) -> bool:
    if rule.get("match_labels_norm") and label_norm(observed_label) not in rule["match_labels_norm"]:
        return False

    pattern = normalized_ws(rule.get("match_regex"))
    if pattern:
        try:
            if not re.search(pattern, text, re.I | re.M):
                return False
        except re.error as exc:
            raise RuntimeError(f"Invalid regex in {rule['rule_id']}: {exc}") from exc

    exclude = normalized_ws(rule.get("exclude_regex"))
    if exclude:
        try:
            if re.search(exclude, text, re.I | re.M):
                return False
        except re.error as exc:
            raise RuntimeError(f"Invalid exclude_regex in {rule['rule_id']}: {exc}") from exc

    # At least one match mechanism must exist.
    return bool(rule.get("match_labels_norm") or pattern)

def matching_rules(rules: Sequence[Dict[str, Any]], observed_label: str, text: str) -> List[Dict[str, Any]]:
    return [r for r in rules if rule_matches(r, observed_label, text)]

def conflict_groups(matches: Sequence[Dict[str, Any]]) -> List[str]:
    groups = sorted({normalized_ws(r.get("hard_group")) for r in matches if normalized_ws(r.get("hard_group"))})
    return groups

def choose_rule(matches: Sequence[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    A hard conflict exists only when multiple HARD groups match a single source
    contribution and those groups target different canonical labels.
    """
    if not matches:
        return None, None

    hard = [r for r in matches if normalized_ws(r.get("hard_group"))]
    hard_targets = {(r["hard_group"], r["target_label"], r["target_kind"]) for r in hard}
    groups = {x[0] for x in hard_targets}
    labels = {(x[1], x[2]) for x in hard_targets}
    if len(groups) > 1 and len(labels) > 1:
        return None, {
            "reason": "multiple_hard_domain_groups_match_same_source_candidate",
            "hard_groups": sorted(groups),
            "targets": sorted([{"label": l, "kind": k} for l, k in labels], key=lambda x: (x["label"], x["kind"])),
            "matching_rule_ids": [r["rule_id"] for r in matches],
        }

    return matches[0], None

def canonical_fallback_contribution(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "contribution_id": stable_id("fc_", rec.get("canonical_id"), rec.get("canonical_label")),
        "source_candidate_id": None,
        "base_canonical_id": rec.get("canonical_id"),
        "base_label": rec.get("canonical_label"),
        "base_kind": rec.get("canonical_kind") or "description_only",
        "repair_event_ids": list(rec.get("repair_event_ids") or []),
        "mention_count": int0(rec.get("mention_count")),
        "recorded_pieces": int0(rec.get("recorded_pieces")),
        "observed_label": rec.get("canonical_label"),
        "text": normalized_ws(rec.get("canonical_label")),
        "source": "resolver_recurring_fallback",
        "original_alias_labels": list(rec.get("absorbed_alias_labels") or []),
    }

def contribution_from_candidate(
    candidate: Dict[str, Any],
    base_label: str,
    base_kind: str,
    base_canonical_id: Optional[str],
    source: str,
) -> Dict[str, Any]:
    return {
        "contribution_id": stable_id("fc_", candidate.get("candidate_id"), base_label, source),
        "source_candidate_id": candidate.get("candidate_id"),
        "base_canonical_id": base_canonical_id,
        "base_label": base_label,
        "base_kind": base_kind,
        "repair_event_ids": row_events(candidate),
        "mention_count": int0(candidate.get("mention_count")),
        "recorded_pieces": int0(candidate.get("recorded_pieces")),
        "observed_label": normalized_ws(candidate.get("display_label") or base_label),
        "text": candidate_text(candidate, base_label),
        "source": source,
        "original_alias_labels": [],
    }

def build_base_contributions(
    recurring: Sequence[Dict[str, Any]],
    candidates_by_id: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], set[str]]:
    out = []
    used_candidate_ids: set[str] = set()

    for rec in recurring:
        source_ids = [str(x) for x in (rec.get("source_candidate_ids") or []) if str(x)]
        got = 0
        for cid in source_ids:
            c = candidates_by_id.get(cid)
            if not c:
                continue
            if cid in used_candidate_ids:
                # A candidate should contribute only once globally. If upstream
                # somehow attached it to multiple recurring canonicals, quarantine
                # would be safer than double counting; the first deterministic row wins.
                continue
            used_candidate_ids.add(cid)
            got += 1
            x = contribution_from_candidate(
                c,
                normalized_ws(rec.get("canonical_label")),
                normalized_ws(rec.get("canonical_kind") or c.get("candidate_kind") or "description_only"),
                rec.get("canonical_id"),
                "resolver_recurring_source_candidate",
            )
            x["original_alias_labels"] = list(rec.get("absorbed_alias_labels") or [])
            out.append(x)

        if got == 0:
            out.append(canonical_fallback_contribution(rec))

    return out, used_candidate_ids

def recover_rule_matched_candidates(
    all_candidates: Sequence[Dict[str, Any]],
    used_candidate_ids: set[str],
    rules: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out = []
    for c in all_candidates:
        cid = str(c.get("candidate_id") or "")
        if not cid or cid in used_candidate_ids:
            continue
        observed = normalized_ws(c.get("display_label"))
        text = candidate_text(c, observed)
        matches = [
            r for r in matching_rules(rules, observed, text)
            if r.get("recover_unselected")
        ]
        chosen, conflict = choose_rule(matches)
        if conflict or not chosen:
            continue
        out.append(
            contribution_from_candidate(
                c,
                observed,
                normalized_ws(c.get("candidate_kind") or "description_only"),
                None,
                "rule_recovered_from_review_candidates",
            )
        )
        used_candidate_ids.add(cid)
    return out

def apply_rules(
    contributions: Sequence[Dict[str, Any]],
    rules: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    resolved = []
    conflicts = []

    for c0 in contributions:
        c = dict(c0)
        observed = normalized_ws(c.get("observed_label") or c.get("base_label"))
        text = c.get("text") or observed
        matches = matching_rules(rules, observed, text)
        chosen, conflict = choose_rule(matches)

        if conflict:
            conflict_row = {
                "conflict_id": stable_id("cf_", c.get("contribution_id")),
                "source_candidate_id": c.get("source_candidate_id"),
                "base_label": c.get("base_label"),
                "observed_label": observed,
                "repair_event_ids": c.get("repair_event_ids"),
                "mention_count": c.get("mention_count"),
                "recorded_pieces": c.get("recorded_pieces"),
                "conflict": conflict,
                "evidence_excerpt": (text[:2000] if text else None),
                "raw_evidence_preserved": True,
            }
            conflicts.append(conflict_row)
            continue

        if chosen:
            c["final_label"] = chosen["target_label"]
            c["final_kind"] = chosen["target_kind"]
            c["matched_rule_id"] = chosen["rule_id"]
            c["matched_rule_authority"] = chosen.get("authority")
            c["matched_rule_reason"] = chosen.get("reason")
            c["matched_hard_group"] = chosen.get("hard_group")
            c["recovered_unselected"] = c.get("source") == "rule_recovered_from_review_candidates"
        else:
            c["final_label"] = normalized_ws(c.get("base_label"))
            c["final_kind"] = normalized_ws(c.get("base_kind") or "description_only")
            c["matched_rule_id"] = None
            c["recovered_unselected"] = False

        resolved.append(c)

    return resolved, conflicts

def aggregate_final(
    resolved: Sequence[Dict[str, Any]],
    family: str,
    min_events: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    groups: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for c in resolved:
        label = normalized_ws(c.get("final_label"))
        kind = normalized_ws(c.get("final_kind") or "description_only")
        if not label:
            continue
        key = (kind, label_norm(label))
        g = groups.setdefault(key, {
            "family": family,
            "canonical_label": label,
            "canonical_kind": kind,
            "repair_event_ids": set(),
            "mention_count": 0,
            "recorded_pieces": 0,
            "source_candidate_ids": set(),
            "source_contribution_ids": set(),
            "absorbed_labels": set(),
            "matched_rule_ids": set(),
            "authorities": set(),
            "recovered_candidate_ids": set(),
        })

        g["repair_event_ids"].update(str(x) for x in (c.get("repair_event_ids") or []) if str(x))
        g["mention_count"] += int0(c.get("mention_count"))
        g["recorded_pieces"] += int0(c.get("recorded_pieces"))
        if c.get("source_candidate_id"):
            g["source_candidate_ids"].add(str(c["source_candidate_id"]))
        if c.get("contribution_id"):
            g["source_contribution_ids"].add(str(c["contribution_id"]))
        for raw_label in (
            c.get("base_label"),
            c.get("observed_label"),
            *(c.get("original_alias_labels") or []),
        ):
            raw_label = normalized_ws(raw_label)
            if raw_label and label_norm(raw_label) != label_norm(label):
                g["absorbed_labels"].add(raw_label)
        if c.get("matched_rule_id"):
            g["matched_rule_ids"].add(str(c["matched_rule_id"]))
        if c.get("matched_rule_authority"):
            g["authorities"].add(str(c["matched_rule_authority"]))
        if c.get("recovered_unselected") and c.get("source_candidate_id"):
            g["recovered_candidate_ids"].add(str(c["source_candidate_id"]))

    all_rows = []
    recurring_rows = []
    for (kind, key_norm), g in groups.items():
        events = sorted(g["repair_event_ids"])
        row = {
            "canonical_id": stable_id("fp_", family, kind, key_norm),
            "family": family,
            "canonical_label": g["canonical_label"],
            "canonical_kind": kind,
            "repair_event_count": len(events),
            "repair_event_ids": events,
            "mention_count": g["mention_count"],
            "recorded_pieces": g["recorded_pieces"],
            "source_candidate_ids": sorted(g["source_candidate_ids"]),
            "source_contribution_ids": sorted(g["source_contribution_ids"]),
            "absorbed_alias_labels": sorted(g["absorbed_labels"]),
            "matched_rule_ids": sorted(g["matched_rule_ids"]),
            "authorities": sorted(g["authorities"]),
            "recovered_screened_candidate_ids": sorted(g["recovered_candidate_ids"]),
            "accepted_facts": 0,
            "qdrant_entries": 0,
        }
        all_rows.append(row)
        if len(events) >= min_events:
            r = dict(row)
            r["output_role"] = "final_80_20_recurring_parts"
            recurring_rows.append(r)

    all_rows.sort(key=lambda r: (-r["repair_event_count"], -r["mention_count"], r["canonical_label"].casefold()))
    recurring_rows.sort(key=lambda r: (-r["repair_event_count"], -r["mention_count"], r["canonical_label"].casefold()))
    return all_rows, recurring_rows

def main() -> int:
    ap = argparse.ArgumentParser(description="Nova DRL Final Parts Consolidator v1.6.4")
    ap.add_argument("--resolver-root", required=True)
    ap.add_argument("--review-root", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--output-root", required=True)
    ap.add_argument("--family", required=True)
    ap.add_argument("--min-recurring-events", type=int, default=2)
    ap.add_argument("--plan-only", action="store_true")
    args = ap.parse_args()

    resolver_root = Path(args.resolver_root)
    review_root = Path(args.review_root)
    rules_path = Path(args.rules)
    output_root = Path(args.output_root)
    family = normalized_ws(args.family)

    recurring_path = resolver_root / "recurring_parts_80_20_v1_6_3_1.jsonl"
    canon_path = resolver_root / "canonical_parts_v1_6_3_1.jsonl"
    candidates_path = review_root / "review_candidates.jsonl"

    recurring = read_jsonl(recurring_path)
    canonicals = read_jsonl(canon_path)
    candidates = read_jsonl(candidates_path)
    candidates_by_id = {str(c.get("candidate_id")): c for c in candidates if c.get("candidate_id")}
    rules = load_rules(rules_path, family)

    base, used = build_base_contributions(recurring, candidates_by_id)
    recovered = recover_rule_matched_candidates(candidates, used, rules)
    all_contrib = base + recovered
    resolved, conflicts = apply_rules(all_contrib, rules)
    final_all, final_recurring = aggregate_final(resolved, family, args.min_recurring_events)

    changed = [c for c in resolved if c.get("matched_rule_id")]
    recovered_changed = [c for c in changed if c.get("recovered_unselected")]

    manifest = {
        "version": VERSION,
        "schema": SCHEMA,
        "family": family,
        "built_at_utc": now_utc(),
        "inputs": {
            "resolver_root": str(resolver_root),
            "review_root": str(review_root),
            "rules": str(rules_path),
            "upstream_recurring_rows": len(recurring),
            "upstream_canonical_rows": len(canonicals),
            "review_candidates": len(candidates),
        },
        "outputs": {
            "base_contributions": len(base),
            "recovered_rule_matched_candidates": len(recovered),
            "rule_changed_contributions": len(changed),
            "hard_conflicts": len(conflicts),
            "final_canonical_rows": len(final_all),
            "final_recurring_rows": len(final_recurring),
        },
        "policy": {
            "upstream_resolver_modified": False,
            "frozen_evidence_modified": False,
            "hard_domain_conflicts_quarantined": True,
            "unselected_candidates_recovered_only_by_explicit_rule": True,
            "accepted_facts": 0,
            "qdrant_entries": 0,
            "80_20_rule": "fixed default",
        },
        "hashes": {
            "upstream_recurring": sha256_file(recurring_path),
            "upstream_canonicals": sha256_file(canon_path),
            "review_candidates": sha256_file(candidates_path),
            "rules": sha256_file(rules_path),
        },
    }

    print("# Nova DRL Final Parts Consolidator v1.6.4")
    print(f"Family:                         {family}")
    print(f"Upstream recurring rows:        {len(recurring)}")
    print(f"Base source contributions:      {len(base)}")
    print(f"Recovered rule-matched rows:    {len(recovered)}")
    print(f"Rule-changed contributions:     {len(changed)}")
    print(f"Hard conflicts quarantined:     {len(conflicts)}")
    print(f"Final canonical rows:           {len(final_all)}")
    print(f"Final recurring rows:           {len(final_recurring)}")
    print("Upstream resolver modified:     NO")
    print("Frozen evidence modified:       NO")
    print("Accepted facts:                 0")
    print("Qdrant:                         OFF")

    if changed:
        print("\nRule-applied source contributions:")
        for c in sorted(changed, key=lambda x: (x.get("final_label") or "", x.get("observed_label") or ""))[:80]:
            print(
                f"  {c.get('observed_label')} -> {c.get('final_label')}"
                f" | rule={c.get('matched_rule_id')}"
                f" | events={len(c.get('repair_event_ids') or [])}"
                + (" | RECOVERED" if c.get("recovered_unselected") else "")
            )

    if conflicts:
        print("\nHARD CONFLICTS:")
        for c in conflicts[:30]:
            print(
                f"  {c.get('observed_label')} | candidate={c.get('source_candidate_id')}"
                f" | groups={','.join(c.get('conflict', {}).get('hard_groups') or [])}"
            )

    print("\nFinal recurring Parts:")
    for i, r in enumerate(final_recurring, 1):
        print(
            f"{i:2}. {r['canonical_label']}"
            f" | repairs={r['repair_event_count']}"
            f" | mentions={r['mention_count']}"
            f" | pieces={r['recorded_pieces']}"
        )

    if args.plan_only:
        print("\nPLAN ONLY: no output files written.")
        return 0

    output_root.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_root / "final_parts_canonical_v1_6_4.jsonl", final_all)
    write_jsonl(output_root / "final_parts_recurring_80_20_v1_6_4.jsonl", final_recurring)
    write_jsonl(output_root / "final_parts_contributions_v1_6_4.jsonl", resolved)
    write_jsonl(output_root / "final_parts_conflicts_v1_6_4.jsonl", conflicts)
    write_json(output_root / "final_parts_manifest_v1_6_4.json", manifest)

    summary = [
        "# Nova DRL Final Parts Consolidator v1.6.4",
        "",
        f"Family: {family}",
        f"Upstream recurring rows: {len(recurring)}",
        f"Recovered rule-matched candidates: {len(recovered)}",
        f"Rule-changed contributions: {len(changed)}",
        f"Hard conflicts quarantined: {len(conflicts)}",
        f"Final canonical rows: {len(final_all)}",
        f"Final recurring rows: {len(final_recurring)}",
        "",
        "POLICY",
        "------",
        "Upstream resolver modified: NO",
        "Frozen evidence modified: NO",
        "Screened candidates recovered only by explicit human/domain rule: YES",
        "Hard domain conflicts quarantined instead of guessed: YES",
        "Accepted facts: 0",
        "Qdrant: OFF",
        "80/20 rule: FIXED DEFAULT",
        "",
        "FINAL RECURRING PARTS",
        "---------------------",
    ]
    for i, r in enumerate(final_recurring, 1):
        summary.append(
            f"{i:2}. {r['canonical_label']} | repairs={r['repair_event_count']}"
            f" | mentions={r['mention_count']} | pieces={r['recorded_pieces']}"
        )
    (output_root / "final_parts_summary_v1_6_4.txt").write_text(
        "\n".join(summary) + "\n", encoding="utf-8"
    )

    print(f"\nOutputs: {output_root}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
