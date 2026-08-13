#!/usr/bin/env python3
"""
Nova DRL Traveler Corpus Prospector + Sorter v1.3.6.0

Purpose
-------
Sort only AFTER v1.3.5.1 has completed whole-Traveler acquisition.

Roles
-----
1) Qwen3-VL 8B text pass = high-recall prospector. It proposes exact raw phrases
   that may matter; it does not approve facts.
2) Qwen2.5 32B = cross-record reasoning. It proposes group memberships using only
   candidate IDs; it does not rewrite evidence or determine recurrence counts.
3) Python = evidence/accounting guardrail. It verifies candidate support against
   immutable raw transcriptions, counts distinct logs/source hashes, enforces the
   >=2-log recurrence rule, masks the global Hours-in-Final-Testing field from
   knowledge sorting, and writes provenance-rich provisional outputs.

Non-negotiable behavior
-----------------------
- Source Traveler images and v1.3.5.1 raw transcriptions are never modified.
- No Qdrant writes.
- No automatic fact approval.
- Unsupported model quotes are retained only in an audit rejection file.
- Recurrent groups require at least two distinct DRL logs AND two distinct source
  hashes; the LLM is not trusted to count.
- "Hours in Final Testing" remains in immutable source evidence but is suppressed
  from the sort view and cannot become a candidate.
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

VERSION = "1.3.6.0"
REQUIRED_COLLECTOR_VERSION = "1.3.5.1"
DEFAULT_MANIFEST = Path("/opt/nova-drl/output/whole_traveler_corpus_v1_3_5_1/corpus_manifest_v1_3_5_1.json")
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/traveler_corpus_sort_v1_3_6_0")
DEFAULT_PROSPECT_MODEL = "qwen3-vl-drl:8b-q8-16k"
DEFAULT_REASON_MODEL = "qwen25-drl:32b-16k"
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"

ALLOWED_KINDS = {
    "customer_requirement",
    "repair_or_service",
    "component_or_part",
    "diagnostic_or_failure",
    "testing_or_process",
    "shop_term_or_abbreviation",
    "part_number_or_identifier",
    "unclear_ocr",
    "other",
}
UNUSUAL_KINDS = {
    "shop_term_or_abbreviation",
    "part_number_or_identifier",
    "testing_or_process",
    "unclear_ocr",
}
RECURRING_GROUP_TYPES = {
    "boilerplate_or_customer_requirement",
    "repair_or_service",
    "component_or_part",
    "diagnostic_or_failure",
}

PROSPECT_PROMPT = """You are the HIGH-RECALL PROSPECTOR for one raw DRL Traveler transcription.

Your only job is to surface phrases from this one record that may matter later.
Do NOT approve facts, normalize wording, expand abbreviations, infer missing information,
or decide what is recurring across repairs.

Return JSON only using exactly this shape:
{
  "log_number": "<log>",
  "candidates": [
    {"kind": "<kind>", "raw_quote": "<verbatim text copied from the transcription>"}
  ]
}

Allowed kind values:
- customer_requirement
- repair_or_service
- component_or_part
- diagnostic_or_failure
- testing_or_process
- shop_term_or_abbreviation
- part_number_or_identifier
- unclear_ocr
- other

RULES:
- Favor recall. Surface all potentially meaningful handwritten/event-specific text,
  repair/service wording, symptoms, observed faults, causal or suspected-causal wording,
  component/part wording, explicit quantities/identifiers, unusual DRL shop language,
  abbreviations, test names, and unclear OCR worth preserving.
- raw_quote MUST be copied from the supplied transcription. Do not paraphrase it.
- Keep strange spellings, punctuation, apostrophes, capitalization, and abbreviations.
- Do not use outside knowledge.
- Do not invent quantities.
- Do not include generic printed form labels merely because they are visible.
- The field "Hours in Final Testing" has already been masked by deterministic policy.
  Do not reconstruct or discuss it.
- A phrase may be assigned more than one kind only when the same exact raw phrase truly
  serves both roles; otherwise choose the closest kind.
"""

REASON_PROMPT = """You are the CROSS-RECORD REASONING layer for a DRL Traveler candidate ledger.
The ledger contains only evidence-backed candidate IDs and their exact raw source wording.

Return JSON only using exactly this shape:
{
  "recurring_groups": [
    {
      "group_type": "boilerplate_or_customer_requirement|repair_or_service|component_or_part|diagnostic_or_failure",
      "label": "short provisional label that does not expand unexplained abbreviations",
      "member_candidate_ids": ["candidate-id", "candidate-id"]
    }
  ],
  "unique_high_value_candidate_ids": ["candidate-id"],
  "human_review_candidate_ids": ["candidate-id"]
}

RULES:
- Use ONLY candidate IDs present in the ledger. Never invent an ID.
- Group candidates only when their raw wording supports a genuinely similar concept.
- Do not merge different axes merely because both mention drift or motion.
- Do not turn repair work into a diagnosed failure unless the raw wording states a symptom,
  observed fault, cause, or suspected cause.
- Do not expand or redefine unexplained DRL abbreviations or shop terms.
- Preserve unusual wording by referring to the candidate IDs; the Python layer will render
  the exact raw variants.
- "Recurring" means two or more different repair logs. Python will enforce the count, so
  include only groups you believe span multiple logs.
- Mark unique but potentially valuable shop language, part identifiers, test names, and
  unusual wording in unique_high_value_candidate_ids.
- Mark unclear, conflicting, suspicious, or ambiguous OCR in human_review_candidate_ids.
- Do not use outside knowledge.
- Do not discuss Hours in Final Testing.
"""


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


def call_ollama_text(model: str, prompt: str, num_ctx: int, num_predict: int, timeout: int) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
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
        # Conservative fallback: select the outermost JSON object only.
        first = stripped.find("{")
        last = stripped.rfind("}")
        if first >= 0 and last > first:
            return json.loads(stripped[first:last + 1])
        raise


# The global policy says the Hours-in-Final-Testing field is raw/audit only and must
# not influence adjacent Final O.K. interpretation. Mask just the field name/value,
# while retaining the rest of that source line for audit-neutral sorting.
_HOURS_FINAL_RE = re.compile(r"(?i)hours\s+in\s+final\s+testing\s*:?\s*(?:\d[0-9A-Za-z+._-]*)?")


def mask_global_audit_fields(raw: str) -> Tuple[str, List[Dict[str, str]]]:
    suppressed: List[Dict[str, str]] = []

    def repl(match: re.Match[str]) -> str:
        suppressed.append({"policy": "hours_in_final_testing_raw_audit_only", "raw": match.group(0)})
        return "[AUDIT FIELD SUPPRESSED FROM KNOWLEDGE SORT]"

    return _HOURS_FINAL_RE.sub(repl, raw), suppressed


def normalized_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _token_boundary_ok(source: str, start: int, end: int, quote: str) -> bool:
    """Reject partial-token matches such as `Blue Schmoo` inside `Blue Schmoo's`."""
    continuation = set("_'’")
    if quote and quote[0].isalnum() and start > 0:
        prev = source[start - 1]
        if prev.isalnum() or prev in continuation:
            return False
    if quote and quote[-1].isalnum() and end < len(source):
        nxt = source[end]
        if nxt.isalnum() or nxt in continuation:
            return False
    return True


def find_supported_source_slice(source: str, proposed_quote: str) -> Optional[Tuple[str, str]]:
    """Return (support_mode, exact_source_slice) using exact or whitespace-only normalization.

    No letters, punctuation, apostrophes, digits, or abbreviations are normalized. Partial
    token matches are rejected, so `Blue Schmoo` cannot silently stand in for
    `Blue Schmoo's`.
    """
    if not proposed_quote:
        return None
    pos0 = source.find(proposed_quote)
    while pos0 >= 0:
        end0 = pos0 + len(proposed_quote)
        if _token_boundary_ok(source, pos0, end0, proposed_quote):
            return ("exact", proposed_quote)
        pos0 = source.find(proposed_quote, pos0 + 1)

    # Build a whitespace-collapsed representation plus an index map into the source.
    norm_chars: List[str] = []
    index_map: List[int] = []
    in_ws = False
    for idx, ch in enumerate(source):
        if ch.isspace():
            if not in_ws:
                norm_chars.append(" ")
                index_map.append(idx)
                in_ws = True
        else:
            norm_chars.append(ch)
            index_map.append(idx)
            in_ws = False
    norm_source = "".join(norm_chars).strip()
    norm_quote = normalized_whitespace(proposed_quote)
    pos = norm_source.find(norm_quote)
    if pos < 0:
        return None

    # Account for .strip() possibly removing a leading collapsed space.
    raw_norm = "".join(norm_chars)
    offset = raw_norm.find(norm_source)
    start_norm = pos + max(0, offset)
    end_norm = start_norm + len(norm_quote) - 1
    if start_norm >= len(index_map) or end_norm >= len(index_map):
        return None
    start_src = index_map[start_norm]
    end_src = index_map[end_norm] + 1
    actual = source[start_src:end_src]
    if not _token_boundary_ok(source, start_src, end_src, proposed_quote):
        return None
    return ("whitespace_only", actual)


def candidate_id(log_number: str, source_sha: str, raw_source_text: str, kind: str) -> str:
    basis = f"{log_number}\n{source_sha}\n{kind}\n{raw_source_text}"
    return "c_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def validate_collector_manifest(manifest: Dict[str, Any], allow_incomplete: bool = False) -> List[Dict[str, Any]]:
    if str(manifest.get("collector_version")) != REQUIRED_COLLECTOR_VERSION:
        raise ValueError(
            f"Expected collector v{REQUIRED_COLLECTOR_VERSION}, got {manifest.get('collector_version')!r}."
        )
    if manifest.get("inventory_only"):
        raise ValueError("Collector manifest is inventory-only; raw transcriptions were not acquired.")
    if manifest.get("interrupted") and not allow_incomplete:
        raise ValueError("Collector manifest is marked interrupted; complete acquisition before sorting.")

    records = list(manifest.get("records") or [])
    if not records:
        raise ValueError("Collector manifest contains no records.")
    bad = [r for r in records if r.get("vision_status") != "ok" or not r.get("raw_transcription_path")]
    if bad and not allow_incomplete:
        logs = ", ".join(str(r.get("log_number")) for r in bad)
        raise ValueError(f"Acquisition is incomplete for log(s): {logs}")
    return [r for r in records if r.get("vision_status") == "ok" and r.get("raw_transcription_path")]


def load_and_verify_raw(record: Dict[str, Any]) -> Tuple[str, str]:
    path = Path(record["raw_transcription_path"])
    if not path.exists():
        raise FileNotFoundError(f"Raw transcription not found: {path}")
    raw = path.read_text(encoding="utf-8", errors="strict")
    sha = sha256_text(raw)
    expected = record.get("raw_transcription_sha256")
    if expected and sha != expected:
        raise ValueError(f"Raw transcription SHA mismatch for log {record.get('log_number')}: {path}")
    return raw, sha


def prospect_cache_paths(output_root: Path, record: Dict[str, Any]) -> Dict[str, Path]:
    log = str(record.get("log_number"))
    rid = str(record.get("record_id") or "unknown")
    d = output_root / "records" / log / rid
    return {
        "dir": d,
        "raw_response": d / "prospector_raw_response.txt",
        "validated": d / "prospector_candidates.json",
        "metadata": d / "prospector_run.json",
    }


def make_prospect_prompt(log_number: str, analysis_text: str) -> str:
    return f"{PROSPECT_PROMPT}\n\nDRL LOG: {log_number}\nRAW TRANSCRIPTION FOR PROSPECTING:\n{analysis_text}\n"


def validate_prospector_output(
    parsed: Any,
    record: Dict[str, Any],
    analysis_text: str,
    raw_transcription_sha: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    accepted_by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    rejected: List[Dict[str, Any]] = []
    log = str(record.get("log_number"))
    source_sha = str(record.get("source_sha256") or "")

    if not isinstance(parsed, dict) or not isinstance(parsed.get("candidates"), list):
        raise ValueError("Prospector response does not contain a candidates array.")

    for index, item in enumerate(parsed["candidates"]):
        if not isinstance(item, dict):
            rejected.append({"index": index, "reason": "candidate_not_object", "model_item": item})
            continue
        kind = str(item.get("kind") or "").strip()
        quote = str(item.get("raw_quote") or "")
        if kind not in ALLOWED_KINDS:
            rejected.append({"index": index, "reason": "invalid_kind", "model_item": item})
            continue
        if "hours in final testing" in quote.lower():
            rejected.append({"index": index, "reason": "global_audit_field_suppressed", "model_item": item})
            continue
        support = find_supported_source_slice(analysis_text, quote)
        if support is None:
            rejected.append({"index": index, "reason": "raw_quote_not_supported_by_transcription", "model_item": item})
            continue
        mode, actual = support
        key = (actual, kind)
        if key in accepted_by_key:
            continue
        cid = candidate_id(log, source_sha, actual, kind)
        accepted_by_key[key] = {
            "candidate_id": cid,
            "log_number": log,
            "record_id": record.get("record_id"),
            "source_sha256": source_sha,
            "raw_transcription_sha256": raw_transcription_sha,
            "kind": kind,
            "raw_source_text": actual,
            "support_mode": mode,
            "source_path": record.get("source_path"),
            "raw_transcription_path": record.get("raw_transcription_path"),
            "status": "evidence_backed_candidate_not_approved",
        }
    return list(accepted_by_key.values()), rejected


def prospect_one(
    record: Dict[str, Any],
    output_root: Path,
    model_info: Dict[str, Any],
    args: argparse.Namespace,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    raw, raw_sha = load_and_verify_raw(record)
    analysis_text, suppressed = mask_global_audit_fields(raw)
    prompt = make_prospect_prompt(str(record.get("log_number")), analysis_text)
    prompt_sha = sha256_text(prompt)
    paths = prospect_cache_paths(output_root, record)
    paths["dir"].mkdir(parents=True, exist_ok=True)

    if not args.force_prospect and paths["metadata"].exists() and paths["validated"].exists():
        try:
            meta = load_json(paths["metadata"])
            val = load_json(paths["validated"])
            if (
                meta.get("raw_transcription_sha256") == raw_sha
                and meta.get("prompt_sha256") == prompt_sha
                and meta.get("model", {}).get("requested_model") == args.prospect_model
                and int(meta.get("num_ctx") or -1) == int(args.prospect_num_ctx)
                and int(meta.get("num_predict") or -1) == int(args.prospect_num_predict)
                and (not model_info.get("digest") or meta.get("model", {}).get("digest") == model_info.get("digest"))
            ):
                return list(val.get("accepted_candidates") or []), list(val.get("rejected_candidates") or []), {
                    **meta,
                    "run_action": "reused_existing",
                }
        except Exception:
            pass

    started = time.time()
    response = call_ollama_text(
        args.prospect_model,
        prompt,
        args.prospect_num_ctx,
        args.prospect_num_predict,
        args.timeout,
    )
    paths["raw_response"].write_text(response, encoding="utf-8")
    parsed = parse_json_response(response)
    accepted, rejected = validate_prospector_output(parsed, record, analysis_text, raw_sha)
    save_json(paths["validated"], {
        "log_number": record.get("log_number"),
        "accepted_candidates": accepted,
        "rejected_candidates": rejected,
        "global_policy_suppressed_segments": suppressed,
    })
    meta = {
        "sorter_version": VERSION,
        "run_action": "model_run",
        "log_number": record.get("log_number"),
        "record_id": record.get("record_id"),
        "raw_transcription_sha256": raw_sha,
        "prompt_sha256": prompt_sha,
        "model": model_info,
        "num_ctx": args.prospect_num_ctx,
        "num_predict": args.prospect_num_predict,
        "elapsed_seconds": round(time.time() - started, 3),
        "accepted_candidate_count": len(accepted),
        "rejected_candidate_count": len(rejected),
        "global_policy_suppressed_count": len(suppressed),
        "automatic_fact_acceptance": False,
        "qdrant_entries_created": 0,
    }
    save_json(paths["metadata"], meta)
    return accepted, rejected, meta


def repeated_line_inventory(records_with_raw: Sequence[Tuple[Dict[str, Any], str]]) -> List[Dict[str, Any]]:
    """Deterministically inventory exact repeated nontrivial lines across distinct logs."""
    by_norm: Dict[str, Dict[str, Any]] = {}
    for record, raw in records_with_raw:
        analysis_text, _ = mask_global_audit_fields(raw)
        log = str(record.get("log_number"))
        for line in analysis_text.splitlines():
            clean = line.strip()
            if len(clean) < 18 or clean.startswith("[AUDIT FIELD SUPPRESSED"):
                continue
            norm = normalized_whitespace(clean).lower()
            slot = by_norm.setdefault(norm, {"raw_variants": Counter(), "logs": set()})
            slot["raw_variants"][clean] += 1
            slot["logs"].add(log)
    result: List[Dict[str, Any]] = []
    for norm, slot in by_norm.items():
        logs = sorted(slot["logs"])
        if len(logs) < 2:
            continue
        variants = [x for x, _ in slot["raw_variants"].most_common()]
        result.append({
            "normalized_for_counting_only": norm,
            "raw_variants": variants,
            "distinct_log_count": len(logs),
            "logs": logs,
        })
    result.sort(key=lambda x: (-x["distinct_log_count"], x["normalized_for_counting_only"]))
    return result


def compact_reasoning_ledger(candidates: Sequence[Dict[str, Any]], repeated_lines: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "candidates": [
            {
                "id": c["candidate_id"],
                "log": c["log_number"],
                "kind": c["kind"],
                "raw": c["raw_source_text"],
            }
            for c in candidates
        ],
        "deterministic_repeated_lines": [
            {
                "logs": x["logs"],
                "raw_variants": x["raw_variants"][:3],
            }
            for x in repeated_lines[:80]
        ],
    }


def validate_reasoning_output(
    parsed: Any,
    candidates: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str], List[str]]:
    if not isinstance(parsed, dict):
        raise ValueError("Reasoning response is not a JSON object.")
    by_id = {c["candidate_id"]: c for c in candidates}
    accepted_groups: List[Dict[str, Any]] = []
    rejected_groups: List[Dict[str, Any]] = []

    for idx, group in enumerate(parsed.get("recurring_groups") or []):
        if not isinstance(group, dict):
            rejected_groups.append({"index": idx, "reason": "group_not_object", "model_group": group})
            continue
        gtype = str(group.get("group_type") or "")
        ids = [str(x) for x in (group.get("member_candidate_ids") or [])]
        unknown = [x for x in ids if x not in by_id]
        valid_ids = list(dict.fromkeys(x for x in ids if x in by_id))
        logs = sorted({str(by_id[x]["log_number"]) for x in valid_ids})
        source_hashes = sorted({str(by_id[x].get("source_sha256") or "") for x in valid_ids})
        if gtype not in RECURRING_GROUP_TYPES:
            rejected_groups.append({"index": idx, "reason": "invalid_group_type", "model_group": group})
            continue
        if unknown:
            rejected_groups.append({"index": idx, "reason": "unknown_candidate_ids", "unknown_ids": unknown, "model_group": group})
            continue
        if len(logs) < 2 or len(source_hashes) < 2:
            rejected_groups.append({
                "index": idx,
                "reason": "recurrence_rule_failed_requires_2_distinct_logs_and_source_hashes",
                "distinct_logs": logs,
                "distinct_source_hash_count": len(source_hashes),
                "model_group": group,
            })
            continue
        members = [by_id[x] for x in valid_ids]
        accepted_groups.append({
            "group_id": "g_" + hashlib.sha256((gtype + "\n" + "\n".join(sorted(valid_ids))).encode("utf-8")).hexdigest()[:16],
            "group_type": gtype,
            "model_label_candidate": str(group.get("label") or "").strip(),
            "member_candidate_ids": valid_ids,
            "distinct_log_count": len(logs),
            "logs": logs,
            "distinct_source_hash_count": len(source_hashes),
            "raw_variants": [
                {"candidate_id": m["candidate_id"], "log": m["log_number"], "raw": m["raw_source_text"], "kind": m["kind"]}
                for m in members
            ],
            "status": "provisional_model_group_python_recurrence_verified_not_approved",
        })

    def valid_id_list(key: str) -> List[str]:
        return list(dict.fromkeys(str(x) for x in (parsed.get(key) or []) if str(x) in by_id))

    return accepted_groups, rejected_groups, valid_id_list("unique_high_value_candidate_ids"), valid_id_list("human_review_candidate_ids")


def make_reason_prompt(ledger: Dict[str, Any]) -> str:
    return REASON_PROMPT + "\n\nCANDIDATE LEDGER:\n" + json.dumps(ledger, ensure_ascii=False, separators=(",", ":")) + "\n"


def summarize_text(
    output_root: Path,
    records: Sequence[Dict[str, Any]],
    candidates: Sequence[Dict[str, Any]],
    rejected_candidates: Sequence[Dict[str, Any]],
    repeated_lines: Sequence[Dict[str, Any]],
    recurring_groups: Sequence[Dict[str, Any]],
    rejected_groups: Sequence[Dict[str, Any]],
    unique_ids: Sequence[str],
    review_ids: Sequence[str],
) -> None:
    by_id = {c["candidate_id"]: c for c in candidates}
    all_unusual = [c for c in candidates if c.get("kind") in UNUSUAL_KINDS]
    unique_union = list(dict.fromkeys(list(unique_ids) + [c["candidate_id"] for c in all_unusual]))
    review_union = list(dict.fromkeys(list(review_ids) + [c["candidate_id"] for c in candidates if "[unclear]" in c.get("raw_source_text", "").lower()]))

    lines = [
        f"# Nova DRL Traveler Corpus Prospector + Sorter v{VERSION}",
        "",
        f"Input records:                   {len(records)}",
        f"Evidence-backed candidates:      {len(candidates)}",
        f"Rejected prospector candidates:  {len(rejected_candidates)}",
        f"Deterministic repeated lines:     {len(repeated_lines)}",
        f"Verified recurring groups:        {len(recurring_groups)}",
        f"Rejected reasoning groups:        {len(rejected_groups)}",
        f"Preserved unusual/review IDs:     {len(unique_union)}",
        f"Human-review candidate IDs:       {len(review_union)}",
        "Accepted repair facts:          0",
        "Qdrant entries created:         0",
        "",
        "VERIFIED RECURRING GROUPS (PROVISIONAL, NOT APPROVED)",
        "----------------------------------------------------",
    ]
    for group in recurring_groups:
        lines.append(f"{group['group_type']} | {group['model_label_candidate'] or '[no label]'} | {group['distinct_log_count']} logs: {', '.join(group['logs'])}")
        for v in group["raw_variants"]:
            lines.append(f"  {v['log']} | {v['raw']}")

    lines += ["", "UNUSUAL / HIGH-RECALL CANDIDATES PRESERVED", "------------------------------------------"]
    for cid in unique_union:
        c = by_id.get(cid)
        if c:
            lines.append(f"{c['log_number']} | {c['kind']} | {c['raw_source_text']}")

    lines += ["", "HUMAN REVIEW CANDIDATES", "-----------------------"]
    for cid in review_union:
        c = by_id.get(cid)
        if c:
            lines.append(f"{c['log_number']} | {c['kind']} | {c['raw_source_text']}")
    if rejected_candidates:
        lines.append(f"Prospector unsupported/invalid candidates are preserved separately in rejected_prospector_candidates_v1_3_6_0.json ({len(rejected_candidates)} items).")
    if rejected_groups:
        lines.append(f"Reasoning groups rejected by Python recurrence/ID policy are preserved separately in rejected_reasoning_groups_v1_3_6_0.json ({len(rejected_groups)} items).")

    (output_root / "provisional_sort_summary_v1_3_6_0.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Nova DRL Traveler Corpus Prospector + Sorter v{VERSION}")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help=f"Completed v1.3.5.1 corpus manifest (default: {DEFAULT_MANIFEST})")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help=f"Writable sorter output root (default: {DEFAULT_OUTPUT_ROOT})")
    parser.add_argument("--prospect-model", default=DEFAULT_PROSPECT_MODEL, help=f"High-recall 8B prospector model (default: {DEFAULT_PROSPECT_MODEL})")
    parser.add_argument("--reason-model", default=DEFAULT_REASON_MODEL, help=f"32B reasoning model (default: {DEFAULT_REASON_MODEL})")
    parser.add_argument("--prospect-num-ctx", type=int, default=16384)
    parser.add_argument("--prospect-num-predict", type=int, default=4096)
    parser.add_argument("--reason-num-ctx", type=int, default=16384)
    parser.add_argument("--reason-num-predict", type=int, default=8192)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--prospect-only", action="store_true", help="Run/reuse the 8B high-recall pass and deterministic candidate ledger, then stop before 32B reasoning")
    parser.add_argument("--force-prospect", action="store_true", help="Re-run 8B prospecting even when a matching cached result exists")
    parser.add_argument("--force-reason", action="store_true", help="Re-run 32B reasoning even when a matching result exists")
    parser.add_argument("--allow-incomplete", action="store_true", help="Pilot/debug only: sort only available OK records from an incomplete collector manifest")
    parser.add_argument("--no-stop-models", action="store_true", help="Do not ask Ollama to unload the 8B model before loading 32B")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    output_root = Path(args.output_root)
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    try:
        collector_manifest = load_json(manifest_path)
        records = validate_collector_manifest(collector_manifest, allow_incomplete=args.allow_incomplete)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    output_root.mkdir(parents=True, exist_ok=True)
    prospect_model_info = get_ollama_model_info(args.prospect_model)
    if prospect_model_info.get("available") is False:
        print(f"ERROR: prospector model not installed: {args.prospect_model}", file=sys.stderr)
        return 4
    reason_model_info = get_ollama_model_info(args.reason_model) if not args.prospect_only else {"requested_model": args.reason_model, "not_checked_prospect_only": True}
    if not args.prospect_only and reason_model_info.get("available") is False:
        print(f"ERROR: reasoning model not installed: {args.reason_model}", file=sys.stderr)
        return 5

    print(f"# Nova DRL Traveler Corpus Prospector + Sorter v{VERSION}")
    print(f"Collector manifest: {manifest_path}")
    print(f"Records ready:      {len(records)}")
    print(f"8B prospector:      {args.prospect_model}")
    print(f"32B reasoning:      {'SKIPPED' if args.prospect_only else args.reason_model}")
    print("Accepted facts:     0")
    print("Qdrant:             OFF")
    print()

    all_candidates: List[Dict[str, Any]] = []
    all_rejected_candidates: List[Dict[str, Any]] = []
    prospect_runs: List[Dict[str, Any]] = []
    records_with_raw: List[Tuple[Dict[str, Any], str]] = []

    for idx, record in enumerate(records, 1):
        log = str(record.get("log_number"))
        try:
            raw, _ = load_and_verify_raw(record)
            records_with_raw.append((record, raw))
            candidates, rejected, meta = prospect_one(record, output_root, prospect_model_info, args)
        except Exception as exc:
            print(f"ERROR: prospecting failed for {log}: {exc}", file=sys.stderr)
            return 6
        all_candidates.extend(candidates)
        for r in rejected:
            all_rejected_candidates.append({"log_number": log, "record_id": record.get("record_id"), **r})
        prospect_runs.append(meta)
        print(f"[{idx}/{len(records)}] {log} | candidates {len(candidates)} | rejected {len(rejected)} | {meta.get('run_action')}")

    # Stable ordering makes audits/diffs deterministic.
    all_candidates.sort(key=lambda c: (str(c.get("log_number")), str(c.get("candidate_id")), str(c.get("kind"))))
    repeated_lines = repeated_line_inventory(records_with_raw)
    save_json(output_root / "candidate_ledger_v1_3_6_0.json", {"sorter_version": VERSION, "candidates": all_candidates})
    write_jsonl(output_root / "candidate_ledger_v1_3_6_0.jsonl", all_candidates)
    save_json(output_root / "rejected_prospector_candidates_v1_3_6_0.json", all_rejected_candidates)
    save_json(output_root / "repeated_line_inventory_v1_3_6_0.json", repeated_lines)

    base_manifest = {
        "sorter_version": VERSION,
        "architecture": "acquire_all_then_8b_high_recall_then_32b_reasoning_then_python_accounting",
        "collector_manifest": str(manifest_path),
        "collector_manifest_sha256": sha256_text(manifest_path.read_text(encoding="utf-8")),
        "record_count": len(records),
        "prospector_model": prospect_model_info,
        "reasoning_model": reason_model_info,
        "prospector_prompt_sha256": sha256_text(PROSPECT_PROMPT),
        "reasoning_prompt_sha256": sha256_text(REASON_PROMPT),
        "prospect_run_count": len(prospect_runs),
        "candidate_count": len(all_candidates),
        "rejected_prospector_candidate_count": len(all_rejected_candidates),
        "deterministic_repeated_line_count": len(repeated_lines),
        "global_policy": {
            "hours_in_final_testing": "raw_audit_only_suppressed_from_sort_view",
            "source_transcriptions_modified": False,
            "automatic_fact_acceptance": False,
            "accepted_fact_count": 0,
            "qdrant_write_enabled": False,
            "qdrant_entries_created": 0,
            "recurring_group_min_distinct_logs": 2,
            "recurring_group_min_distinct_source_hashes": 2,
        },
    }

    if args.prospect_only:
        base_manifest.update({"reasoning_performed": False, "status": "prospect_only_complete"})
        save_json(output_root / "sort_manifest_v1_3_6_0.json", base_manifest)
        print()
        print(f"Evidence-backed candidates:     {len(all_candidates)}")
        print(f"Rejected prospector candidates: {len(all_rejected_candidates)}")
        print("32B reasoning:                  NOT RUN (--prospect-only)")
        print("Accepted repair facts:          0")
        print("Qdrant entries created:         0")
        return 0

    if not args.no_stop_models:
        stop_ollama_model(args.prospect_model)

    ledger = compact_reasoning_ledger(all_candidates, repeated_lines)
    reason_prompt = make_reason_prompt(ledger)
    reason_prompt_sha = sha256_text(reason_prompt)
    reason_raw_path = output_root / "reasoning_raw_response_v1_3_6_0.txt"
    reason_parsed_path = output_root / "reasoning_model_proposal_v1_3_6_0.json"
    reason_run_meta_path = output_root / "reasoning_run_v1_3_6_0.json"

    parsed_reason: Any = None
    reason_action = "model_run"
    if not args.force_reason and reason_run_meta_path.exists() and reason_parsed_path.exists():
        try:
            old_meta = load_json(reason_run_meta_path)
            if (
                old_meta.get("reason_prompt_sha256") == reason_prompt_sha
                and old_meta.get("model", {}).get("requested_model") == args.reason_model
                and int(old_meta.get("num_ctx") or -1) == int(args.reason_num_ctx)
                and int(old_meta.get("num_predict") or -1) == int(args.reason_num_predict)
                and (not reason_model_info.get("digest") or old_meta.get("model", {}).get("digest") == reason_model_info.get("digest"))
            ):
                parsed_reason = load_json(reason_parsed_path)
                reason_action = "reused_existing"
        except Exception:
            parsed_reason = None

    if parsed_reason is None:
        started = time.time()
        try:
            reason_raw = call_ollama_text(args.reason_model, reason_prompt, args.reason_num_ctx, args.reason_num_predict, args.timeout)
            reason_raw_path.write_text(reason_raw, encoding="utf-8")
            parsed_reason = parse_json_response(reason_raw)
        except Exception as exc:
            print(f"ERROR: 32B reasoning failed: {exc}", file=sys.stderr)
            return 7
        save_json(reason_parsed_path, parsed_reason)
        save_json(reason_run_meta_path, {
            "sorter_version": VERSION,
            "run_action": "model_run",
            "reason_prompt_sha256": reason_prompt_sha,
            "candidate_ledger_sha256": sha256_text(json.dumps(ledger, ensure_ascii=False, sort_keys=True)),
            "model": reason_model_info,
            "num_ctx": args.reason_num_ctx,
            "num_predict": args.reason_num_predict,
            "elapsed_seconds": round(time.time() - started, 3),
            "automatic_fact_acceptance": False,
            "qdrant_entries_created": 0,
        })

    try:
        recurring_groups, rejected_groups, unique_ids, review_ids = validate_reasoning_output(parsed_reason, all_candidates)
    except Exception as exc:
        print(f"ERROR: reasoning validation failed: {exc}", file=sys.stderr)
        return 8

    save_json(output_root / "verified_recurring_groups_v1_3_6_0.json", recurring_groups)
    save_json(output_root / "rejected_reasoning_groups_v1_3_6_0.json", rejected_groups)

    by_id = {c["candidate_id"]: c for c in all_candidates}
    all_unusual_ids = [c["candidate_id"] for c in all_candidates if c.get("kind") in UNUSUAL_KINDS]
    unique_union = list(dict.fromkeys(unique_ids + all_unusual_ids))
    review_union = list(dict.fromkeys(review_ids + [c["candidate_id"] for c in all_candidates if "[unclear]" in c.get("raw_source_text", "").lower()]))

    provisional = {
        "sorter_version": VERSION,
        "status": "provisional_not_approved",
        "recurring_groups": recurring_groups,
        "unique_high_recall_candidates": [by_id[x] for x in unique_union if x in by_id],
        "human_review_candidates": [by_id[x] for x in review_union if x in by_id],
        "repeated_line_inventory": repeated_lines,
        "model_reasoning_proposal": parsed_reason,
        "automatic_fact_acceptance": False,
        "accepted_fact_count": 0,
        "qdrant_entries_created": 0,
    }
    save_json(output_root / "provisional_sort_v1_3_6_0.json", provisional)
    summarize_text(
        output_root,
        records,
        all_candidates,
        all_rejected_candidates,
        repeated_lines,
        recurring_groups,
        rejected_groups,
        unique_ids,
        review_ids,
    )

    base_manifest.update({
        "reasoning_performed": True,
        "reasoning_run_action": reason_action,
        "reason_prompt_sha256": reason_prompt_sha,
        "verified_recurring_group_count": len(recurring_groups),
        "rejected_reasoning_group_count": len(rejected_groups),
        "unique_high_recall_candidate_count": len(unique_union),
        "human_review_candidate_count": len(review_union),
        "status": "complete_provisional_sort_not_approved",
    })
    save_json(output_root / "sort_manifest_v1_3_6_0.json", base_manifest)

    print()
    print(f"Evidence-backed candidates:     {len(all_candidates)}")
    print(f"Rejected prospector candidates: {len(all_rejected_candidates)}")
    print(f"Verified recurring groups:      {len(recurring_groups)}")
    print(f"Rejected reasoning groups:      {len(rejected_groups)}")
    print(f"High-recall unique candidates:  {len(unique_union)}")
    print(f"Human-review candidates:        {len(review_union)}")
    print("Accepted repair facts:          0")
    print("Qdrant entries created:         0")
    print(f"Summary: {output_root / 'provisional_sort_summary_v1_3_6_0.txt'}")
    print(f"Manifest: {output_root / 'sort_manifest_v1_3_6_0.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
