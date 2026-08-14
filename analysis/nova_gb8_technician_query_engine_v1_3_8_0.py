#!/usr/bin/env python3
"""Nova DRL GB8 Technician Query Engine v1.3.8.0.

Read-only, Python-only query layer over the frozen v1.3.7.3 technician-signal baseline.
No LLM calls. No Qdrant. No approvals. No source mutation.

Designed for practical 80/20 technician questions such as:
- Y axis drifting
- vacuum leak
- what normally gets replaced
- show history for serial 80050608
- what happened on log 130130006
- common GB8 service areas
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shlex
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set, Tuple

VERSION = "1.3.8.0"
REQUIRED_SIGNAL_VERSION = "1.3.7.3"
DEFAULT_INPUT_ROOT = Path("/opt/nova-drl/output/traveler_technician_signal_v1_3_7_3")

DEFAULT_ALIASES = {
    "vac": ["vacuum"],
    "vacuum": ["vac"],
    "leadscrew": ["lead screw", "lead screws"],
    "leadscrews": ["lead screw", "lead screws"],
    "lead screw": ["leadscrew", "lead screws"],
    "lead screws": ["leadscrew", "lead screw"],
    "drift": ["drifting"],
    "drifting": ["drift"],
    "belt": ["belts"],
    "belts": ["belt"],
    "motor": ["motors"],
    "motors": ["motor"],
    "encoder": ["encoders"],
    "encoders": ["encoder"],
    "bearing": ["bearings", "bears", "bers"],
    "bearings": ["bearing", "bears", "bers"],
    "brush": ["brushes"],
    "brushes": ["brush"],
    "rebuild": ["rebuilt", "refurb", "refurbish", "refurbished"],
    "rebuilt": ["rebuild", "refurb", "refurbished"],
    "servo off": ["servo loop"],
    "servo loop": ["servo off"],
    "home": ["homing", "home flag", "home sensor"],
    "homing": ["home", "home flag", "home sensor"],
    "a1 a2": ["a1+a2", "a1/a2", "a1 and a2"],
    "a1+a2": ["a1 a2", "a1/a2", "a1 and a2"],
    "rtz": ["r t z", "r,t,z", "r+t+z"],
}

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "for", "from", "gb8", "genmark",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "please", "show", "the", "to",
    "what", "when", "where", "which", "with", "normally", "usually", "common", "about",
}

STOCK_INTENT = re.compile(r"\b(stock|stocking|parts?|replace|replaced|replacement|what gets|normally gets|usually gets|consumables?|spares?)\b", re.I)
SERVICE_INTENT = re.compile(r"\b(service areas?|common issues?|common problems?|common repairs?|high[- ]attention|inspection areas?|where to start)\b", re.I)
SERIAL_RE = re.compile(r"(?<!\d)(80\d{6})(?!\d)")
LOG_RE = re.compile(r"(?<!\d)(\d{9})(?!\d)")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def norm(value: Any) -> str:
    s = str(value or "").lower()
    s = s.replace("_", " ")
    s = re.sub(r"[^a-z0-9+./-]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def token_set(text: str) -> Set[str]:
    toks = set(re.findall(r"[a-z0-9]+", norm(text)))
    return {t for t in toks if t not in STOPWORDS and len(t) > 1}


def compact_text(value: Any, limit: int = 190) -> str:
    s = re.sub(r"\s+", " ", str(value or "")).strip()
    return s if len(s) <= limit else s[: max(0, limit - 1)] + "…"


def group_id(g: Dict[str, Any]) -> str:
    return str(g.get("group_id") or g.get("recurring_group_id") or "")


def group_logs(g: Dict[str, Any]) -> Set[str]:
    logs = {str(x) for x in (g.get("logs") or []) if str(x)}
    if logs:
        return logs
    return {str(r.get("log_number")) for r in (g.get("raw_variants") or []) if r.get("log_number")}


def group_serials(g: Dict[str, Any]) -> Set[str]:
    serials = {str(x) for x in (g.get("serial_numbers") or []) if str(x) and str(x) != "?"}
    if serials:
        return serials
    return {str(r.get("serial_number")) for r in (g.get("raw_variants") or []) if r.get("serial_number") and str(r.get("serial_number")) != "?"}


def group_variants(g: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [r for r in (g.get("raw_variants") or []) if isinstance(r, dict)]


def group_label(g: Dict[str, Any]) -> str:
    return str(g.get("concept_label") or g.get("concept_key") or group_id(g) or "unlabeled")


def load_aliases(path: Path | None) -> Dict[str, List[str]]:
    aliases = {k: list(v) for k, v in DEFAULT_ALIASES.items()}
    if path and path.exists():
        payload = load_json(path)
        for k, v in (payload.get("aliases") or {}).items():
            if isinstance(v, list):
                aliases[norm(k)] = [norm(x) for x in v if norm(x)]
    return aliases


def expand_query(query: str, aliases: Dict[str, List[str]]) -> Tuple[Set[str], Set[str]]:
    qn = norm(query)
    phrases: Set[str] = {qn} if qn else set()
    tokens = token_set(qn)
    # Add phrase and token aliases conservatively. These improve recall but never rewrite evidence.
    for key, vals in aliases.items():
        if key and key in qn:
            phrases.update(vals)
            for v in vals:
                tokens.update(token_set(v))
    return tokens, {p for p in phrases if p}


def load_index(input_root: Path) -> Dict[str, Any]:
    tech_path = input_root / "technician_patterns_v1_3_7_3.json"
    ref_path = input_root / "reference_patterns_v1_3_7_3.json"
    svc_path = input_root / "service_area_rollup_v1_3_7_3.json"
    stock_path = input_root / "stocking_attention_v1_3_7_3.json"
    manifest_path = input_root / "technician_signal_manifest_v1_3_7_3.json"
    for p in (tech_path, ref_path, svc_path, stock_path, manifest_path):
        if not p.exists():
            raise FileNotFoundError(f"Required v1.3.7.3 input missing: {p}")

    tech = load_json(tech_path)
    ref = load_json(ref_path)
    svc = load_json(svc_path)
    stock = load_json(stock_path)
    manifest = load_json(manifest_path)

    if str(tech.get("signal_cleaner_version")) != REQUIRED_SIGNAL_VERSION:
        raise ValueError(f"Expected technician signal {REQUIRED_SIGNAL_VERSION}, got {tech.get('signal_cleaner_version')}")
    technician = [g for g in (tech.get("groups") or []) if isinstance(g, dict)]
    reference = [g for g in (ref.get("groups") or []) if isinstance(g, dict)]
    expected_total = int(manifest.get("source_recurring_group_count") or len(technician) + len(reference))
    if len(technician) + len(reference) != expected_total:
        raise ValueError(f"v1.3.7.3 accounting mismatch: {len(technician)} + {len(reference)} != {expected_total}")
    if int(tech.get("accepted_fact_count") or 0) != 0 or int(tech.get("qdrant_entries_created") or 0) != 0:
        raise ValueError("v1.3.7.3 baseline unexpectedly contains accepted facts or Qdrant entries")

    return {
        "technician": technician,
        "reference": reference,
        "service_areas": [x for x in (svc.get("areas") or []) if isinstance(x, dict)],
        "stocking": [x for x in (stock.get("items") or []) if isinstance(x, dict)],
        "manifest": manifest,
        "counts": {
            "source": expected_total,
            "technician": len(technician),
            "reference": len(reference),
        },
    }


def searchable_blob(g: Dict[str, Any], include_evidence: bool = True) -> str:
    parts = [group_label(g), str(g.get("concept_key") or ""), str(g.get("lane") or "")]
    parts.extend(str(x) for x in (g.get("v1_3_7_3_service_areas") or []))
    if include_evidence:
        parts.extend(str(r.get("raw_source_text") or "") for r in group_variants(g))
    return norm(" ".join(parts))


def recurrence_bonus(g: Dict[str, Any]) -> float:
    serials = int(g.get("distinct_serial_count") or len(group_serials(g)))
    logs = int(g.get("distinct_log_count") or len(group_logs(g)))
    return min(5.0, math.log2(1 + serials) * 0.8 + math.log2(1 + logs) * 0.35)


def score_group(g: Dict[str, Any], query: str, tokens: Set[str], phrases: Set[str]) -> Tuple[float, List[str]]:
    label = norm(group_label(g) + " " + str(g.get("concept_key") or ""))
    label_tokens = token_set(label)
    evidence = norm(" ".join(str(r.get("raw_source_text") or "") for r in group_variants(g)))
    evidence_tokens = token_set(evidence)
    service = norm(" ".join(str(x) for x in (g.get("v1_3_7_3_service_areas") or [])))
    service_tokens = token_set(service)
    score = 0.0
    reasons: List[str] = []
    qn = norm(query)

    if qn and qn in label:
        score += 28.0
        reasons.append("exact phrase in pattern label")
    elif qn and qn in evidence:
        score += 12.0
        reasons.append("exact phrase in Traveler evidence")

    phrase_label_hits = [p for p in phrases if p and p != qn and p in label]
    phrase_evidence_hits = [p for p in phrases if p and p != qn and p in evidence]
    if phrase_label_hits:
        score += min(18.0, 9.0 * len(phrase_label_hits))
        reasons.append("query alias in pattern label")
    if phrase_evidence_hits:
        score += min(8.0, 3.0 * len(phrase_evidence_hits))
        reasons.append("query alias in evidence")

    lt = tokens & label_tokens
    st = tokens & service_tokens
    et = tokens & evidence_tokens
    score += 7.5 * len(lt)
    score += 3.5 * len(st - lt)
    score += min(10.0, 1.6 * len(et - lt))
    if lt:
        reasons.append("label terms: " + ", ".join(sorted(lt)))
    elif st:
        reasons.append("service-area terms: " + ", ".join(sorted(st)))
    elif et:
        reasons.append("evidence terms: " + ", ".join(sorted(et)[:5]))

    if tokens and tokens <= (label_tokens | evidence_tokens | service_tokens):
        score += 5.0
        reasons.append("all query terms represented")

    score += recurrence_bonus(g)
    return score, reasons


def match_specific_id(groups: Sequence[Dict[str, Any]], serial: str | None, log: str | None) -> List[Dict[str, Any]]:
    out = []
    for g in groups:
        if serial and serial not in group_serials(g):
            continue
        if log and log not in group_logs(g):
            continue
        out.append(g)
    return sorted(out, key=lambda g: (-int(g.get("distinct_serial_count") or 0), -int(g.get("distinct_log_count") or 0), group_label(g).lower()))


def evidence_for_group(g: Dict[str, Any], query_tokens: Set[str], serial: str | None, log: str | None, limit: int) -> List[Dict[str, Any]]:
    rows = []
    for r in group_variants(g):
        rs = str(r.get("serial_number") or "")
        rl = str(r.get("log_number") or "")
        if serial and rs != serial:
            continue
        if log and rl != log:
            continue
        text = norm(r.get("raw_source_text"))
        hits = len(query_tokens & token_set(text))
        rows.append((hits, r))
    rows.sort(key=lambda x: (-x[0], str(x[1].get("log_number") or ""), str(x[1].get("candidate_id") or "")))
    # If a specific serial/log filter found nothing within this group, fall back to representative evidence only if no filter was requested.
    if (serial or log) and not rows:
        return []
    return [r for _, r in rows[:limit]]


def detect_mode(query: str, explicit: str) -> str:
    if explicit != "auto":
        return explicit
    q = norm(query)
    if SERIAL_RE.search(q):
        return "serial"
    if LOG_RE.search(q):
        return "log"
    if STOCK_INTENT.search(q):
        return "stocking"
    if SERVICE_INTENT.search(q):
        return "service-areas"
    return "search"


def union_coverage(groups: Sequence[Dict[str, Any]]) -> Tuple[int, int]:
    serials: Set[str] = set()
    logs: Set[str] = set()
    for g in groups:
        serials.update(group_serials(g))
        logs.update(group_logs(g))
    return len(serials), len(logs)


def render_group(g: Dict[str, Any], rank: int, score: float | None, reasons: Sequence[str], examples: Sequence[Dict[str, Any]]) -> List[str]:
    lane = str(g.get("lane") or "?")
    serials = int(g.get("distinct_serial_count") or len(group_serials(g)))
    logs = int(g.get("distinct_log_count") or len(group_logs(g)))
    line = f"{rank:>2}. {group_label(g)} | lane={lane} | serials={serials} | logs={logs}"
    if score is not None:
        line += f" | match={score:.1f}"
    out = [line]
    if reasons:
        out.append("    why: " + "; ".join(reasons[:3]))
    for r in examples:
        out.append(f"    {r.get('log_number') or '?'} | {r.get('serial_number') or '?'} | {compact_text(r.get('raw_source_text'))}")
    return out


def query_search(index: Dict[str, Any], query: str, aliases: Dict[str, List[str]], top: int, examples: int, include_reference: bool) -> Dict[str, Any]:
    tokens, phrases = expand_query(query, aliases)
    groups = list(index["technician"]) + (list(index["reference"]) if include_reference else [])
    scored = []
    for g in groups:
        score, reasons = score_group(g, query, tokens, phrases)
        if score > recurrence_bonus(g) + 0.2:  # require real query signal, not recurrence alone
            scored.append((score, g, reasons))
    scored.sort(key=lambda x: (-x[0], -int(x[1].get("distinct_serial_count") or 0), -int(x[1].get("distinct_log_count") or 0), group_label(x[1]).lower()))
    selected = scored[:max(1, top)]
    gs = [x[1] for x in selected]
    sc, lc = union_coverage(gs)
    return {
        "mode": "search",
        "query": query,
        "query_tokens": sorted(tokens),
        "match_count": len(scored),
        "top_union_serial_count": sc,
        "top_union_log_count": lc,
        "results": [
            {
                "score": round(score, 3),
                "reasons": reasons,
                "group": g,
                "examples": evidence_for_group(g, tokens, None, None, examples),
            }
            for score, g, reasons in selected
        ],
    }


def query_id(index: Dict[str, Any], query: str, mode: str, top: int, examples: int, include_reference: bool) -> Dict[str, Any]:
    groups = list(index["technician"]) + (list(index["reference"]) if include_reference else [])
    serial = SERIAL_RE.search(query)
    log = LOG_RE.search(query)
    serial_s = serial.group(1) if serial and mode == "serial" else None
    log_s = log.group(1) if log and mode == "log" else None
    matched = match_specific_id(groups, serial_s, log_s)
    selected = matched[:max(1, top)]
    return {
        "mode": mode,
        "query": query,
        "serial_number": serial_s,
        "log_number": log_s,
        "match_count": len(matched),
        "results": [
            {"group": g, "examples": evidence_for_group(g, set(), serial_s, log_s, examples)}
            for g in selected
        ],
    }


def query_stocking(index: Dict[str, Any], query: str, top: int) -> Dict[str, Any]:
    return {"mode": "stocking", "query": query, "items": index["stocking"][:max(1, top)]}


def query_service_areas(index: Dict[str, Any], query: str, top: int) -> Dict[str, Any]:
    return {"mode": "service-areas", "query": query, "areas": index["service_areas"][:max(1, top)]}


def execute_query(index: Dict[str, Any], query: str, aliases: Dict[str, List[str]], mode: str, top: int, examples: int, include_reference: bool) -> Dict[str, Any]:
    resolved = detect_mode(query, mode)
    if resolved in ("serial", "log"):
        return query_id(index, query, resolved, top, examples, include_reference)
    if resolved == "stocking":
        return query_stocking(index, query, top)
    if resolved == "service-areas":
        return query_service_areas(index, query, top)
    return query_search(index, query, aliases, top, examples, include_reference)


def render_result(result: Dict[str, Any]) -> str:
    mode = result["mode"]
    lines = [f"# Nova DRL GB8 Technician Query v{VERSION}", f"Query: {result.get('query')}", f"Mode: {mode}", "Status: FAST PROVISIONAL 80/20 — evidence remains authoritative", ""]

    if mode == "stocking":
        lines += ["PROVISIONAL PARTS / STOCKING ATTENTION", "--------------------------------------", "Repeated families worth technician/parts-manager attention; not an approved BOM."]
        for i, row in enumerate(result.get("items") or [], 1):
            name = row.get("item") or row.get("item_family") or row.get("name") or row.get("label") or "item"
            serials = row.get("distinct_serial_coverage", row.get("distinct_serial_count", row.get("serial_count", len(row.get("serials") or []))))
            logs = row.get("distinct_log_coverage", row.get("distinct_log_count", row.get("log_count", len(row.get("logs") or []))))
            groups = row.get("recurring_group_count", row.get("group_count", len(row.get("group_ids") or [])))
            lines.append(f"{i:>2}. {name} | serials={serials} | logs={logs} | groups={groups}")
            examples = row.get("example_pattern_labels") or row.get("example_group_labels") or row.get("examples") or []
            if examples:
                lines.append("    patterns: " + "; ".join(str(x) for x in examples[:4]))
        return "\n".join(lines) + "\n"

    if mode == "service-areas":
        lines += ["GB8 TECHNICIAN SERVICE AREAS", "----------------------------"]
        for i, row in enumerate(result.get("areas") or [], 1):
            name = row.get("service_area") or "area"
            serials = row.get("distinct_serial_coverage", row.get("distinct_serial_count", len(row.get("serials") or [])))
            logs = row.get("distinct_log_coverage", row.get("distinct_log_count", len(row.get("logs") or [])))
            groups = row.get("recurring_group_count", row.get("group_count", len(row.get("group_ids") or [])))
            lines.append(f"{i:>2}. {name} | serials={serials} | logs={logs} | recurring_groups={groups}")
            top_groups = row.get("top_patterns") or row.get("top_groups") or []
            for g in top_groups[:3]:
                if isinstance(g, dict):
                    lines.append(f"    - {g.get('concept_label') or g.get('label')} ({g.get('distinct_serial_count') or '?'} serials / {g.get('distinct_log_count') or '?'} logs)")
        return "\n".join(lines) + "\n"

    results = result.get("results") or []
    if mode == "search":
        lines.append(f"Matching recurring groups: {result.get('match_count', 0)}")
        lines.append(f"Coverage among top results: {result.get('top_union_serial_count', 0)} serials / {result.get('top_union_log_count', 0)} logs")
    else:
        identifier = result.get("serial_number") or result.get("log_number")
        lines.append(f"Matching recurring groups for {identifier}: {result.get('match_count', 0)}")
    lines.append("")

    if not results:
        lines.append("No recurring pattern matched this query in the selected scope.")
        lines.append("Try a shorter term, --include-reference, or a specific log/serial number.")
        return "\n".join(lines) + "\n"

    lines += ["TOP MATCHES", "-----------"]
    for i, row in enumerate(results, 1):
        g = row["group"]
        lines.extend(render_group(g, i, row.get("score"), row.get("reasons") or [], row.get("examples") or []))
        lines.append("")
    lines.append("Policy: read-only query; new LLM calls=0; accepted facts=0; Qdrant=OFF")
    return "\n".join(lines) + "\n"


def interactive_loop(index: Dict[str, Any], aliases: Dict[str, List[str]], args: argparse.Namespace) -> int:
    print(f"Nova DRL GB8 Technician Query Engine v{VERSION}")
    print(f"Loaded {index['counts']['technician']} technician groups + {index['counts']['reference']} reference groups ({index['counts']['source']} total).")
    print("Type a GB8 question. Commands: :help, :reference on|off, :quit")
    include_reference = bool(args.include_reference)
    while True:
        try:
            q = input("nova-gb8> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            continue
        if q in {":quit", ":q", "quit", "exit"}:
            return 0
        if q == ":help":
            print("Examples: Y axis drifting | vacuum leak | what normally gets replaced | common service areas | serial 80050608 | log 130130006")
            continue
        if q.startswith(":reference"):
            parts = q.split()
            if len(parts) == 2 and parts[1].lower() in {"on", "off"}:
                include_reference = parts[1].lower() == "on"
                print(f"reference search: {'ON' if include_reference else 'OFF'}")
            else:
                print(f"reference search: {'ON' if include_reference else 'OFF'}")
            continue
        result = execute_query(index, q, aliases, "auto", args.top, args.examples, include_reference)
        print(render_result(result))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nova DRL GB8 Technician Query Engine v1.3.8.0 — read-only Python query layer")
    parser.add_argument("query", nargs="*", help="Technician question, e.g. 'Y axis drifting'")
    parser.add_argument("--input-root", default=str(DEFAULT_INPUT_ROOT), help="Frozen v1.3.7.3 technician-signal output root")
    parser.add_argument("--aliases", default="", help="Optional query alias JSON; defaults to config when present")
    parser.add_argument("--mode", choices=["auto", "search", "stocking", "service-areas", "serial", "log"], default="auto")
    parser.add_argument("--top", type=int, default=8, help="Maximum result groups/items")
    parser.add_argument("--examples", type=int, default=3, help="Traveler evidence examples per group")
    parser.add_argument("--include-reference", action="store_true", help="Also search the 105 reference/admin groups")
    parser.add_argument("--interactive", action="store_true", help="Start repeated-query technician console")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON to stdout")
    parser.add_argument("--self-check", action="store_true", help="Validate v1.3.7.3 input accounting and exit")
    args = parser.parse_args(argv)

    input_root = Path(args.input_root)
    index = load_index(input_root)

    alias_path: Path | None = None
    if args.aliases:
        alias_path = Path(args.aliases)
    else:
        candidate = Path(__file__).resolve().parents[1] / "config" / "gb8_query_aliases_v1_3_8_0.json"
        if candidate.exists():
            alias_path = candidate
    aliases = load_aliases(alias_path)

    if args.self_check:
        print(f"PASS: v{VERSION} input self-check | source={index['counts']['source']} technician={index['counts']['technician']} reference={index['counts']['reference']} | accepted facts=0 | Qdrant=OFF")
        return 0

    if args.interactive or not args.query:
        return interactive_loop(index, aliases, args)

    query = " ".join(args.query).strip()
    result = execute_query(index, query, aliases, args.mode, max(1, args.top), max(1, args.examples), bool(args.include_reference))
    if args.json:
        # Keep output compact enough for downstream tooling while preserving matched group provenance.
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_result(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
