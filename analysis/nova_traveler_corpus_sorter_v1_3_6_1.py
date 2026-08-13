#!/usr/bin/env python3
"""
Nova DRL Traveler Corpus Prospector + Sorter v1.3.6.1

Purpose
-------
Sort only AFTER v1.3.5.1 has completed whole-Traveler acquisition.
Acquisition remains broad and immutable; this version improves only the working
sort view and downstream candidate handling.

Roles
-----
1) Python sanitation = remove routine form/admin noise from the model working view
   while preserving every byte of the v1.3.5.1 raw transcription.
2) Qwen3-VL 8B text pass = high-recall prospector. It proposes source phrases that
   may matter; it does not approve facts or decide recurrence.
3) Qwen2.5 32B = cross-record reasoning. It proposes group memberships using only
   evidence-backed candidate IDs; it does not rewrite evidence or determine counts.
4) Python = evidence/accounting authority. It verifies quote support, applies only
   deterministic kind overrides, enforces group-kind compatibility and >=2-log
   recurrence, builds an OCR recheck queue, and writes provenance-rich provisional
   outputs.

Non-negotiable behavior
-----------------------
- Source Traveler images and v1.3.5.1 raw transcriptions are never modified.
- No Qdrant writes and no automatic fact approval.
- Routine form/admin suppression affects only the temporary prospecting view.
- Unsupported model quotes are retained in an audit rejection file.
- Evidence matching may tolerate layout whitespace and a single terminal punctuation
  difference only; spelling, apostrophes, digits, part numbers, and abbreviations are
  never normalized.
- Recurrent groups require at least two distinct DRL logs AND two distinct source
  hashes; Python, not either LLM, owns the count.
- "Hours in Final Testing" remains raw/audit only and is absent from the model sort
  view; it cannot become knowledge or influence adjacent Final O.K. text.
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

VERSION = "1.3.6.1"
REQUIRED_COLLECTOR_VERSION = "1.3.5.1"
DEFAULT_MANIFEST = Path("/opt/nova-drl/output/whole_traveler_corpus_v1_3_5_1/corpus_manifest_v1_3_5_1.json")
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/traveler_corpus_sort_v1_3_6_1")
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
AUTO_HIGH_VALUE_KINDS = {
    "shop_term_or_abbreviation",
    "part_number_or_identifier",
}
RECURRING_GROUP_TYPES = {
    "boilerplate_or_customer_requirement",
    "repair_or_service",
    "component_or_part",
    "diagnostic_or_failure",
}

PROSPECT_PROMPT = """You are the HIGH-RECALL PROSPECTOR for one DRL Traveler working transcription view.

The immutable raw transcription already exists elsewhere. This working view has only
routine form/admin lines removed by deterministic Python sanitation. Your job is to
surface source phrases that may matter later. Do NOT approve facts, normalize wording,
expand abbreviations, infer missing information, or decide what is recurring.

Return JSON only using exactly this shape:
{
  "log_number": "<log>",
  "candidates": [
    {"kind": "<kind>", "raw_quote": "<verbatim text copied from the supplied working view>"}
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
- Favor recall for EVENT-BEARING and potentially useful material: customer requirements,
  handwritten repair/service wording, symptoms, observed faults, causal or suspected-causal
  wording, components/parts, explicit quantities or identifiers, unusual DRL shop language,
  abbreviations, named tests/processes, and unclear OCR worth preserving.
- raw_quote MUST be copied from the supplied working view. Do not paraphrase it.
- Keep strange spellings, punctuation, apostrophes, capitalization, and abbreviations.
- Do not use outside knowledge and do not invent quantities.
- Do not reconstruct routine form/admin text that is absent from the supplied working view.
- Do not expand terms such as FA, RTZ, FE, NPF, BERS, or any other unexplained abbreviation.
- The field "Hours in Final Testing" is absent by deterministic policy. Never reconstruct or discuss it.
- A phrase may be assigned more than one kind only when the same exact raw phrase truly serves
  both roles; otherwise choose the closest kind.
"""

REASON_PROMPT = """You are the CROSS-RECORD REASONING layer for a DRL Traveler evidence-backed candidate ledger.

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
- Group candidates only when their exact raw wording supports a genuinely similar concept.
- Preserve axes and mechanisms: do not merge R, T, Y, Z, Theta, vacuum, belt, motor, etc. merely
  because they are all robot repair language.
- Do not turn repair work into a diagnosed failure unless the raw wording states a symptom,
  observed fault, cause, or suspected cause.
- Do not expand or redefine unexplained DRL abbreviations, part strings, or shop terms.
- Preserve unusual wording by candidate ID; Python will render exact raw variants.
- Recurring means two or more DIFFERENT repair logs. Python will enforce the count and group-kind
  compatibility, so do not pad a group with merely related one-log items.
- Mark unique but potentially valuable shop language, part identifiers, named tests/processes, and
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
# not influence adjacent Final O.K. interpretation. Remove only the field/value from
# the working view; the immutable source transcription remains unchanged.
_HOURS_FINAL_RE = re.compile(r"(?i)hours\s+in\s+final\s+testing\s*:?\s*(?:\d[0-9A-Za-z+._-]*)?")

# Routine form/admin lines that add little or no repair knowledge. These are removed
# only from the temporary prospecting view. The raw v1.3.5.1 transcription is untouched.
_FORM_ADMIN_FULLLINE_PATTERNS: Sequence[Tuple[str, re.Pattern[str]]] = [
    ("document_title", re.compile(r"^Direct Repair Laboratories\s*-?\s*Testing Traveler.*$", re.I)),
    ("legacy_template_path", re.compile(r"^[\"“]?\\Drlserver\\ctrack database\\traveler\.doc[\"”]?$", re.I)),
    ("event_identity", re.compile(r"^Log\s*#.*$", re.I)),
    ("event_identity", re.compile(r"^(?:Customer(?: Name)?|CustRMA|Cust PO|Customer PO(?: Number)?|Unit Type|Serial\s*#|Board Serial\s*#|Frame Serial\s*#|Board\(s\) serial #\(s\)|Frame\(s\) serial#\(s\)|Sales Rep|DRL SalesRep|DRL Rep|Point Of Contact|POC Phone|POC Email|Contact|Phone|Email)(?:\s*:|\s+|$).*$", re.I)),
    ("warranty_admin", re.compile(r"^(?:Warranty|Warranty Date|Warranty Type|Sticker Swap|Pricing Approved|pricing approved|needs quote)\b.*$", re.I)),
    ("warranty_admin", re.compile(r"^[✓✔☑☐XxVv ]*pricing approved\b.*$", re.I)),
    ("table_heading", re.compile(r"^(?:Special Notes \(if any\) below\..*|Responsible tech\. to init\. & date compliance\.?|\[Notes \(specific to this .+\)\]|PACKAGING STATUS:|Packaging Status:|Repaired Replaced|Detailed description of repairs/replacements|\(including any costs for new parts\)|Inits\. Date|\(m/d/y{1,2}\)|~Revised~)$", re.I)),
    ("shipping_admin", re.compile(r"^Date Shipped\b.*$", re.I)),
    ("packaging_admin", re.compile(r"^(?:Saved \(in shipping area\)|Saved \(in warehouse\)|Unusable \(discarded\))\b.*$", re.I)),
    ("final_ok_admin", re.compile(r"^Final O\.K\.?\b.*$", re.I)),
    ("final_checklist", re.compile(r"^(?:Cleaned|All Screws|Tech File Created|WAD installed|Consumer Warranty|Appearance|Latest Firmware|Warranty Sticker Applied|Reman\.|Aligned|Adjusted|Scanned)\b(?:\s*(?:[✓✔☑☐XxVv]|\[[^\]]*\]))*(?:\s+(?:Aligned|Adjusted|Latest Firmware|Reman\.|Appearance|Warranty Sticker Applied|Tech File Created|Scanned|Consumer Warranty|All Screws|WAD installed)\b(?:\s*(?:[✓✔☑☐XxVv]|\[[^\]]*\]))*)*$", re.I)),
]

# Printed labels that can share a line with useful handwritten/event-specific text.
# Strip only the known label and checkbox-like mark; retain any exact trailing source text.
_FORM_PREFIX_PATTERNS: Sequence[Tuple[str, re.Pattern[str]]] = [
    ("final_test_heading", re.compile(r"^Final Unit Test Results and Notes\s*:?\s*", re.I)),
    ("final_test_option", re.compile(r"^(?:No Trouble Found|Passed All Tests|Basic Functional Tests Only|Power-on Tests Only|Untestable, Inspection Only)\s*(?:(?:[✓✔☑☐XxVv])|(?:\[[^\]]*\]))?\s*", re.I)),
    ("time_admin_label", re.compile(r"^Ttl Time Spent \(Hours\)\s*(?:(?:[✓✔☑☐XxVv])|(?:\[[^\]]*\]))?\s*", re.I)),
    ("money_admin_label", re.compile(r"^Ttl Money Spent \(Dollars\)\s*(?:(?:[✓✔☑☐XxVv])|(?:\[[^\]]*\]))?\s*", re.I)),
]

_CUSTOMER_REQUIREMENT_PATTERNS: Sequence[re.Pattern[str]] = [
    re.compile(r"\bthis customer requires\b", re.I),
    re.compile(r"\brobot\s+FAs?\s+are put inside packaging with unit\b", re.I),
    re.compile(r"\brepair\s+rpt\s+goes inside crate\b", re.I),
    re.compile(r"^\s*shipping\s*:", re.I),
    re.compile(r"^\s*incoming shipping\s*:", re.I),
    re.compile(r"^\s*incoming logistics\s*:", re.I),
    re.compile(r"\bdo not ship in wood crates\b", re.I),
    re.compile(r"\bonly use new style keal cases\b", re.I),
    re.compile(r"\ball mtv robots must\b", re.I),
    re.compile(r"\bput .*customer name .*box tags\b", re.I),
    re.compile(r"\bput end customer name on linecard\b", re.I),
]

GROUP_KIND_COMPATIBILITY = {
    "boilerplate_or_customer_requirement": {"customer_requirement"},
    "repair_or_service": {"repair_or_service"},
    "component_or_part": {"component_or_part", "part_number_or_identifier"},
    "diagnostic_or_failure": {"diagnostic_or_failure"},
}


def mask_global_audit_fields(raw: str) -> Tuple[str, List[Dict[str, str]]]:
    suppressed: List[Dict[str, str]] = []

    def repl(match: re.Match[str]) -> str:
        suppressed.append({"policy": "hours_in_final_testing_raw_audit_only", "raw": match.group(0)})
        return ""

    return _HOURS_FINAL_RE.sub(repl, raw), suppressed


def normalized_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def form_admin_noise_reason(line: str) -> Optional[str]:
    clean = normalized_whitespace(line)
    if not clean:
        return "blank"
    for reason, pat in _FORM_ADMIN_FULLLINE_PATTERNS:
        if pat.fullmatch(clean):
            return reason
    return None


def sanitize_for_prospecting(raw: str) -> Tuple[str, List[Dict[str, str]]]:
    """Create a temporary low-noise model view without modifying raw evidence.

    Full routine form/admin lines are omitted. For known printed labels that share a
    line with handwritten/event-specific content, only the printed prefix is removed
    and the exact trailing source text is retained.
    """
    masked, audit = mask_global_audit_fields(raw)
    kept: List[str] = []
    for line in masked.splitlines():
        original_line = line
        clean = line.strip()
        if not clean:
            kept.append("")
            continue

        reason = form_admin_noise_reason(clean)
        if reason and reason != "blank":
            audit.append({"policy": "routine_form_admin_suppressed_from_prospecting_view", "reason": reason, "raw": original_line})
            kept.append("")
            continue

        stripped = clean
        prefix_reason: Optional[str] = None
        for reason2, pat in _FORM_PREFIX_PATTERNS:
            m = pat.match(stripped)
            if m:
                remainder = stripped[m.end():].strip()
                if remainder == stripped:
                    continue
                prefix_reason = reason2
                audit.append({
                    "policy": "routine_form_label_stripped_from_prospecting_view",
                    "reason": reason2,
                    "raw": original_line,
                    "retained_exact_trailing_text": remainder,
                })
                stripped = remainder
                break

        if prefix_reason and not stripped:
            kept.append("")
        else:
            kept.append(stripped if prefix_reason else original_line)

    view = "\n".join(kept)
    if masked.endswith("\n"):
        view += "\n"
    return view, audit


def deterministic_kind_override(raw_source_text: str, model_kind: str) -> Tuple[str, Optional[str]]:
    """Apply narrow deterministic kind corrections without changing evidence text."""
    text = normalized_whitespace(raw_source_text)
    for pat in _CUSTOMER_REQUIREMENT_PATTERNS:
        if pat.search(text):
            if model_kind != "customer_requirement":
                return "customer_requirement", "deterministic_customer_requirement_pattern"
            return model_kind, None
    return model_kind, None


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


def _whitespace_flexible_pattern(text: str) -> Optional[re.Pattern[str]]:
    parts = re.split(r"\s+", text.strip())
    parts = [p for p in parts if p]
    if not parts:
        return None
    return re.compile(r"\s+".join(re.escape(p) for p in parts))


def _find_pattern_slice(source: str, quote_for_boundary: str, pattern_text: str) -> Optional[str]:
    pat = _whitespace_flexible_pattern(pattern_text)
    if pat is None:
        return None
    for m in pat.finditer(source):
        if _token_boundary_ok(source, m.start(), m.end(), quote_for_boundary):
            return source[m.start():m.end()]
    return None


def find_supported_source_slice(source: str, proposed_quote: str) -> Optional[Tuple[str, str]]:
    """Return (support_mode, exact_source_slice) with narrow layout tolerance.

    Accepted modes:
    - exact
    - whitespace_only (line wrapping / repeated whitespace only)
    - whitespace_terminal_punctuation (same as above plus one model-added trailing
      period/comma/semicolon/colon)

    Spelling, apostrophes, digits, part strings, and internal punctuation are never
    normalized. `Blue Schmoo` therefore cannot stand in for `Blue Schmoo's`.
    """
    if not proposed_quote or not proposed_quote.strip():
        return None

    pos = source.find(proposed_quote)
    while pos >= 0:
        end = pos + len(proposed_quote)
        if _token_boundary_ok(source, pos, end, proposed_quote):
            return ("exact", proposed_quote)
        pos = source.find(proposed_quote, pos + 1)

    actual = _find_pattern_slice(source, proposed_quote, proposed_quote)
    if actual is not None:
        return ("whitespace_only", actual)

    # Permit only a single model-added terminal punctuation character. Do not remove
    # apostrophes or any internal punctuation.
    q = proposed_quote.strip()
    if q and q[-1] in ".,;:":
        q_base = q[:-1].rstrip()
        actual2 = _find_pattern_slice(source, q_base, q_base)
        if actual2 is not None:
            return ("whitespace_terminal_punctuation", actual2)
    return None


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
        "prospecting_view": d / "prospecting_view.txt",
        "sanitation_audit": d / "sanitation_audit.json",
        "raw_response": d / "prospector_raw_response.txt",
        "validated": d / "prospector_candidates.json",
        "metadata": d / "prospector_run.json",
    }


def make_prospect_prompt(log_number: str, analysis_text: str) -> str:
    return f"{PROSPECT_PROMPT}\n\nDRL LOG: {log_number}\nRAW TRANSCRIPTION FOR PROSPECTING:\n{analysis_text}\n"


def validate_prospector_output(
    parsed: Any,
    record: Dict[str, Any],
    prospecting_view: str,
    raw_transcription: str,
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
        model_kind = str(item.get("kind") or "").strip()
        quote = str(item.get("raw_quote") or "")
        if model_kind not in ALLOWED_KINDS:
            rejected.append({"index": index, "reason": "invalid_kind", "model_item": item})
            continue
        if "hours in final testing" in quote.lower():
            rejected.append({"index": index, "reason": "global_audit_field_suppressed", "model_item": item})
            continue

        support = find_supported_source_slice(prospecting_view, quote)
        if support is None:
            rejected.append({"index": index, "reason": "raw_quote_not_supported_by_prospecting_view", "model_item": item})
            continue
        mode, view_actual = support

        # Re-bind the candidate to the immutable raw transcription. The working view
        # may remove form labels but never changes retained event-bearing characters.
        raw_support = find_supported_source_slice(raw_transcription, view_actual)
        if raw_support is None:
            rejected.append({
                "index": index,
                "reason": "prospecting_view_text_not_rebindable_to_immutable_raw",
                "model_item": item,
                "prospecting_view_slice": view_actual,
            })
            continue
        _, raw_actual = raw_support

        admin_reason = form_admin_noise_reason(raw_actual)
        if admin_reason and admin_reason != "blank":
            rejected.append({
                "index": index,
                "reason": "routine_form_admin_candidate_rejected",
                "admin_reason": admin_reason,
                "model_item": item,
            })
            continue

        kind, override_reason = deterministic_kind_override(raw_actual, model_kind)
        key = (raw_actual, kind)
        if key in accepted_by_key:
            continue
        cid = candidate_id(log, source_sha, raw_actual, kind)
        accepted_by_key[key] = {
            "candidate_id": cid,
            "log_number": log,
            "record_id": record.get("record_id"),
            "source_sha256": source_sha,
            "raw_transcription_sha256": raw_transcription_sha,
            "kind": kind,
            "model_kind": model_kind,
            "kind_override_reason": override_reason,
            "raw_source_text": raw_actual,
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
    prospecting_view, sanitation_audit = sanitize_for_prospecting(raw)
    prompt = make_prospect_prompt(str(record.get("log_number")), prospecting_view)
    prompt_sha = sha256_text(prompt)
    view_sha = sha256_text(prospecting_view)
    paths = prospect_cache_paths(output_root, record)
    paths["dir"].mkdir(parents=True, exist_ok=True)

    if not args.force_prospect and paths["metadata"].exists() and paths["validated"].exists():
        try:
            meta = load_json(paths["metadata"])
            val = load_json(paths["validated"])
            if (
                meta.get("raw_transcription_sha256") == raw_sha
                and meta.get("prospecting_view_sha256") == view_sha
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

    paths["prospecting_view"].write_text(prospecting_view, encoding="utf-8")
    save_json(paths["sanitation_audit"], {
        "log_number": record.get("log_number"),
        "raw_transcription_sha256": raw_sha,
        "prospecting_view_sha256": view_sha,
        "suppressed_or_stripped_segments": sanitation_audit,
        "raw_transcription_modified": False,
    })

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
    accepted, rejected = validate_prospector_output(parsed, record, prospecting_view, raw, raw_sha)
    save_json(paths["validated"], {
        "log_number": record.get("log_number"),
        "accepted_candidates": accepted,
        "rejected_candidates": rejected,
        "sanitation_audit_count": len(sanitation_audit),
    })
    meta = {
        "sorter_version": VERSION,
        "run_action": "model_run",
        "log_number": record.get("log_number"),
        "record_id": record.get("record_id"),
        "raw_transcription_sha256": raw_sha,
        "prospecting_view_sha256": view_sha,
        "prompt_sha256": prompt_sha,
        "model": model_info,
        "num_ctx": args.prospect_num_ctx,
        "num_predict": args.prospect_num_predict,
        "elapsed_seconds": round(time.time() - started, 3),
        "accepted_candidate_count": len(accepted),
        "rejected_candidate_count": len(rejected),
        "sanitation_audit_count": len(sanitation_audit),
        "automatic_fact_acceptance": False,
        "qdrant_entries_created": 0,
    }
    save_json(paths["metadata"], meta)
    return accepted, rejected, meta


def repeated_line_inventory(records_with_raw: Sequence[Tuple[Dict[str, Any], str]]) -> List[Dict[str, Any]]:
    """Inventory repeated lines only after the same deterministic sanitation used by prospecting."""
    by_norm: Dict[str, Dict[str, Any]] = {}
    for record, raw in records_with_raw:
        analysis_text, _ = sanitize_for_prospecting(raw)
        log = str(record.get("log_number"))
        for line in analysis_text.splitlines():
            clean = line.strip()
            if len(clean) < 18:
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
        allowed_member_kinds = GROUP_KIND_COMPATIBILITY[gtype]
        incompatible = [x for x in valid_ids if str(by_id[x].get("kind")) not in allowed_member_kinds]
        if incompatible:
            rejected_groups.append({
                "index": idx,
                "reason": "group_type_candidate_kind_mismatch",
                "group_type": gtype,
                "allowed_candidate_kinds": sorted(allowed_member_kinds),
                "incompatible_candidate_ids": incompatible,
                "incompatible_member_kinds": {x: by_id[x].get("kind") for x in incompatible},
                "model_group": group,
            })
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


def _contains_partlike_token(text: str) -> bool:
    # Exact-character-risk patterns common in handwritten technical notes. These are
    # only queue signals; they never normalize or infer the token.
    if re.search(r"\b[A-Za-z]{1,3}\d[A-Za-z0-9-]{2,}\b", text):
        return True
    if re.search(r"\b[A-Za-z]\.[0-9]+\b", text):
        return True
    if re.search(r"\b[A-Za-z]{2,}-[A-Za-z0-9-]*\d[A-Za-z0-9-]*\b", text):
        return True
    return False


def _is_trivial_shop_term(text: str) -> bool:
    t = normalized_whitespace(text).strip()
    if len(t) <= 2:
        return True
    if re.fullmatch(r"\[[A-Za-z]{1,3}\](?:\s+on\s+\[[^\]]+\])?", t):
        return True
    if re.fullmatch(r"(?:UT|VT|EF|OM|JG|BE|MD|BP|WB|AM|CM|MP|NP|RB|SF)", t, re.I):
        return True
    if re.match(r"^(?:DRL Rep|RMA Number|Serial #|Frame Serial #|Board Serial #|Q\.C\. By|Serial Check)\b", t, re.I):
        return True
    return False


def _is_distinctive_testing_phrase(text: str) -> bool:
    t = normalized_whitespace(text)
    if not t or form_admin_noise_reason(t):
        return False
    low = t.lower()
    return any(k in low for k in (
        " test", "test ", "testing", "overnight", "drift", "torture", "script",
        "wafer", "linearity", "station", "ran for", "run for", "controller testing",
    ))


def deterministic_high_value_ids(
    candidates: Sequence[Dict[str, Any]],
    model_unique_ids: Sequence[str],
    recurring_groups: Sequence[Dict[str, Any]],
) -> List[str]:
    by_id = {c["candidate_id"]: c for c in candidates}
    recurring_member_ids = {
        cid
        for g in recurring_groups
        for cid in (g.get("member_candidate_ids") or [])
    }
    result: List[str] = []
    seen: set[str] = set()

    def add(cid: str) -> None:
        if cid in by_id and cid not in recurring_member_ids and cid not in seen:
            seen.add(cid)
            result.append(cid)

    for cid in model_unique_ids:
        add(str(cid))

    for c in candidates:
        cid = c["candidate_id"]
        kind = str(c.get("kind") or "")
        text = str(c.get("raw_source_text") or "")
        if kind == "customer_requirement":
            add(cid)
        elif kind == "diagnostic_or_failure":
            add(cid)
        elif kind in AUTO_HIGH_VALUE_KINDS and not _is_trivial_shop_term(text):
            add(cid)
        elif kind == "testing_or_process" and _is_distinctive_testing_phrase(text):
            add(cid)
        elif kind == "component_or_part" and (_contains_partlike_token(text) or "[unclear]" in text.lower()):
            add(cid)
        elif kind == "repair_or_service" and (
            _contains_partlike_token(text)
            or re.search(r"(?i)(?:\b\d+\s*x\b|\bx\s*\d+\b)", text)
            or "'" in text
            or "’" in text
        ):
            add(cid)
    return result


def build_ocr_recheck_queue(
    candidates: Sequence[Dict[str, Any]],
    rejected_candidates: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    queue: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for c in candidates:
        text = str(c.get("raw_source_text") or "")
        kind = str(c.get("kind") or "")
        reasons: List[str] = []
        if "[unclear]" in text.lower():
            reasons.append("contains_unclear_ocr_marker")
        if kind == "part_number_or_identifier":
            reasons.append("exact_character_identifier_requires_secondary_check_before_normalization")
        if _contains_partlike_token(text):
            reasons.append("mixed_alphanumeric_or_partlike_token_exact_character_risk")
        if re.search(r"\([^)]*\d[^)]*\)", text) and kind in {"component_or_part", "repair_or_service", "part_number_or_identifier"}:
            reasons.append("numeric_parenthetical_technical_string")
        if not reasons:
            continue
        key = "candidate:" + str(c.get("candidate_id"))
        if key in seen:
            continue
        seen.add(key)
        queue.append({
            "recheck_id": "ocr_" + str(c.get("candidate_id")),
            "source": "evidence_backed_candidate",
            "candidate_id": c.get("candidate_id"),
            "log_number": c.get("log_number"),
            "kind": kind,
            "raw_source_text": text,
            "reasons": list(dict.fromkeys(reasons)),
            "source_path": c.get("source_path"),
            "raw_transcription_path": c.get("raw_transcription_path"),
            "source_sha256": c.get("source_sha256"),
            "status": "secondary_vision_or_human_recheck_required_not_approved",
        })

    for r in rejected_candidates:
        if str(r.get("reason")) not in {
            "raw_quote_not_supported_by_prospecting_view",
            "prospecting_view_text_not_rebindable_to_immutable_raw",
        }:
            continue
        item = r.get("model_item") or {}
        if not isinstance(item, dict):
            continue
        quote = str(item.get("raw_quote") or "")
        kind = str(item.get("kind") or "")
        if not quote:
            continue
        if not ("[unclear]" in quote.lower() or _contains_partlike_token(quote) or re.search(r"\d", quote)):
            continue
        h = hashlib.sha256((str(r.get("log_number")) + "\n" + kind + "\n" + quote).encode("utf-8")).hexdigest()[:16]
        key = "rejected:" + h
        if key in seen:
            continue
        seen.add(key)
        queue.append({
            "recheck_id": "ocr_rejected_" + h,
            "source": "rejected_prospector_quote",
            "candidate_id": None,
            "log_number": r.get("log_number"),
            "kind": kind,
            "raw_source_text": quote,
            "reasons": ["prospector_saw_technical_text_but_quote_failed_evidence_binding"],
            "source_path": None,
            "raw_transcription_path": None,
            "source_sha256": None,
            "status": "secondary_vision_or_human_recheck_required_not_approved",
        })

    queue.sort(key=lambda x: (str(x.get("log_number")), str(x.get("recheck_id"))))
    return queue


def candidate_kind_counts(candidates: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    counts = Counter(str(c.get("kind") or "") for c in candidates)
    return dict(sorted(counts.items()))


def summarize_text(
    output_root: Path,
    records: Sequence[Dict[str, Any]],
    candidates: Sequence[Dict[str, Any]],
    rejected_candidates: Sequence[Dict[str, Any]],
    repeated_lines: Sequence[Dict[str, Any]],
    recurring_groups: Sequence[Dict[str, Any]],
    rejected_groups: Sequence[Dict[str, Any]],
    high_value_ids: Sequence[str],
    review_ids: Sequence[str],
    ocr_recheck_queue: Sequence[Dict[str, Any]],
    sanitation_audit_count: int,
) -> None:
    by_id = {c["candidate_id"]: c for c in candidates}
    kind_counts = candidate_kind_counts(candidates)

    lines = [
        f"# Nova DRL Traveler Corpus Prospector + Sorter v{VERSION}",
        "",
        f"Input records:                    {len(records)}",
        f"Routine form/admin audit entries: {sanitation_audit_count}",
        f"Evidence-backed candidates:       {len(candidates)}",
        f"Candidate kinds:                  {json.dumps(kind_counts, ensure_ascii=False, sort_keys=True)}",
        f"Rejected prospector candidates:   {len(rejected_candidates)}",
        f"Deterministic repeated lines:      {len(repeated_lines)}",
        f"Verified recurring groups:         {len(recurring_groups)}",
        f"Rejected reasoning groups:         {len(rejected_groups)}",
        f"High-value unique candidates:      {len(high_value_ids)}",
        f"OCR recheck queue:                 {len(ocr_recheck_queue)}",
        f"Human-review candidate IDs:        {len(review_ids)}",
        "Accepted repair facts:           0",
        "Qdrant entries created:          0",
        "",
        "VERIFIED RECURRING GROUPS (PROVISIONAL, NOT APPROVED)",
        "----------------------------------------------------",
    ]
    if not recurring_groups:
        lines.append("[none]")
    for group in recurring_groups:
        lines.append(f"{group['group_type']} | {group['model_label_candidate'] or '[no label]'} | {group['distinct_log_count']} logs: {', '.join(group['logs'])}")
        for v in group["raw_variants"]:
            lines.append(f"  {v['log']} | {v['kind']} | {v['raw']}")

    lines += ["", "HIGH-VALUE / UNIQUE CANDIDATES PRESERVED", "----------------------------------------"]
    if not high_value_ids:
        lines.append("[none]")
    for cid in high_value_ids:
        c = by_id.get(cid)
        if c:
            override = f" | model_kind={c.get('model_kind')} -> {c.get('kind')}" if c.get("kind_override_reason") else ""
            lines.append(f"{c['log_number']} | {c['kind']} | {c['raw_source_text']}{override}")

    lines += ["", "OCR RECHECK QUEUE (NOT CORRECTED / NOT APPROVED)", "-----------------------------------------------"]
    if not ocr_recheck_queue:
        lines.append("[none]")
    for item in ocr_recheck_queue:
        reasons = ", ".join(item.get("reasons") or [])
        lines.append(f"{item.get('log_number')} | {item.get('kind')} | {item.get('raw_source_text')} | {reasons}")

    lines += ["", "HUMAN REVIEW CANDIDATES", "-----------------------"]
    if not review_ids:
        lines.append("[none]")
    for cid in review_ids:
        c = by_id.get(cid)
        if c:
            lines.append(f"{c['log_number']} | {c['kind']} | {c['raw_source_text']}")

    lines += [
        "",
        "AUDIT FILES",
        "-----------",
        f"Rejected prospector quotes: rejected_prospector_candidates_v1_3_6_1.json ({len(rejected_candidates)} items)",
        f"Rejected reasoning groups: rejected_reasoning_groups_v1_3_6_1.json ({len(rejected_groups)} items)",
        f"OCR recheck queue: ocr_recheck_queue_v1_3_6_1.json ({len(ocr_recheck_queue)} items)",
        "Per-record prospecting_view.txt + sanitation_audit.json preserve exactly what Python removed from the working view.",
        "Raw v1.3.5.1 transcriptions and source Traveler images remain unchanged.",
    ]

    (output_root / "provisional_sort_summary_v1_3_6_1.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    sanitation_audit_count = sum(int(x.get("sanitation_audit_count") or 0) for x in prospect_runs)
    ocr_recheck_queue = build_ocr_recheck_queue(all_candidates, all_rejected_candidates)
    save_json(output_root / "candidate_ledger_v1_3_6_1.json", {"sorter_version": VERSION, "candidates": all_candidates})
    write_jsonl(output_root / "candidate_ledger_v1_3_6_1.jsonl", all_candidates)
    save_json(output_root / "rejected_prospector_candidates_v1_3_6_1.json", all_rejected_candidates)
    save_json(output_root / "repeated_line_inventory_v1_3_6_1.json", repeated_lines)
    save_json(output_root / "ocr_recheck_queue_v1_3_6_1.json", ocr_recheck_queue)
    save_json(output_root / "sanitation_summary_v1_3_6_1.json", {
        "sorter_version": VERSION,
        "raw_transcriptions_modified": False,
        "total_suppressed_or_stripped_segments": sanitation_audit_count,
        "records": [
            {
                "log_number": x.get("log_number"),
                "record_id": x.get("record_id"),
                "sanitation_audit_count": int(x.get("sanitation_audit_count") or 0),
                "prospecting_view_sha256": x.get("prospecting_view_sha256"),
            }
            for x in prospect_runs
        ],
    })

    base_manifest = {
        "sorter_version": VERSION,
        "architecture": "acquire_all_then_python_sanitize_then_8b_high_recall_then_32b_reasoning_then_python_accounting",
        "collector_manifest": str(manifest_path),
        "collector_manifest_sha256": sha256_text(manifest_path.read_text(encoding="utf-8")),
        "record_count": len(records),
        "prospector_model": prospect_model_info,
        "reasoning_model": reason_model_info,
        "prospector_prompt_sha256": sha256_text(PROSPECT_PROMPT),
        "reasoning_prompt_sha256": sha256_text(REASON_PROMPT),
        "prospect_run_count": len(prospect_runs),
        "candidate_count": len(all_candidates),
        "candidate_kind_counts": candidate_kind_counts(all_candidates),
        "rejected_prospector_candidate_count": len(all_rejected_candidates),
        "deterministic_repeated_line_count": len(repeated_lines),
        "sanitation_audit_count": sanitation_audit_count,
        "ocr_recheck_queue_count": len(ocr_recheck_queue),
        "global_policy": {
            "hours_in_final_testing": "raw_audit_only_suppressed_from_sort_view",
            "routine_form_admin": "suppressed_or_prefix_stripped_from_working_view_only",
            "source_transcriptions_modified": False,
            "evidence_match_tolerance": ["exact", "whitespace_only", "whitespace_terminal_punctuation"],
            "silent_spelling_term_part_normalization": False,
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
        save_json(output_root / "sort_manifest_v1_3_6_1.json", base_manifest)
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
    reason_raw_path = output_root / "reasoning_raw_response_v1_3_6_1.txt"
    reason_parsed_path = output_root / "reasoning_model_proposal_v1_3_6_1.json"
    reason_run_meta_path = output_root / "reasoning_run_v1_3_6_1.json"

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

    save_json(output_root / "verified_recurring_groups_v1_3_6_1.json", recurring_groups)
    save_json(output_root / "rejected_reasoning_groups_v1_3_6_1.json", rejected_groups)

    by_id = {c["candidate_id"]: c for c in all_candidates}
    high_value_ids = deterministic_high_value_ids(all_candidates, unique_ids, recurring_groups)
    review_union = list(dict.fromkeys(
        list(review_ids)
        + [c["candidate_id"] for c in all_candidates if "[unclear]" in str(c.get("raw_source_text") or "").lower()]
        + [str(x.get("candidate_id")) for x in ocr_recheck_queue if x.get("candidate_id") in by_id]
    ))

    provisional = {
        "sorter_version": VERSION,
        "status": "provisional_not_approved",
        "recurring_groups": recurring_groups,
        "high_value_unique_candidates": [by_id[x] for x in high_value_ids if x in by_id],
        "human_review_candidates": [by_id[x] for x in review_union if x in by_id],
        "ocr_recheck_queue": ocr_recheck_queue,
        "repeated_line_inventory": repeated_lines,
        "model_reasoning_proposal": parsed_reason,
        "candidate_kind_counts": candidate_kind_counts(all_candidates),
        "sanitation_audit_count": sanitation_audit_count,
        "automatic_fact_acceptance": False,
        "accepted_fact_count": 0,
        "qdrant_entries_created": 0,
    }
    save_json(output_root / "provisional_sort_v1_3_6_1.json", provisional)
    summarize_text(
        output_root,
        records,
        all_candidates,
        all_rejected_candidates,
        repeated_lines,
        recurring_groups,
        rejected_groups,
        high_value_ids,
        review_union,
        ocr_recheck_queue,
        sanitation_audit_count,
    )

    base_manifest.update({
        "reasoning_performed": True,
        "reasoning_run_action": reason_action,
        "reason_prompt_sha256": reason_prompt_sha,
        "verified_recurring_group_count": len(recurring_groups),
        "rejected_reasoning_group_count": len(rejected_groups),
        "high_value_unique_candidate_count": len(high_value_ids),
        "ocr_recheck_queue_count": len(ocr_recheck_queue),
        "human_review_candidate_count": len(review_union),
        "status": "complete_provisional_sort_not_approved",
    })
    save_json(output_root / "sort_manifest_v1_3_6_1.json", base_manifest)

    print()
    print(f"Routine form/admin audit entries: {sanitation_audit_count}")
    print(f"Evidence-backed candidates:       {len(all_candidates)}")
    print(f"Rejected prospector candidates:   {len(all_rejected_candidates)}")
    print(f"Verified recurring groups:        {len(recurring_groups)}")
    print(f"Rejected reasoning groups:        {len(rejected_groups)}")
    print(f"High-value unique candidates:     {len(high_value_ids)}")
    print(f"OCR recheck queue:                {len(ocr_recheck_queue)}")
    print(f"Human-review candidates:          {len(review_union)}")
    print("Accepted repair facts:            0")
    print("Qdrant entries created:           0")
    print(f"Summary: {output_root / 'provisional_sort_summary_v1_3_6_1.txt'}")
    print(f"Manifest: {output_root / 'sort_manifest_v1_3_6_1.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
