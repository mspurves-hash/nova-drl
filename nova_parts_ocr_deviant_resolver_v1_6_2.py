#!/usr/bin/env python3
"""
Nova DRL OCR Deviant Resolver / Canonical Parts Gate v1.6.2

Purpose
-------
Use the first human parts screening as labeled evidence without turning human
screening into a corpus-wide bottleneck.

This stage:
- reads the frozen candidate buckets produced from v1.4.3 replacement mentions,
- treats human-kept rows as authoritative canonical vocabulary,
- treats human-screened rows as NON-canonical standalone labels,
- detects supplier/distributor part numbers BEFORE OCR learning,
- preserves supplier PNs as vendor aliases instead of OCR mistakes,
- recovers only high-confidence OCR/format aliases back to known canonicals,
- queues recurring plausible unknown part numbers for web validation,
- preserves low-frequency / unresolved evidence without promoting it,
- emits an 80/20 recurring parts view,
- never changes frozen source evidence,
- never writes Qdrant,
- never marks accepted facts.

Important semantic rule
-----------------------
A human-screened candidate is NOT promoted as a new canonical merely because it
looks plausible. It may:
  (a) resolve as an alias of a known canonical,
  (b) enter the web-validation queue if recurring and plausible, or
  (c) remain preserved/unpromoted.

A web result can promote a new canonical only when explicitly supplied as a
validated result in --web-results.

Supplier/distributor identity is separate from manufacturer identity:
- Known Mouser wrapper prefix `511-` is preserved as a Mouser supplier PN and
  stripped only for manufacturer-PN matching/canonicalization.
- DigiKey PNs ending in `-ND` are preserved as DigiKey supplier PNs. The `-ND`
  suffix is removed only to form a manufacturer-body candidate for matching;
  it does NOT by itself authorize creation of a new manufacturer canonical.
- Supplier wrappers are never learned as OCR-confusion patterns.

No network calls are made by this script. The web-validation queue is designed
for a later online checker (ChatGPT/web search, distributor API, or other
approved source), so the local pipeline remains reproducible and deterministic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.6.2"
SCHEMA = "nova-drl-parts-resolver-v1"

# Conservative OCR lookalike groups. We use these only to reduce substitution
# cost while comparing a candidate against an already trusted canonical.
OCR_GROUPS = [
    set("0ODQ"),
    set("1IL"),
    set("2Z"),
    set("5S"),
    set("6G"),
    set("8B"),
]

# Things that are often quantities/specifications rather than manufacturer PNs.
# They remain preserved evidence; this only affects whether they are worth a web lookup.
SPEC_ONLY_RE = re.compile(
    r"^(?:"
    r"\d+(?:\.\d+)?(?:V|A|W|OHM|R|K|M|UF|PF|NF|HZ|MHZ|KHZ|MM|IN|AMP|AMPS)"
    r"|(?:X)?\d+(?:UF|PF|NF)"
    r"|(?:\d+X)?\d+(?:V|A|W)"
    r")$",
    re.I,
)

NOISE_WORDS = {
    "BAD", "GOOD", "USED", "NEW", "PART", "PARTS", "BOARD", "BOARDS",
    "COMPONENT", "COMPONENTS", "REPLACED", "REPLACE", "CHANGED", "CHANGE",
    "QTY", "QUANTITY", "PCS", "PIECES", "EACH", "MANY", "SEVERAL",
}

# Supplier/distributor wrappers known from DRL parts history.
#
# Mouser uses numeric manufacturer/supplier prefixes in its catalog. We only
# strip prefixes Matt has explicitly identified as supplier wrappers. 511 is
# currently trusted for this resolver. Additional prefixes can be supplied via
# --mouser-prefixes without changing frozen evidence.
DEFAULT_MOUSER_PREFIXES = ("511",)
DIGIKEY_SUFFIX = "-ND"

def parse_mouser_prefixes(value: Any) -> Tuple[str, ...]:
    vals = []
    for x in str(value or "").split(","):
        x = re.sub(r"[^0-9A-Z]+", "", x.upper())
        if x and x not in vals:
            vals.append(x)
    return tuple(vals or DEFAULT_MOUSER_PREFIXES)

def supplier_pn_compact(value: Any) -> str:
    # Preserve meaningful PN punctuation while removing OCR/display whitespace.
    return re.sub(r"\s+", "", normalized_ws(value).upper())

def detect_supplier_part_number(
    value: Any,
    mouser_prefixes: Sequence[str] = DEFAULT_MOUSER_PREFIXES,
) -> Optional[Dict[str, Any]]:
    raw = normalized_ws(value)
    pn = supplier_pn_compact(raw)
    if not pn:
        return None

    for prefix in mouser_prefixes:
        prefix = re.sub(r"[^0-9A-Z]+", "", str(prefix).upper())
        marker = prefix + "-"
        if prefix and pn.startswith(marker) and len(pn) > len(marker) + 2:
            body = pn[len(marker):]
            return {
                "supplier": "Mouser",
                "supplier_part_number": pn,
                "manufacturer_body_candidate": body,
                "wrapper_type": "prefix",
                "wrapper": marker,
                # Matt explicitly identified this prefix as a supplier wrapper,
                # so the body may seed a manufacturer canonical when the
                # human-selected row supplies no separate manufacturer PN.
                "body_can_seed_human_canonical": True,
            }

    if pn.endswith(DIGIKEY_SUFFIX) and len(pn) > len(DIGIKEY_SUFFIX) + 3:
        body = pn[:-len(DIGIKEY_SUFFIX)]
        return {
            "supplier": "DigiKey",
            "supplier_part_number": pn,
            "manufacturer_body_candidate": body,
            "wrapper_type": "suffix",
            "wrapper": DIGIKEY_SUFFIX,
            # -ND strongly identifies a DigiKey catalog PN, but the remaining
            # body is not always guaranteed to be the exact manufacturer PN.
            "body_can_seed_human_canonical": False,
        }

    return None

def supplier_infos_for_row(
    row: Dict[str, Any],
    mouser_prefixes: Sequence[str],
) -> List[Dict[str, Any]]:
    if row_kind(row) != "explicit_part_number":
        return []

    values = []
    label = candidate_label(row)
    if label:
        values.append(label)
    values.extend(str(x) for x in (row.get("part_number_variants") or []) if x)

    out = []
    seen = set()
    for value in values:
        info = detect_supplier_part_number(value, mouser_prefixes)
        if not info:
            continue
        key = (info["supplier"], info["supplier_part_number"])
        if key in seen:
            continue
        seen.add(key)
        out.append(info)
    return out

def best_supplier_info_for_row(
    row: Dict[str, Any],
    mouser_prefixes: Sequence[str],
) -> Optional[Dict[str, Any]]:
    infos = supplier_infos_for_row(row, mouser_prefixes)
    if not infos:
        return None
    # Prefer the most informative stripped body. This handles cases such as
    # 511-STTH1506 vs 511-STTH1506TPI by retaining STTH1506TPI.
    infos.sort(
        key=lambda x: (
            -len(compact(x.get("manufacturer_body_candidate"))),
            x.get("supplier_part_number") or "",
        )
    )
    return infos[0]

def canonical_row_from_label(
    row: Dict[str, Any],
    family: str,
    label: str,
    authority: str = "human_selected",
) -> Dict[str, Any]:
    label = normalized_ws(label)
    return {
        "canonical_id": stable_id("cp_", family, row_kind(row), compact(label) or desc_norm(label)),
        "family": family,
        "canonical_label": label,
        "canonical_kind": row_kind(row),
        "authority": authority,
        "source_candidate_ids": [row.get("candidate_id")] if row.get("candidate_id") else [],
        "repair_event_ids": row_events(row),
        "repair_event_count": row_event_count(row),
        "mention_count": row_mentions(row),
        "recorded_pieces": row_pieces(row),
        "evidence_hashes": [row.get("evidence_hash")] if row.get("evidence_hash") else [],
        "qdrant_entries": 0,
        "accepted_facts": 0,
    }

def supplier_body_match(
    info: Dict[str, Any],
    canonicals: Sequence[Dict[str, Any]],
    threshold: float,
    margin: float,
) -> Optional[Dict[str, Any]]:
    body = normalized_ws(info.get("manufacturer_body_candidate"))
    scored = []
    for can in canonicals:
        if can.get("canonical_kind") != "explicit_part_number":
            continue
        score, reasons = pn_similarity(body, can.get("canonical_label") or "")
        if score > 0:
            scored.append((score, can, reasons))
    scored.sort(key=lambda x: (-x[0], x[1].get("canonical_label") or ""))
    if not scored:
        return None
    best_score, best, reasons = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if best_score < threshold or (best_score - second) < margin:
        return None
    return {
        "canonical": best,
        "score": best_score,
        "second_best_score": second,
        "margin": best_score - second,
        "reasons": reasons,
    }

def make_supplier_alias(
    row: Dict[str, Any],
    family: str,
    info: Dict[str, Any],
    canonical: Optional[Dict[str, Any]],
    source_state: str,
    match: Optional[Dict[str, Any]] = None,
    counts_already_in_canonical: bool = False,
) -> Dict[str, Any]:
    out = {
        "supplier_alias_id": stable_id(
            "sa_",
            family,
            row.get("candidate_id"),
            info.get("supplier"),
            info.get("supplier_part_number"),
        ),
        "family": family,
        "supplier": info.get("supplier"),
        "supplier_part_number": info.get("supplier_part_number"),
        "manufacturer_body_candidate": info.get("manufacturer_body_candidate"),
        "wrapper_type": info.get("wrapper_type"),
        "wrapper": info.get("wrapper"),
        "source_candidate_id": row.get("candidate_id"),
        "source_state": source_state,
        "repair_event_ids": row_events(row),
        "repair_event_count": row_event_count(row),
        "mention_count": row_mentions(row),
        "recorded_pieces": row_pieces(row),
        "counts_already_in_canonical": bool(counts_already_in_canonical),
        "raw_evidence_preserved": True,
        "qdrant_entries": 0,
        "accepted_facts": 0,
    }
    if canonical:
        out["canonical_id"] = canonical.get("canonical_id")
        out["canonical_label"] = canonical.get("canonical_label")
        out["resolution"] = "linked_supplier_alias"
    else:
        out["canonical_id"] = None
        out["canonical_label"] = None
        out["resolution"] = "supplier_alias_needs_manufacturer_cross"
    if match:
        out["match_score"] = round(float(match.get("score") or 0.0), 6)
        out["match_margin"] = round(float(match.get("margin") or 0.0), 6)
        out["match_reasons"] = list(match.get("reasons") or [])
    return out

def build_human_canonical_vocabulary(
    rows: Sequence[Dict[str, Any]],
    family: str,
    mouser_prefixes: Sequence[str],
    threshold: float,
    margin: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Returns:
      manufacturer canonicals,
      resolved supplier aliases,
      unresolved human-selected supplier rows needing manufacturer cross.
    """
    normal_rows = []
    supplier_rows = []
    for row in rows:
        info = best_supplier_info_for_row(row, mouser_prefixes)
        if info:
            supplier_rows.append((row, info))
        else:
            normal_rows.append(row)

    canonicals = [canonical_row_from_human(r, family) for r in normal_rows]
    canonicals = merge_duplicate_canonicals(canonicals)

    supplier_aliases = []
    unresolved_supplier_rows = []

    # Trusted Mouser wrappers can seed a manufacturer canonical from the
    # human-approved supplier row if no canonical is already available.
    for row, info in supplier_rows:
        match = supplier_body_match(info, canonicals, threshold, margin)
        can = match["canonical"] if match else None

        if can is None and info.get("body_can_seed_human_canonical"):
            body = normalized_ws(info.get("manufacturer_body_candidate"))
            plausible, _ = looks_like_part_number(body, max(2, row_event_count(row)))
            if plausible:
                can = canonical_row_from_label(
                    row,
                    family,
                    body,
                    authority="human_selected_supplier_body",
                )
                canonicals = merge_duplicate_canonicals(canonicals + [can])
                # Re-resolve to the merged object/ID.
                match2 = supplier_body_match(info, canonicals, threshold, 0.0)
                if match2:
                    can = match2["canonical"]
                    match = match2

        if can is not None:
            supplier_aliases.append(
                make_supplier_alias(
                    row,
                    family,
                    info,
                    can,
                    source_state="human_selected",
                    match=match,
                    counts_already_in_canonical=True,
                )
            )
        else:
            x = dict(row)
            x["_supplier_info"] = info
            x["_supplier_source_state"] = "human_selected"
            unresolved_supplier_rows.append(x)

    return merge_duplicate_canonicals(canonicals), supplier_aliases, unresolved_supplier_rows

def resolve_supplier_rows(
    rows: Sequence[Dict[str, Any]],
    family: str,
    canonicals: Sequence[Dict[str, Any]],
    state_by_id: Dict[str, str],
    mouser_prefixes: Sequence[str],
    threshold: float,
    margin: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Resolve supplier PNs before OCR alias learning.
    Returns linked supplier aliases and residual rows.
    """
    linked = []
    residual = []
    for row in rows:
        info = best_supplier_info_for_row(row, mouser_prefixes)
        if not info:
            residual.append(row)
            continue

        match = supplier_body_match(info, canonicals, threshold, margin)
        if match:
            linked.append(
                make_supplier_alias(
                    row,
                    family,
                    info,
                    match["canonical"],
                    source_state=state_by_id.get(str(row.get("candidate_id") or ""), "unreviewed"),
                    match=match,
                    counts_already_in_canonical=False,
                )
            )
        else:
            x = dict(row)
            x["_supplier_info"] = info
            residual.append(x)
    return linked, residual

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def normalized_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()

def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

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
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception as exc:
                raise RuntimeError(f"Invalid JSONL at {path}:{n}: {exc}") from exc
            if isinstance(obj, dict):
                rows.append(obj)
    return rows

def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)

def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)

def compact(value: Any) -> str:
    """Uppercase alphanumeric skeleton used only for comparison."""
    return re.sub(r"[^A-Z0-9]+", "", normalized_ws(value).upper())

def punct_compact(value: Any) -> str:
    """Uppercase PN retaining common meaningful punctuation."""
    return re.sub(r"\s+", "", normalized_ws(value).upper())

def desc_norm(value: Any) -> str:
    s = normalized_ws(value).casefold()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def singularish(value: str) -> str:
    s = desc_norm(value)
    words = s.split()
    out = []
    for w in words:
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 4 and w.endswith("es"):
            w = w[:-2]
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return " ".join(out)

def candidate_label(row: Dict[str, Any]) -> str:
    return normalized_ws(
        row.get("review_decision", {}).get("canonical_label")
        or row.get("canonical_label")
        or row.get("display_label")
        or (row.get("part_number_variants") or [None])[0]
        or (row.get("description_variants") or [None])[0]
        or row.get("bucket_key")
    )

def row_kind(row: Dict[str, Any]) -> str:
    k = str(row.get("candidate_kind") or "").strip()
    if k:
        return k
    if row.get("part_number_variants"):
        return "explicit_part_number"
    return "description_only"

def row_events(row: Dict[str, Any]) -> List[str]:
    vals = [str(x) for x in (row.get("repair_event_ids") or []) if str(x)]
    if vals:
        return sorted(set(vals))
    return []

def row_mentions(row: Dict[str, Any]) -> int:
    try:
        return int(row.get("mention_count") or 0)
    except Exception:
        return 0

def row_pieces(row: Dict[str, Any]) -> int:
    try:
        return int(row.get("recorded_pieces") or 0)
    except Exception:
        return 0

def row_event_count(row: Dict[str, Any]) -> int:
    ev = row_events(row)
    if ev:
        return len(ev)
    try:
        return int(row.get("repair_event_count") or 0)
    except Exception:
        return 0

def same_ocr_group(a: str, b: str) -> bool:
    if a == b:
        return True
    return any(a in g and b in g for g in OCR_GROUPS)

def weighted_edit_distance(a: str, b: str) -> float:
    """
    Levenshtein-like distance with lower substitution cost for common OCR
    lookalikes and a modest adjacent-transposition discount.
    """
    a = compact(a)
    b = compact(b)
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
            sub_cost = 0.0 if ca == cb else (0.25 if same_ocr_group(ca, cb) else 1.0)
            cur[j] = min(
                prev[j] + 1.0,        # delete
                cur[j - 1] + 1.0,     # insert
                prev[j - 1] + sub_cost,
            )
            if (
                prev2 is not None and i > 1 and j > 1
                and a[i - 1] == b[j - 2]
                and a[i - 2] == b[j - 1]
            ):
                cur[j] = min(cur[j], prev2[j - 2] + 0.60)
        prev2, prev = prev, cur
    return prev[-1]

def pn_similarity(a: str, b: str) -> Tuple[float, List[str]]:
    aa, bb = compact(a), compact(b)
    reasons: List[str] = []
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

    # Strong signal from the first screen: OCR often loses a short manufacturer
    # prefix/suffix but retains a long distinctive PN body.
    if len(shorter) >= 6 and missing <= 5:
        if longer.endswith(shorter):
            cov = len(shorter) / len(longer)
            cont = min(0.985, 0.90 + 0.08 * cov)
            if cont > score:
                score = cont
            reasons.append(f"suffix_containment={cov:.3f}")
        elif longer.startswith(shorter):
            cov = len(shorter) / len(longer)
            cont = min(0.975, 0.89 + 0.08 * cov)
            if cont > score:
                score = cont
            reasons.append(f"prefix_containment={cov:.3f}")
        elif shorter in longer and missing <= 3:
            cov = len(shorter) / len(longer)
            cont = min(0.955, 0.87 + 0.07 * cov)
            if cont > score:
                score = cont
            reasons.append(f"internal_containment={cov:.3f}")

    # Punctuation/spacing-only differences are effectively exact.
    if punct_compact(a) == punct_compact(b):
        score = max(score, 0.998)
        reasons.append("same_punct_compact")

    return min(1.0, score), reasons

def description_similarity(a: str, b: str) -> Tuple[float, List[str]]:
    aa, bb = desc_norm(a), desc_norm(b)
    if not aa or not bb:
        return 0.0, []
    if aa == bb:
        return 1.0, ["same_description"]
    if singularish(aa) == singularish(bb):
        return 0.985, ["singular_plural_equivalent"]
    return 0.0, []

def similarity(row: Dict[str, Any], canonical: Dict[str, Any]) -> Tuple[float, List[str]]:
    kind_a = row_kind(row)
    kind_b = row_kind(canonical)
    # Do not fuzzy-map a generic description to a manufacturer PN or vice versa.
    if kind_a != kind_b:
        return 0.0, ["kind_mismatch"]
    a, b = candidate_label(row), candidate_label(canonical)
    if kind_a == "explicit_part_number":
        return pn_similarity(a, b)
    return description_similarity(a, b)

def looks_like_part_number(label: str, event_count: int = 0) -> Tuple[bool, List[str]]:
    raw = normalized_ws(label).upper()
    skel = compact(raw)
    reasons: List[str] = []
    if len(skel) < 4 or len(skel) > 36:
        return False, ["length_outside_pn_range"]
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9./_+\- ]*[A-Z0-9]", raw):
        return False, ["contains_unusual_characters"]
    if SPEC_ONLY_RE.fullmatch(skel):
        return False, ["looks_like_spec_not_pn"]

    has_alpha = any(c.isalpha() for c in skel)
    has_digit = any(c.isdigit() for c in skel)

    if has_alpha and has_digit:
        reasons.append("mixed_alpha_digit")
        return True, reasons

    # Numeric-only device markings can be real, but are too ambiguous as one-offs.
    if skel.isdigit() and len(skel) >= 5 and event_count >= 3:
        return True, ["recurring_numeric_identifier"]

    return False, ["weak_pn_shape"]

def looks_like_clean_description(label: str) -> bool:
    d = desc_norm(label)
    if not d:
        return False
    words = [w for w in d.split() if w.upper() not in NOISE_WORDS]
    if not words:
        return False
    alpha = sum(ch.isalpha() for ch in d)
    alnum = sum(ch.isalnum() for ch in d)
    if alnum == 0 or alpha / alnum < 0.60:
        return False
    if len(d) < 3 or len(d) > 80:
        return False
    return True

def load_review_root(root: Path) -> Dict[str, Any]:
    candidates_path = root / "review_candidates.jsonl"
    selected_path = root / "selected_parts.jsonl"
    suppressed_path = root / "suppressed_parts.jsonl"
    ledger_path = root / "human_review_decisions.jsonl"
    if not candidates_path.exists():
        raise RuntimeError(f"Missing review candidates: {candidates_path}")
    return {
        "root": root,
        "candidates_path": candidates_path,
        "selected_path": selected_path,
        "suppressed_path": suppressed_path,
        "ledger_path": ledger_path,
        "candidates": read_jsonl(candidates_path),
        "selected": read_jsonl(selected_path),
        "suppressed": read_jsonl(suppressed_path),
    }

def latest_valid_decisions(candidate_rows: Sequence[Dict[str, Any]], ledger_rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_id = {str(c.get("candidate_id")): c for c in candidate_rows if c.get("candidate_id")}
    latest: Dict[str, Dict[str, Any]] = {}
    for row in ledger_rows:
        cid = str(row.get("candidate_id") or "")
        if cid:
            latest[cid] = row
    valid = {}
    for cid, d in latest.items():
        c = by_id.get(cid)
        if c and d.get("evidence_hash") == c.get("evidence_hash"):
            valid[cid] = d
    return valid

def derive_selected_suppressed(review: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, str]]:
    """
    Prefer explicit selected/suppressed exports. If absent, derive from the
    append-only ledger. Returns selected, suppressed, decision_state_by_id.
    """
    candidates = review["candidates"]
    selected = list(review["selected"])
    suppressed = list(review["suppressed"])
    state: Dict[str, str] = {}

    if selected or suppressed:
        for r in selected:
            if r.get("candidate_id"):
                state[str(r["candidate_id"])] = "human_selected"
        for r in suppressed:
            if r.get("candidate_id"):
                state[str(r["candidate_id"])] = "human_screened"
        return selected, suppressed, state

    ledger = read_jsonl(review["ledger_path"])
    valid = latest_valid_decisions(candidates, ledger)
    by_id = {str(c.get("candidate_id")): c for c in candidates if c.get("candidate_id")}
    for cid, d in valid.items():
        c = by_id[cid]
        if d.get("decision") == "confirm" and not d.get("suppress_from_future_review"):
            x = dict(c)
            x["review_decision"] = d
            selected.append(x)
            state[cid] = "human_selected"
        elif d.get("suppress_from_future_review") or d.get("decision") == "reject":
            x = dict(c)
            x["review_decision"] = d
            suppressed.append(x)
            state[cid] = "human_screened"
    return selected, suppressed, state

def unique_rows(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    seen = set()
    for row in rows:
        cid = str(row.get("candidate_id") or "")
        key = cid or stable_json(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out

def canonical_row_from_human(row: Dict[str, Any], family: str) -> Dict[str, Any]:
    return canonical_row_from_label(
        row,
        family,
        candidate_label(row),
        authority="human_selected",
    )

def learn_aliases(
    screened_rows: Sequence[Dict[str, Any]],
    canonicals: Sequence[Dict[str, Any]],
    threshold: float,
    margin: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    aliases = []
    unresolved = []
    for row in screened_rows:
        label = candidate_label(row)
        scored = []
        for can in canonicals:
            score, reasons = similarity(row, {
                "candidate_kind": can["canonical_kind"],
                "display_label": can["canonical_label"],
            })
            if score > 0:
                scored.append((score, can, reasons))
        scored.sort(key=lambda x: (-x[0], x[1]["canonical_label"]))
        if not scored:
            unresolved.append(row)
            continue
        best_score, best, reasons = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        delta = best_score - second_score

        if best_score >= threshold and delta >= margin:
            aliases.append({
                "alias_id": stable_id("pa_", row.get("candidate_id"), best["canonical_id"]),
                "family": best["family"],
                "observed_label": label,
                "observed_kind": row_kind(row),
                "source_candidate_id": row.get("candidate_id"),
                "canonical_id": best["canonical_id"],
                "canonical_label": best["canonical_label"],
                "similarity_score": round(best_score, 6),
                "second_best_score": round(second_score, 6),
                "score_margin": round(delta, 6),
                "reasons": reasons,
                "repair_event_ids": row_events(row),
                "repair_event_count": row_event_count(row),
                "mention_count": row_mentions(row),
                "recorded_pieces": row_pieces(row),
                "source_state": "human_screened_noncanonical",
                "raw_evidence_preserved": True,
                "qdrant_entries": 0,
                "accepted_facts": 0,
            })
        else:
            x = dict(row)
            x["_best_match"] = {
                "canonical_id": best["canonical_id"],
                "canonical_label": best["canonical_label"],
                "score": round(best_score, 6),
                "second_best_score": round(second_score, 6),
                "margin": round(delta, 6),
                "reasons": reasons,
            }
            unresolved.append(x)
    return aliases, unresolved

def learn_confusion_profile(aliases: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    subs = Counter()
    containment = Counter()
    pairs = []

    # A lightweight diagnostic profile, not a self-modifying scoring model.
    for a in aliases:
        obs = compact(a["observed_label"])
        can = compact(a["canonical_label"])
        reasons = list(a.get("reasons") or [])
        if any("containment" in r for r in reasons):
            if can.endswith(obs):
                containment["canonical_prefix_missing_in_ocr"] += 1
            elif can.startswith(obs):
                containment["canonical_suffix_missing_in_ocr"] += 1
            else:
                containment["internal_substring_relation"] += 1

        # Greedy aligned substitutions for equal-length strings.
        if len(obs) == len(can):
            for x, y in zip(obs, can):
                if x != y:
                    subs[f"{x}->{y}"] += 1

        pairs.append({
            "observed": a["observed_label"],
            "canonical": a["canonical_label"],
            "score": a["similarity_score"],
            "reasons": reasons,
        })

    return {
        "version": VERSION,
        "purpose": "diagnostic summary of what the first human screen taught us",
        "note": "Counts are descriptive only; they do not rewrite frozen OCR and do not automatically alter scoring rules.",
        "high_confidence_alias_count": len(aliases),
        "substitution_counts_equal_length_pairs": dict(subs.most_common()),
        "containment_pattern_counts": dict(containment.most_common()),
        "learned_pairs": pairs[:500],
    }

def load_web_results(path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for row in read_jsonl(path):
        cid = str(row.get("candidate_id") or "")
        if cid:
            out[cid] = row
    return out

def web_status(row: Dict[str, Any], web_results: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    cid = str(row.get("candidate_id") or "")
    return web_results.get(cid)

def make_web_queue_row(row: Dict[str, Any], family: str, source_state: str, best_match: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    label = candidate_label(row)
    supplier_info = row.get("_supplier_info") if isinstance(row.get("_supplier_info"), dict) else None
    queries = [
        f'"{label}"',
        f'"{label}" datasheet',
        f'"{label}" electronic component',
    ]
    if supplier_info:
        spn = supplier_info.get("supplier_part_number")
        body = supplier_info.get("manufacturer_body_candidate")
        queries = [
            f'"{spn}"',
            f'"{spn}" {supplier_info.get("supplier")}',
            f'"{body}" datasheet',
            f'"{body}" manufacturer part number',
        ]
    return {
        "validation_id": stable_id("wv_", family, row.get("candidate_id"), label),
        "family": family,
        "candidate_id": row.get("candidate_id"),
        "observed_label": label,
        "candidate_kind": row_kind(row),
        "repair_event_count": row_event_count(row),
        "repair_event_ids": row_events(row),
        "mention_count": row_mentions(row),
        "recorded_pieces": row_pieces(row),
        "source_state": source_state,
        "supplier_info": supplier_info,
        "best_known_canonical_match": best_match,
        "suggested_queries": queries,
        "decision_required": (
            "validate_supplier_cross_and_return_manufacturer_canonical_or_invalid"
            if supplier_info
            else "validate_part_number_and_return_canonical_or_invalid"
        ),
        "raw_evidence_preserved": True,
        "qdrant_entries": 0,
        "accepted_facts": 0,
    }

def promote_web_validated(row: Dict[str, Any], result: Dict[str, Any], family: str) -> Dict[str, Any]:
    canonical = normalized_ws(result.get("canonical_label") or candidate_label(row))
    return {
        "canonical_id": stable_id("cp_", family, row_kind(row), compact(canonical) or desc_norm(canonical)),
        "family": family,
        "canonical_label": canonical,
        "canonical_kind": row_kind(row),
        "authority": "web_validated",
        "source_candidate_ids": [row.get("candidate_id")] if row.get("candidate_id") else [],
        "repair_event_ids": row_events(row),
        "repair_event_count": row_event_count(row),
        "mention_count": row_mentions(row),
        "recorded_pieces": row_pieces(row),
        "web_validation": {
            "status": result.get("status"),
            "source": result.get("source"),
            "source_url": result.get("source_url"),
            "manufacturer": result.get("manufacturer"),
            "notes": result.get("notes"),
        },
        "qdrant_entries": 0,
        "accepted_facts": 0,
    }

def aggregate_recurring(
    canonicals: Sequence[Dict[str, Any]],
    aliases: Sequence[Dict[str, Any]],
    supplier_aliases: Sequence[Dict[str, Any]],
    min_events: int,
) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for c in canonicals:
        cid = c["canonical_id"]
        agg = by_id.setdefault(cid, {
            "canonical_id": cid,
            "family": c["family"],
            "canonical_label": c["canonical_label"],
            "canonical_kind": c["canonical_kind"],
            "authorities": set(),
            "source_candidate_ids": set(),
            "repair_event_ids": set(),
            "mention_count": 0,
            "recorded_pieces": 0,
            "alias_labels": set(),
        })
        agg["authorities"].add(c.get("authority"))
        agg["source_candidate_ids"].update(x for x in c.get("source_candidate_ids", []) if x)
        agg["repair_event_ids"].update(c.get("repair_event_ids", []))
        agg["mention_count"] += int(c.get("mention_count") or 0)
        agg["recorded_pieces"] += int(c.get("recorded_pieces") or 0)

    for a in aliases:
        cid = a["canonical_id"]
        if cid not in by_id:
            continue
        agg = by_id[cid]
        agg["source_candidate_ids"].add(a.get("source_candidate_id"))
        agg["repair_event_ids"].update(a.get("repair_event_ids", []))
        agg["mention_count"] += int(a.get("mention_count") or 0)
        agg["recorded_pieces"] += int(a.get("recorded_pieces") or 0)
        agg["alias_labels"].add(a.get("observed_label"))

    for a in supplier_aliases:
        cid = a.get("canonical_id")
        if not cid or cid not in by_id:
            continue
        agg = by_id[cid]
        agg["source_candidate_ids"].add(a.get("source_candidate_id"))
        agg["repair_event_ids"].update(a.get("repair_event_ids", []))
        if not a.get("counts_already_in_canonical"):
            agg["mention_count"] += int(a.get("mention_count") or 0)
            agg["recorded_pieces"] += int(a.get("recorded_pieces") or 0)
        agg["alias_labels"].add(
            f"{a.get('supplier')}: {a.get('supplier_part_number')}"
        )

    out = []
    for agg in by_id.values():
        events = sorted(x for x in agg["repair_event_ids"] if x)
        if len(events) < min_events:
            continue
        out.append({
            "canonical_id": agg["canonical_id"],
            "family": agg["family"],
            "canonical_label": agg["canonical_label"],
            "canonical_kind": agg["canonical_kind"],
            "repair_event_count": len(events),
            "repair_event_ids": events,
            "mention_count": agg["mention_count"],
            "recorded_pieces": agg["recorded_pieces"],
            "authorities": sorted(x for x in agg["authorities"] if x),
            "source_candidate_ids": sorted(x for x in agg["source_candidate_ids"] if x),
            "absorbed_alias_labels": sorted(x for x in agg["alias_labels"] if x),
            "output_role": "80_20_recurring_parts",
            "qdrant_entries": 0,
            "accepted_facts": 0,
        })
    out.sort(key=lambda r: (-r["repair_event_count"], -r["mention_count"], r["canonical_label"].casefold()))
    return out

def classify_unresolved(
    rows: Sequence[Dict[str, Any]],
    family: str,
    state_by_id: Dict[str, str],
    web_results: Dict[str, Dict[str, Any]],
    min_web_events: int,
    min_auto_description_events: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Returns:
      web_promoted_canonicals, recurrence_canonicals, web_queue, preserved
    """
    web_promoted = []
    recurrence_canonicals = []
    web_queue = []
    preserved = []

    for row in rows:
        cid = str(row.get("candidate_id") or "")
        state = state_by_id.get(cid, "unreviewed")
        label = candidate_label(row)
        kind = row_kind(row)
        events = row_event_count(row)
        result = web_status(row, web_results)

        if result:
            status = str(result.get("status") or "").strip().lower()
            if status in {"valid", "confirmed", "canonical"}:
                web_promoted.append(promote_web_validated(row, result, family))
                continue
            if status in {"invalid", "junk", "not_a_part"}:
                x = dict(row)
                x["resolution"] = "preserved_web_invalid"
                x["web_validation"] = result
                preserved.append(x)
                continue
            # ambiguous web results remain unresolved/preserved or queued again.

        if kind == "explicit_part_number":
            plausible, reasons = looks_like_part_number(label, events)
            if plausible and events >= min_web_events:
                web_queue.append(
                    make_web_queue_row(
                        row,
                        family,
                        state,
                        best_match=row.get("_best_match"),
                    )
                )
            else:
                x = dict(row)
                x["resolution"] = "preserved_unpromoted"
                x["preserve_reason"] = reasons + (
                    ["below_web_recurrence_threshold"] if events < min_web_events else []
                )
                preserved.append(x)
            continue

        # Generic descriptions can be auto-promoted only when they are recurring,
        # clean, and were NOT explicitly screened out by the human.
        if (
            state != "human_screened"
            and events >= min_auto_description_events
            and looks_like_clean_description(label)
        ):
            recurrence_canonicals.append({
                "canonical_id": stable_id("cp_", family, kind, desc_norm(label)),
                "family": family,
                "canonical_label": label,
                "canonical_kind": kind,
                "authority": "recurrence_clean_description",
                "source_candidate_ids": [row.get("candidate_id")] if row.get("candidate_id") else [],
                "repair_event_ids": row_events(row),
                "repair_event_count": events,
                "mention_count": row_mentions(row),
                "recorded_pieces": row_pieces(row),
                "qdrant_entries": 0,
                "accepted_facts": 0,
            })
        else:
            x = dict(row)
            x["resolution"] = "preserved_unpromoted"
            x["preserve_reason"] = (
                ["human_screened_noncanonical"] if state == "human_screened" else []
            ) + (
                ["below_description_recurrence_threshold"] if events < min_auto_description_events else []
            )
            preserved.append(x)

    return web_promoted, recurrence_canonicals, web_queue, preserved

def merge_duplicate_canonicals(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge canonicals with the same family/kind/normalized canonical label.
    Human authority wins the label if present.
    """
    buckets: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        key_norm = compact(r["canonical_label"]) if r["canonical_kind"] == "explicit_part_number" else desc_norm(r["canonical_label"])
        buckets[(r["family"], r["canonical_kind"], key_norm)].append(r)

    out = []
    authority_rank = {
        "human_selected": 0,
        "web_validated": 1,
        "recurrence_clean_description": 2,
    }

    for (family, kind, key_norm), group in buckets.items():
        group = sorted(group, key=lambda r: authority_rank.get(r.get("authority"), 99))
        primary = dict(group[0])
        ev = set()
        src = set()
        hashes = set()
        authorities = set()
        mentions = 0
        pieces = 0
        web_validation = []
        for r in group:
            ev.update(r.get("repair_event_ids", []))
            src.update(x for x in r.get("source_candidate_ids", []) if x)
            hashes.update(x for x in r.get("evidence_hashes", []) if x)
            authorities.add(r.get("authority"))
            mentions += int(r.get("mention_count") or 0)
            pieces += int(r.get("recorded_pieces") or 0)
            if r.get("web_validation"):
                web_validation.append(r["web_validation"])
        primary["canonical_id"] = stable_id("cp_", family, kind, key_norm)
        primary["repair_event_ids"] = sorted(ev)
        primary["repair_event_count"] = len(ev) if ev else max(int(r.get("repair_event_count") or 0) for r in group)
        primary["source_candidate_ids"] = sorted(src)
        primary["evidence_hashes"] = sorted(hashes)
        primary["authorities"] = sorted(x for x in authorities if x)
        primary["mention_count"] = mentions
        primary["recorded_pieces"] = pieces
        if web_validation:
            primary["web_validations"] = web_validation
        out.append(primary)

    out.sort(key=lambda r: (r["family"], r["canonical_kind"], r["canonical_label"].casefold()))
    return out

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Nova DRL OCR Deviant Resolver / Canonical Parts Gate v1.6.2"
    )
    ap.add_argument(
        "--candidate-root",
        required=True,
        help="Review-workspace output directory containing review_candidates.jsonl for the target family.",
    )
    ap.add_argument(
        "--training-review-root",
        default=None,
        help="Human-reviewed workspace used as labeled OCR training. Defaults to --candidate-root.",
    )
    ap.add_argument("--output-root", required=True)
    ap.add_argument("--family", default=None, help="Override family label; otherwise inferred from candidate rows.")
    ap.add_argument("--web-results", default=None, help="Optional JSONL of completed web validations.")
    ap.add_argument(
        "--mouser-prefixes",
        default="511",
        help="Comma-separated Mouser supplier prefixes known to DRL; default: 511",
    )
    ap.add_argument("--auto-alias-threshold", type=float, default=0.92)
    ap.add_argument("--auto-alias-margin", type=float, default=0.08)
    ap.add_argument("--min-web-events", type=int, default=2)
    ap.add_argument("--min-auto-description-events", type=int, default=3)
    ap.add_argument("--min-recurring-events", type=int, default=2)
    ap.add_argument("--plan-only", action="store_true")
    args = ap.parse_args()

    candidate_root = Path(args.candidate_root)
    training_root = Path(args.training_review_root or args.candidate_root)
    output_root = Path(args.output_root)
    web_results_path = Path(args.web_results) if args.web_results else None
    mouser_prefixes = parse_mouser_prefixes(args.mouser_prefixes)

    target = load_review_root(candidate_root)
    training = load_review_root(training_root)

    training_selected, training_screened, training_state = derive_selected_suppressed(training)
    target_selected, target_screened, target_state = derive_selected_suppressed(target)

    # Infer family from target rows first, then training rows.
    family = normalized_ws(args.family)
    if not family:
        for r in list(target["candidates"]) + list(training_selected):
            if r.get("family"):
                family = normalized_ws(r["family"])
                break
    if not family:
        family = "UNKNOWN_FAMILY"

    # Known canonical vocabulary is the union of human selections in the training
    # review and any selections in the target review. Supplier identities are
    # separated BEFORE this vocabulary is used for OCR learning.
    known_human_rows = unique_rows(list(training_selected) + list(target_selected))
    (
        human_canonicals,
        human_supplier_aliases,
        unresolved_human_supplier_rows,
    ) = build_human_canonical_vocabulary(
        known_human_rows,
        family,
        mouser_prefixes,
        args.auto_alias_threshold,
        args.auto_alias_margin,
    )

    # Target decision states: explicit target decisions take priority. If candidate
    # root == training root, training states naturally fill the same IDs.
    state_by_id = dict(training_state if candidate_root.resolve() == training_root.resolve() else {})
    state_by_id.update(target_state)

    # Human-selected supplier rows have already been represented either as a
    # manufacturer canonical + supplier alias, or as unresolved supplier crosses.
    already_human_selected = {
        str(r.get("candidate_id")) for r in target_selected if r.get("candidate_id")
    }

    # TRAINING: supplier wrappers are removed from OCR-learning input.
    training_supplier_aliases, training_screened_residual = resolve_supplier_rows(
        training_screened,
        family,
        human_canonicals,
        training_state,
        mouser_prefixes,
        args.auto_alias_threshold,
        args.auto_alias_margin,
    )
    training_aliases, training_unresolved = learn_aliases(
        training_screened_residual,
        human_canonicals,
        args.auto_alias_threshold,
        args.auto_alias_margin,
    )
    profile = learn_confusion_profile(training_aliases)
    profile["supplier_wrappers_excluded_from_ocr_learning"] = {
        "resolved_training_supplier_aliases": len(training_supplier_aliases),
        "mouser_prefixes": list(mouser_prefixes),
        "digikey_suffix": DIGIKEY_SUFFIX,
    }

    # TARGET: supplier detection happens before OCR matching.
    target_noncanonical = [
        r for r in target["candidates"]
        if str(r.get("candidate_id") or "") not in already_human_selected
    ]
    target_supplier_aliases, target_after_supplier = resolve_supplier_rows(
        target_noncanonical,
        family,
        human_canonicals,
        state_by_id,
        mouser_prefixes,
        args.auto_alias_threshold,
        args.auto_alias_margin,
    )
    target_aliases, target_unresolved = learn_aliases(
        target_after_supplier,
        human_canonicals,
        args.auto_alias_threshold,
        args.auto_alias_margin,
    )

    # Important: target_aliases can include unreviewed rows as well as screened rows.
    # Rewrite source_state to reflect actual target state.
    for a in target_aliases:
        cid = str(a.get("source_candidate_id") or "")
        a["source_state"] = state_by_id.get(cid, "unreviewed")

    alias_ids = {str(a.get("source_candidate_id")) for a in target_aliases}
    supplier_linked_ids = {
        str(a.get("source_candidate_id"))
        for a in target_supplier_aliases
        if a.get("canonical_id")
    }
    unresolved_rows = [
        r for r in target_unresolved
        if str(r.get("candidate_id") or "") not in alias_ids
        and str(r.get("candidate_id") or "") not in supplier_linked_ids
        and str(r.get("candidate_id") or "") not in already_human_selected
    ]

    # Human-selected DigiKey (or future supplier) rows that could not be crossed
    # locally are intentionally sent to the web-validation queue.
    unresolved_rows.extend(unresolved_human_supplier_rows)

    web_results = load_web_results(web_results_path)
    web_promoted, recurrence_canonicals, web_queue, preserved = classify_unresolved(
        unresolved_rows,
        family,
        state_by_id,
        web_results,
        args.min_web_events,
        args.min_auto_description_events,
    )

    all_canonicals = merge_duplicate_canonicals(
        human_canonicals + web_promoted + recurrence_canonicals
    )

    # If web validation resolved a supplier PN, attach the supplier alias to the
    # newly promoted manufacturer canonical.
    web_promoted_by_candidate = {}
    for can in all_canonicals:
        for cid in can.get("source_candidate_ids", []):
            if cid:
                web_promoted_by_candidate[str(cid)] = can

    existing_supplier_source_ids = {
        str(a.get("source_candidate_id"))
        for a in (human_supplier_aliases + target_supplier_aliases)
    }
    web_supplier_aliases = []
    for row in unresolved_rows:
        cid = str(row.get("candidate_id") or "")
        info = row.get("_supplier_info") if isinstance(row.get("_supplier_info"), dict) else None
        can = web_promoted_by_candidate.get(cid)
        if info and can and cid not in existing_supplier_source_ids:
            web_supplier_aliases.append(
                make_supplier_alias(
                    row,
                    family,
                    info,
                    can,
                    source_state=state_by_id.get(cid, row.get("_supplier_source_state", "unreviewed")),
                    counts_already_in_canonical=True,
                )
            )

    all_supplier_aliases = (
        human_supplier_aliases
        + target_supplier_aliases
        + web_supplier_aliases
    )

    # Remap alias canonical IDs to merged canonical IDs by family/kind/label.
    canonical_lookup = {}
    for c in all_canonicals:
        k = (
            c["family"],
            c["canonical_kind"],
            compact(c["canonical_label"]) if c["canonical_kind"] == "explicit_part_number" else desc_norm(c["canonical_label"]),
        )
        canonical_lookup[k] = c
    for a in target_aliases:
        k = (
            a["family"],
            a["observed_kind"],
            compact(a["canonical_label"]) if a["observed_kind"] == "explicit_part_number" else desc_norm(a["canonical_label"]),
        )
        if k in canonical_lookup:
            a["canonical_id"] = canonical_lookup[k]["canonical_id"]

    for a in all_supplier_aliases:
        if not a.get("canonical_label"):
            continue
        k = (
            a["family"],
            "explicit_part_number",
            compact(a["canonical_label"]),
        )
        if k in canonical_lookup:
            a["canonical_id"] = canonical_lookup[k]["canonical_id"]
            a["canonical_label"] = canonical_lookup[k]["canonical_label"]

    recurring = aggregate_recurring(
        all_canonicals,
        target_aliases,
        all_supplier_aliases,
        args.min_recurring_events,
    )

    manifest = {
        "version": VERSION,
        "schema": SCHEMA,
        "family": family,
        "built_at_utc": now_utc(),
        "candidate_root": str(candidate_root),
        "training_review_root": str(training_root),
        "web_results_path": str(web_results_path) if web_results_path else None,
        "inputs": {
            "target_candidates": len(target["candidates"]),
            "target_human_selected": len(target_selected),
            "target_human_screened": len(target_screened),
            "training_human_selected": len(training_selected),
            "training_human_screened": len(training_screened),
        },
        "outputs": {
            "canonical_parts": len(all_canonicals),
            "ocr_auto_aliases_target": len(target_aliases),
            "supplier_aliases": len(all_supplier_aliases),
            "web_validation_queue": len(web_queue),
            "preserved_unpromoted": len(preserved),
            "recurring_80_20_parts": len(recurring),
            "training_high_confidence_ocr_aliases": len(training_aliases),
            "training_supplier_aliases_excluded_from_ocr_learning": len(training_supplier_aliases),
        },
        "thresholds": {
            "auto_alias_threshold": args.auto_alias_threshold,
            "auto_alias_margin": args.auto_alias_margin,
            "min_web_events": args.min_web_events,
            "min_auto_description_events": args.min_auto_description_events,
            "min_recurring_events": args.min_recurring_events,
        },
        "policy": {
            "frozen_evidence_modified": False,
            "screened_candidate_auto_promoted_as_new_canonical": False,
            "supplier_identity_separate_from_manufacturer_identity": True,
            "supplier_wrappers_excluded_from_ocr_learning": True,
            "mouser_prefixes": list(mouser_prefixes),
            "digikey_suffix": DIGIKEY_SUFFIX,
            "digikey_suffix_alone_can_seed_manufacturer_canonical": False,
            "web_validation_required_for_recurring_unknown_explicit_pn": True,
            "web_calls_inside_script": False,
            "low_frequency_unresolved_preserved": True,
            "accepted_facts": 0,
            "qdrant_entries": 0,
            "80_20_rule": "fixed default",
        },
        "input_hashes": {
            "target_review_candidates": sha256_file(target["candidates_path"]),
            "target_selected_parts": sha256_file(target["selected_path"]),
            "target_suppressed_parts": sha256_file(target["suppressed_path"]),
            "training_review_candidates": sha256_file(training["candidates_path"]),
            "training_selected_parts": sha256_file(training["selected_path"]),
            "training_suppressed_parts": sha256_file(training["suppressed_path"]),
            "web_results": sha256_file(web_results_path) if web_results_path else None,
        },
    }

    print("# Nova DRL OCR Deviant Resolver / Canonical Parts Gate v1.6.2")
    print(f"Family:                         {family}")
    print(f"Target candidates:              {len(target['candidates'])}")
    print(f"Human canonical vocabulary:     {len(human_canonicals)}")
    print(f"Training screened candidates:   {len(training_screened)}")
    print(f"Training learned OCR aliases:   {len(training_aliases)}")
    print(f"Training supplier aliases:      {len(training_supplier_aliases)}")
    print(f"Target OCR aliases:             {len(target_aliases)}")
    print(f"Supplier aliases total:         {len(all_supplier_aliases)}")
    print(f"Canonical parts after gate:     {len(all_canonicals)}")
    print(f"Web validation queue:           {len(web_queue)}")
    print(f"Preserved/unpromoted:           {len(preserved)}")
    print(f"80/20 recurring parts:          {len(recurring)}")
    print("Frozen evidence modified:       NO")
    print("Accepted facts:                 0")
    print("Qdrant:                         OFF")
    print(f"Mouser supplier prefixes:       {','.join(mouser_prefixes)}")
    print(f"DigiKey supplier suffix:        {DIGIKEY_SUFFIX}")
    print("Online calls in this script:    NONE")

    if all_supplier_aliases:
        print("\nSupplier/vendor aliases:")
        for a in sorted(
            all_supplier_aliases,
            key=lambda x: (-x.get("repair_event_count", 0), x.get("supplier") or "", x.get("supplier_part_number") or ""),
        )[:20]:
            target = a.get("canonical_label") or "NEEDS MANUFACTURER CROSS"
            print(
                f"  {a.get('supplier')}: {a.get('supplier_part_number')}  ->  {target}"
                f" | events={a.get('repair_event_count', 0)}"
                f" | state={a.get('source_state')}"
            )

    if target_aliases:
        print("\nTop target OCR/format aliases:")
        for a in sorted(target_aliases, key=lambda x: (-x["repair_event_count"], -x["similarity_score"], x["observed_label"]))[:20]:
            print(
                f"  {a['observed_label']}  ->  {a['canonical_label']}"
                f" | score={a['similarity_score']:.3f}"
                f" | events={a['repair_event_count']}"
                f" | state={a['source_state']}"
            )

    if web_queue:
        print("\nTop web-validation candidates:")
        for r in sorted(web_queue, key=lambda x: (-x["repair_event_count"], -x["mention_count"], x["observed_label"]))[:20]:
            print(
                f"  {r['observed_label']}"
                f" | events={r['repair_event_count']}"
                f" | mentions={r['mention_count']}"
                f" | state={r['source_state']}"
            )

    if args.plan_only:
        print("\nPLAN ONLY: no output files written.")
        return 0

    output_root.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_root / "canonical_parts_v1_6_1.jsonl", all_canonicals)
    write_jsonl(output_root / "part_aliases_v1_6_1.jsonl", target_aliases)
    write_jsonl(output_root / "web_validation_queue_v1_6_1.jsonl", web_queue)
    write_jsonl(output_root / "preserved_unpromoted_v1_6_1.jsonl", preserved)
    write_jsonl(output_root / "recurring_parts_80_20_v1_6_1.jsonl", recurring)
    write_json(output_root / "ocr_deviant_learning_v1_6_1.json", profile)
    write_json(output_root / "parts_resolver_manifest_v1_6_1.json", manifest)

    summary_lines = [
        "# Nova DRL Parts Resolver v1.6.2",
        "",
        f"Family: {family}",
        "",
        "COUNTS",
        "------",
        f"Target candidates: {len(target['candidates'])}",
        f"Human canonical vocabulary: {len(human_canonicals)}",
        f"Training human-screened: {len(training_screened)}",
        f"Training high-confidence OCR aliases learned: {len(training_aliases)}",
        f"Training supplier aliases excluded from OCR learning: {len(training_supplier_aliases)}",
        f"Target OCR aliases resolved to canonicals: {len(target_aliases)}",
        f"Supplier aliases preserved: {len(all_supplier_aliases)}",
        f"Canonical parts after gate: {len(all_canonicals)}",
        f"Web-validation queue: {len(web_queue)}",
        f"Preserved/unpromoted: {len(preserved)}",
        f"80/20 recurring parts: {len(recurring)}",
        "",
        "POLICY",
        "------",
        "Frozen evidence modified: NO",
        "Supplier identity separated from manufacturer identity: YES",
        "Mouser 511- wrapper: SUPPLIER ALIAS, not OCR",
        "DigiKey -ND suffix: SUPPLIER ALIAS, not OCR",
        "DigiKey -ND body alone creates manufacturer canonical: NO",
        "Human-screened item promoted as standalone canonical without external validation: NO",
        "Recurring unknown explicit PN: WEB VALIDATION QUEUE",
        "Low-frequency unresolved candidate: PRESERVED ONLY",
        "Accepted facts: 0",
        "Qdrant: OFF",
        "80/20 rule: FIXED DEFAULT",
        "",
        "OUTPUTS",
        "-------",
        "canonical_parts_v1_6_1.jsonl",
        "part_aliases_v1_6_1.jsonl",
        "web_validation_queue_v1_6_1.jsonl",
        "preserved_unpromoted_v1_6_1.jsonl",
        "recurring_parts_80_20_v1_6_1.jsonl",
        "ocr_deviant_learning_v1_6_1.json",
        "parts_resolver_manifest_v1_6_1.json",
    ]
    (output_root / "parts_resolver_summary_v1_6_1.txt").write_text(
        "\n".join(summary_lines) + "\n", encoding="utf-8"
    )

    print(f"\nOutputs: {output_root}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
