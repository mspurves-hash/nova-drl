#!/usr/bin/env python3
"""
Nova DRL Global Manufacturer-PN Validation Worklist v1.7.4

Purpose
-------
Take the FULL-CORPUS recurring manufacturer-PN queue from v1.7.3 and turn it
into a small, prioritized, reusable validation worklist.

This is NOT a canonicalizer and NOT a web validator. It performs only safe,
global classification before external/manufacturer validation.

Input
-----
Default:
  /opt/nova-drl/output/full_corpus_parts_gate_v1_7_3/
    manufacturer_pn_validation_queue_v1_7_3.jsonl

Expected upstream run:
  nova_parts_full_corpus_gate_v1_7_3.py
    --global-validation-min-events 10
    --fuzzy-min-events 10

Safe global classifications
---------------------------
- placeholders such as N/A, NONE, UNKNOWN -> not a part identity
- value/component descriptions such as "100uF cap" -> component_spec
- obvious Amazon-style ASINs (B0xxxxxxxx) -> retail_reference
- everything else remains an unvalidated manufacturer-PN candidate

Prioritization
--------------
Tier A:
  >=20 repair events AND >=2 equipment families

Tier B:
  >=20 repair events in one family
  OR 10-19 events across >=3 families

Tier C:
  remaining 10+ event candidates

No candidate is auto-canonicalized.
No suffix/prefix fuzzy pair is merged.
No family-specific rules.
No LLM, web, Qdrant, or accepted-fact writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

VERSION = "1.7.4"
SCHEMA = "nova-drl-global-manufacturer-pn-validation-worklist-v1"

DEFAULT_ROOT = Path("/opt/nova-drl/output/full_corpus_parts_gate_v1_7_3")
DEFAULT_QUEUE = DEFAULT_ROOT / "manufacturer_pn_validation_queue_v1_7_3.jsonl"
DEFAULT_OUTPUT = Path("/opt/nova-drl/output/global_pn_validation_v1_7_4")

PLACEHOLDERS = {
    "", "N/A", "NA", "N.A.", "NONE", "NO PART", "NO PART NUMBER",
    "UNKNOWN", "UNK", "TBD", "?", "-", "--", "NULL",
}

VALUE_UNIT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:"
    r"UF|PF|NF|MF|F|V|VAC|VDC|A|AMP|AMPS|W|WATT|WATTS|"
    r"OHM|OHMS|R|K|M|HZ|KHZ|MHZ"
    r")\b",
    re.I,
)

COMPONENT_WORD_RE = re.compile(
    r"\b(?:CAP|CAPS|CAPACITOR|CAPACITORS|FUSE|RESISTOR|RESISTORS|"
    r"BEARING|BEARINGS|BELT|BELTS|MOTOR|MOTORS|DIODE|DIODES|"
    r"TRANSISTOR|TRANSISTORS|MOSFET|MOSFETS|RELAY|RELAYS|"
    r"CONNECTOR|CONNECTORS|SENSOR|SENSORS|IC|CHIP|CHIPS)\b",
    re.I,
)

# Amazon ASINs are 10-character alphanumeric identifiers and commonly begin B0.
AMAZON_ASIN_RE = re.compile(r"^B0[A-Z0-9]{8}$", re.I)


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
        raise RuntimeError(
            f"Missing upstream validation queue: {path}\n"
            "Run v1.7.3 non-plan with the 10+ thresholds first."
        )
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


def display_label(row: Dict[str, Any]) -> str:
    return normalized_ws(row.get("display_label"))


def classify(label: str) -> Dict[str, Any]:
    raw = normalized_ws(label)
    upper = raw.upper()

    if upper in PLACEHOLDERS:
        return {
            "classification": "not_a_part_identity",
            "reason": "placeholder_or_missing_value",
            "eligible_for_manufacturer_validation": False,
        }

    if AMAZON_ASIN_RE.fullmatch(upper):
        return {
            "classification": "retail_reference",
            "reason": "amazon_asin_shape",
            "eligible_for_manufacturer_validation": False,
        }

    has_value = bool(VALUE_UNIT_RE.search(raw))
    has_component_word = bool(COMPONENT_WORD_RE.search(raw))
    if has_value and has_component_word:
        return {
            "classification": "component_spec",
            "reason": "value_plus_component_description",
            "eligible_for_manufacturer_validation": False,
        }

    # Strings that are overwhelmingly a rating/spec should not enter manufacturer
    # validation merely because the upstream mixed-alpha-digit heuristic accepted them.
    if has_value:
        residual = VALUE_UNIT_RE.sub(" ", upper)
        residual = re.sub(r"[^A-Z0-9]+", " ", residual)
        residual_words = [
            x for x in residual.split()
            if x not in {
                "CAP", "CAPS", "CAPACITOR", "CAPACITORS", "FUSE",
                "RESISTOR", "RESISTORS"
            }
        ]
        if not residual_words:
            return {
                "classification": "component_spec",
                "reason": "electrical_rating_or_value",
                "eligible_for_manufacturer_validation": False,
            }

    return {
        "classification": "manufacturer_pn_candidate",
        "reason": "recurring_explicit_identity_requires_external_validation",
        "eligible_for_manufacturer_validation": True,
    }


def priority_tier(repairs: int, families: int) -> str:
    if repairs >= 20 and families >= 2:
        return "A"
    if repairs >= 20 or (repairs >= 10 and families >= 3):
        return "B"
    return "C"


def priority_score(repairs: int, families: int, variants: int) -> int:
    # Simple transparent ranking: recurrence dominates, cross-family reuse is
    # second, and variant count adds a small reward because one validation may
    # resolve more observed renderings.
    return repairs * 100 + min(families, 99) * 10 + min(variants, 9)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Nova DRL Global Manufacturer-PN Validation Worklist v1.7.4"
    )
    ap.add_argument("--queue", default=str(DEFAULT_QUEUE))
    ap.add_argument("--output-root", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--plan-only", action="store_true")
    args = ap.parse_args()

    queue_path = Path(args.queue)
    output_root = Path(args.output_root)
    rows = read_jsonl(queue_path)

    worklist = []
    excluded = []

    for row in rows:
        label = display_label(row)
        c = classify(label)
        repairs = int(row.get("repair_event_count") or 0)
        families = int(row.get("family_count") or 0)
        variants = len(row.get("observed_variants") or [])

        base = {
            "validation_id": stable_id(
                "pv_", row.get("global_identity_id"), row.get("identity_key")
            ),
            "global_identity_id": row.get("global_identity_id"),
            "identity_key": row.get("identity_key"),
            "display_label": label,
            "observed_variants": list(row.get("observed_variants") or []),
            "repair_event_count": repairs,
            "family_count": families,
            "families": list(row.get("families") or []),
            "mention_count": int(row.get("mention_count") or 0),
            "recorded_pieces": int(row.get("recorded_pieces") or 0),
            "classification": c["classification"],
            "classification_reason": c["reason"],
            "eligible_for_manufacturer_validation": c[
                "eligible_for_manufacturer_validation"
            ],
        }

        if c["eligible_for_manufacturer_validation"]:
            tier = priority_tier(repairs, families)
            base["priority_tier"] = tier
            base["priority_score"] = priority_score(repairs, families, variants)
            base["validation_status"] = "PENDING_GLOBAL_VALIDATION"
            base["canonical_label"] = None
            base["manufacturer"] = None
            base["manufacturer_source"] = None
            base["notes"] = None
            worklist.append(base)
        else:
            base["validation_status"] = "NOT_MANUFACTURER_PN"
            excluded.append(base)

    worklist.sort(
        key=lambda r: (
            r["priority_tier"],
            -r["priority_score"],
            r["display_label"].casefold(),
        )
    )
    excluded.sort(
        key=lambda r: (
            r["classification"],
            -r["repair_event_count"],
            r["display_label"].casefold(),
        )
    )

    tiers = Counter(r["priority_tier"] for r in worklist)
    exclusions = Counter(r["classification"] for r in excluded)

    print("# Nova DRL Global Manufacturer-PN Validation Worklist v1.7.4")
    print()
    print(f"Upstream recurring PN candidates:       {len(rows):,}")
    print(f"Manufacturer validation worklist:       {len(worklist):,}")
    print(f"Safe non-manufacturer exclusions:       {len(excluded):,}")
    print(f"Tier A:                                  {tiers.get('A', 0):,}")
    print(f"Tier B:                                  {tiers.get('B', 0):,}")
    print(f"Tier C:                                  {tiers.get('C', 0):,}")
    print("Automatic canonical merges:             0")
    print("Fuzzy merges:                            0")
    print("Family-specific rules:                   0")
    print("Web calls in this stage:                 0")
    print("Accepted facts:                          0")
    print("Qdrant:                                  OFF")

    if exclusions:
        print("\nSAFE EXCLUSIONS")
        print("---------------")
        for k, v in exclusions.most_common():
            print(f"{k:26} {v:,}")
        for r in excluded[:20]:
            print(
                f"  {r['display_label']}"
                f" | repairs={r['repair_event_count']}"
                f" | {r['classification_reason']}"
            )

    print("\nTOP VALIDATION WORKLIST")
    print("-----------------------")
    for i, r in enumerate(worklist[:100], 1):
        print(
            f"{i:3}. [{r['priority_tier']}] {r['display_label']}"
            f" | repairs={r['repair_event_count']}"
            f" | families={r['family_count']}"
            f" | variants={len(r['observed_variants'])}"
        )

    print("\n80/20 POLICY")
    print("------------")
    print("Validate Tier A first.")
    print("Tier B follows only after Tier A results are reusable/cached.")
    print("Tier C remains queued; no need to validate it before it becomes operationally useful.")
    print("Each identity is validated once globally and reused across all equipment families.")

    if args.plan_only:
        print("\nPLAN ONLY: no output files written.")
        return 0

    output_root.mkdir(parents=True, exist_ok=True)
    write_jsonl(
        output_root / "manufacturer_validation_worklist_v1_7_4.jsonl",
        worklist,
    )
    write_jsonl(
        output_root / "non_manufacturer_recurring_identities_v1_7_4.jsonl",
        excluded,
    )
    write_json(
        output_root / "manufacturer_validation_manifest_v1_7_4.json",
        {
            "version": VERSION,
            "schema": SCHEMA,
            "built_at_utc": now_utc(),
            "input": {
                "queue": str(queue_path),
                "queue_sha256": sha256_file(queue_path),
            },
            "counts": {
                "upstream_candidates": len(rows),
                "manufacturer_validation_worklist": len(worklist),
                "safe_exclusions": len(excluded),
                "tier_a": tiers.get("A", 0),
                "tier_b": tiers.get("B", 0),
                "tier_c": tiers.get("C", 0),
            },
            "exclusion_classes": dict(exclusions),
            "policy": {
                "global_validation_once": True,
                "automatic_canonical_merges": 0,
                "fuzzy_merges": 0,
                "family_specific_rules": 0,
                "web_calls": 0,
                "accepted_facts": 0,
                "qdrant_entries": 0,
                "80_20_rule": "fixed default",
            },
        },
    )
    print(f"\nOutputs: {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
