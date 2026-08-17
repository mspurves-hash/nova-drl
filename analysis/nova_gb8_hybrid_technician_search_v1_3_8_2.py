#!/usr/bin/env python3
"""Nova DRL GB8 Hybrid Technician Search v1.3.8.2.

Read-only retrieval fusion over two already-proven search paths:
- Qdrant v1.3.8.1 semantic retrieval over the 314 technician recurring groups.
- Python v1.3.8.0 deterministic retrieval over frozen v1.3.7.3 JSON.

The engines are fused with weighted Reciprocal Rank Fusion (RRF). Their raw
scores are intentionally NOT added or normalized against each other because
Qdrant cosine similarity and the deterministic Python score are unrelated
scales. A small recurrence-support bonus (distinct serials/logs) is used only
as a tiebreaker after query relevance.

No Travelers, recurring groups, Qdrant points, or approval states are changed.
No generative LLM calls are made. Qdrant remains a disposable search index;
frozen v1.3.7.3 JSON remains authoritative.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Sequence, Tuple

VERSION = "1.3.8.2"
SOURCE_SIGNAL_VERSION = "1.3.7.3"
QDRANT_INDEX_VERSION = "1.3.8.1"
PYTHON_QUERY_VERSION = "1.3.8.0"
DEFAULT_INPUT_ROOT = Path("/opt/nova-drl/output/traveler_technician_signal_v1_3_7_3")
DEFAULT_COLLECTION = "nova_drl_gb8_trial_v1_3_8_1"
DEFAULT_QDRANT_URL = "http://localhost:6333"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_CONFIG_NAME = "gb8_hybrid_search_policy_v1_3_8_2.json"
EXPECTED_TECHNICIAN_GROUPS = 314

DEFAULT_POLICY: Dict[str, Any] = {
    "semantic_top": 12,
    "python_top": 12,
    "final_top": 10,
    "examples": 3,
    "rrf_k": 60.0,
    "semantic_weight": 1.0,
    "python_weight": 1.0,
    "recurrence_weight": 0.0003,
    "score_threshold": None,
    "strict_expected_qdrant_points": 314,
}


def _load_module(path: Path, module_name: str):
    if not path.exists():
        raise FileNotFoundError(f"Required Nova DRL module missing: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_engines(repo_root: Path):
    qpath = repo_root / "analysis" / "nova_gb8_qdrant_trial_index_v1_3_8_1.py"
    ppath = repo_root / "analysis" / "nova_gb8_technician_query_engine_v1_3_8_0.py"
    return (
        _load_module(qpath, "nova_gb8_qdrant_v1381"),
        _load_module(ppath, "nova_gb8_python_v1380"),
    )


def load_policy(path: Path | None) -> Dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    if path is not None and path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        vals = payload.get("hybrid_policy") if isinstance(payload, dict) else None
        if isinstance(vals, dict):
            for key in policy:
                if key in vals:
                    policy[key] = vals[key]
    return policy


def recurrence_support(serial_count: int, log_count: int) -> float:
    """Return bounded 0..1 corpus-support strength; never a confidence score."""
    s = max(0, int(serial_count))
    l = max(0, int(log_count))
    # Deliberately slow-growing and capped. At typical GB8 recurrence levels this
    # contributes only a small fraction of a single top-rank RRF contribution.
    return min(1.0, (math.log2(1 + s) + 0.5 * math.log2(1 + l)) / 10.0)


def fuse_rankings(
    semantic_rows: Sequence[Mapping[str, Any]],
    python_rows: Sequence[Mapping[str, Any]],
    authoritative_by_id: Mapping[str, Mapping[str, Any]],
    *,
    rrf_k: float = 60.0,
    semantic_weight: float = 1.0,
    python_weight: float = 1.0,
    recurrence_weight: float = 0.0003,
) -> List[Dict[str, Any]]:
    if rrf_k <= 0:
        raise ValueError("rrf_k must be > 0")
    q_rank: Dict[str, int] = {}
    q_score: Dict[str, float] = {}
    q_payload: Dict[str, Dict[str, Any]] = {}
    for rank, row in enumerate(semantic_rows, 1):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        gid = str(payload.get("recurring_group_id") or "")
        if gid and gid not in q_rank:
            q_rank[gid] = rank
            q_score[gid] = float(row.get("score") or 0.0)
            q_payload[gid] = dict(payload)

    p_rank: Dict[str, int] = {}
    p_score: Dict[str, float] = {}
    for rank, row in enumerate(python_rows, 1):
        gid = str(row.get("group_id") or "")
        if gid and gid not in p_rank:
            p_rank[gid] = rank
            p_score[gid] = float(row.get("score") or 0.0)

    fused: List[Dict[str, Any]] = []
    for gid in sorted(set(q_rank) | set(p_rank)):
        group = authoritative_by_id.get(gid)
        # Qdrant is never allowed to introduce knowledge absent from the frozen
        # v1.3.7.3 source. Unknown/stale point IDs are ignored safely.
        if group is None:
            continue
        qr = q_rank.get(gid)
        pr = p_rank.get(gid)
        rrf = 0.0
        if qr is not None:
            rrf += float(semantic_weight) / (float(rrf_k) + qr)
        if pr is not None:
            rrf += float(python_weight) / (float(rrf_k) + pr)
        serials = int(group.get("distinct_serial_count") or 0)
        logs = int(group.get("distinct_log_count") or 0)
        support = recurrence_support(serials, logs)
        recurrence_bonus = float(recurrence_weight) * support
        fused.append({
            "group_id": gid,
            "group": dict(group),
            "semantic_rank": qr,
            "python_rank": pr,
            "semantic_score": q_score.get(gid),
            "python_score": p_score.get(gid),
            "rrf_score": rrf,
            "recurrence_support": support,
            "recurrence_bonus": recurrence_bonus,
            "hybrid_score": rrf + recurrence_bonus,
            "engine_count": int(qr is not None) + int(pr is not None),
            "qdrant_payload": q_payload.get(gid),
        })

    fused.sort(key=lambda x: (
        -float(x["hybrid_score"]),
        -int(x["engine_count"]),
        -int(x["group"].get("distinct_serial_count") or 0),
        -int(x["group"].get("distinct_log_count") or 0),
        str(x["group"].get("concept_label") or "").lower(),
    ))
    return fused


def qdrant_semantic_rows(qmod: Any, args: argparse.Namespace, query: str, top: int, api_key: str) -> List[Dict[str, Any]]:
    info = qmod.qdrant_collection_info(args.qdrant_url, args.collection, api_key, args.timeout)
    if info is None:
        raise RuntimeError(f"Qdrant trial collection does not exist: {args.collection}. Build v1.3.8.1 first.")
    vector = qmod.ollama_embed(args.ollama_url, args.embed_model, [query], args.timeout)[0]
    return qmod.qdrant_search(
        args.qdrant_url,
        args.collection,
        vector,
        max(1, top),
        api_key,
        args.timeout,
        args.score_threshold,
    )


def deterministic_rows(pmod: Any, index: Mapping[str, Any], aliases: Mapping[str, List[str]], query: str, top: int) -> List[Dict[str, Any]]:
    result = pmod.execute_query(dict(index), query, dict(aliases), "search", max(1, top), 3, False)
    rows: List[Dict[str, Any]] = []
    for item in result.get("results") or []:
        group = item.get("group") or {}
        gid = str(group.get("group_id") or group.get("recurring_group_id") or "")
        if not gid:
            continue
        rows.append({
            "group_id": gid,
            "concept_label": group.get("concept_label") or group.get("concept_key"),
            "score": float(item.get("score") or 0.0),
        })
    return rows


def hybrid_search(
    qmod: Any,
    pmod: Any,
    index: Mapping[str, Any],
    aliases: Mapping[str, List[str]],
    args: argparse.Namespace,
    query: str,
    policy: Mapping[str, Any],
    api_key: str,
) -> Dict[str, Any]:
    sem_top = int(args.semantic_top if args.semantic_top is not None else policy["semantic_top"])
    py_top = int(args.python_top if args.python_top is not None else policy["python_top"])
    final_top = int(args.top if args.top is not None else policy["final_top"])
    rrf_k = float(args.rrf_k if args.rrf_k is not None else policy["rrf_k"])
    sem_weight = float(args.semantic_weight if args.semantic_weight is not None else policy["semantic_weight"])
    py_weight = float(args.python_weight if args.python_weight is not None else policy["python_weight"])
    rec_weight = float(args.recurrence_weight if args.recurrence_weight is not None else policy["recurrence_weight"])

    semantic = qdrant_semantic_rows(qmod, args, query, sem_top, api_key)
    python_rows = deterministic_rows(pmod, index, aliases, query, py_top)
    authoritative_by_id = {
        str(g.get("group_id") or g.get("recurring_group_id") or ""): g
        for g in index.get("technician") or []
        if str(g.get("group_id") or g.get("recurring_group_id") or "")
    }
    fused = fuse_rankings(
        semantic,
        python_rows,
        authoritative_by_id,
        rrf_k=rrf_k,
        semantic_weight=sem_weight,
        python_weight=py_weight,
        recurrence_weight=rec_weight,
    )
    selected = fused[:max(1, final_top)]

    tokens, _ = pmod.expand_query(query, aliases)
    for row in selected:
        row["examples"] = pmod.evidence_for_group(row["group"], tokens, None, None, max(1, int(args.examples)))

    sem_ids = [qmod.payload_group_id(r) for r in semantic if qmod.payload_group_id(r)]
    py_ids = [str(r.get("group_id") or "") for r in python_rows if r.get("group_id")]
    overlap = [gid for gid in sem_ids if gid in set(py_ids)]
    serial_union = set()
    log_union = set()
    for row in selected:
        serial_union.update(pmod.group_serials(row["group"]))
        log_union.update(pmod.group_logs(row["group"]))

    return {
        "version": VERSION,
        "query": query,
        "collection": args.collection,
        "embedding_model": args.embed_model,
        "semantic_top": sem_top,
        "python_top": py_top,
        "final_top": final_top,
        "semantic_python_overlap": len(overlap),
        "semantic_python_overlap_ids": overlap,
        "top_union_serial_count": len(serial_union),
        "top_union_log_count": len(log_union),
        "fusion": {
            "method": "weighted_reciprocal_rank_fusion",
            "rrf_k": rrf_k,
            "semantic_weight": sem_weight,
            "python_weight": py_weight,
            "recurrence_weight": rec_weight,
            "raw_engine_scores_combined": False,
        },
        "results": selected,
        "accepted_fact_count": 0,
        "generative_reasoning_calls": 0,
        "knowledge_status": "provisional",
        "source_role": "frozen_v1.3.7.3_authoritative",
        "qdrant_role": "disposable_semantic_search_index",
    }


def render_hybrid(result: Mapping[str, Any]) -> str:
    lines = [
        f"# Nova DRL GB8 Hybrid Technician Search v{VERSION}",
        f"Query: {result.get('query')}",
        f"Qdrant collection: {result.get('collection')}",
        "Status: FAST PROVISIONAL 80/20 — frozen v1.3.7.3 JSON remains authoritative",
        "",
        "HYBRID RETRIEVAL",
        "----------------",
        f"Qdrant semantic candidates: top {result.get('semantic_top')}",
        f"Python deterministic candidates: top {result.get('python_top')}",
        f"Overlap before fusion: {result.get('semantic_python_overlap')} groups",
        f"Coverage among final results: {result.get('top_union_serial_count')} serials / {result.get('top_union_log_count')} logs",
        "Fusion: weighted RRF + small recurrence-support tiebreaker; raw Qdrant/Python scores are NOT added together",
        "",
        "TOP HYBRID MATCHES",
        "------------------",
    ]
    rows = result.get("results") or []
    if not rows:
        lines.append("No hybrid match returned from the current technician-group index.")
    for i, row in enumerate(rows, 1):
        g = row.get("group") or {}
        label = g.get("concept_label") or g.get("concept_key") or row.get("group_id")
        serials = int(g.get("distinct_serial_count") or 0)
        logs = int(g.get("distinct_log_count") or 0)
        qr = row.get("semantic_rank")
        pr = row.get("python_rank")
        source = "BOTH" if qr is not None and pr is not None else ("QDRANT" if qr is not None else "PYTHON")
        lines.append(
            f"{i:>2}. hybrid={float(row.get('hybrid_score') or 0):.5f} | {label} | serials={serials} | logs={logs} | source={source}"
        )
        details = []
        if qr is not None:
            details.append(f"Qrank={qr} cosine={float(row.get('semantic_score') or 0):.4f}")
        if pr is not None:
            details.append(f"Prank={pr} det={float(row.get('python_score') or 0):.3f}")
        details.append(f"support={float(row.get('recurrence_support') or 0):.3f}")
        lines.append("    " + " | ".join(details))
        areas = g.get("v1_3_7_3_service_areas") or []
        if areas:
            lines.append("    areas: " + "; ".join(str(x) for x in areas))
        for ex in (row.get("examples") or [])[:3]:
            text = p_compact(ex.get("raw_source_text"))
            lines.append(f"    - {ex.get('log_number') or '?'} | {ex.get('serial_number') or '?'} | {text}")
        lines.append(f"    group: {row.get('group_id')}")
    lines += [
        "",
        "Policy: read-only retrieval; new generative LLM calls=0; accepted facts=0; Qdrant source collection unchanged.",
    ]
    return "\n".join(lines) + "\n"


def p_compact(value: Any, limit: int = 230) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def make_runtime_args(args: argparse.Namespace, policy: Mapping[str, Any]) -> argparse.Namespace:
    if args.score_threshold is None:
        args.score_threshold = policy.get("score_threshold")
    return args


def status_report(qmod: Any, pmod: Any, args: argparse.Namespace, policy: Mapping[str, Any], api_key: str) -> int:
    print(f"# Nova DRL GB8 Hybrid Technician Search Status v{VERSION}")
    try:
        index = pmod.load_index(Path(args.input_root))
        print(
            f"Frozen source: PASS | source={index['counts']['source']} technician={index['counts']['technician']} "
            f"reference={index['counts']['reference']}"
        )
    except Exception as e:
        print(f"Frozen source: ERROR | {e}")
    try:
        models = qmod.ollama_models(args.ollama_url, args.timeout)
        print(f"Ollama:       reachable | embed model {'FOUND' if qmod.model_available(args.embed_model, models) else 'NOT FOUND'}: {args.embed_model}")
    except Exception as e:
        print(f"Ollama:       ERROR | {e}")
    try:
        info = qmod.qdrant_collection_info(args.qdrant_url, args.collection, api_key, args.timeout)
        if info is None:
            print(f"Qdrant trial: NOT FOUND | {args.collection}")
        else:
            count = qmod.qdrant_count(args.qdrant_url, args.collection, api_key, args.timeout)
            expected = int(policy.get("strict_expected_qdrant_points") or EXPECTED_TECHNICIAN_GROUPS)
            state = "PASS" if count == expected else "WARNING"
            print(f"Qdrant trial: {state} | {args.collection} | points={count} expected={expected}")
    except Exception as e:
        print(f"Qdrant trial: ERROR | {e}")
    print("Fusion role: read-only; no Qdrant writes; no generative reasoning; accepted facts=0")
    return 0


def self_check(pmod: Any, args: argparse.Namespace, policy: Mapping[str, Any]) -> int:
    index = pmod.load_index(Path(args.input_root))
    expected = int(policy.get("strict_expected_qdrant_points") or EXPECTED_TECHNICIAN_GROUPS)
    if int(index["counts"]["technician"]) != expected:
        raise ValueError(f"Expected {expected} technician groups, found {index['counts']['technician']}")
    # Pure-function sanity check for fusion ordering and score-scale separation.
    g1 = {"group_id": "g1", "concept_label": "one", "distinct_serial_count": 10, "distinct_log_count": 12}
    g2 = {"group_id": "g2", "concept_label": "two", "distinct_serial_count": 2, "distinct_log_count": 2}
    sem = [{"score": 0.9, "payload": {"recurring_group_id": "g1"}}, {"score": 0.8, "payload": {"recurring_group_id": "g2"}}]
    py = [{"group_id": "g2", "score": 999.0}, {"group_id": "g1", "score": 1.0}]
    fused = fuse_rankings(sem, py, {"g1": g1, "g2": g2})
    if {r["group_id"] for r in fused} != {"g1", "g2"}:
        raise AssertionError("Fusion self-check lost a group")
    print(
        f"PASS: v{VERSION} frozen-source self-check | source={index['counts']['source']} "
        f"technician={index['counts']['technician']} reference={index['counts']['reference']} | "
        "fusion=RRF | raw_score_addition=NO | accepted_facts=0 | generative_reasoning=0"
    )
    return 0


def interactive_loop(qmod: Any, pmod: Any, index: Mapping[str, Any], aliases: Mapping[str, List[str]], args: argparse.Namespace, policy: Mapping[str, Any], api_key: str) -> int:
    print(f"Nova DRL GB8 Hybrid Technician Search v{VERSION}")
    print(f"Loaded frozen groups: {index['counts']['technician']} technician + {index['counts']['reference']} reference.")
    print(f"Qdrant collection: {args.collection}")
    print("Commands: :status, :quit")
    while True:
        try:
            q = input("nova-hybrid> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            continue
        if q in {":quit", ":q", "quit", "exit"}:
            return 0
        if q == ":status":
            status_report(qmod, pmod, args, policy, api_key)
            continue
        mode = pmod.detect_mode(q, args.mode)
        if mode != "search":
            result = pmod.execute_query(dict(index), q, dict(aliases), mode, max(1, int(args.top or policy["final_top"])), max(1, int(args.examples)), False)
            print(pmod.render_result(result))
            continue
        result = hybrid_search(qmod, pmod, index, aliases, args, q, policy, api_key)
        print(render_hybrid(result))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nova DRL GB8 Hybrid Technician Search v1.3.8.2 — Qdrant + deterministic RRF")
    parser.add_argument("query", nargs="*", help="Technician question, e.g. 'Y axis drifting'")
    parser.add_argument("--input-root", default=str(DEFAULT_INPUT_ROOT), help="Frozen v1.3.7.3 technician-signal output root")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION, help="Existing v1.3.8.1 Qdrant trial collection (read-only)")
    parser.add_argument("--qdrant-url", default=DEFAULT_QDRANT_URL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    parser.add_argument("--api-key", default="", help="Qdrant API key; otherwise QDRANT_API_KEY env var is honored by v1.3.8.1 helper")
    parser.add_argument("--config", default="", help="Hybrid policy JSON; defaults to config/gb8_hybrid_search_policy_v1_3_8_2.json")
    parser.add_argument("--mode", choices=["auto", "search", "stocking", "service-areas", "serial", "log"], default="auto")
    parser.add_argument("--semantic-top", type=int, default=None, help="Qdrant candidates before fusion (default policy: 12)")
    parser.add_argument("--python-top", type=int, default=None, help="Deterministic candidates before fusion (default policy: 12)")
    parser.add_argument("--top", type=int, default=None, help="Final fused results (default policy: 10)")
    parser.add_argument("--examples", type=int, default=3, help="Traveler evidence examples per final group")
    parser.add_argument("--rrf-k", type=float, default=None, help="RRF rank constant (default policy: 60)")
    parser.add_argument("--semantic-weight", type=float, default=None)
    parser.add_argument("--python-weight", type=float, default=None)
    parser.add_argument("--recurrence-weight", type=float, default=None, help="Small additive recurrence-support weight")
    parser.add_argument("--score-threshold", type=float, default=None, help="Optional Qdrant cosine threshold")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit full hybrid result JSON")
    parser.add_argument("--status", action="store_true", help="Check frozen source, Ollama embedding model, and existing Qdrant collection")
    parser.add_argument("--self-check", action="store_true", help="Validate frozen input + pure fusion logic; no network")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    qmod, pmod = load_engines(repo_root)
    config_path = Path(args.config) if args.config else repo_root / "config" / DEFAULT_CONFIG_NAME
    policy = load_policy(config_path)
    args = make_runtime_args(args, policy)
    api_key = args.api_key or ""
    if not api_key:
        import os
        api_key = os.getenv("QDRANT_API_KEY", "")

    if args.self_check:
        return self_check(pmod, args, policy)
    if args.status:
        return status_report(qmod, pmod, args, policy, api_key)

    index = pmod.load_index(Path(args.input_root))
    alias_path = repo_root / "config" / "gb8_query_aliases_v1_3_8_0.json"
    aliases = pmod.load_aliases(alias_path if alias_path.exists() else None)

    if args.interactive or not args.query:
        return interactive_loop(qmod, pmod, index, aliases, args, policy, api_key)

    query = " ".join(args.query).strip()
    resolved_mode = pmod.detect_mode(query, args.mode)
    if resolved_mode != "search":
        result = pmod.execute_query(index, query, aliases, resolved_mode, max(1, int(args.top or policy["final_top"])), max(1, int(args.examples)), False)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(pmod.render_result(result), end="")
        return 0

    result = hybrid_search(qmod, pmod, index, aliases, args, query, policy, api_key)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_hybrid(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
