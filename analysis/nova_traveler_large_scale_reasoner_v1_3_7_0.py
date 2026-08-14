#!/usr/bin/env python3
"""
Nova DRL Large-Scale Batched Corpus Reasoner v1.3.7.0

Purpose
-------
Reason across a completed v1.3.6.1 evidence-backed candidate ledger at real
corpus scale without attempting to place thousands of candidates into one LLM
prompt.

Architecture
------------
1) v1.3.5.1 acquisition remains immutable evidence authority.
2) v1.3.6.1 8B prospecting remains the source of evidence-backed candidates.
3) Python filters only obvious event-identity/form metadata from the 32B working
   set, preserving every excluded candidate in an audit file.
4) Qwen2.5 32B clusters evidence in resumable, dynamically sized batches.
5) Python validates candidate IDs and guarantees every eligible candidate is
   preserved exactly once, falling back to singleton clusters if a batch fails.
6) Qwen2.5 32B performs resumable hierarchical merge passes over compact cluster
   summaries, never raw source replacement.
7) Python owns recurrence counts and provenance across distinct logs, source
   hashes, and serial/unit folders.

Non-negotiable policy
---------------------
- No source image or raw transcription modification.
- No automatic fact approval.
- No Qdrant writes.
- No silent spelling, part-number, abbreviation, or shop-term normalization.
- Model labels/concept keys are provisional organizational metadata only.
- Recurrence requires >=2 distinct DRL logs AND >=2 distinct source hashes.
- Exact raw candidate evidence remains attached to every final cluster.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.3.7.0"
REQUIRED_SORTER_VERSION = "1.3.6.1"
DEFAULT_INPUT_ROOT = Path("/opt/nova-drl/output/traveler_corpus_sort_v1_3_6_1")
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/traveler_large_scale_reason_v1_3_7_0")
DEFAULT_REASON_MODEL = "qwen25-drl:32b-16k"
DEFAULT_PROSPECT_MODEL = "qwen3-vl-drl:8b-q8-16k"
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"

LANE_KIND_MAP = {
    "customer_requirements": {"customer_requirement"},
    "repairs": {"repair_or_service"},
    "components": {"component_or_part", "part_number_or_identifier"},
    "diagnostics": {"diagnostic_or_failure"},
    "testing_process": {"testing_or_process"},
    "terminology": {"shop_term_or_abbreviation"},
    "other": {"other"},
}

GENERIC_CONCEPT_KEYS = {
    "repair", "repairs", "service", "component", "components", "part", "parts",
    "failure", "failures", "diagnostic", "diagnostics", "test", "testing",
    "process", "term", "terms", "other", "misc", "miscellaneous", "unknown",
    "general", "robot", "equipment",
}

STAGE1_PROMPT = r"""You are the BATCH CLUSTERING layer for a DRL repair-history corpus.

You receive one lane of evidence-backed candidate phrases. Each candidate includes an immutable
candidate ID, DRL log number, optional serial/unit identity, kind, and exact raw evidence text.

Return JSON only in this exact shape:
{
  "clusters": [
    {
      "concept_label": "short provisional label",
      "concept_key": "short_snake_case_provisional_key",
      "member_candidate_ids": ["candidate-id", "candidate-id"]
    }
  ]
}

RULES:
- Every supplied candidate ID must appear in exactly one cluster. Singleton clusters are allowed.
- Group only phrases that genuinely express the same repair/service/component/failure/test/term concept.
- Preserve axes, mechanisms, components, and distinct failure modes. Do not merge items merely because
  they concern the same equipment family.
- Do not use outside knowledge.
- Do not expand unexplained abbreviations, part strings, shop terms, or OCR fragments.
- concept_label and concept_key are provisional organizational metadata only. They must not rewrite
  or replace the raw evidence.
- If an item is ambiguous, keep it as a singleton rather than forcing a merge.
- Do not invent candidate IDs.
"""

MERGE_PROMPT = r"""You are the HIERARCHICAL MERGE layer for provisional DRL repair-history clusters.

You receive compact cluster summaries from one semantic lane. A cluster contains a cluster ID,
provisional label/key, counts, and a few exact raw evidence examples.

Return JSON only in this exact shape:
{
  "merge_groups": [
    {
      "concept_label": "short provisional label",
      "concept_key": "short_snake_case_provisional_key",
      "member_cluster_ids": ["cluster-id", "cluster-id"]
    }
  ]
}

RULES:
- Output only merges containing 2 or more supplied cluster IDs.
- Merge only clusters that are genuinely the same concept, not merely related.
- Preserve axes, mechanisms, components, quantities, and distinct failure modes.
- Do not use outside knowledge.
- Do not expand unexplained abbreviations, part strings, shop terms, or OCR fragments.
- If uncertain, do not merge.
- Do not invent cluster IDs.
- concept_label and concept_key are provisional organizational metadata only.
"""

# Generic identity/admin candidates are preserved in audit but are not useful for semantic
# repair recurrence. These patterns intentionally target document identity/administration,
# not equipment-specific technical meaning.
_METADATA_PATTERNS = [
    re.compile(r"^\s*(?:DRL\s+part\s*#|Serial\s*#|Frame\s+Serial\s*#|Board\s+Serial\s*#)(?:\s|:|$)", re.I),
    re.compile(r"^\s*(?:RMA\s+Number|Cust(?:omer)?\s*RMA|Customer\s+PO|Cust\s+PO|POC\s+Phone|Point\s+of\s+Contact|DRL\s+Rep|Sales\s+Rep)\b", re.I),
    re.compile(r"^\s*(?:Warranty\s+Date|Warranty\s+Type|Date\s+Shipped|SHIPPED\b|Serial\s+Check|Q\.C\.\s*By)\b", re.I),
    re.compile(r"^\s*(?:Saved \(in shipping area\)|Saved \(in warehouse\)|Unusable \(discarded\))(?:\s|:|$)", re.I),
    re.compile(r"^\s*(?:pricing approved|needs quote)\b", re.I),
    re.compile(r"^\s*(?:Cleaned|Aligned|Adjusted|Latest Firmware|Appearance|All Screws|Warranty Sticker Applied)\b.*[✓☐X\[\]]", re.I),
    re.compile(r"^\s*Direct Repair Laboratories\b", re.I),
]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL line {lineno} is not an object: {path}")
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def ollama_version() -> Optional[str]:
    try:
        p = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=10)
        return (p.stdout or p.stderr or "").strip() or None
    except Exception:
        return None


def get_ollama_model_info(model: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {"requested_model": model, "ollama_version": ollama_version()}
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        for item in data.get("models") or []:
            if item.get("name") == model or item.get("model") == model:
                info.update({
                    "resolved_name": item.get("name") or item.get("model"),
                    "digest": item.get("digest"),
                    "size_bytes": item.get("size"),
                    "modified_at": item.get("modified_at"),
                    "details": item.get("details"),
                    "available": True,
                })
                break
        else:
            info["available"] = False
    except Exception as exc:
        info.update({"available": None, "model_info_error": str(exc)})
    return info


def stop_ollama_model(model: str) -> None:
    try:
        subprocess.run(["ollama", "stop", model], capture_output=True, text=True, timeout=30)
    except Exception:
        pass


def parse_json_response(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.I)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        first = stripped.find("{")
        last = stripped.rfind("}")
        if first >= 0 and last > first:
            return json.loads(stripped[first:last + 1])
        raise


def call_ollama_json(
    model: str,
    prompt: str,
    num_ctx: int,
    num_predict: int,
    timeout: int,
) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_ctx": int(num_ctx),
            "num_predict": int(num_predict),
        },
    }
    req = urllib.request.Request(
        OLLAMA_GENERATE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data.get("response") or "")


def call_json_with_retries(
    model: str,
    prompt: str,
    num_ctx: int,
    num_predict: int,
    timeout: int,
    retries: int,
    raw_dir: Path,
) -> Tuple[Optional[Any], List[Dict[str, Any]]]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    attempts: List[Dict[str, Any]] = []
    current_prompt = prompt
    for attempt in range(1, max(1, retries + 1) + 1):
        started = time.time()
        raw = ""
        error: Optional[str] = None
        parsed: Optional[Any] = None
        try:
            raw = call_ollama_json(model, current_prompt, num_ctx, num_predict, timeout)
            (raw_dir / f"raw_attempt_{attempt:02d}.txt").write_text(raw, encoding="utf-8")
            parsed = parse_json_response(raw)
        except Exception as exc:
            error = str(exc)
            if raw:
                (raw_dir / f"raw_attempt_{attempt:02d}.txt").write_text(raw, encoding="utf-8")
        attempts.append({
            "attempt": attempt,
            "elapsed_seconds": round(time.time() - started, 3),
            "parse_or_call_error": error,
        })
        if parsed is not None:
            return parsed, attempts
        current_prompt = prompt + "\n\nIMPORTANT: Your previous attempt was invalid JSON. Return only valid JSON matching the required schema."
    return None, attempts


def normalized_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def lexical_sort_key(text: str) -> str:
    # Ordering aid only. It never changes evidence or becomes a fact.
    return re.sub(r"[^a-z0-9]+", " ", normalized_ws(text).lower()).strip()


def safe_slug(text: str, max_len: int = 72) -> str:
    t = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    if not t:
        t = "cluster"
    return t[:max_len].rstrip("_") or "cluster"


def is_metadata_candidate(candidate: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    text = normalized_ws(candidate.get("raw_source_text") or "")
    kind = str(candidate.get("kind") or "")
    for idx, pat in enumerate(_METADATA_PATTERNS, 1):
        if pat.search(text):
            return True, f"generic_identity_or_admin_pattern_{idx}"
    # A bare model/equipment identity inside part-number candidate is administrative identity,
    # not a replacement-part event. Preserve in audit only.
    if kind == "part_number_or_identifier" and re.fullmatch(r"(?:DRL\s+part\s*#\s*)?RBT\s*-?\s*GB8(?:-MT)?(?:\s*\(GENMARK\))?", text, re.I):
        return True, "equipment_model_identity_not_repair_part"
    return False, None


def lane_for_candidate(candidate: Dict[str, Any]) -> Optional[str]:
    kind = str(candidate.get("kind") or "")
    if kind == "unclear_ocr":
        return None
    for lane, kinds in LANE_KIND_MAP.items():
        if kind in kinds:
            return lane
    return "other"


def extract_serial_from_source_path(source_path: str) -> Optional[str]:
    try:
        parts = Path(source_path).parts
    except Exception:
        parts = tuple(str(source_path).split("/"))
    for part in reversed(parts[:-1] if len(parts) > 1 else parts):
        m = re.search(r"\bSN\s+(?:GB8-MT-)?(\d{8})\b", part, re.I)
        if m:
            return m.group(1)
    return None


def unit_folder_from_source_path(source_path: str) -> Optional[str]:
    try:
        p = Path(source_path)
        return p.parent.name or None
    except Exception:
        return None


def validate_input_manifest(input_root: Path) -> Dict[str, Any]:
    manifest_path = input_root / "sort_manifest_v1_3_6_1.json"
    ledger_path = input_root / "candidate_ledger_v1_3_6_1.jsonl"
    if not manifest_path.exists():
        raise FileNotFoundError(f"v1.3.6.1 manifest not found: {manifest_path}")
    if not ledger_path.exists():
        raise FileNotFoundError(f"v1.3.6.1 candidate ledger not found: {ledger_path}")
    manifest = load_json(manifest_path)
    if str(manifest.get("sorter_version")) != REQUIRED_SORTER_VERSION:
        raise ValueError(f"Expected sorter_version {REQUIRED_SORTER_VERSION}, got {manifest.get('sorter_version')}")
    if manifest.get("status") != "prospect_only_complete":
        raise ValueError(f"Expected prospect_only_complete input, got status={manifest.get('status')}")
    if manifest.get("reasoning_performed") is not False:
        raise ValueError("Input must be the prospect-only v1.3.6.1 ledger before large-scale reasoning.")
    gp = manifest.get("global_policy") or {}
    if int(gp.get("accepted_fact_count") or 0) != 0:
        raise ValueError("Input manifest reports accepted facts; large-scale reasoner requires zero accepted facts.")
    if int(gp.get("qdrant_entries_created") or 0) != 0:
        raise ValueError("Input manifest reports Qdrant entries; large-scale reasoner requires zero Qdrant writes.")
    return manifest


def load_candidates(input_root: Path, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    path = input_root / "candidate_ledger_v1_3_6_1.jsonl"
    candidates = read_jsonl(path)
    expected = int(manifest.get("candidate_count") or 0)
    if expected and len(candidates) != expected:
        raise ValueError(f"Candidate ledger count mismatch: manifest={expected}, ledger={len(candidates)}")
    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for idx, c in enumerate(candidates):
        cid = str(c.get("candidate_id") or "")
        if not cid:
            raise ValueError(f"Candidate {idx} missing candidate_id")
        if cid in seen:
            raise ValueError(f"Duplicate candidate_id in input ledger: {cid}")
        seen.add(cid)
        row = dict(c)
        row["serial_number"] = extract_serial_from_source_path(str(c.get("source_path") or ""))
        row["unit_folder"] = unit_folder_from_source_path(str(c.get("source_path") or ""))
        out.append(row)
    return out


def prepare_reasoning_candidates(candidates: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    eligible: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for c in candidates:
        lane = lane_for_candidate(c)
        if lane is None:
            excluded.append({**c, "reasoning_exclusion_reason": "unclear_ocr_preserved_for_human_or_secondary_vision_review"})
            continue
        is_meta, reason = is_metadata_candidate(c)
        if is_meta:
            excluded.append({**c, "reasoning_exclusion_reason": reason})
            continue
        row = dict(c)
        row["reasoning_lane"] = lane
        eligible.append(row)
    return eligible, excluded


def compact_candidate(c: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": c["candidate_id"],
        "log": str(c.get("log_number") or ""),
        "serial": c.get("serial_number"),
        "kind": c.get("kind"),
        "raw": c.get("raw_source_text"),
    }


def estimate_item_chars(item: Dict[str, Any]) -> int:
    return len(json.dumps(item, ensure_ascii=False, separators=(",", ":"))) + 2


def pack_candidates(candidates: Sequence[Dict[str, Any]], max_chars: int) -> List[List[Dict[str, Any]]]:
    by_lane: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        by_lane[str(c["reasoning_lane"])].append(c)
    batches: List[List[Dict[str, Any]]] = []
    for lane in sorted(by_lane):
        rows = sorted(by_lane[lane], key=lambda c: (lexical_sort_key(c.get("raw_source_text") or ""), str(c.get("log_number")), str(c.get("candidate_id"))))
        current: List[Dict[str, Any]] = []
        current_chars = 0
        for row in rows:
            size = estimate_item_chars(compact_candidate(row))
            if current and current_chars + size > max_chars:
                batches.append(current)
                current = []
                current_chars = 0
            current.append(row)
            current_chars += size
        if current:
            batches.append(current)
    return batches


def make_stage1_prompt(batch: Sequence[Dict[str, Any]]) -> str:
    lane = str(batch[0]["reasoning_lane"]) if batch else "unknown"
    payload = {
        "lane": lane,
        "candidates": [compact_candidate(c) for c in batch],
    }
    return STAGE1_PROMPT + "\n\nBATCH INPUT:\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def fallback_singleton_cluster(candidate: Dict[str, Any], lane: str, reason: str) -> Dict[str, Any]:
    cid = str(candidate["candidate_id"])
    raw = normalized_ws(candidate.get("raw_source_text") or "")
    label = raw[:80] if raw else cid
    cluster_id = "cl_" + hashlib.sha256((lane + "\n" + cid).encode("utf-8")).hexdigest()[:16]
    return {
        "cluster_id": cluster_id,
        "lane": lane,
        "concept_label": label,
        "concept_key": safe_slug(raw or cid),
        "member_candidate_ids": [cid],
        "stage1_origin": reason,
        "stage1_model_group_index": None,
    }


def validate_stage1_clusters(parsed: Any, batch: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    lane = str(batch[0]["reasoning_lane"]) if batch else "unknown"
    by_id = {str(c["candidate_id"]): c for c in batch}
    assigned: set[str] = set()
    clusters: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    groups = parsed.get("clusters") if isinstance(parsed, dict) else None
    if not isinstance(groups, list):
        groups = []
        rejected.append({"reason": "response_missing_clusters_array"})
    for idx, g in enumerate(groups):
        if not isinstance(g, dict):
            rejected.append({"index": idx, "reason": "cluster_not_object", "model_cluster": g})
            continue
        ids = [str(x) for x in (g.get("member_candidate_ids") or [])]
        unknown = [x for x in ids if x not in by_id]
        dup = [x for x in ids if x in assigned]
        valid = list(dict.fromkeys(x for x in ids if x in by_id and x not in assigned))
        if unknown or dup:
            rejected.append({"index": idx, "reason": "unknown_or_duplicate_candidate_ids", "unknown_ids": unknown, "already_assigned_ids": dup, "model_cluster": g})
        if not valid:
            continue
        assigned.update(valid)
        label = normalized_ws(g.get("concept_label") or "") or normalized_ws(by_id[valid[0]].get("raw_source_text") or "")[:80]
        key = safe_slug(g.get("concept_key") or label)
        cluster_id = "cl_" + hashlib.sha256((lane + "\n" + "\n".join(sorted(valid))).encode("utf-8")).hexdigest()[:16]
        clusters.append({
            "cluster_id": cluster_id,
            "lane": lane,
            "concept_label": label,
            "concept_key": key,
            "member_candidate_ids": valid,
            "stage1_origin": "model_cluster",
            "stage1_model_group_index": idx,
        })
    for cid, c in by_id.items():
        if cid not in assigned:
            clusters.append(fallback_singleton_cluster(c, lane, "fallback_singleton_unassigned_or_batch_failure"))
    clusters.sort(key=lambda x: (x["lane"], x["concept_key"], x["cluster_id"]))
    return clusters, rejected


def enrich_cluster(cluster: Dict[str, Any], by_candidate: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    members = [by_candidate[cid] for cid in cluster.get("member_candidate_ids") or [] if cid in by_candidate]
    logs = sorted({str(m.get("log_number") or "") for m in members if str(m.get("log_number") or "")})
    hashes = sorted({str(m.get("source_sha256") or "") for m in members if str(m.get("source_sha256") or "")})
    serials = sorted({str(m.get("serial_number")) for m in members if m.get("serial_number")})
    units = sorted({str(m.get("unit_folder")) for m in members if m.get("unit_folder")})
    result = dict(cluster)
    result.update({
        "candidate_count": len(members),
        "distinct_log_count": len(logs),
        "logs": logs,
        "distinct_source_hash_count": len(hashes),
        "distinct_serial_count": len(serials),
        "serial_numbers": serials,
        "distinct_unit_folder_count": len(units),
        "unit_folders": units,
    })
    return result


def consolidate_exact_model_keys(clusters: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    singles: List[Dict[str, Any]] = []
    for c in clusters:
        key = safe_slug(c.get("concept_key") or "")
        if key in GENERIC_CONCEPT_KEYS or len(key) < 4:
            singles.append(dict(c))
        else:
            grouped[(str(c.get("lane")), key)].append(c)
    out: List[Dict[str, Any]] = singles[:]
    for (lane, key), rows in grouped.items():
        if len(rows) == 1:
            out.append(dict(rows[0]))
            continue
        ids = sorted({cid for r in rows for cid in (r.get("member_candidate_ids") or [])})
        labels = Counter(normalized_ws(r.get("concept_label") or "") for r in rows if normalized_ws(r.get("concept_label") or ""))
        label = labels.most_common(1)[0][0] if labels else key
        cluster_id = "cl_" + hashlib.sha256((lane + "\nexact_key\n" + "\n".join(ids)).encode("utf-8")).hexdigest()[:16]
        out.append({
            "cluster_id": cluster_id,
            "lane": lane,
            "concept_label": label,
            "concept_key": key,
            "member_candidate_ids": ids,
            "stage1_origin": "python_exact_provisional_concept_key_consolidation",
            "source_cluster_ids": [r.get("cluster_id") for r in rows],
        })
    out.sort(key=lambda x: (str(x.get("lane")), str(x.get("concept_key")), str(x.get("cluster_id"))))
    return out


def compact_cluster_summary(cluster: Dict[str, Any], by_candidate: Dict[str, Dict[str, Any]], max_examples: int = 3) -> Dict[str, Any]:
    examples: List[Dict[str, Any]] = []
    seen_text: set[str] = set()
    for cid in cluster.get("member_candidate_ids") or []:
        c = by_candidate.get(cid)
        if not c:
            continue
        raw = normalized_ws(c.get("raw_source_text") or "")
        if not raw or raw.lower() in seen_text:
            continue
        seen_text.add(raw.lower())
        examples.append({"log": c.get("log_number"), "raw": raw[:300]})
        if len(examples) >= max_examples:
            break
    enriched = enrich_cluster(cluster, by_candidate)
    return {
        "id": enriched["cluster_id"],
        "lane": enriched["lane"],
        "label": enriched.get("concept_label"),
        "key": enriched.get("concept_key"),
        "candidate_count": enriched["candidate_count"],
        "distinct_logs": enriched["distinct_log_count"],
        "distinct_serials": enriched["distinct_serial_count"],
        "examples": examples,
    }


def estimate_cluster_summary_chars(summary: Dict[str, Any]) -> int:
    return len(json.dumps(summary, ensure_ascii=False, separators=(",", ":"))) + 2


def pack_cluster_summaries(
    clusters: Sequence[Dict[str, Any]],
    by_candidate: Dict[str, Dict[str, Any]],
    max_chars: int,
    overlap: int,
) -> List[List[Dict[str, Any]]]:
    by_lane: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in clusters:
        by_lane[str(c.get("lane"))].append(c)
    batches: List[List[Dict[str, Any]]] = []
    for lane in sorted(by_lane):
        rows = sorted(by_lane[lane], key=lambda c: (safe_slug(c.get("concept_key") or c.get("concept_label") or ""), str(c.get("cluster_id"))))
        summaries = [(c, compact_cluster_summary(c, by_candidate)) for c in rows]
        current: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        current_chars = 0
        lane_batches: List[List[Tuple[Dict[str, Any], Dict[str, Any]]]] = []
        for pair in summaries:
            size = estimate_cluster_summary_chars(pair[1])
            if current and current_chars + size > max_chars:
                lane_batches.append(current)
                carry = current[-max(0, overlap):] if overlap > 0 else []
                current = list(carry)
                current_chars = sum(estimate_cluster_summary_chars(x[1]) for x in current)
            current.append(pair)
            current_chars += size
        if current:
            lane_batches.append(current)
        for lb in lane_batches:
            batches.append([x[0] for x in lb])
    return batches


def make_merge_prompt(batch: Sequence[Dict[str, Any]], by_candidate: Dict[str, Dict[str, Any]]) -> str:
    lane = str(batch[0].get("lane")) if batch else "unknown"
    payload = {
        "lane": lane,
        "clusters": [compact_cluster_summary(c, by_candidate) for c in batch],
    }
    return MERGE_PROMPT + "\n\nMERGE INPUT:\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


class UnionFind:
    def __init__(self, items: Iterable[str]):
        self.parent = {x: x for x in items}
        self.rank = {x: 0 for x in items}

    def find(self, x: str) -> str:
        p = self.parent[x]
        if p != x:
            self.parent[x] = self.find(p)
        return self.parent[x]

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


def validate_merge_output(parsed: Any, batch: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    known = {str(c.get("cluster_id")) for c in batch}
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    groups = parsed.get("merge_groups") if isinstance(parsed, dict) else None
    if not isinstance(groups, list):
        return [], [{"reason": "response_missing_merge_groups_array"}]
    for idx, g in enumerate(groups):
        if not isinstance(g, dict):
            rejected.append({"index": idx, "reason": "merge_group_not_object", "model_group": g})
            continue
        ids = list(dict.fromkeys(str(x) for x in (g.get("member_cluster_ids") or [])))
        unknown = [x for x in ids if x not in known]
        valid = [x for x in ids if x in known]
        if unknown:
            rejected.append({"index": idx, "reason": "unknown_cluster_ids", "unknown_ids": unknown, "model_group": g})
            continue
        if len(valid) < 2:
            rejected.append({"index": idx, "reason": "merge_requires_at_least_two_clusters", "model_group": g})
            continue
        accepted.append({
            "concept_label": normalized_ws(g.get("concept_label") or ""),
            "concept_key": safe_slug(g.get("concept_key") or g.get("concept_label") or "merged"),
            "member_cluster_ids": valid,
            "model_group_index": idx,
        })
    return accepted, rejected


def apply_merge_proposals(
    clusters: Sequence[Dict[str, Any]],
    proposals: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    by_id = {str(c["cluster_id"]): c for c in clusters}
    uf = UnionFind(by_id.keys())
    proposal_by_pair_root_hint: List[Dict[str, Any]] = []
    for p in proposals:
        ids = [x for x in p.get("member_cluster_ids") or [] if x in by_id]
        if len(ids) < 2:
            continue
        first = ids[0]
        for other in ids[1:]:
            uf.union(first, other)
        proposal_by_pair_root_hint.append(p)
    comps: Dict[str, List[str]] = defaultdict(list)
    for cid in by_id:
        comps[uf.find(cid)].append(cid)
    merge_count = sum(1 for ids in comps.values() if len(ids) > 1)
    if merge_count == 0:
        return [dict(c) for c in clusters], 0

    out: List[Dict[str, Any]] = []
    for ids in comps.values():
        if len(ids) == 1:
            out.append(dict(by_id[ids[0]]))
            continue
        member_candidates = sorted({cand for cid in ids for cand in (by_id[cid].get("member_candidate_ids") or [])})
        lane = str(by_id[ids[0]].get("lane"))
        applicable = [p for p in proposal_by_pair_root_hint if set(p.get("member_cluster_ids") or []).issubset(set(ids))]
        labels = [normalized_ws(p.get("concept_label") or "") for p in applicable if normalized_ws(p.get("concept_label") or "")]
        keys = [safe_slug(p.get("concept_key") or "") for p in applicable if safe_slug(p.get("concept_key") or "")]
        if not labels:
            labels = [normalized_ws(by_id[cid].get("concept_label") or "") for cid in ids]
        if not keys:
            keys = [safe_slug(by_id[cid].get("concept_key") or "") for cid in ids]
        label = Counter(labels).most_common(1)[0][0] if labels else lane
        key = Counter(keys).most_common(1)[0][0] if keys else safe_slug(label)
        new_id = "cl_" + hashlib.sha256((lane + "\nmerge\n" + "\n".join(member_candidates)).encode("utf-8")).hexdigest()[:16]
        out.append({
            "cluster_id": new_id,
            "lane": lane,
            "concept_label": label,
            "concept_key": key,
            "member_candidate_ids": member_candidates,
            "merge_origin": "hierarchical_32b_proposal_python_union",
            "source_cluster_ids": sorted(ids),
        })
    out.sort(key=lambda x: (str(x.get("lane")), safe_slug(x.get("concept_key") or ""), str(x.get("cluster_id"))))
    return out, merge_count


def recurrence_groups(clusters: Sequence[Dict[str, Any]], by_candidate: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: List[Dict[str, Any]] = []
    for c in clusters:
        e = enrich_cluster(c, by_candidate)
        if e["distinct_log_count"] < 2 or e["distinct_source_hash_count"] < 2:
            continue
        members = [by_candidate[cid] for cid in e.get("member_candidate_ids") or [] if cid in by_candidate]
        raw_variants = []
        seen: set[Tuple[str, str]] = set()
        for m in sorted(members, key=lambda x: (str(x.get("log_number")), str(x.get("candidate_id")))):
            key = (str(m.get("log_number")), normalized_ws(m.get("raw_source_text") or ""))
            if key in seen:
                continue
            seen.add(key)
            raw_variants.append({
                "candidate_id": m.get("candidate_id"),
                "log_number": m.get("log_number"),
                "serial_number": m.get("serial_number"),
                "kind": m.get("kind"),
                "raw_source_text": m.get("raw_source_text"),
                "source_sha256": m.get("source_sha256"),
                "source_path": m.get("source_path"),
                "raw_transcription_path": m.get("raw_transcription_path"),
            })
        e.update({
            "group_id": "rg_" + hashlib.sha256((str(e.get("lane")) + "\n" + "\n".join(sorted(e.get("member_candidate_ids") or []))).encode("utf-8")).hexdigest()[:16],
            "raw_variants": raw_variants,
            "status": "provisional_recurrence_python_counted_not_approved",
        })
        groups.append(e)
    groups.sort(key=lambda g: (
        -int(g.get("distinct_serial_count") or 0),
        -int(g.get("distinct_log_count") or 0),
        -int(g.get("candidate_count") or 0),
        str(g.get("lane")),
        str(g.get("concept_label")),
    ))
    return groups


def stage1_batch_cache_dir(output_root: Path, lane: str, batch_index: int) -> Path:
    return output_root / "stage1_batches" / lane / f"batch_{batch_index:04d}"


def merge_batch_cache_dir(output_root: Path, round_num: int, lane: str, batch_index: int) -> Path:
    return output_root / "merge_rounds" / f"round_{round_num:02d}" / lane / f"batch_{batch_index:04d}"


def cache_matches(meta_path: Path, parsed_path: Path, prompt_sha: str, model_info: Dict[str, Any], num_ctx: int, num_predict: int) -> bool:
    if not meta_path.exists() or not parsed_path.exists():
        return False
    try:
        meta = load_json(meta_path)
        return (
            meta.get("prompt_sha256") == prompt_sha
            and meta.get("model", {}).get("requested_model") == model_info.get("requested_model")
            and int(meta.get("num_ctx") or -1) == int(num_ctx)
            and int(meta.get("num_predict") or -1) == int(num_predict)
            and (not model_info.get("digest") or meta.get("model", {}).get("digest") == model_info.get("digest"))
        )
    except Exception:
        return False


def render_summary(
    output_root: Path,
    input_manifest: Dict[str, Any],
    all_candidates: Sequence[Dict[str, Any]],
    eligible: Sequence[Dict[str, Any]],
    excluded: Sequence[Dict[str, Any]],
    stage1_batches: int,
    stage1_failures: int,
    initial_clusters: Sequence[Dict[str, Any]],
    final_clusters: Sequence[Dict[str, Any]],
    recurring: Sequence[Dict[str, Any]],
    merge_round_stats: Sequence[Dict[str, Any]],
) -> None:
    lane_counts = Counter(str(c.get("reasoning_lane")) for c in eligible)
    recurring_lane_counts = Counter(str(g.get("lane")) for g in recurring)
    lines = [
        f"# Nova DRL Large-Scale Batched Corpus Reasoner v{VERSION}",
        "",
        f"Input records:                     {int(input_manifest.get('record_count') or 0)}",
        f"Input evidence-backed candidates:  {len(all_candidates)}",
        f"32B reasoning-eligible candidates: {len(eligible)}",
        f"Audit-preserved excluded candidates:{len(excluded):>5}",
        f"Stage-1 batches:                   {stage1_batches}",
        f"Stage-1 failed/fallback batches:   {stage1_failures}",
        f"Initial provisional clusters:      {len(initial_clusters)}",
        f"Final hierarchical clusters:       {len(final_clusters)}",
        f"Recurring groups (>=2 logs/hashes):{len(recurring):>5}",
        "Accepted repair facts:             0",
        "Qdrant entries created:            0",
        "",
        "ELIGIBLE CANDIDATES BY LANE",
        "---------------------------",
    ]
    for lane, count in sorted(lane_counts.items()):
        lines.append(f"{lane:24s} {count}")
    lines += ["", "MERGE ROUND STATS", "-----------------"]
    for st in merge_round_stats:
        lines.append(
            f"round {st.get('round'):>2}: input_clusters={st.get('input_cluster_count')} batches={st.get('batch_count')} "
            f"accepted_merge_proposals={st.get('accepted_merge_proposal_count')} merge_components={st.get('merge_component_count')} "
            f"output_clusters={st.get('output_cluster_count')} failed_batches={st.get('failed_batch_count')}"
        )
    lines += ["", "RECURRING GROUPS BY LANE", "------------------------"]
    for lane, count in sorted(recurring_lane_counts.items()):
        lines.append(f"{lane:24s} {count}")

    lane_order = ["repairs", "components", "diagnostics", "testing_process", "terminology", "customer_requirements", "other"]
    for lane in lane_order:
        groups = [g for g in recurring if g.get("lane") == lane]
        if not groups:
            continue
        lines += ["", f"TOP {lane.upper()} PATTERNS (PROVISIONAL, NOT APPROVED)", "-" * (len(lane) + 42)]
        for g in groups[:40]:
            label = normalized_ws(g.get("concept_label") or g.get("concept_key") or "")
            lines.append(
                f"{g.get('group_id')} | serials={g.get('distinct_serial_count')} | logs={g.get('distinct_log_count')} | "
                f"candidates={g.get('candidate_count')} | {label}"
            )
            for rv in (g.get("raw_variants") or [])[:4]:
                raw = normalized_ws(rv.get("raw_source_text") or "")
                lines.append(f"  {rv.get('log_number')} | {rv.get('serial_number') or '?'} | {raw}")
    lines += [
        "",
        "POLICY",
        "------",
        "Original source Travelers and v1.3.5.1 raw transcriptions modified: NO",
        "v1.3.6.1 evidence quotes normalized/replaced: NO",
        "32B labels treated as approved facts: NO",
        "Recurrence counts owned by Python: YES",
        "Minimum recurrence: 2 distinct logs AND 2 distinct source hashes",
        "Automatic human approval: NO",
        "Qdrant writes: OFF",
        "",
        "Full provenance: recurring_patterns_v1_3_7_0.json",
        "All final clusters: final_cluster_ledger_v1_3_7_0.json",
        "Excluded-but-preserved metadata/unclear candidates: reasoning_exclusion_audit_v1_3_7_0.json",
    ]
    (output_root / "large_scale_reasoning_summary_v1_3_7_0.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Nova DRL Large-Scale Batched Corpus Reasoner v{VERSION}")
    parser.add_argument("--input-root", default=str(DEFAULT_INPUT_ROOT), help=f"Completed v1.3.6.1 prospect-only output (default: {DEFAULT_INPUT_ROOT})")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help=f"Writable output root (default: {DEFAULT_OUTPUT_ROOT})")
    parser.add_argument("--reason-model", default=DEFAULT_REASON_MODEL)
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--stage1-num-predict", type=int, default=6144)
    parser.add_argument("--merge-num-predict", type=int, default=4096)
    parser.add_argument("--stage1-max-chars", type=int, default=22000, help="Dynamic batch input-character target before prompt overhead")
    parser.add_argument("--merge-max-chars", type=int, default=22000)
    parser.add_argument("--merge-overlap", type=int, default=3, help="Cluster summaries carried into adjacent merge batches")
    parser.add_argument("--max-merge-rounds", type=int, default=3)
    parser.add_argument("--json-retries", type=int, default=2, help="Retries after malformed JSON; failures fall back safely")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--plan-only", action="store_true", help="Build/print the large-scale batching plan without 32B calls")
    parser.add_argument("--force-stage1", action="store_true")
    parser.add_argument("--force-merge", action="store_true")
    parser.add_argument("--no-stop-prospector", action="store_true")
    args = parser.parse_args()

    input_root = Path(args.input_root)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    try:
        input_manifest = validate_input_manifest(input_root)
        all_candidates = load_candidates(input_root, input_manifest)
        eligible, excluded = prepare_reasoning_candidates(all_candidates)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    by_candidate = {str(c["candidate_id"]): c for c in eligible}
    stage1_batches = pack_candidates(eligible, max(4000, int(args.stage1_max_chars)))
    lane_batch_counts = Counter(str(b[0]["reasoning_lane"]) for b in stage1_batches if b)

    save_json(output_root / "reasoning_exclusion_audit_v1_3_7_0.json", {
        "reasoner_version": VERSION,
        "excluded_candidate_count": len(excluded),
        "candidates": excluded,
        "automatic_fact_acceptance": False,
        "qdrant_entries_created": 0,
    })
    plan = {
        "reasoner_version": VERSION,
        "input_root": str(input_root),
        "input_manifest_sha256": sha256_text((input_root / "sort_manifest_v1_3_6_1.json").read_text(encoding="utf-8")),
        "candidate_ledger_sha256": sha256_text((input_root / "candidate_ledger_v1_3_6_1.jsonl").read_text(encoding="utf-8")),
        "input_record_count": int(input_manifest.get("record_count") or 0),
        "input_candidate_count": len(all_candidates),
        "eligible_candidate_count": len(eligible),
        "excluded_candidate_count": len(excluded),
        "lane_candidate_counts": dict(sorted(Counter(str(c["reasoning_lane"]) for c in eligible).items())),
        "stage1_batch_count": len(stage1_batches),
        "stage1_batches_by_lane": dict(sorted(lane_batch_counts.items())),
        "stage1_max_chars": args.stage1_max_chars,
        "merge_max_chars": args.merge_max_chars,
        "max_merge_rounds": args.max_merge_rounds,
        "automatic_fact_acceptance": False,
        "accepted_fact_count": 0,
        "qdrant_write_enabled": False,
        "qdrant_entries_created": 0,
    }
    save_json(output_root / "large_scale_reasoning_plan_v1_3_7_0.json", plan)

    print(f"# Nova DRL Large-Scale Batched Corpus Reasoner v{VERSION}")
    print(f"Input records:       {plan['input_record_count']}")
    print(f"Input candidates:    {len(all_candidates)}")
    print(f"Eligible for 32B:    {len(eligible)}")
    print(f"Audit exclusions:    {len(excluded)}")
    print(f"Stage-1 batches:     {len(stage1_batches)}")
    for lane, count in sorted(lane_batch_counts.items()):
        print(f"  {lane:22s} {count} batches")
    print("Accepted facts:      0")
    print("Qdrant:              OFF")

    if args.plan_only:
        print("Status:              PLAN ONLY (no model calls)")
        print(f"Plan: {output_root / 'large_scale_reasoning_plan_v1_3_7_0.json'}")
        return 0

    model_info = get_ollama_model_info(args.reason_model)
    if model_info.get("available") is False:
        print(f"ERROR: reasoning model not installed: {args.reason_model}", file=sys.stderr)
        return 3
    if not args.no_stop_prospector:
        stop_ollama_model(DEFAULT_PROSPECT_MODEL)

    all_stage1_clusters: List[Dict[str, Any]] = []
    all_stage1_rejections: List[Dict[str, Any]] = []
    stage1_run_meta: List[Dict[str, Any]] = []
    stage1_failures = 0
    lane_seen_index: Counter[str] = Counter()

    for global_idx, batch in enumerate(stage1_batches, 1):
        lane = str(batch[0]["reasoning_lane"])
        lane_seen_index[lane] += 1
        lane_idx = lane_seen_index[lane]
        cache_dir = stage1_batch_cache_dir(output_root, lane, lane_idx)
        cache_dir.mkdir(parents=True, exist_ok=True)
        input_payload = {"lane": lane, "candidate_ids": [c["candidate_id"] for c in batch], "candidates": [compact_candidate(c) for c in batch]}
        save_json(cache_dir / "input.json", input_payload)
        prompt = make_stage1_prompt(batch)
        prompt_sha = sha256_text(prompt)
        parsed_path = cache_dir / "parsed.json"
        meta_path = cache_dir / "run.json"
        parsed: Optional[Any] = None
        run_action = "model_run"
        attempts: List[Dict[str, Any]] = []
        if not args.force_stage1 and cache_matches(meta_path, parsed_path, prompt_sha, model_info, args.num_ctx, args.stage1_num_predict):
            try:
                parsed = load_json(parsed_path)
                run_action = "reused_existing"
            except Exception:
                parsed = None
        if parsed is None:
            parsed, attempts = call_json_with_retries(
                args.reason_model, prompt, args.num_ctx, args.stage1_num_predict, args.timeout, args.json_retries, cache_dir
            )
            if parsed is not None:
                save_json(parsed_path, parsed)
            else:
                stage1_failures += 1
                parsed = {"clusters": []}
                run_action = "model_failed_fallback_singletons"
            save_json(meta_path, {
                "reasoner_version": VERSION,
                "run_action": run_action,
                "prompt_sha256": prompt_sha,
                "model": model_info,
                "num_ctx": args.num_ctx,
                "num_predict": args.stage1_num_predict,
                "candidate_count": len(batch),
                "attempts": attempts,
                "automatic_fact_acceptance": False,
                "qdrant_entries_created": 0,
            })
        clusters, rejected = validate_stage1_clusters(parsed, batch)
        all_stage1_clusters.extend(clusters)
        all_stage1_rejections.extend({"lane": lane, "batch_index": lane_idx, **x} for x in rejected)
        stage1_run_meta.append({"lane": lane, "batch_index": lane_idx, "candidate_count": len(batch), "cluster_count": len(clusters), "rejected_group_count": len(rejected), "run_action": run_action})
        print(f"[stage1 {global_idx}/{len(stage1_batches)}] {lane} | candidates {len(batch)} | clusters {len(clusters)} | rejected {len(rejected)} | {run_action}")

    initial_clusters = consolidate_exact_model_keys(all_stage1_clusters)
    save_json(output_root / "stage1_cluster_ledger_v1_3_7_0.json", {
        "reasoner_version": VERSION,
        "raw_stage1_cluster_count": len(all_stage1_clusters),
        "after_exact_model_key_consolidation_count": len(initial_clusters),
        "clusters": initial_clusters,
    })
    save_json(output_root / "stage1_rejected_model_groups_v1_3_7_0.json", all_stage1_rejections)
    save_json(output_root / "stage1_run_summary_v1_3_7_0.json", stage1_run_meta)

    current_clusters = [dict(c) for c in initial_clusters]
    merge_round_stats: List[Dict[str, Any]] = []
    all_merge_rejections: List[Dict[str, Any]] = []

    for round_num in range(1, max(0, int(args.max_merge_rounds)) + 1):
        merge_batches = pack_cluster_summaries(current_clusters, by_candidate, max(4000, int(args.merge_max_chars)), max(0, int(args.merge_overlap)))
        proposals: List[Dict[str, Any]] = []
        round_failed = 0
        lane_merge_idx: Counter[str] = Counter()
        for global_idx, batch in enumerate(merge_batches, 1):
            lane = str(batch[0].get("lane"))
            lane_merge_idx[lane] += 1
            lane_idx = lane_merge_idx[lane]
            cache_dir = merge_batch_cache_dir(output_root, round_num, lane, lane_idx)
            cache_dir.mkdir(parents=True, exist_ok=True)
            save_json(cache_dir / "input.json", {"lane": lane, "cluster_ids": [c["cluster_id"] for c in batch], "clusters": [compact_cluster_summary(c, by_candidate) for c in batch]})
            prompt = make_merge_prompt(batch, by_candidate)
            prompt_sha = sha256_text(prompt)
            parsed_path = cache_dir / "parsed.json"
            meta_path = cache_dir / "run.json"
            parsed: Optional[Any] = None
            run_action = "model_run"
            attempts: List[Dict[str, Any]] = []
            if not args.force_merge and cache_matches(meta_path, parsed_path, prompt_sha, model_info, args.num_ctx, args.merge_num_predict):
                try:
                    parsed = load_json(parsed_path)
                    run_action = "reused_existing"
                except Exception:
                    parsed = None
            if parsed is None:
                parsed, attempts = call_json_with_retries(
                    args.reason_model, prompt, args.num_ctx, args.merge_num_predict, args.timeout, args.json_retries, cache_dir
                )
                if parsed is not None:
                    save_json(parsed_path, parsed)
                else:
                    parsed = {"merge_groups": []}
                    round_failed += 1
                    run_action = "model_failed_no_merge"
                save_json(meta_path, {
                    "reasoner_version": VERSION,
                    "run_action": run_action,
                    "prompt_sha256": prompt_sha,
                    "model": model_info,
                    "num_ctx": args.num_ctx,
                    "num_predict": args.merge_num_predict,
                    "cluster_count": len(batch),
                    "attempts": attempts,
                    "automatic_fact_acceptance": False,
                    "qdrant_entries_created": 0,
                })
            accepted, rejected = validate_merge_output(parsed, batch)
            proposals.extend(accepted)
            all_merge_rejections.extend({"round": round_num, "lane": lane, "batch_index": lane_idx, **x} for x in rejected)
            print(f"[merge r{round_num} {global_idx}/{len(merge_batches)}] {lane} | clusters {len(batch)} | proposals {len(accepted)} | rejected {len(rejected)} | {run_action}")

        next_clusters, merge_components = apply_merge_proposals(current_clusters, proposals)
        stat = {
            "round": round_num,
            "input_cluster_count": len(current_clusters),
            "batch_count": len(merge_batches),
            "accepted_merge_proposal_count": len(proposals),
            "merge_component_count": merge_components,
            "output_cluster_count": len(next_clusters),
            "failed_batch_count": round_failed,
        }
        merge_round_stats.append(stat)
        save_json(output_root / f"merge_round_{round_num:02d}_stats_v1_3_7_0.json", stat)
        current_clusters = next_clusters
        if merge_components == 0:
            break

    final_clusters = [enrich_cluster(c, by_candidate) for c in current_clusters]
    final_clusters.sort(key=lambda c: (str(c.get("lane")), -int(c.get("distinct_serial_count") or 0), -int(c.get("distinct_log_count") or 0), str(c.get("concept_label"))))
    recurring = recurrence_groups(current_clusters, by_candidate)

    save_json(output_root / "final_cluster_ledger_v1_3_7_0.json", {
        "reasoner_version": VERSION,
        "cluster_count": len(final_clusters),
        "clusters": final_clusters,
        "automatic_fact_acceptance": False,
        "qdrant_entries_created": 0,
    })
    save_json(output_root / "recurring_patterns_v1_3_7_0.json", {
        "reasoner_version": VERSION,
        "minimum_distinct_logs": 2,
        "minimum_distinct_source_hashes": 2,
        "recurring_group_count": len(recurring),
        "groups": recurring,
        "automatic_fact_acceptance": False,
        "accepted_fact_count": 0,
        "qdrant_entries_created": 0,
    })
    save_json(output_root / "merge_rejected_model_groups_v1_3_7_0.json", all_merge_rejections)

    render_summary(output_root, input_manifest, all_candidates, eligible, excluded, len(stage1_batches), stage1_failures, initial_clusters, final_clusters, recurring, merge_round_stats)

    reasoner_manifest = {
        **plan,
        "status": "complete_provisional_large_scale_reasoning_not_approved",
        "reasoning_model": model_info,
        "stage1_prompt_sha256": sha256_text(STAGE1_PROMPT),
        "merge_prompt_sha256": sha256_text(MERGE_PROMPT),
        "stage1_failed_batch_count": stage1_failures,
        "initial_cluster_count": len(initial_clusters),
        "final_cluster_count": len(final_clusters),
        "recurring_group_count": len(recurring),
        "merge_round_stats": merge_round_stats,
        "rejected_stage1_model_group_count": len(all_stage1_rejections),
        "rejected_merge_model_group_count": len(all_merge_rejections),
        "global_policy": {
            "source_images_modified": False,
            "raw_transcriptions_modified": False,
            "candidate_raw_evidence_modified": False,
            "model_labels_are_provisional_only": True,
            "recurrence_count_authority": "python",
            "minimum_distinct_logs": 2,
            "minimum_distinct_source_hashes": 2,
            "automatic_fact_acceptance": False,
            "accepted_fact_count": 0,
            "qdrant_write_enabled": False,
            "qdrant_entries_created": 0,
        },
    }
    save_json(output_root / "reasoner_manifest_v1_3_7_0.json", reasoner_manifest)

    print()
    print(f"Stage-1 failed/fallback batches: {stage1_failures}")
    print(f"Initial clusters:                {len(initial_clusters)}")
    print(f"Final clusters:                  {len(final_clusters)}")
    print(f"Recurring groups:                {len(recurring)}")
    print("Accepted repair facts:           0")
    print("Qdrant entries created:          0")
    print(f"Summary: {output_root / 'large_scale_reasoning_summary_v1_3_7_0.txt'}")
    print(f"Patterns: {output_root / 'recurring_patterns_v1_3_7_0.json'}")
    print(f"Manifest: {output_root / 'reasoner_manifest_v1_3_7_0.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
