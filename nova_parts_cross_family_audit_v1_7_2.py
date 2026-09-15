#!/usr/bin/env python3
"""
Nova DRL Cross-Family Parts Generalization Audit v1.7.2

Purpose
-------
Evaluate whether the conservative Parts lessons learned on RCL1A generalize
across a deliberately diverse 10-family cohort.

This is an AUDIT, not an auto-canonicalization pass.

It reads the clean v1.4.7 enriched replacement corpus, builds the same
conservative candidate buckets used by the human-review workspace, and reports:
- recurring vs one-off candidate pressure,
- explicit-PN vs description-only mix,
- recurring candidate event coverage,
- supplier-wrapper observations,
- spec-like strings masquerading as PNs,
- conservative OCR/format near-duplicate PAIRS within each family.

Important safeguards
--------------------
- No RCL1A canonical vocabulary is applied to other families.
- No RCL1A fuse rules are applied globally.
- Near-duplicate pairs are PROPOSALS ONLY, never merges.
- Numeric model/rating conflicts veto a safe OCR proposal.
- Supplier wrappers are reported separately from OCR deviations.
- No LLM calls, web calls, Qdrant writes, or accepted facts.
- Frozen evidence is never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.7.2"
SCHEMA = "nova-drl-cross-family-parts-generalization-audit-v1"

DEFAULT_ROOT = Path("/opt/nova-drl/output/drl_10pct_tracking_enrichment_v1_4_7")
DEFAULT_EVENTS = DEFAULT_ROOT / "repair_events_enriched_v1_4_7.jsonl"
DEFAULT_REPLACEMENTS = DEFAULT_ROOT / "replacement_mentions_enriched_v1_4_7.jsonl"
DEFAULT_OUTPUT = Path("/opt/nova-drl/output/parts_generalization_audit_v1_7_2")

DEFAULT_FAMILIES = [
    "THERM ARRAY - 1957617006K PRESCOT",
    "RBT - GB8-MT GENMARK",
    "BRD - BM23995 EXEC CAR ASYST",
    "SVO MTR - 14204E239 PITTMAN",
    "PS -00010-93076 AMAT",
    "MTR - 0010-70264 AMAT",
    "HEAT EXCHANGER - ETN23A-SC-B ORION",
    "BRD - 3200-1000-09 ARM CNTL ASYST",
    "BRD - BM23994 CAR CHARGER ASYST",
    "SVO DRV - MR-J2S-20A MITSUBISHI",
]

OCR_GROUPS = [
    set("0ODQ"),
    set("1IL"),
    set("2Z"),
    set("5S"),
    set("6G"),
    set("8B"),
]

RATING_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:A|AMP|AMPS|V|VAC|VDC|W|WATT|WATTS|OHM|OHMS|UF|PF|NF|HZ|KHZ|MHZ)\b",
    re.I,
)


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized_ws(v: Any) -> str:
    return " ".join(str(v or "").split()).strip()


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
        ("equipment_family", "product_family", "family", "equipment", "unit_family", "unit_type"),
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
    return first_value(row, ("part_number", "manufacturer_part_number", "canonical_part_number", "pn"))


def description(row: Dict[str, Any]) -> str:
    return first_value(row, ("description", "text", "raw_quote", "part_description"))


def raw_quote(row: Dict[str, Any]) -> str:
    return first_value(row, ("raw_quote", "evidence_quote", "description", "text"))


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
        return v if 0 < v <= 10000 else None
    return None


def source_record_ids(row: Dict[str, Any]) -> List[str]:
    vals = row.get("source_record_ids") or row.get("primary_source_record_ids") or []
    return sorted({str(x) for x in vals if str(x)})


def row_is_usable(row: Dict[str, Any]) -> bool:
    for k in ("product_part_eligible", "knowledge_eligible", "usable_as_part", "eligible_for_product_parts"):
        if k in row and row.get(k) is False:
            return False
    if row.get("procurement_only") is True or row.get("is_procurement_only") is True:
        return False
    role = normalized_ws(row.get("source_role") or row.get("role")).casefold()
    return role not in {"procurement_only", "procurement-only"}


def compact(v: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", normalized_ws(v).upper())


def punct_compact(v: Any) -> str:
    return re.sub(r"\s+", "", normalized_ws(v).upper())


def pn_bucket_key(v: str) -> str:
    # Same conservative behavior as the human review workspace: remove spaces,
    # retain punctuation.
    return punct_compact(v)


def desc_bucket_key(v: str) -> str:
    return normalized_ws(v).casefold()


def same_ocr_group(a: str, b: str) -> bool:
    if a == b:
        return True
    return any(a in g and b in g for g in OCR_GROUPS)


def weighted_edit_distance(a: str, b: str) -> float:
    a, b = compact(a), compact(b)
    if a == b:
        return 0.0
    if not a:
        return float(len(b))
    if not b:
        return float(len(a))
    prev2 = None
    prev = [float(i) for i in range(len(b) + 1)]
    for i, ca in enumerate(a, 1):
        cur = [float(i)] + [0.0] * len(b)
        for j, cb in enumerate(b, 1):
            sub = 0.0 if ca == cb else (0.25 if same_ocr_group(ca, cb) else 1.0)
            cur[j] = min(prev[j] + 1.0, cur[j-1] + 1.0, prev[j-1] + sub)
            if prev2 is not None and i > 1 and j > 1 and a[i-1] == b[j-2] and a[i-2] == b[j-1]:
                cur[j] = min(cur[j], prev2[j-2] + 0.60)
        prev2, prev = prev, cur
    return prev[-1]


def pn_similarity(a: str, b: str) -> Tuple[float, List[str]]:
    aa, bb = compact(a), compact(b)
    reasons = []
    if not aa or not bb:
        return 0.0, reasons
    if aa == bb:
        return 1.0, ["same_alnum_skeleton"]
    maxlen = max(len(aa), len(bb))
    dist = weighted_edit_distance(aa, bb)
    score = max(0.0, 1.0 - dist / maxlen)
    reasons.append(f"weighted_edit={score:.3f}")
    shorter, longer = (aa, bb) if len(aa) <= len(bb) else (bb, aa)
    missing = len(longer) - len(shorter)
    if len(shorter) >= 6 and missing <= 5:
        if longer.endswith(shorter):
            cov = len(shorter) / len(longer)
            score = max(score, min(0.985, 0.90 + 0.08 * cov))
            reasons.append(f"suffix_containment={cov:.3f}")
        elif longer.startswith(shorter):
            cov = len(shorter) / len(longer)
            score = max(score, min(0.975, 0.89 + 0.08 * cov))
            reasons.append(f"prefix_containment={cov:.3f}")
        elif shorter in longer and missing <= 3:
            cov = len(shorter) / len(longer)
            score = max(score, min(0.955, 0.87 + 0.07 * cov))
            reasons.append(f"internal_containment={cov:.3f}")
    if punct_compact(a) == punct_compact(b):
        score = max(score, 0.998)
        reasons.append("same_punct_compact")
    return min(1.0, score), reasons


def numeric_signature(label: str) -> Tuple[int, ...]:
    vals = []
    for token in re.findall(r"\d+", compact(label)):
        try:
            vals.append(int(token))
        except Exception:
            pass
    return tuple(vals)


def rating_tokens(label: str) -> Tuple[str, ...]:
    vals = []
    for x in RATING_RE.findall(normalized_ws(label).upper()):
        vals.append(re.sub(r"\s+", "", x.upper()))
    return tuple(sorted(set(vals)))


def looks_spec_like(label: str) -> bool:
    raw = normalized_ws(label).upper()
    ratings = rating_tokens(raw)
    # If the string is mostly numbers + electrical units, it is a spec, not a PN.
    scrub = RATING_RE.sub(" ", raw)
    scrub = re.sub(r"[^A-Z]+", " ", scrub)
    words = [w for w in scrub.split() if w not in {"FUSE", "MOSFET", "MOTOR", "CAP", "CAPACITOR", "RESISTOR"}]
    return bool(ratings) and not words


def looks_like_part_number(label: str, event_count: int) -> bool:
    raw = normalized_ws(label).upper()
    skel = compact(raw)
    if len(skel) < 4 or len(skel) > 36:
        return False
    if looks_spec_like(raw):
        return False
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9./_+\- ]*[A-Z0-9]", raw):
        return False
    has_alpha = any(c.isalpha() for c in skel)
    has_digit = any(c.isdigit() for c in skel)
    if has_alpha and has_digit:
        return True
    return skel.isdigit() and len(skel) >= 5 and event_count >= 3


def detect_supplier(label: str) -> Optional[Dict[str, str]]:
    raw = punct_compact(label)
    if raw.startswith("511-") and len(raw) > 4:
        return {"supplier": "Mouser", "supplier_part_number": raw, "body": raw[4:]}
    if raw.endswith("-ND") and len(raw) > 3:
        return {"supplier": "DigiKey", "supplier_part_number": raw, "body": raw[:-3]}
    return None


def build_candidate_rows(mentions: Sequence[Dict[str, Any]], family: str) -> List[Dict[str, Any]]:
    buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for m in mentions:
        pn = part_number(m)
        desc = description(m) or raw_quote(m)
        if pn:
            kind = "explicit_part_number"; key = pn_bucket_key(pn)
        else:
            kind = "description_only"; key = desc_bucket_key(desc)
        if not key:
            continue
        buckets[(kind, key)].append(m)

    out = []
    for (kind, key), rows in buckets.items():
        events = sorted({event_id(r) for r in rows if event_id(r)})
        pns = sorted({part_number(r) for r in rows if part_number(r)})
        descs = sorted({description(r) for r in rows if description(r)})
        examples = []
        for r in rows:
            q = raw_quote(r)
            if q and q not in examples:
                examples.append(q)
            if len(examples) >= 6:
                break
        pieces = 0; q_unstated = 0
        for r in rows:
            q = quantity(r)
            if q is None: q_unstated += 1
            else: pieces += q
        label = pns[0] if pns else (descs[0] if descs else key)
        out.append({
            "candidate_id": stable_id("pc_", family, kind, key),
            "family": family,
            "candidate_kind": kind,
            "bucket_key": key,
            "display_label": label,
            "part_number_variants": pns,
            "description_variants": descs[:20],
            "repair_event_count": len(events),
            "repair_event_ids": events,
            "mention_count": len(rows),
            "recorded_pieces": pieces,
            "quantity_unstated_mentions": q_unstated,
            "evidence_examples": examples,
            "source_record_ids": sorted({sid for r in rows for sid in source_record_ids(r)}),
        })
    out.sort(key=lambda r: (-r["repair_event_count"], -r["mention_count"], r["display_label"].casefold()))
    return out


def safe_pair(a: Dict[str, Any], b: Dict[str, Any], threshold: float) -> Optional[Dict[str, Any]]:
    if a["candidate_kind"] != "explicit_part_number" or b["candidate_kind"] != "explicit_part_number":
        return None
    la, lb = a["display_label"], b["display_label"]
    if detect_supplier(la) or detect_supplier(lb):
        return None
    if not looks_like_part_number(la, a["repair_event_count"]):
        return None
    if not looks_like_part_number(lb, b["repair_event_count"]):
        return None
    score, reasons = pn_similarity(la, lb)
    if score < threshold:
        return None
    siga, sigb = numeric_signature(la), numeric_signature(lb)
    numeric_conflict = bool(siga and sigb and siga != sigb)
    ra, rb = rating_tokens(la), rating_tokens(lb)
    rating_conflict = bool(ra and rb and ra != rb)
    if numeric_conflict or rating_conflict:
        return None
    combined_events = sorted(set(a["repair_event_ids"]) | set(b["repair_event_ids"]))
    if len(combined_events) < 2:
        return None
    pair_type = "format_duplicate" if compact(la) == compact(lb) else "ocr_near_duplicate"
    return {
        "pair_id": stable_id("ap_", a["family"], a["candidate_id"], b["candidate_id"]),
        "family": a["family"],
        "pair_type": pair_type,
        "left_candidate_id": a["candidate_id"],
        "left_label": la,
        "left_events": a["repair_event_count"],
        "right_candidate_id": b["candidate_id"],
        "right_label": lb,
        "right_events": b["repair_event_count"],
        "combined_event_count": len(combined_events),
        "similarity": round(score, 6),
        "reasons": reasons,
        "numeric_signature": list(siga or sigb),
        "status": "AUDIT_PROPOSAL_ONLY",
    }


def pair_signature(pair: Dict[str, Any]) -> str:
    reasons = pair.get("reasons") or []
    if pair.get("pair_type") == "format_duplicate":
        return "punctuation_or_spacing_only"
    if any("suffix_containment" in x for x in reasons):
        return "missing_prefix_body_retained"
    if any("prefix_containment" in x for x in reasons):
        return "missing_suffix_body_retained"
    if any("internal_containment" in x for x in reasons):
        return "internal_containment"
    return "weighted_ocr_edit"


def analyze_family(candidates: Sequence[Dict[str, Any]], family_event_ids: set[str], threshold: float) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    recurring = [c for c in candidates if c["repair_event_count"] >= 2]
    recurring3 = [c for c in candidates if c["repair_event_count"] >= 3]
    oneoffs = [c for c in candidates if c["repair_event_count"] == 1]
    pns = [c for c in candidates if c["candidate_kind"] == "explicit_part_number"]
    descs = [c for c in candidates if c["candidate_kind"] == "description_only"]
    recurring_events = set()
    for c in recurring:
        recurring_events.update(c["repair_event_ids"])

    supplier = []
    spec_like = []
    for c in pns:
        d = detect_supplier(c["display_label"])
        if d:
            supplier.append({**d, "candidate_id": c["candidate_id"], "events": c["repair_event_count"]})
        if looks_spec_like(c["display_label"]):
            spec_like.append({"candidate_id": c["candidate_id"], "label": c["display_label"], "events": c["repair_event_count"]})

    pairs = []
    for i in range(len(pns)):
        for j in range(i+1, len(pns)):
            p = safe_pair(pns[i], pns[j], threshold)
            if p:
                p["pattern"] = pair_signature(p)
                pairs.append(p)
    pairs.sort(key=lambda p: (-p["combined_event_count"], -p["similarity"], p["left_label"], p["right_label"]))

    total_events = len(family_event_ids)
    metric = {
        "family": candidates[0]["family"] if candidates else "UNKNOWN",
        "replacement_repair_events": total_events,
        "candidates_total": len(candidates),
        "explicit_pn_candidates": len(pns),
        "description_only_candidates": len(descs),
        "recurring_2plus_candidates": len(recurring),
        "recurring_3plus_candidates": len(recurring3),
        "oneoff_candidates": len(oneoffs),
        "oneoff_share": round(len(oneoffs) / len(candidates), 6) if candidates else 0.0,
        "repair_events_covered_by_recurring_candidates": len(recurring_events),
        "recurring_candidate_event_coverage": round(len(recurring_events) / total_events, 6) if total_events else 0.0,
        "supplier_wrapper_candidates": len(supplier),
        "spec_like_pn_candidates": len(spec_like),
        "safe_alias_pair_proposals": len(pairs),
        "pair_patterns": dict(Counter(p["pattern"] for p in pairs)),
        "top_candidates": [
            {
                "label": c["display_label"],
                "kind": c["candidate_kind"],
                "repairs": c["repair_event_count"],
                "mentions": c["mention_count"],
                "pieces": c["recorded_pieces"],
            }
            for c in candidates[:12]
        ],
        "supplier_examples": supplier[:10],
        "spec_like_examples": spec_like[:10],
    }
    return metric, pairs


def main() -> int:
    ap = argparse.ArgumentParser(description="Nova DRL Cross-Family Parts Generalization Audit v1.7.2")
    ap.add_argument("--events", default=str(DEFAULT_EVENTS))
    ap.add_argument("--replacements", default=str(DEFAULT_REPLACEMENTS))
    ap.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--family", action="append", default=[], help="Repeat to override the default 10-family cohort.")
    ap.add_argument("--alias-threshold", type=float, default=0.92)
    ap.add_argument(
        "--expected-usable-global",
        type=int,
        default=3287,
        help="Audit expectation from unified-index v1.4.8; 0 disables the warning.",
    )
    ap.add_argument("--plan-only", action="store_true")
    args = ap.parse_args()

    families = [normalized_ws(x) for x in (args.family or DEFAULT_FAMILIES) if normalized_ws(x)]
    wanted = set(families)
    events = read_jsonl(Path(args.events))
    mentions = read_jsonl(Path(args.replacements))
    event_by_id = {event_id(r): r for r in events if event_id(r)}

    by_family_mentions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    family_events: Dict[str, set[str]] = defaultdict(set)
    skipped = 0
    usable_global = 0
    for m in mentions:
        if not row_is_usable(m):
            skipped += 1; continue
        usable_global += 1
        eid = event_id(m)
        fam = family_value(m)
        if not fam and eid in event_by_id:
            fam = family_value(event_by_id[eid])
        if fam not in wanted:
            continue
        by_family_mentions[fam].append(m)
        if eid:
            family_events[fam].add(eid)

    metrics = []
    all_pairs = []
    candidate_rows = []
    missing_families = []
    for fam in families:
        ms = by_family_mentions.get(fam, [])
        if not ms:
            missing_families.append(fam)
            continue
        candidates = build_candidate_rows(ms, fam)
        candidate_rows.extend(candidates)
        metric, pairs = analyze_family(candidates, family_events[fam], args.alias_threshold)
        metrics.append(metric); all_pairs.extend(pairs)

    pattern_families: Dict[str, set[str]] = defaultdict(set)
    for p in all_pairs:
        pattern_families[p["pattern"]].add(p["family"])
    cross_patterns = [
        {
            "pattern": pat,
            "family_count": len(fams),
            "families": sorted(fams),
            "candidate_global_rule": len(fams) >= 3,
        }
        for pat, fams in sorted(pattern_families.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]

    total_events = sum(m["replacement_repair_events"] for m in metrics)
    total_candidates = sum(m["candidates_total"] for m in metrics)
    total_recurring = sum(m["recurring_2plus_candidates"] for m in metrics)
    total_oneoffs = sum(m["oneoff_candidates"] for m in metrics)
    total_pairs = len(all_pairs)

    print("# Nova DRL Cross-Family Parts Generalization Audit v1.7.2")
    print()
    print(f"Requested families:                 {len(families)}")
    print(f"Families found:                     {len(metrics)}")
    print(f"Families missing:                   {len(missing_families)}")
    print(f"Replacement repair-events sampled: {total_events}")
    print(f"Candidate buckets:                  {total_candidates}")
    print(f"Recurring 2+ candidates:            {total_recurring}")
    print(f"One-off candidates:                 {total_oneoffs}")
    print(f"Safe alias-pair proposals:          {total_pairs}")
    print("Auto-merges performed:              0")
    print("RCL1A canonical vocabulary applied: NO")
    print("RCL1A family rules applied:         NO")
    print("Frozen evidence modified:           NO")
    print("LLM calls:                          0")
    print("Web calls:                          0")
    print("Accepted facts:                     0")
    print("Qdrant:                             OFF")
    eligibility_mismatch = (
        args.expected_usable_global > 0
        and usable_global != args.expected_usable_global
    )
    print(f"Global rows passing local eligibility: {usable_global}")
    if eligibility_mismatch:
        print(
            f"ELIGIBILITY CONTRACT WARNING: local audit passed {usable_global} rows, "
            f"while unified-index v1.4.8 expected {args.expected_usable_global}. "
            "Candidate counts are an upper-bound audit until that exclusion contract is reused exactly."
        )

    print("\nFAMILY AUDIT")
    print("------------")
    for m in metrics:
        print(
            f"{m['family']} | repairs={m['replacement_repair_events']}"
            f" | candidates={m['candidates_total']}"
            f" | recurring2+={m['recurring_2plus_candidates']}"
            f" | oneoffs={m['oneoff_candidates']}"
            f" | recurring-event-coverage={m['recurring_candidate_event_coverage']:.1%}"
            f" | alias-pairs={m['safe_alias_pair_proposals']}"
        )
        for c in m["top_candidates"][:5]:
            print(f"    {c['label']} | {c['kind']} | repairs={c['repairs']} | mentions={c['mentions']}")

    if all_pairs:
        print("\nTOP CONSERVATIVE OCR/FORMAT PAIR PROPOSALS")
        print("------------------------------------------")
        for p in sorted(all_pairs, key=lambda x: (-x['combined_event_count'], -x['similarity']))[:30]:
            print(
                f"{p['family']} | {p['left_label']}  <->  {p['right_label']}"
                f" | score={p['similarity']:.3f} | events={p['combined_event_count']}"
                f" | pattern={p['pattern']}"
            )

    print("\nCROSS-FAMILY PATTERNS")
    print("---------------------")
    if not cross_patterns:
        print("None")
    else:
        for p in cross_patterns:
            print(
                f"{p['pattern']} | families={p['family_count']}"
                f" | global-rule-candidate={'YES' if p['candidate_global_rule'] else 'NO'}"
            )

    if missing_families:
        print("\nMISSING FAMILIES")
        for f in missing_families:
            print("  " + f)

    print("\nINTERPRETATION RULE")
    print("-------------------")
    print("Only patterns recurring across multiple independent families should influence global Parts logic.")
    print("Family-specific anomalies remain local/preserved and do not trigger another pipeline rewrite.")

    if args.plan_only:
        print("\nPLAN ONLY: no output files written.")
        return 0

    root = Path(args.output_root); root.mkdir(parents=True, exist_ok=True)
    write_jsonl(root / "family_candidate_metrics_v1_7_2.jsonl", metrics)
    write_jsonl(root / "candidate_buckets_v1_7_2.jsonl", candidate_rows)
    write_jsonl(root / "safe_alias_pair_proposals_v1_7_2.jsonl", all_pairs)
    write_jsonl(root / "cross_family_patterns_v1_7_2.jsonl", cross_patterns)
    write_json(root / "parts_generalization_manifest_v1_7_2.json", {
        "version": VERSION,
        "schema": SCHEMA,
        "built_at_utc": now_utc(),
        "inputs": {
            "events": str(args.events), "events_sha256": sha256_file(Path(args.events)),
            "replacements": str(args.replacements), "replacements_sha256": sha256_file(Path(args.replacements)),
        },
        "families_requested": families,
        "families_missing": missing_families,
        "counts": {
            "families_found": len(metrics), "replacement_repair_events_sampled": total_events,
            "candidate_buckets": total_candidates, "recurring_2plus_candidates": total_recurring,
            "oneoff_candidates": total_oneoffs, "safe_alias_pair_proposals": total_pairs,
            "skipped_ineligible_replacement_rows_global": skipped,
            "local_usable_replacement_rows_global": usable_global,
            "expected_usable_replacement_rows_global": args.expected_usable_global,
            "eligibility_contract_mismatch": eligibility_mismatch,
        },
        "policy": {
            "auto_merges": 0, "rcl1a_canonical_vocabulary_applied": False,
            "rcl1a_family_rules_applied": False, "numeric_conflict_veto": True,
            "supplier_wrappers_separate_from_ocr": True, "frozen_evidence_modified": False,
            "llm_calls": 0, "web_calls": 0, "accepted_facts": 0, "qdrant_entries": 0,
            "80_20_rule": "fixed default",
        },
    })
    print(f"\nOutputs: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
