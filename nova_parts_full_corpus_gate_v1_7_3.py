#!/usr/bin/env python3
"""
Nova DRL Full-Corpus Recurring Parts Gate v1.7.3

Purpose
-------
Apply the lessons from RCL1A and the v1.7.2 cross-family audit directly to the
existing FULL v1.5.2 replacement corpus.

This is a deterministic 80/20 Parts gate, NOT a new extraction pass.

Inputs
------
/opt/nova-drl/output/drl_full_corpus_v1_5_2/repair_events_v1_5_2.jsonl
/opt/nova-drl/output/drl_full_corpus_v1_5_2/replacement_mentions_v1_5_2.jsonl

Core policy
-----------
- Frozen v1.5.2 evidence is read-only.
- Exact punctuation/spacing variants may share an alphanumeric identity key.
- Supplier wrappers are separated BEFORE manufacturer identity handling.
- Mouser 511- and DigiKey -ND do not seed a manufacturer canonical by themselves.
- DigiKey/Mouser aliases may attach only when their body already exists as an
  independently observed non-supplier identity.
- Ratings/specs (47uF, 33V, 15A 250V, etc.) remain useful recurring component
  evidence but are NOT called manufacturer PNs.
- Numeric-only device markings remain recurring identity candidates but are
  explicitly marked ambiguous.
- Fuzzy prefix/suffix/OCR similarity is AUDIT ONLY. It never auto-merges.
- One-off long-tail candidates stay preserved in the source corpus and are not
  promoted into normal recurring Parts outputs.
- No LLM, web, Qdrant, or accepted-fact writes.

80/20 defaults
--------------
- normal family recurring output: >=2 repair events
- global validation queue: explicit manufacturer-PN candidate seen in >=3 events
- recurring descriptions: >=2 events within an equipment family
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.7.3"
SCHEMA = "nova-drl-full-corpus-recurring-parts-gate-v1"

DEFAULT_ROOT = Path("/opt/nova-drl/output/drl_full_corpus_v1_5_2")
DEFAULT_EVENTS = DEFAULT_ROOT / "repair_events_v1_5_2.jsonl"
DEFAULT_PARTS = DEFAULT_ROOT / "replacement_mentions_v1_5_2.jsonl"
DEFAULT_DB = Path("/opt/nova-drl/index/drl_knowledge_index.sqlite")
DEFAULT_OUTPUT = Path("/opt/nova-drl/output/full_corpus_parts_gate_v1_7_3")

OCR_GROUPS = [
    set("0ODQ"),
    set("1IL"),
    set("2Z"),
    set("5S"),
    set("6G"),
    set("8B"),
]

# Component ratings / specs rather than manufacturer part numbers.
SPEC_PATTERNS = [
    re.compile(r"^\s*\d+(?:\.\d+)?\s*(?:UF|PF|NF|MF|F)\s*$", re.I),
    re.compile(r"^\s*\d+(?:\.\d+)?\s*(?:V|VAC|VDC|A|AMP|AMPS|W|WATT|WATTS|HZ|KHZ|MHZ)\s*$", re.I),
    re.compile(r"^\s*\d+(?:\.\d+)?\s*(?:OHM|OHMS|R|K|M)\s*$", re.I),
    re.compile(r"^\s*\d+(?:\.\d+)?\s*(?:A|AMP|AMPS)\s+\d+(?:\.\d+)?\s*(?:V|VAC|VDC)\s*(?:FUSE)?\s*$", re.I),
    re.compile(r"^\s*\d+(?:\.\d+)?\s*(?:V|VAC|VDC)\s+\d+(?:\.\d+)?\s*(?:A|AMP|AMPS)\s*(?:FUSE)?\s*$", re.I),
]

GENERIC_COMPONENT_WORDS = {
    "BEARING", "BEARINGS", "BELT", "BELTS", "BOARD", "CAP", "CAPACITOR",
    "CAPACITORS", "CHIP", "CONNECTOR", "DIODE", "ENCODER", "FAN", "FUSE",
    "IC", "LED", "MOTOR", "MOSFET", "O-RING", "ORING", "RELAY", "RESISTOR",
    "SCREW", "SEAL", "SENSOR", "SOCKET", "SWITCH", "TRANSISTOR", "WIRE",
}


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
        raise RuntimeError(f"Missing required JSONL: {path}")
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


def compact(v: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", normalized_ws(v).upper())


def punct_compact(v: Any) -> str:
    return re.sub(r"\s+", "", normalized_ws(v).upper())


def safe_text(v: Any) -> str:
    return normalized_ws(v)


def event_id(row: Dict[str, Any]) -> str:
    for k in ("repair_event_id", "event_id", "repair_id"):
        v = safe_text(row.get(k))
        if v:
            return v
    return ""


def family_value(row: Dict[str, Any]) -> str:
    for k in ("equipment_family", "product_family", "family", "unit_type"):
        v = safe_text(row.get(k))
        if v:
            return v
    return "UNKNOWN_EQUIPMENT_FAMILY"


def part_number(row: Dict[str, Any]) -> str:
    for k in ("part_number", "manufacturer_part_number", "pn"):
        v = safe_text(row.get(k))
        if v:
            return v
    return ""


def description(row: Dict[str, Any]) -> str:
    for k in ("text", "description", "part_description", "evidence_quote"):
        v = safe_text(row.get(k))
        if v:
            return v
    return ""


def evidence_quote(row: Dict[str, Any]) -> str:
    return safe_text(row.get("evidence_quote") or row.get("raw_quote") or description(row))


def quantity(row: Dict[str, Any]) -> Optional[int]:
    for k in ("quantity", "qty", "recorded_quantity"):
        if k not in row:
            continue
        v = row.get(k)
        if isinstance(v, bool):
            return None
        try:
            i = int(v)
        except Exception:
            return None
        return i if 0 < i <= 10000 else None
    return None


def is_spec_like(label: str) -> bool:
    raw = normalized_ws(label).upper()
    if not raw:
        return False
    return any(p.fullmatch(raw) for p in SPEC_PATTERNS)


def is_generic_component(label: str) -> bool:
    raw = normalized_ws(label).upper()
    return raw in GENERIC_COMPONENT_WORDS


def classify_explicit_identity(label: str) -> str:
    raw = normalized_ws(label).upper()
    skel = compact(raw)
    if is_spec_like(raw):
        return "component_spec"
    if is_generic_component(raw):
        return "generic_component_label"
    if skel.isdigit():
        return "numeric_marking_candidate"
    return "manufacturer_pn_candidate"


def detect_supplier(label: str) -> Optional[Dict[str, str]]:
    raw = punct_compact(label)
    if raw.startswith("511-") and len(raw) > 4:
        return {
            "supplier": "Mouser",
            "supplier_part_number": raw,
            "body": raw[4:],
        }
    if raw.endswith("-ND") and len(raw) > 3:
        return {
            "supplier": "DigiKey",
            "supplier_part_number": raw,
            "body": raw[:-3],
        }
    return None


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
            if (
                prev2 is not None and i > 1 and j > 1
                and a[i - 1] == b[j - 2]
                and a[i - 2] == b[j - 1]
            ):
                cur[j] = min(cur[j], prev2[j-2] + 0.60)
        prev2, prev = prev, cur
    return prev[-1]


def pn_similarity(a: str, b: str) -> Tuple[float, List[str]]:
    aa, bb = compact(a), compact(b)
    if not aa or not bb:
        return 0.0, []
    if aa == bb:
        return 1.0, ["same_alnum_skeleton"]

    maxlen = max(len(aa), len(bb))
    score = max(0.0, 1.0 - weighted_edit_distance(aa, bb) / maxlen)
    reasons = [f"weighted_edit={score:.3f}"]

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

    return min(1.0, score), reasons


def numeric_signature(label: str) -> Tuple[int, ...]:
    vals = []
    for token in re.findall(r"\d+", compact(label)):
        try:
            vals.append(int(token))
        except Exception:
            pass
    return tuple(vals)


def build_family_candidates(
    parts: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Conservative candidate buckets.

    Explicit PNs are grouped by the same alphanumeric skeleton. This merges only
    punctuation/spacing differences (D45H11 vs D45-H11) and never suffix/prefix
    differences (2N2222 vs 2N2222A).

    Description-only evidence remains exact apart from whitespace/case.
    """
    buckets: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    supplier_rows = []

    for row in parts:
        fam = family_value(row)
        pn = part_number(row)
        desc = description(row)
        if pn:
            supplier = detect_supplier(pn)
            if supplier:
                supplier_rows.append({
                    "family": fam,
                    "repair_event_id": event_id(row),
                    "observed_label": pn,
                    "supplier": supplier["supplier"],
                    "supplier_part_number": supplier["supplier_part_number"],
                    "supplier_body": supplier["body"],
                    "quantity": quantity(row),
                    "evidence_quote": evidence_quote(row),
                })
                # Supplier identity gets its own exact bucket. It is resolved to
                # a manufacturer body later only if independently observed.
                key = punct_compact(pn)
                kind = "supplier_part_number"
            else:
                key = compact(pn)
                kind = "explicit_part_number"
        else:
            key = normalized_ws(desc).casefold()
            kind = "description_only"

        if not key:
            continue
        buckets[(fam, kind, key)].append(row)

    candidates = []
    for (fam, kind, key), rows in buckets.items():
        event_ids = sorted({event_id(r) for r in rows if event_id(r)})
        variants = sorted({part_number(r) for r in rows if part_number(r)})
        descs = sorted({description(r) for r in rows if description(r)})
        label = (
            variants[0]
            if variants
            else (descs[0] if descs else key)
        )
        pieces = 0
        qty_unstated = 0
        examples = []
        for r in rows:
            q = quantity(r)
            if q is None:
                qty_unstated += 1
            else:
                pieces += q
            e = evidence_quote(r)
            if e and e not in examples:
                examples.append(e)
            if len(examples) >= 6:
                continue

        identity_class = (
            "supplier_part_number"
            if kind == "supplier_part_number"
            else (
                classify_explicit_identity(label)
                if kind == "explicit_part_number"
                else "description_only"
            )
        )
        candidates.append({
            "candidate_id": stable_id("pc_", fam, kind, key),
            "family": fam,
            "candidate_kind": kind,
            "identity_class": identity_class,
            "identity_key": key,
            "display_label": label,
            "part_number_variants": variants,
            "description_variants": descs[:30],
            "repair_event_count": len(event_ids),
            "repair_event_ids": event_ids,
            "mention_count": len(rows),
            "recorded_pieces": pieces,
            "quantity_unstated_mentions": qty_unstated,
            "evidence_examples": examples[:6],
        })

    candidates.sort(
        key=lambda r: (
            r["family"].casefold(),
            -r["repair_event_count"],
            -r["mention_count"],
            r["display_label"].casefold(),
        )
    )
    return candidates, supplier_rows


def build_independent_non_supplier_keys(
    candidates: Sequence[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        if c["candidate_kind"] != "explicit_part_number":
            continue
        by_key[c["identity_key"]].append(c)
    return by_key


def resolve_supplier_aliases(
    candidates: Sequence[Dict[str, Any]],
    supplier_rows: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    independent = build_independent_non_supplier_keys(candidates)

    grouped: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in supplier_rows:
        body_key = compact(row["supplier_body"])
        gkey = (row["supplier"], punct_compact(row["supplier_part_number"]), body_key)
        g = grouped.setdefault(gkey, {
            "supplier": row["supplier"],
            "supplier_part_number": row["supplier_part_number"],
            "supplier_body": row["supplier_body"],
            "body_identity_key": body_key,
            "repair_event_ids": set(),
            "families": set(),
            "mentions": 0,
            "recorded_pieces": 0,
            "quantity_unstated_mentions": 0,
            "evidence_examples": [],
        })
        if row.get("repair_event_id"):
            g["repair_event_ids"].add(row["repair_event_id"])
        g["families"].add(row["family"])
        g["mentions"] += 1
        q = row.get("quantity")
        if q is None:
            g["quantity_unstated_mentions"] += 1
        else:
            g["recorded_pieces"] += int(q)
        e = row.get("evidence_quote")
        if e and e not in g["evidence_examples"] and len(g["evidence_examples"]) < 6:
            g["evidence_examples"].append(e)

    out = []
    for g in grouped.values():
        matches = independent.get(g["body_identity_key"], [])
        matched_labels = sorted({
            c["display_label"] for c in matches
        })
        status = "resolved_to_independently_observed_body" if matches else "supplier_alias_only_unresolved"
        out.append({
            "supplier_alias_id": stable_id(
                "sa_", g["supplier"], g["supplier_part_number"], g["body_identity_key"]
            ),
            "supplier": g["supplier"],
            "supplier_part_number": g["supplier_part_number"],
            "supplier_body": g["supplier_body"],
            "body_identity_key": g["body_identity_key"],
            "status": status,
            "matched_independent_labels": matched_labels,
            "repair_event_count": len(g["repair_event_ids"]),
            "repair_event_ids": sorted(g["repair_event_ids"]),
            "family_count": len(g["families"]),
            "families": sorted(g["families"]),
            "mention_count": g["mentions"],
            "recorded_pieces": g["recorded_pieces"],
            "quantity_unstated_mentions": g["quantity_unstated_mentions"],
            "evidence_examples": g["evidence_examples"],
        })
    out.sort(key=lambda r: (-r["repair_event_count"], r["supplier_part_number"]))
    return out


def build_global_pn_identities(
    candidates: Sequence[Dict[str, Any]],
    supplier_aliases: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Any]] = {}

    for c in candidates:
        if c["candidate_kind"] != "explicit_part_number":
            continue
        key = c["identity_key"]
        g = groups.setdefault(key, {
            "identity_key": key,
            "labels": set(),
            "families": set(),
            "repair_event_ids": set(),
            "source_candidate_ids": set(),
            "mentions": 0,
            "pieces": 0,
            "qty_unstated": 0,
            "classes": Counter(),
        })
        g["labels"].update(c.get("part_number_variants") or [c["display_label"]])
        g["families"].add(c["family"])
        g["repair_event_ids"].update(c["repair_event_ids"])
        g["source_candidate_ids"].add(c["candidate_id"])
        g["mentions"] += int(c["mention_count"])
        g["pieces"] += int(c["recorded_pieces"])
        g["qty_unstated"] += int(c["quantity_unstated_mentions"])
        g["classes"][c["identity_class"]] += 1

    # Resolved supplier aliases add event support to an already independently
    # observed body, but can never create a new manufacturer identity.
    for a in supplier_aliases:
        if a["status"] != "resolved_to_independently_observed_body":
            continue
        key = a["body_identity_key"]
        if key not in groups:
            continue
        g = groups[key]
        g["repair_event_ids"].update(a["repair_event_ids"])
        g["families"].update(a["families"])
        g["mentions"] += int(a["mention_count"])
        g["pieces"] += int(a["recorded_pieces"])
        g["qty_unstated"] += int(a["quantity_unstated_mentions"])

    out = []
    for key, g in groups.items():
        labels = sorted(g["labels"], key=lambda x: (len(x), x.casefold()))
        # Shortest exact-format variant is not automatically "manufacturer
        # canonical"; it is just a stable display choice until validation.
        display = labels[0] if labels else key
        dominant_class = g["classes"].most_common(1)[0][0] if g["classes"] else "manufacturer_pn_candidate"
        out.append({
            "global_identity_id": stable_id("gp_", key),
            "identity_key": key,
            "display_label": display,
            "observed_variants": labels,
            "identity_class": dominant_class,
            "repair_event_count": len(g["repair_event_ids"]),
            "repair_event_ids": sorted(g["repair_event_ids"]),
            "family_count": len(g["families"]),
            "families": sorted(g["families"]),
            "mention_count": g["mentions"],
            "recorded_pieces": g["pieces"],
            "quantity_unstated_mentions": g["qty_unstated"],
            "source_candidate_ids": sorted(g["source_candidate_ids"]),
            "canonical_status": "UNVALIDATED_RECURRING_IDENTITY",
        })

    out.sort(
        key=lambda r: (
            -r["repair_event_count"],
            -r["family_count"],
            -r["mention_count"],
            r["display_label"].casefold(),
        )
    )
    return out


def build_family_recurring(
    candidates: Sequence[Dict[str, Any]],
    supplier_aliases: Sequence[Dict[str, Any]],
    min_events: int,
) -> List[Dict[str, Any]]:
    out = []
    for c in candidates:
        if c["candidate_kind"] == "supplier_part_number":
            continue
        if c["repair_event_count"] < min_events:
            continue
        row = dict(c)
        row["output_role"] = "family_recurring_part_or_component"
        row["promoted_for_normal_output"] = True
        out.append(row)

    # Supplier aliases remain metadata, never a standalone normal part identity.
    out.sort(
        key=lambda r: (
            r["family"].casefold(),
            -r["repair_event_count"],
            -r["mention_count"],
            r["display_label"].casefold(),
        )
    )
    return out


def pair_type(reasons: Sequence[str], a: str, b: str) -> str:
    if compact(a) == compact(b):
        return "format_equivalent"
    if any("suffix_containment" in x for x in reasons):
        return "missing_prefix_or_leading_token"
    if any("prefix_containment" in x for x in reasons):
        return "missing_suffix_or_trailing_token"
    if any("internal_containment" in x for x in reasons):
        return "internal_containment"
    return "weighted_ocr_similarity"


def build_fuzzy_audit_pairs(
    global_ids: Sequence[Dict[str, Any]],
    min_events: int,
    threshold: float,
    max_pairs: int,
) -> List[Dict[str, Any]]:
    eligible = [
        r for r in global_ids
        if r["repair_event_count"] >= min_events
        and r["identity_class"] in {"manufacturer_pn_candidate", "numeric_marking_candidate"}
    ]

    out = []
    # Keep pair generation bounded. Compare within rough length windows.
    by_len: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for r in eligible:
        by_len[len(r["identity_key"])].append(r)

    for n, rows in sorted(by_len.items()):
        neighborhood = []
        for m in range(max(1, n - 5), n + 6):
            neighborhood.extend(by_len.get(m, []))
        seen = set()
        for a in rows:
            for b in neighborhood:
                if a["global_identity_id"] == b["global_identity_id"]:
                    continue
                pair_key = tuple(sorted((a["global_identity_id"], b["global_identity_id"])))
                if pair_key in seen:
                    continue
                seen.add(pair_key)

                siga = numeric_signature(a["display_label"])
                sigb = numeric_signature(b["display_label"])
                # Hard veto learned from v1.7.2: similar strings with different
                # numeric model signatures may be genuinely different parts.
                if siga and sigb and siga != sigb:
                    continue

                score, reasons = pn_similarity(a["display_label"], b["display_label"])
                if score < threshold:
                    continue

                out.append({
                    "pair_id": stable_id("fp_", *pair_key),
                    "left_global_identity_id": a["global_identity_id"],
                    "left_label": a["display_label"],
                    "left_events": a["repair_event_count"],
                    "right_global_identity_id": b["global_identity_id"],
                    "right_label": b["display_label"],
                    "right_events": b["repair_event_count"],
                    "combined_event_count": len(
                        set(a["repair_event_ids"]) | set(b["repair_event_ids"])
                    ),
                    "similarity": round(score, 6),
                    "pattern": pair_type(reasons, a["display_label"], b["display_label"]),
                    "reasons": reasons,
                    "status": "AUDIT_ONLY_NO_AUTO_MERGE",
                })

    # Deduplicate in case a pair was visited from more than one length bucket.
    uniq = {}
    for p in out:
        uniq[p["pair_id"]] = p
    out = list(uniq.values())
    out.sort(
        key=lambda r: (
            -r["combined_event_count"],
            -r["similarity"],
            r["left_label"].casefold(),
            r["right_label"].casefold(),
        )
    )
    return out[:max_pairs]


def sqlite_index_snapshot(path: Path) -> Dict[str, Any]:
    out = {
        "path": str(path),
        "exists": path.exists(),
        "readable": False,
        "product_parts_rows": None,
        "product_families_rows": None,
        "repair_events_rows": None,
        "error": None,
    }
    if not path.exists():
        return out
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        out["readable"] = True
        for table, key in (
            ("product_parts", "product_parts_rows"),
            ("product_families", "product_families_rows"),
            ("repair_events", "repair_events_rows"),
        ):
            try:
                out[key] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except Exception:
                pass
        conn.close()
    except Exception as exc:
        out["error"] = str(exc)
    return out


def family_summary(
    candidates: Sequence[Dict[str, Any]],
    parts: Sequence[Dict[str, Any]],
    min_events: int,
) -> List[Dict[str, Any]]:
    family_events: Dict[str, set[str]] = defaultdict(set)
    family_mentions = Counter()
    for r in parts:
        fam = family_value(r)
        if event_id(r):
            family_events[fam].add(event_id(r))
        family_mentions[fam] += 1

    by_family: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        by_family[c["family"]].append(c)

    out = []
    for fam, cs in by_family.items():
        recurring = [
            c for c in cs
            if c["candidate_kind"] != "supplier_part_number"
            and c["repair_event_count"] >= min_events
        ]
        recurring_events = set()
        for c in recurring:
            recurring_events.update(c["repair_event_ids"])
        total_events = len(family_events[fam])
        out.append({
            "family": fam,
            "replacement_repair_events": total_events,
            "replacement_mentions": family_mentions[fam],
            "candidate_buckets": len(cs),
            "recurring_candidates": len(recurring),
            "recurring_event_coverage": round(
                len(recurring_events) / total_events, 6
            ) if total_events else 0.0,
            "explicit_pn_candidates": sum(
                1 for c in cs if c["candidate_kind"] == "explicit_part_number"
            ),
            "description_candidates": sum(
                1 for c in cs if c["candidate_kind"] == "description_only"
            ),
            "supplier_candidates": sum(
                1 for c in cs if c["candidate_kind"] == "supplier_part_number"
            ),
        })

    out.sort(
        key=lambda r: (
            -r["replacement_repair_events"],
            -r["replacement_mentions"],
            r["family"].casefold(),
        )
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Nova DRL Full-Corpus Recurring Parts Gate v1.7.3"
    )
    ap.add_argument("--events", default=str(DEFAULT_EVENTS))
    ap.add_argument("--parts", default=str(DEFAULT_PARTS))
    ap.add_argument("--knowledge-db", default=str(DEFAULT_DB))
    ap.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--family-min-events", type=int, default=2)
    ap.add_argument("--global-validation-min-events", type=int, default=3)
    ap.add_argument("--fuzzy-min-events", type=int, default=2)
    ap.add_argument("--fuzzy-threshold", type=float, default=0.92)
    ap.add_argument("--fuzzy-max-pairs", type=int, default=500)
    ap.add_argument("--plan-only", action="store_true")
    args = ap.parse_args()

    events_path = Path(args.events)
    parts_path = Path(args.parts)
    output_root = Path(args.output_root)

    # Event rows are loaded primarily for corpus accounting / hash anchoring.
    events = read_jsonl(events_path)
    parts = read_jsonl(parts_path)

    candidates, supplier_rows = build_family_candidates(parts)
    supplier_aliases = resolve_supplier_aliases(candidates, supplier_rows)
    global_ids = build_global_pn_identities(candidates, supplier_aliases)
    family_recurring = build_family_recurring(
        candidates, supplier_aliases, max(2, args.family_min_events)
    )
    validation_queue = [
        r for r in global_ids
        if r["identity_class"] == "manufacturer_pn_candidate"
        and r["repair_event_count"] >= max(2, args.global_validation_min_events)
    ]
    fuzzy_pairs = build_fuzzy_audit_pairs(
        global_ids,
        max(2, args.fuzzy_min_events),
        args.fuzzy_threshold,
        max(1, args.fuzzy_max_pairs),
    )
    families = family_summary(
        candidates, parts, max(2, args.family_min_events)
    )
    db_snapshot = sqlite_index_snapshot(Path(args.knowledge_db))

    source_events = {event_id(r) for r in parts if event_id(r)}
    recurring_family_events = {
        eid for r in family_recurring for eid in r["repair_event_ids"]
    }

    identity_class_counts = Counter(r["identity_class"] for r in global_ids)
    supplier_status_counts = Counter(r["status"] for r in supplier_aliases)
    fuzzy_pattern_counts = Counter(r["pattern"] for r in fuzzy_pairs)

    print("# Nova DRL Full-Corpus Recurring Parts Gate v1.7.3")
    print()
    print(f"Full v1.5.2 repair-event rows:      {len(events):,}")
    print(f"Full replacement mentions:          {len(parts):,}")
    print(f"Replacement repair events:          {len(source_events):,}")
    print(f"Equipment families in Parts source: {len(families):,}")
    print(f"Conservative candidate buckets:     {len(candidates):,}")
    print(f"Family recurring outputs (2+):      {len(family_recurring):,}")
    print(
        f"Repairs covered by recurring output:{len(recurring_family_events):,} "
        f"({len(recurring_family_events)/len(source_events):.1%})"
        if source_events else
        "Repairs covered by recurring output:0"
    )
    print(f"Global explicit PN identities:      {len(global_ids):,}")
    print(f"Manufacturer-PN validation queue:   {len(validation_queue):,}")
    print(f"Supplier alias identities:          {len(supplier_aliases):,}")
    print(f"Fuzzy audit pairs:                   {len(fuzzy_pairs):,}")
    print("Fuzzy auto-merges:                   0")
    print("One-off candidates promoted:         NO")
    print("Frozen v1.5.2 evidence modified:     NO")
    print("LLM calls:                           0")
    print("Web calls:                           0")
    print("Accepted facts:                      0")
    print("Qdrant:                              OFF")

    print("\nGLOBAL IDENTITY CLASSES")
    print("-----------------------")
    for k, v in identity_class_counts.most_common():
        print(f"{k:28} {v:,}")

    if supplier_aliases:
        print("\nSUPPLIER ALIAS STATUS")
        print("---------------------")
        for k, v in supplier_status_counts.most_common():
            print(f"{k:42} {v:,}")

    print("\nTOP 30 FAMILIES BY FULL-CORPUS REPLACEMENT HISTORY")
    print("--------------------------------------------------")
    for i, r in enumerate(families[:30], 1):
        print(
            f"{i:2}. {r['family']}"
            f" | repairs={r['replacement_repair_events']}"
            f" | mentions={r['replacement_mentions']}"
            f" | recurring={r['recurring_candidates']}"
            f" | recurring-coverage={r['recurring_event_coverage']:.1%}"
        )

    print("\nTOP 50 GLOBAL RECURRING PN / COMPONENT IDENTITIES")
    print("-------------------------------------------------")
    for i, r in enumerate(global_ids[:50], 1):
        print(
            f"{i:2}. {r['display_label']}"
            f" | class={r['identity_class']}"
            f" | repairs={r['repair_event_count']}"
            f" | families={r['family_count']}"
            f" | variants={len(r['observed_variants'])}"
        )

    print("\nTOP MANUFACTURER-PN VALIDATION QUEUE")
    print("------------------------------------")
    for i, r in enumerate(validation_queue[:50], 1):
        print(
            f"{i:2}. {r['display_label']}"
            f" | repairs={r['repair_event_count']}"
            f" | families={r['family_count']}"
            f" | variants={' | '.join(r['observed_variants'][:6])}"
        )

    if fuzzy_pairs:
        print("\nTOP FUZZY AUDIT PAIRS — REVIEW HINTS ONLY")
        print("-----------------------------------------")
        for p in fuzzy_pairs[:30]:
            print(
                f"{p['left_label']}  <->  {p['right_label']}"
                f" | score={p['similarity']:.3f}"
                f" | events={p['combined_event_count']}"
                f" | {p['pattern']}"
            )

        print("\nFUZZY PATTERN COUNTS")
        print("--------------------")
        for k, v in fuzzy_pattern_counts.most_common():
            print(f"{k:38} {v}")

    print("\nEXISTING KNOWLEDGE INDEX SNAPSHOT")
    print("---------------------------------")
    print(f"DB: {db_snapshot['path']}")
    print(f"Exists: {db_snapshot['exists']} | readable with Python sqlite3: {db_snapshot['readable']}")
    print(f"repair_events rows:   {db_snapshot['repair_events_rows']}")
    print(f"product_families rows:{db_snapshot['product_families_rows']}")
    print(f"product_parts rows:   {db_snapshot['product_parts_rows']}")
    if db_snapshot["error"]:
        print(f"DB read warning: {db_snapshot['error']}")

    print("\n80/20 DECISION")
    print("--------------")
    print("Promote recurring identities/components; preserve one-offs in frozen evidence.")
    print("Validate recurring manufacturer PN identities once globally, then reuse.")
    print("Treat fuzzy prefix/suffix similarity as candidate discovery only, never an automatic merge.")
    print("Do not create family-specific cleanup rules unless a high-volume family truly needs one.")

    if args.plan_only:
        print("\nPLAN ONLY: no output files written.")
        return 0

    output_root.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_root / "family_summary_v1_7_3.jsonl", families)
    write_jsonl(output_root / "family_recurring_parts_v1_7_3.jsonl", family_recurring)
    write_jsonl(output_root / "global_recurring_pn_identities_v1_7_3.jsonl", global_ids)
    write_jsonl(output_root / "manufacturer_pn_validation_queue_v1_7_3.jsonl", validation_queue)
    write_jsonl(output_root / "supplier_aliases_v1_7_3.jsonl", supplier_aliases)
    write_jsonl(output_root / "fuzzy_audit_pairs_v1_7_3.jsonl", fuzzy_pairs)

    manifest = {
        "version": VERSION,
        "schema": SCHEMA,
        "built_at_utc": now_utc(),
        "inputs": {
            "events": str(events_path),
            "events_sha256": sha256_file(events_path),
            "parts": str(parts_path),
            "parts_sha256": sha256_file(parts_path),
            "knowledge_db": db_snapshot,
        },
        "counts": {
            "repair_event_rows": len(events),
            "replacement_mentions": len(parts),
            "replacement_repair_events": len(source_events),
            "equipment_families": len(families),
            "candidate_buckets": len(candidates),
            "family_recurring_outputs": len(family_recurring),
            "recurring_output_repair_events": len(recurring_family_events),
            "global_pn_identities": len(global_ids),
            "manufacturer_pn_validation_queue": len(validation_queue),
            "supplier_aliases": len(supplier_aliases),
            "fuzzy_audit_pairs": len(fuzzy_pairs),
        },
        "identity_class_counts": dict(identity_class_counts),
        "supplier_status_counts": dict(supplier_status_counts),
        "fuzzy_pattern_counts": dict(fuzzy_pattern_counts),
        "settings": {
            "family_min_events": args.family_min_events,
            "global_validation_min_events": args.global_validation_min_events,
            "fuzzy_min_events": args.fuzzy_min_events,
            "fuzzy_threshold": args.fuzzy_threshold,
            "fuzzy_max_pairs": args.fuzzy_max_pairs,
        },
        "policy": {
            "source_scope": "full_v1_5_2_repair_corpus",
            "safe_format_normalization": True,
            "supplier_wrappers_separated_first": True,
            "supplier_alias_cannot_seed_manufacturer_identity": True,
            "specs_not_called_manufacturer_pns": True,
            "fuzzy_auto_merge": False,
            "oneoffs_promoted": False,
            "frozen_evidence_modified": False,
            "llm_calls": 0,
            "web_calls": 0,
            "accepted_facts": 0,
            "qdrant_entries": 0,
            "80_20_rule": "fixed default",
        },
    }
    write_json(output_root / "full_corpus_parts_gate_manifest_v1_7_3.json", manifest)
    print(f"\nOutputs: {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
