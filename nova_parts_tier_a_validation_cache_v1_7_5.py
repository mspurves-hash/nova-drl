#!/usr/bin/env python3
"""
Nova DRL Tier-A Global Validation Cache v1.7.5

Purpose
-------
Apply a small evidence-backed validation seed to the Tier-A manufacturer-PN
worklist and produce a reusable global cache.

This stage does NOT search the web itself. Web/manufacturer research is done
outside the runtime and recorded in an appendable validation seed JSONL.

Policy
------
- Validate once globally, reuse everywhere.
- Tier A only by default.
- No fuzzy merges.
- No family-specific rules.
- A validation seed can validate, classify as an industry designation, or defer.
- Deferred identities remain separate and usable as observed evidence.
- Unmatched Tier-A identities remain pending; they are not errors.
- Frozen v1.5.2 / v1.7.x evidence is read-only.
- No Qdrant or accepted-fact writes.
"""

from __future__ import annotations

import argparse, hashlib, json, os, re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

VERSION = "1.7.5"
SCHEMA = "nova-drl-tier-a-global-validation-cache-v1"

DEFAULT_WORKLIST = Path(
    "/opt/nova-drl/output/global_pn_validation_v1_7_4/"
    "manufacturer_validation_worklist_v1_7_4.jsonl"
)
DEFAULT_SEED = Path("/opt/nova-drl/tier_a_validation_seed_v1_7_5.jsonl")
DEFAULT_OUTPUT = Path("/opt/nova-drl/output/tier_a_validation_cache_v1_7_5")


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def normalized_ws(v):
    return " ".join(str(v or "").split()).strip()


def compact(v):
    return re.sub(r"[^A-Z0-9]+", "", normalized_ws(v).upper())


def sha256_file(path: Path):
    if not path.exists():
        return None
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(path: Path):
    if not path.exists():
        raise RuntimeError(f"Missing JSONL: {path}")
    out=[]
    with path.open("r",encoding="utf-8",errors="replace") as f:
        for n,line in enumerate(f,1):
            if not line.strip(): continue
            try: row=json.loads(line)
            except Exception as exc:
                raise RuntimeError(f"Invalid JSONL {path}:{n}: {exc}") from exc
            if isinstance(row,dict): out.append(row)
    return out


def write_jsonl(path: Path, rows: Iterable[Dict[str,Any]]):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    with tmp.open("w",encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r,ensure_ascii=False)+"\n")
    os.replace(tmp,path)


def write_json(path: Path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    with tmp.open("w",encoding="utf-8") as f:
        json.dump(obj,f,indent=2,ensure_ascii=False); f.write("\n")
    os.replace(tmp,path)


def build_seed_index(seed_rows):
    idx={}
    conflicts=[]
    for s in seed_rows:
        labels=list(s.get("match_labels") or [])
        if s.get("canonical_label"): labels.append(s["canonical_label"])
        keys={compact(x) for x in labels if compact(x)}
        for k in keys:
            if k in idx and idx[k] != s:
                conflicts.append({"match_key":k,"left":idx[k],"right":s})
            else:
                idx[k]=s
    return idx,conflicts


def row_match_keys(row):
    vals=[row.get("display_label")]
    vals.extend(row.get("observed_variants") or [])
    return {compact(x) for x in vals if compact(x)}


def main():
    ap=argparse.ArgumentParser(description="Nova DRL Tier-A Global Validation Cache v1.7.5")
    ap.add_argument("--worklist",default=str(DEFAULT_WORKLIST))
    ap.add_argument("--seed",default=str(DEFAULT_SEED))
    ap.add_argument("--output-root",default=str(DEFAULT_OUTPUT))
    ap.add_argument("--tier",default="A")
    ap.add_argument("--plan-only",action="store_true")
    args=ap.parse_args()

    worklist=read_jsonl(Path(args.worklist))
    seed=read_jsonl(Path(args.seed))
    seed_idx,seed_conflicts=build_seed_index(seed)
    if seed_conflicts:
        raise RuntimeError(f"Validation seed has {len(seed_conflicts)} conflicting match keys")

    target=[r for r in worklist if str(r.get("priority_tier") or "")==args.tier]
    validated=[]; deferred=[]; pending=[]

    for r in target:
        matches=[]
        for k in row_match_keys(r):
            if k in seed_idx and seed_idx[k] not in matches:
                matches.append(seed_idx[k])

        if not matches:
            x=dict(r)
            x["cache_status"]="PENDING_VALIDATION"
            pending.append(x)
            continue
        if len(matches)>1:
            x=dict(r)
            x["cache_status"]="DEFERRED_CONFLICTING_SEED_MATCHES"
            x["seed_matches"]=matches
            deferred.append(x)
            continue

        s=matches[0]
        x=dict(r)
        x.update({
            "cache_status":s.get("validation_status"),
            "canonical_label":s.get("canonical_label"),
            "identity_type":s.get("identity_type"),
            "validated_manufacturer":s.get("manufacturer"),
            "validation_confidence":s.get("confidence"),
            "source_authority":s.get("source_authority"),
            "source_url":s.get("source_url"),
            "validation_notes":s.get("notes"),
            "validated_at_utc":now_utc(),
        })
        if str(s.get("validation_status") or "").startswith("validated"):
            validated.append(x)
        else:
            deferred.append(x)

    validated.sort(key=lambda r:(-int(r.get("repair_event_count") or 0), r.get("display_label","")))
    deferred.sort(key=lambda r:(-int(r.get("repair_event_count") or 0), r.get("display_label","")))
    pending.sort(key=lambda r:(-int(r.get("repair_event_count") or 0), r.get("display_label","")))

    print("# Nova DRL Tier-A Global Validation Cache v1.7.5")
    print()
    print(f"Tier {args.tier} worklist identities:          {len(target)}")
    print(f"Seed validation records:             {len(seed)}")
    print(f"Validated/cacheable now:             {len(validated)}")
    print(f"Deferred intentionally:              {len(deferred)}")
    print(f"Still pending:                       {len(pending)}")
    print("Fuzzy merges:                        0")
    print("Family-specific rules:               0")
    print("Frozen evidence modified:            NO")
    print("Accepted facts:                      0")
    print("Qdrant:                              OFF")

    status_counts=Counter(r.get("cache_status") for r in validated+deferred)
    print("\nSEED RESULT STATUS")
    print("------------------")
    for k,v in status_counts.most_common():
        print(f"{str(k):38} {v}")

    print("\nVALIDATED / CACHEABLE")
    print("---------------------")
    for r in validated:
        print(
            f"{r['display_label']} -> {r.get('canonical_label')}"
            f" | {r.get('identity_type')}"
            f" | manufacturer={r.get('validated_manufacturer')}"
            f" | repairs={r.get('repair_event_count')}"
            f" | confidence={r.get('validation_confidence')}"
        )

    if deferred:
        print("\nDEFERRED — PRESERVED AS OBSERVED")
        print("--------------------------------")
        for r in deferred:
            print(
                f"{r['display_label']}"
                f" | repairs={r.get('repair_event_count')}"
                f" | status={r.get('cache_status')}"
                f" | {r.get('validation_notes') or ''}"
            )

    print("\nNEXT 20 PENDING")
    print("---------------")
    for r in pending[:20]:
        print(
            f"{r['display_label']} | repairs={r.get('repair_event_count')}"
            f" | families={r.get('family_count')}"
        )

    if args.plan_only:
        print("\nPLAN ONLY: no output files written.")
        return 0

    root=Path(args.output_root); root.mkdir(parents=True,exist_ok=True)
    write_jsonl(root/"validated_global_identity_cache_v1_7_5.jsonl",validated)
    write_jsonl(root/"deferred_global_identities_v1_7_5.jsonl",deferred)
    write_jsonl(root/"pending_tier_a_identities_v1_7_5.jsonl",pending)
    write_json(root/"tier_a_validation_cache_manifest_v1_7_5.json",{
        "version":VERSION,
        "schema":SCHEMA,
        "built_at_utc":now_utc(),
        "inputs":{
            "worklist":str(args.worklist),
            "worklist_sha256":sha256_file(Path(args.worklist)),
            "seed":str(args.seed),
            "seed_sha256":sha256_file(Path(args.seed)),
        },
        "counts":{
            "tier_target":len(target),
            "seed_records":len(seed),
            "validated":len(validated),
            "deferred":len(deferred),
            "pending":len(pending),
        },
        "policy":{
            "validate_once_globally":True,
            "fuzzy_merges":0,
            "family_specific_rules":0,
            "frozen_evidence_modified":False,
            "accepted_facts":0,
            "qdrant_entries":0,
            "80_20_rule":"fixed default",
        }
    })
    print(f"\nOutputs: {root}")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
