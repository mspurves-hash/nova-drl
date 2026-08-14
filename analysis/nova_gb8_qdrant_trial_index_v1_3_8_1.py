#!/usr/bin/env python3
"""Nova DRL GB8 Qdrant Trial Index v1.3.8.1.

Disposable semantic-search index over the frozen v1.3.7.3 technician signal.

Design principles:
- one Qdrant point per v1.3.7.3 technician recurring group;
- v1.3.7.3 JSON remains authoritative and read-only;
- Qdrant is a disposable/versioned search index, never a knowledge authority;
- no generative reasoning calls and no automatic fact approval;
- embeddings use local Ollama nomic-embed-text by default;
- --rebuild may delete/recreate only a guarded Nova DRL trial collection;
- --compare shows Qdrant semantic results beside deterministic v1.3.8.0 search.

The first GB8 trial intentionally indexes technician groups only (314 in the frozen
baseline), not all 8,621 raw candidates and not the 105 reference/admin groups.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

VERSION = "1.3.8.1"
SOURCE_SIGNAL_VERSION = "1.3.7.3"
PYTHON_QUERY_VERSION = "1.3.8.0"
DEFAULT_INPUT_ROOT = Path("/opt/nova-drl/output/traveler_technician_signal_v1_3_7_3")
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/gb8_qdrant_trial_v1_3_8_1")
DEFAULT_COLLECTION = "nova_drl_gb8_trial_v1_3_8_1"
DEFAULT_QDRANT_URL = "http://localhost:6333"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_BATCH_SIZE = 24
DEFAULT_EVIDENCE_EXAMPLES = 8
DEFAULT_MAX_EMBED_CHARS = 6000
DEFAULT_TOP = 8
EXPECTED_SOURCE_GROUPS = 419
EXPECTED_TECHNICIAN_GROUPS = 314
EXPECTED_REFERENCE_GROUPS = 105
TRIAL_COLLECTION_PREFIX = "nova_drl_gb8_trial_"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_sha256(payload: Any) -> str:
    return sha256_bytes(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def normalized_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def group_id(group: Mapping[str, Any]) -> str:
    return str(group.get("group_id") or group.get("recurring_group_id") or "")


def group_label(group: Mapping[str, Any]) -> str:
    return normalized_ws(group.get("concept_label") or group.get("concept_key") or group_id(group) or "unlabeled")


def group_variants(group: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [dict(x) for x in (group.get("raw_variants") or []) if isinstance(x, dict)]


def group_logs(group: Mapping[str, Any]) -> List[str]:
    vals = {str(x) for x in (group.get("logs") or []) if str(x)}
    if not vals:
        vals = {str(r.get("log_number")) for r in group_variants(group) if r.get("log_number")}
    return sorted(vals)


def group_serials(group: Mapping[str, Any]) -> List[str]:
    vals = {str(x) for x in (group.get("serial_numbers") or []) if str(x) and str(x) != "?"}
    if not vals:
        vals = {
            str(r.get("serial_number")) for r in group_variants(group)
            if r.get("serial_number") and str(r.get("serial_number")) != "?"
        }
    return sorted(vals)


def group_candidate_ids(group: Mapping[str, Any]) -> List[str]:
    vals = {str(x) for x in (group.get("member_candidate_ids") or []) if str(x)}
    vals.update(str(r.get("candidate_id")) for r in group_variants(group) if r.get("candidate_id"))
    return sorted(vals)


def group_source_hashes(group: Mapping[str, Any]) -> List[str]:
    vals = {str(x) for x in (group.get("source_hashes") or []) if str(x)}
    vals.update(str(r.get("source_sha256")) for r in group_variants(group) if r.get("source_sha256"))
    return sorted(vals)


def evidence_examples(group: Mapping[str, Any], limit: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[Tuple[str, str]] = set()
    for r in group_variants(group):
        text = normalized_ws(r.get("raw_source_text"))
        if not text:
            continue
        key = (str(r.get("log_number") or ""), text.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "log_number": r.get("log_number"),
            "serial_number": r.get("serial_number"),
            "candidate_id": r.get("candidate_id"),
            "source_sha256": r.get("source_sha256"),
            "source_path": r.get("source_path"),
            "raw_transcription_path": r.get("raw_transcription_path"),
            "raw_source_text": r.get("raw_source_text"),
        })
        if len(out) >= max(1, limit):
            break
    return out


def build_embedding_text(group: Mapping[str, Any], evidence_limit: int = DEFAULT_EVIDENCE_EXAMPLES, max_chars: int = DEFAULT_MAX_EMBED_CHARS) -> str:
    label = group_label(group)
    lane = normalized_ws(group.get("lane"))
    service = [normalized_ws(x) for x in (group.get("v1_3_7_3_service_areas") or []) if normalized_ws(x)]
    serial_count = int(group.get("distinct_serial_count") or len(group_serials(group)))
    log_count = int(group.get("distinct_log_count") or len(group_logs(group)))
    candidate_count = int(group.get("candidate_count") or len(group_candidate_ids(group)))
    lines = [
        "Nova DRL GB8 historical technician pattern.",
        f"Concept: {label}",
        f"Lane: {lane or 'unknown'}",
        f"Service areas: {', '.join(service) if service else 'unspecified'}",
        f"Recurrence: {serial_count} distinct robot serials; {log_count} distinct repair logs; {candidate_count} evidence candidates.",
        "Representative Traveler evidence:",
    ]
    for ex in evidence_examples(group, evidence_limit):
        prefix = " / ".join(x for x in [str(ex.get("log_number") or "?"), str(ex.get("serial_number") or "?")] if x)
        lines.append(f"- {prefix}: {normalized_ws(ex.get('raw_source_text'))}")
    text = "\n".join(lines).strip()
    if len(text) > max_chars:
        text = text[: max(0, max_chars - 1)] + "…"
    return text


def deterministic_point_id(recurring_group_id: str) -> str:
    if not recurring_group_id:
        raise ValueError("Recurring group has no group_id")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"nova-drl:{VERSION}:{recurring_group_id}"))


def build_point_payload(group: Mapping[str, Any], embedding_text: str, evidence_limit: int = DEFAULT_EVIDENCE_EXAMPLES) -> Dict[str, Any]:
    gid = group_id(group)
    return {
        "equipment_family": "GB8/GB8-MT",
        "oem": "GENMARK",
        "index_version": VERSION,
        "source_signal_version": SOURCE_SIGNAL_VERSION,
        "recurring_group_id": gid,
        "concept_label": group_label(group),
        "concept_key": group.get("concept_key"),
        "lane": group.get("lane"),
        "service_areas": list(group.get("v1_3_7_3_service_areas") or []),
        "distinct_serial_count": int(group.get("distinct_serial_count") or len(group_serials(group))),
        "distinct_log_count": int(group.get("distinct_log_count") or len(group_logs(group))),
        "candidate_count": int(group.get("candidate_count") or len(group_candidate_ids(group))),
        "serial_numbers": group_serials(group),
        "log_numbers": group_logs(group),
        "candidate_ids": group_candidate_ids(group),
        "source_hashes": group_source_hashes(group),
        "evidence_examples": evidence_examples(group, evidence_limit),
        "group_sha256": canonical_sha256(group),
        "embedding_text_sha256": sha256_bytes(embedding_text.encode("utf-8")),
        "embedding_text_excerpt": embedding_text[:1200],
        "knowledge_status": "provisional",
        "approved": False,
        "human_approved": False,
        "qdrant_role": "disposable_semantic_search_index",
        "automatic_fact_acceptance": False,
    }


def prepare_records(groups: Sequence[Mapping[str, Any]], evidence_limit: int, max_embed_chars: int) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for group in groups:
        gid = group_id(group)
        if not gid:
            raise ValueError("Technician group missing group_id")
        if gid in seen_ids:
            raise ValueError(f"Duplicate recurring group id: {gid}")
        seen_ids.add(gid)
        text = build_embedding_text(group, evidence_limit=evidence_limit, max_chars=max_embed_chars)
        records.append({
            "point_id": deterministic_point_id(gid),
            "group_id": gid,
            "embedding_text": text,
            "payload": build_point_payload(group, text, evidence_limit=evidence_limit),
        })
    return records


def load_frozen_baseline(input_root: Path, strict_counts: bool = True) -> Dict[str, Any]:
    tech_path = input_root / "technician_patterns_v1_3_7_3.json"
    ref_path = input_root / "reference_patterns_v1_3_7_3.json"
    manifest_path = input_root / "technician_signal_manifest_v1_3_7_3.json"
    for p in (tech_path, ref_path, manifest_path):
        if not p.exists():
            raise FileNotFoundError(f"Required frozen v1.3.7.3 input missing: {p}")
    tech = load_json(tech_path)
    ref = load_json(ref_path)
    manifest = load_json(manifest_path)
    if str(tech.get("signal_cleaner_version")) != SOURCE_SIGNAL_VERSION:
        raise ValueError(f"Expected source signal {SOURCE_SIGNAL_VERSION}, got {tech.get('signal_cleaner_version')}")
    if str(ref.get("signal_cleaner_version")) != SOURCE_SIGNAL_VERSION:
        raise ValueError(f"Expected reference signal {SOURCE_SIGNAL_VERSION}, got {ref.get('signal_cleaner_version')}")
    groups = [g for g in (tech.get("groups") or []) if isinstance(g, dict)]
    ref_groups = [g for g in (ref.get("groups") or []) if isinstance(g, dict)]
    source_count = int(manifest.get("source_recurring_group_count") or len(groups) + len(ref_groups))
    if len(groups) + len(ref_groups) != source_count:
        raise ValueError(f"Frozen baseline accounting mismatch: {len(groups)} + {len(ref_groups)} != {source_count}")
    if int(tech.get("accepted_fact_count") or 0) != 0 or int(tech.get("qdrant_entries_created") or 0) != 0:
        raise ValueError("Refusing source baseline with accepted facts or prior Qdrant entries")
    if strict_counts:
        expected = (EXPECTED_SOURCE_GROUPS, EXPECTED_TECHNICIAN_GROUPS, EXPECTED_REFERENCE_GROUPS)
        actual = (source_count, len(groups), len(ref_groups))
        if actual != expected:
            raise ValueError(f"Frozen GB8 baseline count drift: expected source/technician/reference={expected}, got {actual}")
    return {
        "technician_groups": groups,
        "reference_groups": ref_groups,
        "manifest": manifest,
        "counts": {"source": source_count, "technician": len(groups), "reference": len(ref_groups)},
        "source_hashes": {
            tech_path.name: sha256_file(tech_path),
            ref_path.name: sha256_file(ref_path),
            manifest_path.name: sha256_file(manifest_path),
        },
    }


def _headers(api_key: str = "") -> Dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        h["api-key"] = api_key
    return h


def http_json(method: str, url: str, payload: Any | None = None, timeout: int = 120, headers: Mapping[str, str] | None = None, allow_404: bool = False) -> Tuple[int, Any]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return int(response.status), json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        if allow_404 and e.code == 404:
            return 404, json.loads(raw) if raw.strip().startswith(("{", "[")) else {"error": raw}
        raise RuntimeError(f"HTTP {e.code} {method} {url}: {raw[:1000]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach {url}: {e.reason}") from e


def join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def ollama_models(ollama_url: str, timeout: int) -> List[str]:
    _, payload = http_json("GET", join_url(ollama_url, "/api/tags"), timeout=timeout, headers={"Accept": "application/json"})
    names = []
    for row in payload.get("models") or []:
        if isinstance(row, dict) and row.get("name"):
            names.append(str(row["name"]))
    return names


def model_available(model: str, names: Sequence[str]) -> bool:
    wanted = model.lower()
    for name in names:
        n = name.lower()
        if n == wanted or n == wanted + ":latest" or n.split(":", 1)[0] == wanted.split(":", 1)[0]:
            return True
    return False


def ollama_embed(ollama_url: str, model: str, texts: Sequence[str], timeout: int) -> List[List[float]]:
    if not texts:
        return []
    _, payload = http_json(
        "POST", join_url(ollama_url, "/api/embed"),
        payload={"model": model, "input": list(texts), "truncate": True},
        timeout=timeout,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    vectors = payload.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise RuntimeError(f"Ollama embedding response count mismatch: expected {len(texts)}, got {len(vectors) if isinstance(vectors, list) else 'invalid'}")
    out: List[List[float]] = []
    dims: set[int] = set()
    for v in vectors:
        if not isinstance(v, list) or not v:
            raise RuntimeError("Ollama returned an empty/invalid embedding")
        fv = [float(x) for x in v]
        dims.add(len(fv))
        out.append(fv)
    if len(dims) != 1:
        raise RuntimeError(f"Embedding dimension mismatch inside one batch: {sorted(dims)}")
    return out


def qdrant_collections(qdrant_url: str, api_key: str, timeout: int) -> List[str]:
    _, payload = http_json("GET", join_url(qdrant_url, "/collections"), timeout=timeout, headers=_headers(api_key))
    rows = ((payload.get("result") or {}).get("collections") or []) if isinstance(payload, dict) else []
    return [str(r.get("name")) for r in rows if isinstance(r, dict) and r.get("name")]


def qdrant_collection_info(qdrant_url: str, collection: str, api_key: str, timeout: int) -> Dict[str, Any] | None:
    status, payload = http_json("GET", join_url(qdrant_url, f"/collections/{urllib.parse.quote(collection, safe='')}"), timeout=timeout, headers=_headers(api_key), allow_404=True)
    if status == 404:
        return None
    return payload


def qdrant_count(qdrant_url: str, collection: str, api_key: str, timeout: int) -> int:
    _, payload = http_json(
        "POST", join_url(qdrant_url, f"/collections/{urllib.parse.quote(collection, safe='')}/points/count"),
        payload={"exact": True}, timeout=timeout, headers=_headers(api_key),
    )
    return int(((payload.get("result") or {}).get("count")) or 0)


def assert_trial_collection_name(collection: str) -> None:
    if not collection.startswith(TRIAL_COLLECTION_PREFIX):
        raise ValueError(f"Refusing destructive trial operation on unguarded collection name: {collection!r}. Collection must start with {TRIAL_COLLECTION_PREFIX!r}.")


def qdrant_delete_collection(qdrant_url: str, collection: str, api_key: str, timeout: int) -> None:
    assert_trial_collection_name(collection)
    http_json("DELETE", join_url(qdrant_url, f"/collections/{urllib.parse.quote(collection, safe='')}"), timeout=timeout, headers=_headers(api_key))


def qdrant_create_collection(qdrant_url: str, collection: str, vector_size: int, api_key: str, timeout: int) -> None:
    http_json(
        "PUT", join_url(qdrant_url, f"/collections/{urllib.parse.quote(collection, safe='')}"),
        payload={"vectors": {"size": int(vector_size), "distance": "Cosine"}},
        timeout=timeout, headers=_headers(api_key),
    )


def qdrant_upsert(qdrant_url: str, collection: str, points: Sequence[Dict[str, Any]], api_key: str, timeout: int) -> None:
    http_json(
        "PUT", join_url(qdrant_url, f"/collections/{urllib.parse.quote(collection, safe='')}/points?wait=true"),
        payload={"points": list(points)}, timeout=timeout, headers=_headers(api_key),
    )


def qdrant_search(qdrant_url: str, collection: str, vector: Sequence[float], limit: int, api_key: str, timeout: int, score_threshold: float | None = None) -> List[Dict[str, Any]]:
    body: Dict[str, Any] = {"vector": list(vector), "limit": max(1, int(limit)), "with_payload": True, "with_vector": False}
    if score_threshold is not None:
        body["score_threshold"] = float(score_threshold)
    _, payload = http_json(
        "POST", join_url(qdrant_url, f"/collections/{urllib.parse.quote(collection, safe='')}/points/search"),
        payload=body, timeout=timeout, headers=_headers(api_key),
    )
    rows = payload.get("result") if isinstance(payload, dict) else None
    return [dict(r) for r in rows] if isinstance(rows, list) else []


def batched(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    step = max(1, int(size))
    for i in range(0, len(items), step):
        yield items[i:i + step]


def make_plan(baseline: Mapping[str, Any], records: Sequence[Mapping[str, Any]], collection: str, embed_model: str, batch_size: int) -> Dict[str, Any]:
    lengths = [len(str(r.get("embedding_text") or "")) for r in records]
    return {
        "index_version": VERSION,
        "status": "plan_only_no_qdrant_writes",
        "source_signal_version": SOURCE_SIGNAL_VERSION,
        "collection": collection,
        "embedding_model": embed_model,
        "source_counts": baseline["counts"],
        "planned_points": len(records),
        "embedding_batch_size": int(batch_size),
        "planned_embedding_batches": (len(records) + max(1, int(batch_size)) - 1) // max(1, int(batch_size)),
        "embedding_text_chars": {
            "min": min(lengths) if lengths else 0,
            "max": max(lengths) if lengths else 0,
            "mean": round(sum(lengths) / len(lengths), 1) if lengths else 0,
        },
        "source_hashes": dict(baseline["source_hashes"]),
        "accepted_fact_count": 0,
        "automatic_fact_acceptance": False,
        "generative_reasoning_calls": 0,
        "qdrant_role": "disposable_semantic_search_index",
    }


def write_index_audit(output_root: Path, records: Sequence[Mapping[str, Any]]) -> None:
    rows = []
    for r in records:
        rows.append({
            "point_id": r["point_id"],
            "group_id": r["group_id"],
            "embedding_text_sha256": sha256_bytes(str(r["embedding_text"]).encode("utf-8")),
            "embedding_text_chars": len(str(r["embedding_text"])),
            "payload": r["payload"],
        })
    save_json(output_root / "indexed_points_audit_v1_3_8_1.json", {
        "index_version": VERSION,
        "point_count": len(rows),
        "vectors_intentionally_omitted": True,
        "rows": rows,
    })


def build_index(args: argparse.Namespace, baseline: Mapping[str, Any], records: Sequence[Mapping[str, Any]], api_key: str) -> int:
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    plan = make_plan(baseline, records, args.collection, args.embed_model, args.batch_size)
    save_json(output_root / "qdrant_trial_plan_v1_3_8_1.json", plan)
    if args.plan_only:
        print("# Nova DRL GB8 Qdrant Trial Index v1.3.8.1 — PLAN ONLY")
        print(f"Source recurring groups:  {baseline['counts']['source']}")
        print(f"Technician groups:        {baseline['counts']['technician']}")
        print(f"Reference/admin groups:   {baseline['counts']['reference']} (not indexed in this trial)")
        print(f"Planned Qdrant points:    {len(records)}")
        print(f"Collection:               {args.collection}")
        print(f"Embedding model:          {args.embed_model}")
        print(f"Embedding batches:        {plan['planned_embedding_batches']}")
        print("Generative reasoning:     0")
        print("Accepted facts:           0")
        print("Qdrant writes:            0 (plan-only)")
        print(f"Plan: {output_root / 'qdrant_trial_plan_v1_3_8_1.json'}")
        return 0

    model_names = ollama_models(args.ollama_url, args.timeout)
    if not model_available(args.embed_model, model_names):
        raise RuntimeError(f"Embedding model {args.embed_model!r} is not installed in Ollama. Installed models include: {', '.join(model_names[:20])}")
    collections = qdrant_collections(args.qdrant_url, api_key, args.timeout)
    exists = args.collection in collections
    if exists and args.rebuild:
        print(f"Deleting disposable trial collection: {args.collection}")
        qdrant_delete_collection(args.qdrant_url, args.collection, api_key, args.timeout)
        exists = False
    elif exists:
        raise RuntimeError(f"Trial collection already exists: {args.collection}. Use --rebuild to delete/recreate only this guarded trial collection.")

    first_vectors = ollama_embed(args.ollama_url, args.embed_model, [str(records[0]["embedding_text"])], args.timeout)
    vector_size = len(first_vectors[0])
    qdrant_create_collection(args.qdrant_url, args.collection, vector_size, api_key, args.timeout)
    print(f"Created {args.collection} | vector_size={vector_size} | distance=Cosine")

    indexed = 0
    embedding_calls = 0
    try:
        for batch_num, batch in enumerate(batched(list(records), args.batch_size), 1):
            texts = [str(r["embedding_text"]) for r in batch]
            if indexed == 0:
                vectors = [first_vectors[0]]
                if len(batch) > 1:
                    rest = ollama_embed(args.ollama_url, args.embed_model, texts[1:], args.timeout)
                    embedding_calls += 1
                    vectors.extend(rest)
                embedding_calls += 1
            else:
                vectors = ollama_embed(args.ollama_url, args.embed_model, texts, args.timeout)
                embedding_calls += 1
            points = []
            for record, vector in zip(batch, vectors):
                points.append({"id": record["point_id"], "vector": vector, "payload": record["payload"]})
            qdrant_upsert(args.qdrant_url, args.collection, points, api_key, args.timeout)
            indexed += len(points)
            print(f"[index {batch_num}] upserted {len(points)} | total {indexed}/{len(records)}")
    except Exception:
        partial_manifest = {
            "index_version": VERSION,
            "status": "partial_build_collection_should_be_rebuilt",
            "collection": args.collection,
            "indexed_before_failure": indexed,
            "planned_points": len(records),
            "embedding_model": args.embed_model,
            "vector_size": vector_size,
            "source_hashes": baseline["source_hashes"],
            "accepted_fact_count": 0,
            "automatic_fact_acceptance": False,
            "generative_reasoning_calls": 0,
            "qdrant_role": "disposable_semantic_search_index",
            "recovery": "rerun with --rebuild",
            "written_at_utc": utc_now(),
        }
        save_json(output_root / "qdrant_trial_manifest_v1_3_8_1.json", partial_manifest)
        raise

    exact_count = qdrant_count(args.qdrant_url, args.collection, api_key, args.timeout)
    if exact_count != len(records):
        raise RuntimeError(f"Qdrant exact point count mismatch after build: expected {len(records)}, got {exact_count}")
    write_index_audit(output_root, records)
    manifest = {
        "index_version": VERSION,
        "status": "complete_disposable_trial_index",
        "source_signal_version": SOURCE_SIGNAL_VERSION,
        "source_counts": baseline["counts"],
        "source_hashes": baseline["source_hashes"],
        "collection": args.collection,
        "qdrant_url": args.qdrant_url,
        "embedding_model": args.embed_model,
        "vector_size": vector_size,
        "distance": "Cosine",
        "indexed_point_count": exact_count,
        "embedding_calls": embedding_calls,
        "generative_reasoning_calls": 0,
        "automatic_fact_acceptance": False,
        "accepted_fact_count": 0,
        "knowledge_status": "provisional",
        "qdrant_role": "disposable_semantic_search_index",
        "reference_admin_groups_indexed": 0,
        "raw_candidates_indexed": 0,
        "rebuild_safe_collection_guard": TRIAL_COLLECTION_PREFIX,
        "written_at_utc": utc_now(),
    }
    save_json(output_root / "qdrant_trial_manifest_v1_3_8_1.json", manifest)
    print("\n# BUILD COMPLETE")
    print(f"Collection:              {args.collection}")
    print(f"Indexed technician groups:{exact_count}")
    print(f"Embedding model:         {args.embed_model}")
    print(f"Vector size:             {vector_size}")
    print("Generative reasoning:    0")
    print("Accepted facts:          0")
    print("Knowledge status:        PROVISIONAL")
    print("Qdrant role:             DISPOSABLE SEARCH INDEX")
    print(f"Manifest: {output_root / 'qdrant_trial_manifest_v1_3_8_1.json'}")
    return 0


def payload_group_id(row: Mapping[str, Any]) -> str:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return str(payload.get("recurring_group_id") or "")


def semantic_search(args: argparse.Namespace, query: str, api_key: str) -> Dict[str, Any]:
    info = qdrant_collection_info(args.qdrant_url, args.collection, api_key, args.timeout)
    if info is None:
        raise RuntimeError(f"Trial collection does not exist: {args.collection}. Build it first with --build or --rebuild.")
    vector = ollama_embed(args.ollama_url, args.embed_model, [query], args.timeout)[0]
    rows = qdrant_search(args.qdrant_url, args.collection, vector, args.top, api_key, args.timeout, args.score_threshold)
    return {
        "index_version": VERSION,
        "query": query,
        "collection": args.collection,
        "embedding_model": args.embed_model,
        "result_count": len(rows),
        "results": rows,
        "accepted_fact_count": 0,
        "knowledge_status": "provisional",
    }


def render_semantic(result: Mapping[str, Any]) -> str:
    lines = [
        f"# Nova DRL GB8 Qdrant Trial Search v{VERSION}",
        f"Query: {result.get('query')}",
        f"Collection: {result.get('collection')}",
        "Status: PROVISIONAL — Qdrant is a disposable semantic index; source JSON remains authoritative",
        "",
        "SEMANTIC MATCHES",
        "----------------",
    ]
    rows = result.get("results") or []
    if not rows:
        lines.append("No semantic matches returned.")
        return "\n".join(lines) + "\n"
    for i, row in enumerate(rows, 1):
        p = row.get("payload") or {}
        lines.append(
            f"{i:>2}. score={float(row.get('score') or 0):.4f} | {p.get('concept_label') or p.get('recurring_group_id')} | "
            f"serials={p.get('distinct_serial_count', '?')} | logs={p.get('distinct_log_count', '?')} | lane={p.get('lane', '?')}"
        )
        if p.get("service_areas"):
            lines.append("    areas: " + "; ".join(str(x) for x in p.get("service_areas") or []))
        for ex in (p.get("evidence_examples") or [])[:3]:
            lines.append(
                f"    - {ex.get('log_number') or '?'} | {ex.get('serial_number') or '?'} | {normalized_ws(ex.get('raw_source_text'))[:220]}"
            )
        lines.append(f"    group: {p.get('recurring_group_id')}")
    lines += ["", "Policy: accepted facts=0; generative reasoning calls=0; source evidence unchanged."]
    return "\n".join(lines) + "\n"


def load_python_query_engine(repo_root: Path):
    path = repo_root / "analysis" / "nova_gb8_technician_query_engine_v1_3_8_0.py"
    if not path.exists():
        raise FileNotFoundError(f"v{PYTHON_QUERY_VERSION} deterministic query engine not found: {path}")
    spec = importlib.util.spec_from_file_location("nova_gb8_query_v1380", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def python_compare_results(input_root: Path, repo_root: Path, query: str, top: int) -> Dict[str, Any]:
    module = load_python_query_engine(repo_root)
    index = module.load_index(input_root)
    aliases = module.load_aliases(None)
    result = module.execute_query(index, query, aliases, "search", max(1, top), 3, False)
    rows = []
    for item in result.get("results") or []:
        g = item.get("group") or {}
        rows.append({
            "group_id": str(g.get("group_id") or g.get("recurring_group_id") or ""),
            "concept_label": g.get("concept_label") or g.get("concept_key"),
            "score": item.get("score"),
            "distinct_serial_count": g.get("distinct_serial_count"),
            "distinct_log_count": g.get("distinct_log_count"),
        })
    return {"query": query, "results": rows, "match_count": result.get("match_count")}


def render_compare(semantic: Mapping[str, Any], python_result: Mapping[str, Any]) -> str:
    qrows = semantic.get("results") or []
    prows = python_result.get("results") or []
    qids = [payload_group_id(r) for r in qrows if payload_group_id(r)]
    pids = [str(r.get("group_id") or "") for r in prows if r.get("group_id")]
    overlap = [gid for gid in qids if gid in set(pids)]
    lines = [
        f"# Nova DRL GB8 Search Comparison v{VERSION}",
        f"Query: {semantic.get('query')}",
        f"Qdrant collection: {semantic.get('collection')}",
        f"Top-{max(len(qrows), len(prows))} overlap: {len(overlap)} groups",
        "",
        "QDRANT SEMANTIC",
        "----------------",
    ]
    for i, row in enumerate(qrows, 1):
        p = row.get("payload") or {}
        lines.append(f"{i:>2}. {float(row.get('score') or 0):.4f} | {p.get('concept_label')} | {p.get('recurring_group_id')}")
    lines += ["", "PYTHON v1.3.8.0 DETERMINISTIC", "-----------------------------"]
    for i, row in enumerate(prows, 1):
        lines.append(f"{i:>2}. {float(row.get('score') or 0):.3f} | {row.get('concept_label')} | {row.get('group_id')}")
    lines += ["", "OVERLAP", "-------"]
    if overlap:
        for gid in overlap:
            lines.append(f"- {gid}")
    else:
        lines.append("No group IDs overlap in these top results. That is useful trial evidence, not automatically a failure.")
    lines += ["", "Policy: both searches are provisional; Qdrant may be deleted/rebuilt without changing source knowledge."]
    return "\n".join(lines) + "\n"


def status_report(args: argparse.Namespace, api_key: str) -> int:
    print(f"# Nova DRL GB8 Qdrant Trial Status v{VERSION}")
    try:
        models = ollama_models(args.ollama_url, args.timeout)
        print(f"Ollama:     reachable | embed model {'FOUND' if model_available(args.embed_model, models) else 'NOT FOUND'}: {args.embed_model}")
    except Exception as e:
        print(f"Ollama:     ERROR | {e}")
    try:
        collections = qdrant_collections(args.qdrant_url, api_key, args.timeout)
        print(f"Qdrant:     reachable | collections={len(collections)}")
        if args.collection in collections:
            print(f"Trial:      EXISTS | {args.collection} | points={qdrant_count(args.qdrant_url, args.collection, api_key, args.timeout)}")
        else:
            print(f"Trial:      NOT BUILT | {args.collection}")
    except Exception as e:
        print(f"Qdrant:     ERROR | {e}")
    print("Source role: frozen v1.3.7.3 remains authoritative")
    return 0


def drop_trial(args: argparse.Namespace, api_key: str) -> int:
    assert_trial_collection_name(args.collection)
    info = qdrant_collection_info(args.qdrant_url, args.collection, api_key, args.timeout)
    if info is None:
        print(f"Trial collection already absent: {args.collection}")
        return 0
    qdrant_delete_collection(args.qdrant_url, args.collection, api_key, args.timeout)
    print(f"Deleted disposable trial collection only: {args.collection}")
    print("Frozen v1.3.7.3 source files were not modified.")
    return 0


def interactive(args: argparse.Namespace, api_key: str) -> int:
    print(f"Nova DRL GB8 Qdrant Trial v{VERSION}")
    print(f"Collection: {args.collection}")
    print("Commands: :status, :compare on|off, :quit")
    compare = False
    repo_root = Path(__file__).resolve().parents[1]
    input_root = Path(args.input_root)
    while True:
        try:
            q = input("nova-qdrant> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            continue
        if q in {":quit", ":q", "quit", "exit"}:
            return 0
        if q == ":status":
            status_report(args, api_key)
            continue
        if q.startswith(":compare"):
            parts = q.split()
            if len(parts) == 2 and parts[1].lower() in {"on", "off"}:
                compare = parts[1].lower() == "on"
            print(f"side-by-side compare: {'ON' if compare else 'OFF'}")
            continue
        sem = semantic_search(args, q, api_key)
        if compare:
            py = python_compare_results(input_root, repo_root, q, args.top)
            print(render_compare(sem, py))
        else:
            print(render_semantic(sem))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nova DRL GB8 Qdrant Trial Index v1.3.8.1 — disposable semantic search over frozen v1.3.7.3")
    action = parser.add_mutually_exclusive_group(required=False)
    action.add_argument("--plan-only", action="store_true", help="Build local index plan/audit inputs without Ollama/Qdrant calls or writes")
    action.add_argument("--build", action="store_true", help="Create a new trial collection; refuses if it already exists")
    action.add_argument("--rebuild", action="store_true", help="Delete/recreate only the guarded trial collection")
    action.add_argument("--status", action="store_true", help="Check Ollama, Qdrant, and trial collection status")
    action.add_argument("--drop-trial", action="store_true", help="Delete only the guarded disposable trial collection")
    action.add_argument("--search", metavar="QUERY", help="Semantic search the trial collection")
    action.add_argument("--compare", metavar="QUERY", help="Compare Qdrant semantic results with Python v1.3.8.0 deterministic results")
    action.add_argument("--interactive", action="store_true", help="Interactive semantic search; :compare on enables side-by-side results")
    action.add_argument("--self-check", action="store_true", help="Validate frozen v1.3.7.3 accounting and local point preparation; no network")
    parser.add_argument("--input-root", default=str(DEFAULT_INPUT_ROOT), help="Frozen v1.3.7.3 technician-signal output root")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help="Local v1.3.8.1 audit/manifest output root")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--qdrant-url", default=DEFAULT_QDRANT_URL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--evidence-examples", type=int, default=DEFAULT_EVIDENCE_EXAMPLES)
    parser.add_argument("--max-embed-chars", type=int, default=DEFAULT_MAX_EMBED_CHARS)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--score-threshold", type=float, default=None)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--qdrant-api-key-env", default="QDRANT_API_KEY", help="Optional environment variable holding a Qdrant API key; value is never printed")
    args = parser.parse_args(argv)

    api_key = os.environ.get(args.qdrant_api_key_env, "") if args.qdrant_api_key_env else ""

    if args.status:
        return status_report(args, api_key)
    if args.drop_trial:
        return drop_trial(args, api_key)
    if args.search:
        result = semantic_search(args, args.search, api_key)
        print(render_semantic(result), end="")
        return 0
    if args.compare:
        sem = semantic_search(args, args.compare, api_key)
        repo_root = Path(__file__).resolve().parents[1]
        py = python_compare_results(Path(args.input_root), repo_root, args.compare, args.top)
        print(render_compare(sem, py), end="")
        return 0
    if args.interactive:
        return interactive(args, api_key)

    baseline = load_frozen_baseline(Path(args.input_root), strict_counts=True)
    records = prepare_records(baseline["technician_groups"], max(1, args.evidence_examples), max(500, args.max_embed_chars))
    if len(records) != baseline["counts"]["technician"]:
        raise ValueError(f"Prepared point accounting mismatch: {len(records)} != {baseline['counts']['technician']}")

    if args.self_check:
        print(
            f"PASS: v{VERSION} frozen-source self-check | source={baseline['counts']['source']} "
            f"technician={baseline['counts']['technician']} reference={baseline['counts']['reference']} "
            f"planned_points={len(records)} | generative_reasoning=0 | accepted_facts=0"
        )
        return 0

    # Default action is plan-only so an accidental invocation never writes Qdrant.
    if not (args.plan_only or args.build or args.rebuild):
        args.plan_only = True
    return build_index(args, baseline, records, api_key)


if __name__ == "__main__":
    raise SystemExit(main())
