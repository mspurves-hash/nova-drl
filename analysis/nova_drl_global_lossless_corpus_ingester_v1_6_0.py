#!/usr/bin/env python3
"""Nova DRL Global Lossless Corpus Ingester v1.6.0.

Global production re-ingestion path built from the quantitatively proven evidence-first
Qwen3-VL 8B architecture.  It preserves evidence before interpretation and never lets a
later model rewrite/delete primary machine evidence.

Fixed default pipeline for every selected Traveler / Line Card source record:
  1) frozen v1.3.5.1 whole-page transcription (vision, immutable raw)
  2) frozen v1.3.6.1 high-recall prospector over a deterministic working view (text)
  3) globally generic high-recall technical evidence pass (vision, additive)
  4) globally generic PN/reference hunter pass (vision, additive)
  5) deterministic lossless section/ledger merge (no LLM rewrite)

The v1.5.1 frozen full-corpus membership and v1.5.2 source/event selection machinery are
reused so this changes EVIDENCE QUALITY, not corpus membership.  Qdrant writes remain OFF.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.6.0"
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BASE_SCRIPT = SCRIPT_DIR / "nova_drl_full_corpus_ingester_v1_5_2.py"
PARSER_SCRIPT = REPO_ROOT / "tools" / "drl_lossless_evidence_parser_v1_6_0.py"

DEFAULT_INDEX_DB = Path("/opt/nova-drl/index/drl_file_index.sqlite")
DEFAULT_SHARE_ROOT = Path("/mnt/drl")
DEFAULT_TECH_BASE = "000 folder for tech scans"
DEFAULT_CORPUS_MANIFEST = Path("/opt/nova-drl/corpus/drl_full_corpus_v1_5_1/full_corpus_manifest_v1_5_1.json")
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/drl_global_lossless_corpus_v1_6_0")
DEFAULT_MODEL = "qwen3-vl-drl:8b-q8-16k"
DEFAULT_TYPED_PAIR_ENGINEER = "ROGER"
DEFAULT_CORPUS_SEED = "nova-drl-full-corpus-v1.5.1"

# Exact historically replayed/frozen prompt.  Hash is regression-locked below.
TRANSCRIPTION_PROMPT = """Transcribe this complete DRL Traveler image as faithfully as possible.

RULES:
- Read the entire visible page, including printed, typed, stamped, and handwritten text.
- Return transcription only. Do not summarize, interpret, classify, normalize, explain, or answer questions about the page.
- Preserve wording, unusual shop terms, abbreviations, part numbers, quantities, punctuation, and spelling as you actually read them.
- Do not silently replace unusual wording with a more familiar term.
- Do not infer missing words or unstated quantities.
- Do not decide which text is important, boilerplate, garbage, a repair action, a part, a diagnosis, testing, or administration.
- Do not convert printed form choices into completed actions merely because the words are visible.
- Follow natural page reading order as well as possible.
- If text cannot be read reliably, write [unclear].
- Do not repeat text unless it is actually repeated on the page.
"""

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

# Generic cross-family form: no equipment/part answers embedded.
HIGH_RECALL_PROMPT = r"""Read this ONE DRL Traveler / Line Card as a high-recall evidence collector.

GOAL: preserve essentially every visible TECHNICAL fact that could later help a DRL technician.
Do not summarize several handwritten lines into one generic sentence. Do not decide that a
cleaning, adjustment, alignment, calibration, parameter setting, vacuum check, component swap,
replacement, rebuild, or test is unimportant. Capture each useful fact separately.

Return concise plain text with these headings when evidence exists:
REPORTED FAILURE / CUSTOMER COMPLAINT:
EXPLICIT PARTS / COMPONENTS REPLACED, INSTALLED, SWAPPED, REBUILT OR USED:
PART / REFERENCE NUMBERS:
OTHER TECHNICAL REPAIR / SERVICE ACTIONS:
EXPLICIT TEST / OUTCOME:
TRACKING / ORDER METADATA:

Rules:
- RECALL FIRST. Preserve the visible fact even if you are unsure which later database field it belongs in.
- One bullet per distinct technical fact whenever practical.
- Preserve action + component + axis together when applicable.
- Preserve exact or likely part/reference strings as written. If one character is uncertain, keep the
  useful string and mark only that uncertainty; do not drop the whole PN.
- Replacement means the card explicitly indicates replaced/installed/swapped/rebuilt/used. Do not turn
  cleaning, adjusting, inspecting, testing, or lubricating into a replacement.
- Capture handwritten notes in BOTH the complaint/special-notes area and repair/replacement area.
- Ground everything in the image. Do not invent missing technical details.
- Do not waste output on blank form labels or administrative boilerplate.
"""

PN_FOCUS_PROMPT = r"""Read this ONE DRL Traveler / Line Card as a PART / REFERENCE NUMBER HUNTER.

Do not summarize the repair. Your only job is to preserve visible component/manufacturer/reference
identifiers that may be useful for parts intelligence.

Look carefully in ALL handwritten and typed technical areas, especially replacement notes, ordered-parts
areas, margins, and text next to motors, encoders, ICs, boards, belts, sensors, CCDs, LEDs, solenoids,
and other components.

Return plain text, one candidate per line, in this form when possible:
PART/REFERENCE: <string> | CONTEXT: <nearby component/action text>

Rules:
- Preserve alphanumeric strings and punctuation as written. If one character is uncertain, retain the
  rest and mark only that character with ? rather than dropping the candidate.
- Do not turn DRL log numbers, RMA, Customer PO, dates, phone numbers, prices, DGK/MSR/NWK/DSK order refs,
  or serial numbers into manufacturer PNs.
- Do not invent a PN from the component name.
- If no plausible manufacturer/component/reference string is visible, return: NONE VISIBLE.
"""

FROZEN_PROMPT_HASHES = {
    "transcription": "590415fdf89e713b85784bddf021652e19d08830545071f9378d79e9bc9dc954",
    "prospector": "a0488846ab067b976643186a81f2af42c6a98234c03f96d5b1e2bf7bf0b4ad83",
}

# Historical working-view sanitation preserved from the frozen replay.
_HOURS_FINAL_RE = re.compile(r"(?i)hours\s+in\s+final\s+testing\s*:?\s*(?:\d[0-9A-Za-z+._-]*)?")
_FORM_ADMIN_FULLLINE_PATTERNS = [
    re.compile(r"^Direct Repair Laboratories\s*-?\s*Testing Traveler.*$", re.I),
    re.compile(r'^["“]?\\Drlserver\\ctrack database\\traveler\.doc["”]?$', re.I),
    re.compile(r"^Log\s*#.*$", re.I),
    re.compile(r"^(?:Customer(?: Name)?|CustRMA|Cust PO|Customer PO(?: Number)?|Unit Type|Serial\s*#|Board Serial\s*#|Frame Serial\s*#|Board\(s\) serial #\(s\)|Frame\(s\) serial#\(s\)|Sales Rep|DRL SalesRep|DRL Rep|Point Of Contact|POC Phone|POC Email|Contact|Phone|Email)(?:\s*:|\s+|$).*$", re.I),
    re.compile(r"^(?:Warranty|Warranty Date|Warranty Type|Sticker Swap|Pricing Approved|pricing approved|needs quote)\b.*$", re.I),
    re.compile(r"^[✓✔☑☐XxVv ]*pricing approved\b.*$", re.I),
    re.compile(r"^(?:Special Notes \(if any\) below\..*|Responsible tech\. to init\. & date compliance\.?|\[Notes \(specific to this .+\)\]|PACKAGING STATUS:|Packaging Status:|Repaired Replaced|Detailed description of repairs/replacements|\(including any costs for new parts\)|Inits\. Date|\(m/d/y{1,2}\)|~Revised~)$", re.I),
    re.compile(r"^Date Shipped\b.*$", re.I),
    re.compile(r"^(?:Saved \(in shipping area\)|Saved \(in warehouse\)|Unusable \(discarded\))\b.*$", re.I),
    re.compile(r"^Final O\.K\.?\b.*$", re.I),
]
_FORM_PREFIX_PATTERNS = [
    re.compile(r"^Final Unit Test Results and Notes\s*:?\s*", re.I),
    re.compile(r"^(?:No Trouble Found|Passed All Tests|Basic Functional Tests Only|Power-on Tests Only|Untestable, Inspection Only)\s*(?:(?:[✓✔☑☐XxVv])|(?:\[[^\]]*\]))?\s*", re.I),
    re.compile(r"^Ttl Time Spent \(Hours\)\s*(?:(?:[✓✔☑☐XxVv])|(?:\[[^\]]*\]))?\s*", re.I),
    re.compile(r"^Ttl Money Spent \(Dollars\)\s*(?:(?:[✓✔☑☐XxVv])|(?:\[[^\]]*\]))?\s*", re.I),
]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_module(path: Path, name: str):
    if not path.exists():
        raise RuntimeError(f"Required Nova DRL module not found: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


base152 = load_module(BASE_SCRIPT, "nova_drl_v152_base")
base = base152.base
parser = load_module(PARSER_SCRIPT, "nova_drl_v160_parser")


def sanitize_working_view(raw: str) -> str:
    masked = _HOURS_FINAL_RE.sub("", raw or "")
    kept: List[str] = []
    for line in masked.splitlines():
        clean = line.strip()
        if not clean:
            kept.append("")
            continue
        normalized = re.sub(r"\s+", " ", clean).strip()
        if any(p.fullmatch(normalized) for p in _FORM_ADMIN_FULLLINE_PATTERNS):
            kept.append("")
            continue
        stripped = clean
        changed = False
        for p in _FORM_PREFIX_PATTERNS:
            m = p.match(stripped)
            if m:
                stripped = stripped[m.end() :].strip()
                changed = True
                break
        kept.append(stripped if changed else line)
    return "\n".join(kept) + ("\n" if (raw or "").endswith("\n") else "")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def prompt_hashes() -> Dict[str, str]:
    return {
        "transcription": sha256_text(TRANSCRIPTION_PROMPT),
        "prospector": sha256_text(PROSPECT_PROMPT),
        "high_recall": sha256_text(HIGH_RECALL_PROMPT),
        "pn_focus": sha256_text(PN_FOCUS_PROMPT),
    }


def verify_frozen_prompts() -> None:
    hashes = prompt_hashes()
    for k, expected in FROZEN_PROMPT_HASHES.items():
        if hashes[k] != expected:
            raise RuntimeError(f"FROZEN PROMPT DRIFT: {k} {hashes[k]} != {expected}")


def pipeline_lock_payload(model: str) -> Dict[str, Any]:
    info = base.model_info(model)
    if not info.get("available"):
        raise RuntimeError(f"Proven model is not available in Ollama: {model} | {info.get('error') or ''}")
    return {
        "version": VERSION,
        "pipeline": "global_proven_lossless",
        "vision_model": model,
        "vision_model_digest": info.get("digest"),
        "prompt_sha256": prompt_hashes(),
        "passes": ["transcription", "prospector", "high_recall", "pn_focus"],
        "later_model_rewrite": False,
        "raw_evidence_deletion": False,
        "qdrant_writes": False,
        "accepted_facts": 0,
    }


def establish_or_verify_lock(output_root: Path, model: str, accept_change: bool) -> Dict[str, Any]:
    verify_frozen_prompts()
    current = pipeline_lock_payload(model)
    lock_path = output_root / "proven_pipeline_lock_v1_6_0.json"
    if lock_path.exists():
        old = base.load_json(lock_path)
        comparable = ("vision_model", "vision_model_digest", "prompt_sha256", "passes")
        diffs = {k: (old.get(k), current.get(k)) for k in comparable if old.get(k) != current.get(k)}
        if diffs and not accept_change:
            raise RuntimeError(
                "PROVEN BASELINE LOCK MISMATCH. Refusing silent model/prompt/role change. "
                f"Differences: {json.dumps(diffs, ensure_ascii=False)}. "
                "Only use --accept-unproven-baseline-change after an explicit controlled benchmark/approval."
            )
        if diffs:
            history = output_root / "baseline_change_history_v1_6_0.jsonl"
            with history.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"previous": old, "new": current, "explicit_override": True}, ensure_ascii=False) + "\n")
            base.save_json(lock_path, current)
        return current
    output_root.mkdir(parents=True, exist_ok=True)
    base.save_json(lock_path, current)
    return current


def _error_detail(exc: Exception) -> str:
    try:
        return base152._http_error_detail(exc)
    except Exception:
        return str(exc)


def _pass_cache_ok(meta_path: Path, txt_path: Path, src: Dict[str, Any], model_digest: str, prompt_sha: str) -> bool:
    if not meta_path.exists() or not txt_path.exists():
        return False
    try:
        m = base.load_json(meta_path)
        return (
            m.get("status") == "ok"
            and m.get("source_image_sha256") == src.get("source_image_sha256")
            and m.get("model_digest") == model_digest
            and m.get("prompt_sha256") == prompt_sha
        )
    except Exception:
        return False


def run_vision_pass(
    *,
    args: argparse.Namespace,
    src: Dict[str, Any],
    pass_name: str,
    prompt: str,
    num_predict: int,
    model_digest: str,
) -> Dict[str, Any]:
    """Run one additive vision pass with v1.5.2 resilient image handling."""
    rec_dir = Path(args.output_root) / "source_records" / src["source_record_id"] / pass_name
    rec_dir.mkdir(parents=True, exist_ok=True)
    txt_path = rec_dir / "raw.txt"
    meta_path = rec_dir / "meta.json"
    psha = sha256_text(prompt)
    if _pass_cache_ok(meta_path, txt_path, src, model_digest, psha):
        text = txt_path.read_text(encoding="utf-8", errors="replace")
        return {"status": "ok", "action": "cache", "text": text, "text_path": str(txt_path), "meta_path": str(meta_path), "elapsed_seconds": 0.0}

    source = Path(src["source_image"])
    t0 = time.time()
    attempts: List[Dict[str, Any]] = []
    text = ""
    status = "ok"
    normalized_retry = False
    try:
        text = base.call_ollama(
            args.vision_model,
            prompt,
            image_paths=[source],
            num_ctx=args.num_ctx,
            num_predict=num_predict,
            timeout=args.timeout,
        )
        attempts.append({"input": "original", "ok": True})
        action = "model_run"
    except Exception as exc1:
        attempts.append({"input": "original", "ok": False, "error": _error_detail(exc1)})
        normalized = Path(args.output_root) / "normalized_retry" / f"{src['source_record_id']}.jpg"
        try:
            norm_meta = base152.normalize_image_for_vision(source, normalized)
            normalized_retry = True
            text = base.call_ollama(
                args.vision_model,
                prompt,
                image_paths=[normalized],
                num_ctx=args.num_ctx,
                num_predict=num_predict,
                timeout=args.timeout,
            )
            attempts.append({"input": "normalized_rgb_jpeg", "ok": True, **norm_meta})
            action = "normalized_retry"
        except Exception as exc2:
            attempts.append({"input": "normalized_rgb_jpeg", "ok": False, "error": _error_detail(exc2)})
            status = "exception"
            action = "exception_continued"

    elapsed = time.time() - t0
    # Raw model response is immutable for this source/model/prompt lock once successful.
    txt_path.write_text(text, encoding="utf-8")
    meta = {
        "version": VERSION,
        "status": status,
        "pass": pass_name,
        "source_record_id": src.get("source_record_id"),
        "repair_event_id": src.get("repair_event_id"),
        "source_path": src.get("source_path"),
        "source_image_sha256": src.get("source_image_sha256"),
        "model": args.vision_model,
        "model_digest": model_digest,
        "prompt_sha256": psha,
        "elapsed_seconds": round(elapsed, 3),
        "normalized_retry": normalized_retry,
        "attempts": attempts,
        "accepted_facts": 0,
        "qdrant_entries": 0,
    }
    base.save_json(meta_path, meta)
    return {"status": status, "action": action, "text": text, "text_path": str(txt_path), "meta_path": str(meta_path), "elapsed_seconds": elapsed}


def run_prospector(
    *,
    args: argparse.Namespace,
    src: Dict[str, Any],
    raw_transcription: str,
    model_digest: str,
) -> Dict[str, Any]:
    rec_dir = Path(args.output_root) / "source_records" / src["source_record_id"] / "prospector"
    rec_dir.mkdir(parents=True, exist_ok=True)
    working_path = rec_dir / "working_view.txt"
    raw_path = rec_dir / "raw.txt"
    parsed_path = rec_dir / "parsed.json"
    meta_path = rec_dir / "meta.json"
    working = sanitize_working_view(raw_transcription)
    working_path.write_text(working, encoding="utf-8")
    base_prompt_sha = sha256_text(PROSPECT_PROMPT)
    input_sha = sha256_text(working)
    log = src.get("log_number") or src.get("repair_event_id") or "unknown"
    full_prompt = f"{PROSPECT_PROMPT}\n\nDRL LOG: {log}\nRAW TRANSCRIPTION FOR PROSPECTING:\n{working}\n"

    cache_ok = False
    if meta_path.exists() and raw_path.exists() and parsed_path.exists():
        try:
            m = base.load_json(meta_path)
            cache_ok = (
                m.get("status") == "ok"
                and m.get("source_image_sha256") == src.get("source_image_sha256")
                and m.get("model_digest") == model_digest
                and m.get("base_prompt_sha256") == base_prompt_sha
                and m.get("working_view_sha256") == input_sha
            )
        except Exception:
            cache_ok = False
    if cache_ok:
        raw = raw_path.read_text(encoding="utf-8", errors="replace")
        return {"status": "ok", "action": "cache", "text": raw, "working_view": working, "elapsed_seconds": 0.0, "raw_path": str(raw_path), "parsed_path": str(parsed_path)}

    t0 = time.time()
    try:
        raw = base.call_ollama(
            args.vision_model,
            full_prompt,
            image_paths=None,
            num_ctx=args.num_ctx,
            num_predict=args.prospector_num_predict,
            timeout=args.timeout,
        )
        parsed = parser.parse_json_loose(raw)
        status, action = "ok", "model_run"
    except Exception as exc:
        raw, parsed = "", {}
        status, action = "exception", "exception_continued"
        err = _error_detail(exc)
    elapsed = time.time() - t0
    raw_path.write_text(raw, encoding="utf-8")
    base.save_json(parsed_path, parsed)
    meta = {
        "version": VERSION,
        "status": status,
        "pass": "prospector",
        "source_record_id": src.get("source_record_id"),
        "repair_event_id": src.get("repair_event_id"),
        "source_path": src.get("source_path"),
        "source_image_sha256": src.get("source_image_sha256"),
        "model": args.vision_model,
        "model_digest": model_digest,
        "base_prompt_sha256": base_prompt_sha,
        "full_prompt_sha256": sha256_text(full_prompt),
        "working_view_sha256": input_sha,
        "elapsed_seconds": round(elapsed, 3),
        "error": err if status != "ok" else None,
        "accepted_facts": 0,
        "qdrant_entries": 0,
    }
    base.save_json(meta_path, meta)
    return {"status": status, "action": action, "text": raw, "working_view": working, "elapsed_seconds": elapsed, "raw_path": str(raw_path), "parsed_path": str(parsed_path)}


def record_plan_with_log(src: Dict[str, Any], event_by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    row = dict(src)
    ev = event_by_id.get(src.get("repair_event_id"), {})
    row["log_number"] = ev.get("log_number")
    row["legacy_event_token"] = ev.get("legacy_event_token")
    return row


def process_source_records(args: argparse.Namespace, records: Sequence[Dict[str, Any]], events: Sequence[Dict[str, Any]], lock: Dict[str, Any]) -> List[Dict[str, Any]]:
    event_by_id = {e["repair_event_id"]: e for e in events}
    model_digest = str(lock.get("vision_model_digest") or "")
    out: List[Dict[str, Any]] = []
    durations: Dict[str, List[float]] = defaultdict(list)
    exceptions: List[Dict[str, Any]] = []
    total = len(records)

    for i, raw_src in enumerate(records, 1):
        src = record_plan_with_log(raw_src, event_by_id)
        tr = run_vision_pass(args=args, src=src, pass_name="transcription", prompt=TRANSCRIPTION_PROMPT, num_predict=args.transcription_num_predict, model_digest=model_digest)
        durations["transcription"].append(tr["elapsed_seconds"])
        pr = run_prospector(args=args, src=src, raw_transcription=tr["text"], model_digest=model_digest) if tr["status"] == "ok" else {"status":"skipped","action":"no_transcription","text":"","working_view":"","elapsed_seconds":0.0,"raw_path":None,"parsed_path":None}
        durations["prospector"].append(pr["elapsed_seconds"])
        hr = run_vision_pass(args=args, src=src, pass_name="high_recall", prompt=HIGH_RECALL_PROMPT, num_predict=args.high_recall_num_predict, model_digest=model_digest)
        durations["high_recall"].append(hr["elapsed_seconds"])
        pn = run_vision_pass(args=args, src=src, pass_name="pn_focus", prompt=PN_FOCUS_PROMPT, num_predict=args.pn_num_predict, model_digest=model_digest)
        durations["pn_focus"].append(pn["elapsed_seconds"])

        ledger_rows = parser.evidence_rows_for_record(
            src,
            high_recall_text=hr["text"],
            prospector_text=pr["text"],
            prospector_working_view=pr["working_view"],
            pn_focus_text=pn["text"],
        )
        rec_dir = Path(args.output_root) / "source_records" / src["source_record_id"]
        ledger_path = rec_dir / "evidence_ledger.jsonl"
        write_jsonl(ledger_path, ledger_rows)

        tracking = parser.tracking_from_texts(
            [
                ("transcription", tr["text"]),
                ("high_recall", hr["text"]),
                ("pn_focus", pn["text"]),
            ]
        )
        row = dict(src)
        row.update(
            {
                "pipeline_version": VERSION,
                "transcription_path": tr.get("text_path"),
                "prospector_raw_path": pr.get("raw_path"),
                "prospector_parsed_path": pr.get("parsed_path"),
                "high_recall_path": hr.get("text_path"),
                "pn_focus_path": pn.get("text_path"),
                "evidence_ledger_path": str(ledger_path),
                "evidence_row_count": len(ledger_rows),
                "tracking": tracking,
                "pass_status": {"transcription": tr["status"], "prospector": pr["status"], "high_recall": hr["status"], "pn_focus": pn["status"]},
            }
        )
        out.append(row)
        bad = [k for k, v in row["pass_status"].items() if v not in {"ok"}]
        if bad:
            exceptions.append({"source_record_id": src["source_record_id"], "repair_event_id": src["repair_event_id"], "source_path": src["source_path"], "pass_status": row["pass_status"]})
        print(
            f"[{i:05d}/{total}] {src['repair_event_id']} {src['source_record_id']} | "
            f"T={tr['action']} P={pr['action']} H={hr['action']} PN={pn['action']} | evidence={len(ledger_rows)}"
        )

    write_jsonl(Path(args.output_root) / "source_pass_exceptions_v1_6_0.jsonl", exceptions)
    timing = {}
    for k, vals in durations.items():
        live = [v for v in vals if v > 0]
        timing[k] = {
            "model_runs": len(live),
            "seconds_total": round(sum(live), 3),
            "seconds_median": round(statistics.median(live), 3) if live else 0.0,
            "seconds_mean": round(sum(live) / len(live), 3) if live else 0.0,
        }
    base.save_json(Path(args.output_root) / "pass_timing_v1_6_0.json", timing)
    return out


def compile_event_views(args: argparse.Namespace, events: Sequence[Dict[str, Any]], source_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_event: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in source_rows:
        by_event[r["repair_event_id"]].append(r)
    out: List[Dict[str, Any]] = []
    all_ledger_rows: List[Dict[str, Any]] = []
    rma_rows: List[Dict[str, Any]] = []
    po_rows: List[Dict[str, Any]] = []
    order_rows: List[Dict[str, Any]] = []

    for event in events:
        eid = event["repair_event_id"]
        recs = by_event.get(eid, [])
        if not recs:
            continue
        ledger: List[Dict[str, Any]] = []
        for r in recs:
            ledger.extend(read_jsonl(Path(r["evidence_ledger_path"])))
        all_ledger_rows.extend(ledger)
        facts = parser.derive_event_facts(ledger)

        # Literal tracking union across source records.
        rmas: Dict[str, Dict[str, Any]] = {}
        pos: Dict[str, Dict[str, Any]] = {}
        orders: Dict[str, Dict[str, Any]] = {}
        for r in recs:
            for x in r.get("tracking", {}).get("rma_numbers", []):
                y = dict(x); y["source_path"] = r.get("source_path")
                rmas.setdefault(str(y.get("normalized")), y)
            for x in r.get("tracking", {}).get("customer_po_numbers", []):
                y = dict(x); y["source_path"] = r.get("source_path")
                pos.setdefault(str(y.get("normalized")), y)
            for x in r.get("tracking", {}).get("procurement_refs", []):
                y = dict(x); y["source_path"] = r.get("source_path")
                orders.setdefault(str(y.get("normalized")), y)

        row = {
            "version": VERSION,
            "repair_event_id": eid,
            "log_number": event.get("log_number"),
            "legacy_event_token": event.get("legacy_event_token"),
            "equipment_family": event.get("equipment_family"),
            "equipment_families": event.get("equipment_families"),
            "top_folders": event.get("top_folders"),
            "typed_pair_engineer_match": event.get("typed_pair_engineer_match"),
            "typed_pair_optimization_applied": event.get("typed_pair_optimization_applied"),
            "primary_source_record_ids": [r.get("source_record_id") for r in recs],
            "primary_source_paths": [r.get("source_path") for r in recs],
            "supporting_documents": event.get("supporting_documents"),
            "facts": facts,
            "tracking": {"rma_numbers": list(rmas.values()), "procurement_refs": list(orders.values())},
            "customer_po_numbers": list(pos.values()),
            "evidence_counts": {
                "ledger_rows": len(ledger),
                "reported_failure": len(facts["reported_failure"]),
                "parts_replaced": len(facts["parts_replaced"]),
                "part_references": len(facts["part_references"]),
                "repair_actions": len(facts["repair_actions"]),
                "explicit_test_outcome": len(facts["explicit_test_outcome"]),
                "prospector_candidates": len(facts["prospector_candidates"]),
                "unassigned_high_recall": len(facts["unassigned_high_recall"]),
            },
            "policy": {
                "raw_evidence_preserved": True,
                "later_model_rewrite": False,
                "component_normalization_at_ingest": False,
                "ambiguous_evidence_preserved": True,
                "accepted_facts": 0,
                "qdrant_entries": 0,
            },
        }
        out.append(row)
        for x in rmas.values():
            rma_rows.append({"repair_event_id": eid, "log_number": event.get("log_number"), "equipment_family": event.get("equipment_family"), "rma_number": x.get("value"), "rma_normalized": x.get("normalized"), "evidence_quote": x.get("evidence_quote"), "source_path": x.get("source_path")})
        for x in pos.values():
            po_rows.append({"repair_event_id": eid, "log_number": event.get("log_number"), "equipment_family": event.get("equipment_family"), "customer_po": x.get("value"), "customer_po_normalized": x.get("normalized"), "evidence_quote": x.get("evidence_quote"), "source_path": x.get("source_path")})
        for x in orders.values():
            order_rows.append({"repair_event_id": eid, "log_number": event.get("log_number"), "equipment_family": event.get("equipment_family"), "supplier": x.get("supplier"), "order_ref": x.get("order_ref"), "order_ref_normalized": x.get("normalized"), "evidence_quote": x.get("evidence_quote"), "source_path": x.get("source_path")})

    root = Path(args.output_root)
    write_jsonl(root / "raw_evidence_ledger_v1_6_0.jsonl", all_ledger_rows)
    write_jsonl(root / "repair_events_lossless_v1_6_0.jsonl", out)
    write_jsonl(root / "rma_refs_v1_6_0.jsonl", rma_rows)
    write_jsonl(root / "customer_po_refs_v1_6_0.jsonl", po_rows)
    write_jsonl(root / "procurement_refs_v1_6_0.jsonl", order_rows)

    def write_csv(name: str, rows: List[Dict[str, Any]], fields: List[str]) -> None:
        with (root / name).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k) for k in fields})

    write_csv("rma_lookup_v1_6_0.csv", rma_rows, ["repair_event_id", "log_number", "equipment_family", "rma_number", "evidence_quote", "source_path"])
    write_csv("customer_po_lookup_v1_6_0.csv", po_rows, ["repair_event_id", "log_number", "equipment_family", "customer_po", "evidence_quote", "source_path"])
    write_csv("procurement_refs_v1_6_0.csv", order_rows, ["repair_event_id", "log_number", "equipment_family", "supplier", "order_ref", "evidence_quote", "source_path"])
    return out


def prepare_args(args: argparse.Namespace) -> None:
    # Compatibility attributes used by frozen v1.5.2/v1.4.6 source selection.
    args.sample_manifest = args.corpus_manifest
    args.sample_percent = 100.0
    args.sample_seed = DEFAULT_CORPUS_SEED
    args.force_sample = False
    args.force_corpus = False
    args.limit_sampled_folders = None
    args.typed_pair_engineer = DEFAULT_TYPED_PAIR_ENGINEER
    args.no_reuse_benchmark = True


def prepare_corpus(args: argparse.Namespace, persist: bool):
    prepare_args(args)
    if not Path(args.corpus_manifest).exists():
        raise RuntimeError(
            f"Frozen full-corpus manifest not found: {args.corpus_manifest}. "
            "v1.6.0 intentionally refuses to silently redefine corpus membership."
        )
    return base152.prepare(args, persist=persist)


def plan(args: argparse.Namespace) -> int:
    try:
        _, _, corpus, selection, events = prepare_corpus(args, persist=False)
        source_count = base152.count_planned_vision(events, set(), args.render_dpi)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"# Nova DRL Global Lossless Corpus Ingester v{VERSION} — PLAN ONLY")
    print(f"Frozen corpus folders:            {corpus.get('sample_folder_count')}")
    print(f"Selected Line Cards/Travelers:     {selection.get('selected_document_count')}")
    print(f"Folder exceptions:                 {selection.get('folder_exception_count')}")
    print(f"Distinct repair events:            {len(events)}")
    print(f"Primary source records (planned):  {source_count}")
    print(f"Vision calls / source:             3 (transcription + high-recall + PN focus)")
    print(f"Text model calls / source:         1 (frozen 8B prospector)")
    print(f"Maximum model calls before cache:  {source_count * 4}")
    print(f"Proven model:                      {args.vision_model}")
    print(f"Frozen transcription hash:         {prompt_hashes()['transcription']}")
    print(f"Frozen prospector hash:            {prompt_hashes()['prospector']}")
    print(f"Generic high-recall hash:          {prompt_hashes()['high_recall']}")
    print(f"Generic PN-focus hash:             {prompt_hashes()['pn_focus']}")
    print("Later 14B/32B evidence rewrite:    OFF")
    print("Raw evidence deletion:             OFF")
    print("Component family normalization:    DEFERRED to downstream global resolver")
    print("Accepted facts:                    0")
    print("Qdrant:                            OFF")
    print("80/20 rule:                        HARD GOVERNING RULE")
    return 0


def status(args: argparse.Namespace) -> int:
    print(f"# Nova DRL Global Lossless Corpus Status v{VERSION}")
    print(f"Index:            {'FOUND' if Path(args.index_db).exists() else 'MISSING'} | {args.index_db}")
    print(f"Share:            {'FOUND' if Path(args.share_root).exists() else 'MISSING'} | {args.share_root}")
    print(f"Frozen manifest:  {'FOUND' if Path(args.corpus_manifest).exists() else 'MISSING'} | {args.corpus_manifest}")
    print(f"Base v1.5.2:      {'FOUND' if BASE_SCRIPT.exists() else 'MISSING'} | {BASE_SCRIPT}")
    print(f"Parser v1.6.0:    {'FOUND' if PARSER_SCRIPT.exists() else 'MISSING'} | {PARSER_SCRIPT}")
    info = base.model_info(args.vision_model)
    print(f"Proven model:     {'FOUND' if info.get('available') else 'MISSING'} | {args.vision_model} | digest={info.get('digest')}")
    print(f"Output root:      {args.output_root}")
    print("80/20 rule:       HARD GOVERNING RULE")
    print("Qdrant:           OFF")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=f"Nova DRL Global Lossless Corpus Ingester v{VERSION}")
    ap.add_argument("--index-db", default=str(DEFAULT_INDEX_DB))
    ap.add_argument("--share-root", default=str(DEFAULT_SHARE_ROOT))
    ap.add_argument("--tech-base", default=DEFAULT_TECH_BASE)
    ap.add_argument("--corpus-manifest", default=str(DEFAULT_CORPUS_MANIFEST))
    ap.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    ap.add_argument("--vision-model", default=DEFAULT_MODEL)
    ap.add_argument("--num-ctx", type=int, default=16384)
    ap.add_argument("--transcription-num-predict", type=int, default=4096)
    ap.add_argument("--prospector-num-predict", type=int, default=3072)
    ap.add_argument("--high-recall-num-predict", type=int, default=4096)
    ap.add_argument("--pn-num-predict", type=int, default=1536)
    ap.add_argument("--render-dpi", type=int, default=300)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--limit-events", type=int, default=0, help="Smoke/resume prefix; 0 = full frozen corpus")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--manifest-only", action="store_true")
    ap.add_argument("--accept-unproven-baseline-change", action="store_true", help="Explicit override only after controlled benchmark/approval")
    args = ap.parse_args()

    verify_frozen_prompts()
    if args.vision_model != DEFAULT_MODEL and not args.accept_unproven_baseline_change:
        print(
            f"ERROR: Proven global model is {DEFAULT_MODEL}. Refusing silent substitution to {args.vision_model}. "
            "Use --accept-unproven-baseline-change only after explicit benchmark/approval.",
            file=sys.stderr,
        )
        return 2
    if args.status:
        return status(args)
    if args.plan_only:
        return plan(args)

    try:
        _, _, corpus, selection, all_events = prepare_corpus(args, persist=True)
        lock = establish_or_verify_lock(Path(args.output_root), args.vision_model, args.accept_unproven_baseline_change)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    events = all_events[: args.limit_events or None]
    mode = "SMOKE/RESUMABLE PREFIX" if args.limit_events else "FULL FROZEN CORPUS"
    print(f"# Nova DRL Global Lossless Corpus Ingester v{VERSION}")
    print(f"Mode: {mode}")
    print(f"Frozen full-corpus events: {len(all_events)}")
    print(f"Events this invocation:     {len(events)}")
    print(f"Selected source documents:  {selection.get('selected_document_count')}")
    print("Pipeline: frozen transcription + frozen prospector + generic high-recall + generic PN focus")
    print("Later evidence rewrite: OFF")
    print("Accepted facts: 0")
    print("Qdrant: OFF")

    root = Path(args.output_root)
    base.save_json(root / "full_corpus_manifest_snapshot_v1_6_0.json", corpus)
    base.save_json(root / "source_selection_v1_6_0.json", selection)
    base.save_json(root / "repair_event_plan_v1_6_0.json", {"version": VERSION, "events": all_events})
    if args.manifest_only:
        print("# MANIFEST COMPLETE")
        return 0

    source_records = base.expand_primary_sources(events, root, args.render_dpi)
    print(f"Primary source records this invocation: {len(source_records)}")
    source_rows = process_source_records(args, source_records, events, lock)
    write_jsonl(root / "source_records_v1_6_0.jsonl", source_rows)
    event_rows = compile_event_views(args, events, source_rows)

    total_ledger = sum((e.get("evidence_counts") or {}).get("ledger_rows", 0) for e in event_rows)
    counts = Counter()
    for e in event_rows:
        for k, v in (e.get("evidence_counts") or {}).items():
            counts[k] += int(v or 0)
    partial = len(events) < len(all_events)
    summary = {
        "version": VERSION,
        "partial": partial,
        "frozen_event_count": len(all_events),
        "events_processed": len(event_rows),
        "source_records_processed": len(source_rows),
        "evidence_rows": total_ledger,
        "evidence_counts": dict(counts),
        "pipeline_lock": lock,
        "80_20_rule": "hard governing rule",
        "accepted_facts": 0,
        "qdrant_entries": 0,
    }
    base.save_json(root / "summary_v1_6_0.json", summary)
    lines = [
        f"Nova DRL Global Lossless Corpus v{VERSION}",
        f"Partial run: {partial}",
        f"Frozen repair events: {len(all_events)}",
        f"Events processed this aggregate: {len(event_rows)}",
        f"Source records processed: {len(source_rows)}",
        f"Lossless evidence rows: {total_ledger}",
        f"Reported failure rows: {counts.get('reported_failure',0)}",
        f"Explicit parts-replaced rows: {counts.get('parts_replaced',0)}",
        f"Part/reference rows: {counts.get('part_references',0)}",
        f"Repair-action rows: {counts.get('repair_actions',0)}",
        f"Explicit test/outcome rows: {counts.get('explicit_test_outcome',0)}",
        f"Prospector candidate rows: {counts.get('prospector_candidates',0)}",
        "Later model evidence rewrite: OFF",
        "Raw evidence deletion: OFF",
        "Accepted facts: 0",
        "Qdrant: OFF",
        "80/20 rule: HARD GOVERNING RULE",
    ]
    (root / "drl_global_lossless_corpus_summary_v1_6_0.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n# COMPLETE" if not partial else "\n# PARTIAL / RESUMABLE COMPLETE")
    for line in lines[1:]:
        print(line)
    print(f"Events:   {root / 'repair_events_lossless_v1_6_0.jsonl'}")
    print(f"Evidence: {root / 'raw_evidence_ledger_v1_6_0.jsonl'}")
    print(f"Sources:  {root / 'source_records_v1_6_0.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
