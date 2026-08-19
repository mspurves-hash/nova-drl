#!/usr/bin/env python3
"""
Nova DRL Indexed Repair Event Intelligence v1.4.5

Generic, production-shaped 80/20 repair-history pipeline.

Primary source discovery:
- Persistent Nova DRL SQLite file index.
- Everything-style token query across the full indexed path.
- No recursive NAS walk.

Standing DRL Nova policy:
- 80/20 is the default operating principle until explicitly changed by Matt.
- Prefer high-volume recurring repair knowledge over perfect OCR/forensic cleanup.
- Preserve source paths/evidence so human review can investigate exceptions later.

Roger paired-Line-Card convention:
- This optimization applies ONLY when the indexed path identifies engineer ROGER.
- If a repair event has numbered Line Cards (1) and (2), (2) is the primary typed
  repair narrative and (1) is retained as supporting evidence without a deep vision read.
- Any (3+) Line Card remains primary/additional evidence.
- If (2) is absent, available cards are read normally.
- Other engineers do NOT inherit this convention.

Pipeline:
1) Query DRL index and select real Line Card image/PDF documents.
2) Group documents into repair events by valid 9-digit DRL log; when no valid log exists,
   a shared 8-12 digit legacy filename token may group obviously paired documents without
   pretending it is a valid DRL log.
3) Apply Roger-only primary/supporting-document selection.
4) Qwen3-VL 8B reads only PRIMARY Line Cards for high-signal repair evidence.
5) Qwen2.5 14B composes one structured repair-event record from the evidence.
6) Qwen2.5 14B clusters event facts by category into recurring 80/20 patterns.
7) Python owns event recurrence counts and preserves provenance.

No automatic approval. No Qdrant writes. Original share is read-only.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import re
import shlex
import sqlite3
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.4.5"
DEFAULT_INDEX_DB = Path("/opt/nova-drl/index/drl_file_index.sqlite")
DEFAULT_SHARE_ROOT = Path("/mnt/drl")
DEFAULT_INDEX_QUERY = "XU-RCM7231 LINE"
DEFAULT_OUTPUT_BASE = Path("/opt/nova-drl/output/indexed_repair_intelligence_v1_4_5")
DEFAULT_VISION_MODEL = "qwen3-vl-drl:8b-q8-16k"
DEFAULT_REASON_MODEL = "qwen25-drl:14b-q6-16k"
DEFAULT_ENGINEER_TYPED_PAIR = "ROGER"
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
LOG_RE = re.compile(r"(?<!\d)(\d{9})(?!\d)")
LEGACY_EVENT_RE = re.compile(r"(?<!\d)(\d{8,12})(?!\d)")
LINE_CARD_RE = re.compile(r"line\s*card", re.I)
SEQ_RE = re.compile(r"\((\d+)\)(?=\s*\.[^.]+$|\s*$)", re.I)

VISION_PROMPT = """Read this DRL Line Card for HIGH-SIGNAL REPAIR HISTORY using an 80/20 approach.

Do NOT attempt perfect OCR and do NOT transcribe the whole form. Capture the useful repair information that a veteran technician would care about. Prefer clear typed or clearly legible entries. Preserve exact technical terms and part numbers when reasonably legible, but do not spend effort guessing one uncertain character.

Return concise plain text only, using whichever of these headings have real information:
REPORTED SYMPTOM:
DIAGNOSTICS / FINDINGS:
REPAIR ACTIONS:
PARTS / ASSEMBLIES:
ADJUSTMENTS / CALIBRATION / TEACH:
TEST / VERIFICATION / RESULT:
OTHER TECHNICAL NOTES:

Rules:
- Ground everything in what is visible on this Line Card.
- Do not invent missing details.
- Repair actions may include cleaning, rebuilds, lubrication, alignment, cable/connector work, parameter loading, mechanical repair, etc.
- Capture replaced parts/assemblies and quantities when visible.
- Capture error codes, axis names, symptoms, measurements, calibration/teach operations, and final test results when visible.
- Omit empty/administrative fields unless they materially help identify the repair.
- This is evidence acquisition, not a final diagnosis and not a perfect transcription exercise.
"""

EVENT_PROMPT = """You are converting ONE DRL repair event into a concise structured technician-history record.
The evidence comes from one or more primary Line Cards for the SAME repair event.
Return JSON only with this exact top-level shape:
{
  "reported_symptoms": [{"text":"...","evidence_quote":"..."}],
  "diagnostics": [{"text":"...","evidence_quote":"..."}],
  "repair_actions": [{"text":"...","evidence_quote":"..."}],
  "parts_replaced": [{"text":"...","part_number":null,"quantity":null,"evidence_quote":"..."}],
  "adjustments_calibration": [{"text":"...","evidence_quote":"..."}],
  "testing_verification": [{"text":"...","evidence_quote":"..."}],
  "outcomes": [{"text":"...","evidence_quote":"..."}]
}

80/20 rules:
- Extract the useful technical repair story; do not chase minor wording differences.
- Keep separate facts when they are materially different.
- evidence_quote must be copied from the supplied evidence text, not invented.
- part_number is only populated when a PN/string is actually present in the evidence.
- quantity is an integer only when explicitly stated; otherwise null.
- Do not turn administrative/shop routing into technical repair facts.
- Do not infer a diagnosis that is not supported by the evidence.
- If a category has no useful evidence, return an empty list.
"""

CLUSTER_PROMPT = """You are clustering DRL repair-history facts from MANY repair events using the standing 80/20 rule.
Return JSON only:
{"clusters":[{"label":"short useful recurring technician pattern","member_ids":["f_..."],"reason":"short"}]}

Rules:
- Group facts that are effectively the same recurring failure, diagnostic observation, repair action, replaced component/assembly, adjustment/calibration operation, test practice, or outcome.
- Prefer useful broad recurrence over perfect wording/OCR normalization.
- Different axis/component/function should remain separate when that distinction matters to a technician.
- Use only supplied member IDs. Each ID may appear at most once.
- Do not invent facts.
- Omitted facts are preserved by Python as singletons, so focus on useful grouping.
"""

MERGE_PROMPT = """You are merging already-clustered DRL repair patterns within ONE category using an 80/20 rule.
Return JSON only:
{"clusters":[{"label":"short useful recurring technician pattern","member_cluster_ids":["c_..."]}]}

Rules:
- Merge only patterns that a technician would reasonably consider the same recurring pattern.
- Preserve distinctions that materially change troubleshooting or repair action.
- Use only supplied cluster IDs; each at most once.
- Omitted clusters are preserved by Python.
"""

CATEGORIES = (
    "reported_symptoms",
    "diagnostics",
    "repair_actions",
    "parts_replaced",
    "adjustments_calibration",
    "testing_verification",
    "outcomes",
)


def normalized_ws(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def safe_slug(text: Any) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").casefold()).strip("_")
    return s[:120] or "unknown"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_json_hash(value: Any) -> str:
    return sha256_text(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def tokenize_query(query: str) -> List[str]:
    try:
        toks = shlex.split(query)
    except ValueError:
        toks = query.split()
    return [x.casefold().strip() for x in toks if x.strip()]


def model_info(model: str) -> Dict[str, Any]:
    out = {"requested_model": model, "available": False}
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for item in data.get("models") or []:
            if item.get("name") == model or item.get("model") == model:
                out.update({"available": True, "resolved_name": item.get("name") or item.get("model"), "digest": item.get("digest"), "size_bytes": item.get("size"), "details": item.get("details")})
                break
    except Exception as exc:
        out["error"] = str(exc)
    return out


def call_ollama(model: str, prompt: str, *, image_paths: Optional[Sequence[Path]], num_ctx: int, num_predict: int, timeout: int) -> str:
    payload: Dict[str, Any] = {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0, "num_ctx": int(num_ctx), "num_predict": int(num_predict)}}
    if image_paths:
        payload["images"] = [base64.b64encode(p.read_bytes()).decode("ascii") for p in image_paths]
    req = urllib.request.Request(OLLAMA_GENERATE_URL, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return str(data.get("response") or "")


def parse_json_response(text: str) -> Any:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def call_json(model: str, prompt: str, *, num_ctx: int, num_predict: int, timeout: int, retries: int = 1) -> Tuple[Any, List[Dict[str, Any]]]:
    attempts = []
    last = None
    for n in range(retries + 1):
        try:
            raw = call_ollama(model, prompt, image_paths=None, num_ctx=num_ctx, num_predict=num_predict, timeout=timeout)
            parsed = parse_json_response(raw)
            attempts.append({"attempt": n + 1, "ok": True, "chars": len(raw)})
            return parsed, attempts
        except Exception as exc:
            last = exc
            attempts.append({"attempt": n + 1, "ok": False, "error": str(exc)})
    raise RuntimeError(str(last))


def extract_log(*texts: str) -> Optional[str]:
    for t in texts:
        m = LOG_RE.search(str(t or ""))
        if m:
            return m.group(1)
    return None


def extract_legacy_token(filename: str) -> Optional[str]:
    # Used only when no valid 9-digit log exists. This groups obviously paired files
    # without asserting that the token is a valid DRL log.
    prefix = re.split(r"line\s*card", filename, flags=re.I)[0]
    vals = LEGACY_EVENT_RE.findall(prefix)
    if vals:
        return vals[-1]
    return None


def card_sequence(filename: str) -> Optional[int]:
    stem = Path(filename).name
    m = SEQ_RE.search(stem)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def query_index(db_path: Path, query: str) -> Tuple[int, List[Dict[str, Any]], Dict[str, str]]:
    if not db_path.exists():
        raise FileNotFoundError(f"DRL index DB not found: {db_path}")
    tokens = tokenize_query(query)
    if not tokens:
        raise ValueError("query must contain at least one token")
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        meta = {str(r["key"]): str(r["value"]) for r in conn.execute("SELECT key,value FROM meta")}
        clauses, params = [], []
        for token in tokens:
            clauses.append("instr(search_text, ?) > 0")
            params.append(token)
        where = " AND ".join(clauses)
        total = int(conn.execute(f"SELECT COUNT(*) FROM files WHERE {where}", params).fetchone()[0])
        rows = [dict(r) for r in conn.execute("SELECT relative_path,filename,parent_path,extension,size,mtime_ns,detected_log,file_kind FROM files WHERE " + where + " ORDER BY search_text", params)]
        return total, rows, meta
    finally:
        conn.close()


def select_line_cards(index_db: Path, share_root: Path, query: str) -> Dict[str, Any]:
    total, rows, meta = query_index(index_db, query)
    bound = meta.get("share_root")
    if bound and Path(bound).resolve() != share_root.resolve():
        raise RuntimeError(f"DRL index is bound to {bound}, not requested share root {share_root}")
    selected, excluded = [], Counter()
    for row in rows:
        rel = str(row.get("relative_path") or "")
        name = str(row.get("filename") or Path(rel).name)
        ext = str(row.get("extension") or Path(name).suffix).casefold()
        parts_cf = [x.casefold() for x in Path(rel).parts]
        if ".picasaoriginals" in parts_cf:
            excluded["picasa_backup"] += 1
            continue
        if re.search(r"\ball\s+line\s+cards?\b", name, re.I):
            excluded["combined_all_line_cards"] += 1
            continue
        if not LINE_CARD_RE.search(name):
            excluded["filename_not_line_card"] += 1
            continue
        if ext not in IMAGE_EXTENSIONS and ext != ".pdf":
            excluded["unsupported_extension"] += 1
            continue
        path = share_root / rel
        if not path.exists() or not path.is_file():
            excluded["stale_or_missing_index_entry"] += 1
            continue
        rr = dict(row)
        rr.update({
            "absolute_path": str(path),
            "relative_path": rel,
            "filename": name,
            "extension": ext,
            "log_number": row.get("detected_log") or extract_log(name, rel),
            "legacy_event_token": extract_legacy_token(name) if not (row.get("detected_log") or extract_log(name, rel)) else None,
            "line_card_sequence": card_sequence(name),
        })
        selected.append(rr)
    return {"raw_index_matches": total, "selected_documents": selected, "selected_document_count": len(selected), "excluded_counts": dict(sorted(excluded.items())), "index_meta": meta}


def engineer_matches(doc: Dict[str, Any], engineer: str) -> bool:
    if not engineer:
        return False
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(engineer)}(?![A-Za-z0-9])", str(doc.get("relative_path") or ""), re.I) is not None


def event_identity(doc: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[str]]:
    log = doc.get("log_number")
    if log:
        return f"log_{log}", str(log), None
    legacy = doc.get("legacy_event_token")
    if legacy:
        return f"legacy_{legacy}", None, str(legacy)
    rid = hashlib.sha256(str(doc.get("relative_path") or "").encode("utf-8")).hexdigest()[:16]
    return f"record_{rid}", None, None


def build_event_plan(selected_docs: Sequence[Dict[str, Any]], typed_pair_engineer: str) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    ids: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    for doc in selected_docs:
        eid, log, legacy = event_identity(doc)
        buckets[eid].append(dict(doc))
        ids[eid] = (log, legacy)
    events = []
    for eid, docs in sorted(buckets.items()):
        docs = sorted(docs, key=lambda d: (d.get("line_card_sequence") is None, d.get("line_card_sequence") or 999, str(d.get("relative_path") or "").casefold()))
        is_typed_engineer = any(engineer_matches(d, typed_pair_engineer) for d in docs)
        has_seq2 = is_typed_engineer and any(d.get("line_card_sequence") == 2 for d in docs)
        primary, supporting = [], []
        if has_seq2:
            for d in docs:
                seq = d.get("line_card_sequence")
                if seq == 1:
                    dd = dict(d); dd["selection_reason"] = f"{typed_pair_engineer}_paired_supporting_(1)"
                    supporting.append(dd)
                else:
                    dd = dict(d); dd["selection_reason"] = f"{typed_pair_engineer}_typed_primary_(2+)_or_unnumbered"
                    primary.append(dd)
        else:
            for d in docs:
                dd = dict(d); dd["selection_reason"] = "normal_line_card_primary"
                primary.append(dd)
        log, legacy = ids[eid]
        events.append({
            "repair_event_id": eid,
            "log_number": log,
            "legacy_event_token": legacy,
            "typed_pair_engineer_match": is_typed_engineer,
            "typed_pair_optimization_applied": has_seq2,
            "primary_documents": primary,
            "supporting_documents": supporting,
            "all_documents": docs,
        })
    return events


def pdf_page_count(path: Path) -> int:
    out = subprocess.check_output(["pdfinfo", str(path)], text=True, stderr=subprocess.STDOUT)
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    if not m:
        raise RuntimeError(f"Could not determine PDF page count: {path}")
    return int(m.group(1))


def render_pdf_page(pdf: Path, page: int, out_jpg: Path, dpi: int) -> None:
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    prefix = out_jpg.with_suffix("")
    cmd = ["pdftoppm", "-f", str(page), "-singlefile", "-jpeg", "-r", str(dpi), str(pdf), str(prefix)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    generated = prefix.with_suffix(".jpg")
    if generated != out_jpg and generated.exists():
        generated.replace(out_jpg)


def expand_primary_sources(events: Sequence[Dict[str, Any]], output_root: Path, render_dpi: int) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
    records: List[Dict[str, Any]] = []
    event_record_ids: Dict[str, List[str]] = defaultdict(list)
    index = 0
    for event in events:
        for doc in event["primary_documents"]:
            path = Path(doc["absolute_path"])
            ext = str(doc.get("extension") or path.suffix).casefold()
            if ext in IMAGE_EXTENSIONS:
                index += 1
                file_sha = sha256_file(path)
                rid = "src_" + hashlib.sha256((str(doc["relative_path"]) + "\n" + file_sha).encode("utf-8")).hexdigest()[:16]
                row = {"source_index": index, "source_record_id": rid, "repair_event_id": event["repair_event_id"], "source_path": str(path), "source_relative_path": doc["relative_path"], "source_image": str(path), "source_image_sha256": file_sha, "source_pdf_page": None, "line_card_sequence": doc.get("line_card_sequence"), "selection_reason": doc.get("selection_reason")}
                records.append(row); event_record_ids[event["repair_event_id"]].append(rid)
            elif ext == ".pdf":
                pages = pdf_page_count(path)
                pdf_sha = sha256_file(path)
                for page in range(1, pages + 1):
                    index += 1
                    rendered = output_root / "pdf_adapter" / safe_slug(event["repair_event_id"]) / safe_slug(path.stem) / f"page_{page:04d}.jpg"
                    if not rendered.exists() or rendered.stat().st_size == 0:
                        render_pdf_page(path, page, rendered, render_dpi)
                        print(f"[pdf] event={event['repair_event_id']} {path.name} page={page}/{pages} -> {rendered.name}")
                    img_sha = sha256_file(rendered)
                    rid = "pdf_" + hashlib.sha256((str(doc["relative_path"]) + f"\n{pdf_sha}\n{page}").encode("utf-8")).hexdigest()[:16]
                    row = {"source_index": index, "source_record_id": rid, "repair_event_id": event["repair_event_id"], "source_path": f"{path}#page={page}", "source_relative_path": str(doc["relative_path"]) + f"#page={page}", "source_image": str(rendered), "source_image_sha256": img_sha, "source_pdf_page": page, "line_card_sequence": doc.get("line_card_sequence"), "selection_reason": doc.get("selection_reason")}
                    records.append(row); event_record_ids[event["repair_event_id"]].append(rid)
    return records, event_record_ids


def acquire_repair_evidence(args: argparse.Namespace, source_records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    root = Path(args.output_root) / "vision_evidence"
    vinfo = model_info(args.vision_model)
    out = []
    for i, src in enumerate(source_records, 1):
        d = root / f"record_{i:04d}_{src['source_record_id'][-8:]}"
        d.mkdir(parents=True, exist_ok=True)
        txt = d / "repair_evidence.txt"
        meta = d / "record.json"
        cache_ok = False
        if txt.exists() and meta.exists() and not args.force_vision:
            try:
                m = load_json(meta)
                cache_ok = m.get("source_image_sha256") == src["source_image_sha256"] and m.get("vision_model_digest") == vinfo.get("digest") and m.get("prompt_sha256") == sha256_text(VISION_PROMPT)
            except Exception:
                cache_ok = False
        if cache_ok:
            evidence = txt.read_text(encoding="utf-8", errors="ignore")
            action = "cache"
        else:
            t0 = time.time()
            evidence = call_ollama(args.vision_model, VISION_PROMPT, image_paths=[Path(src["source_image"])], num_ctx=args.vision_num_ctx, num_predict=args.vision_num_predict, timeout=args.timeout)
            txt.write_text(evidence, encoding="utf-8")
            save_json(meta, {"version": VERSION, "source_record_id": src["source_record_id"], "repair_event_id": src["repair_event_id"], "source_path": src["source_path"], "source_image_sha256": src["source_image_sha256"], "vision_model_digest": vinfo.get("digest"), "prompt_sha256": sha256_text(VISION_PROMPT), "elapsed_seconds": round(time.time()-t0,3), "accepted_facts":0, "qdrant_entries":0})
            action = "model_run"
        row = dict(src)
        row.update({"repair_evidence_path": str(txt), "repair_evidence_sha256": sha256_text(evidence), "repair_evidence_chars": len(evidence)})
        out.append(row)
        print(f"[vision {i}/{len(source_records)}] event={src['repair_event_id']} seq={src.get('line_card_sequence') or '-'} chars={len(evidence)} | {action}")
    save_json(Path(args.output_root) / "vision_source_manifest_v1_4_5.json", {"version": VERSION, "records": out})
    return out


def quote_bound(quote: str, evidence: Sequence[str]) -> bool:
    q = normalized_ws(quote)
    if not q:
        return False
    qn = re.sub(r"[^a-z0-9]+", " ", q.casefold()).strip()
    for block in evidence:
        b = normalized_ws(block)
        if q in b:
            return True
        bn = re.sub(r"[^a-z0-9]+", " ", b.casefold()).strip()
        if qn and qn in bn:
            return True
    return False


def validate_event_json(parsed: Any, event_id: str, evidence: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {c: [] for c in CATEGORIES}
    if not isinstance(parsed, dict):
        return out
    for cat in CATEGORIES:
        rows = parsed.get(cat)
        if not isinstance(rows, list):
            continue
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = normalized_ws(row.get("text"))
            quote = str(row.get("evidence_quote") or "").strip()
            if not text or not quote_bound(quote, evidence):
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            item = {"text": text, "evidence_quote": quote}
            if cat == "parts_replaced":
                pn = row.get("part_number")
                item["part_number"] = normalized_ws(pn) if pn not in (None, "") else None
                qty = row.get("quantity")
                if isinstance(qty, bool): qty = None
                try:
                    qty = int(qty) if qty is not None else None
                    if qty is not None and (qty <= 0 or qty > 10000): qty = None
                except Exception:
                    qty = None
                item["quantity"] = qty
            out[cat].append(item)
    return out


def extract_event_records(args: argparse.Namespace, events: Sequence[Dict[str, Any]], vision_records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_event: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in vision_records:
        by_event[r["repair_event_id"]].append(r)
    rinfo = model_info(args.reason_model)
    out = []
    root = Path(args.output_root) / "event_extraction"
    for i, event in enumerate(events, 1):
        recs = by_event.get(event["repair_event_id"], [])
        blocks = [Path(r["repair_evidence_path"]).read_text(encoding="utf-8", errors="ignore") for r in recs]
        manifest = [{"record": r["source_record_id"], "sha256": r["repair_evidence_sha256"]} for r in recs]
        input_hash = stable_json_hash(manifest)
        d = root / f"event_{i:04d}_{safe_slug(event['repair_event_id'])}"
        d.mkdir(parents=True, exist_ok=True)
        parsed_path, run_path = d / "parsed.json", d / "run.json"
        cache_ok = False
        if parsed_path.exists() and run_path.exists() and not args.force_extraction:
            try:
                run = load_json(run_path)
                cache_ok = run.get("evidence_manifest_sha256") == input_hash and run.get("reason_model_digest") == rinfo.get("digest") and run.get("prompt_sha256") == sha256_text(EVENT_PROMPT)
            except Exception:
                cache_ok = False
        if cache_ok:
            parsed = load_json(parsed_path); action = "cache"
        else:
            payload = "\n\n".join(f"EVIDENCE BLOCK {n}:\n{b}" for n,b in enumerate(blocks,1))
            try:
                parsed, attempts = call_json(args.reason_model, EVENT_PROMPT + "\n\n" + payload, num_ctx=args.reason_num_ctx, num_predict=args.event_num_predict, timeout=args.timeout, retries=1)
            except Exception as exc:
                parsed, attempts = {}, [{"ok":False,"error":str(exc)}]
            save_json(parsed_path, parsed)
            save_json(run_path, {"version":VERSION,"repair_event_id":event["repair_event_id"],"evidence_manifest_sha256":input_hash,"reason_model_digest":rinfo.get("digest"),"prompt_sha256":sha256_text(EVENT_PROMPT),"attempts":attempts,"accepted_facts":0,"qdrant_entries":0})
            action = "model_run"
        facts = validate_event_json(parsed, event["repair_event_id"], blocks)
        row = {"repair_event_id": event["repair_event_id"], "log_number": event.get("log_number"), "legacy_event_token": event.get("legacy_event_token"), "primary_source_paths": [d["absolute_path"] for d in event["primary_documents"]], "supporting_source_paths": [d["absolute_path"] for d in event["supporting_documents"]], "typed_pair_optimization_applied": event["typed_pair_optimization_applied"], "facts": facts}
        out.append(row)
        count = sum(len(v) for v in facts.values())
        print(f"[event {i}/{len(events)}] {event['repair_event_id']} facts={count} primary_docs={len(event['primary_documents'])} support_docs={len(event['supporting_documents'])} | {action}")
    write_jsonl(Path(args.output_root) / "repair_events_v1_4_5.jsonl", out)
    return out


def make_fact_candidates(event_rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    bycat: Dict[str, List[Dict[str, Any]]] = {c: [] for c in CATEGORIES}
    for ev in event_rows:
        eid = ev["repair_event_id"]
        for cat in CATEGORIES:
            for idx, fact in enumerate(ev["facts"].get(cat) or [], 1):
                text = normalized_ws(fact.get("text"))
                if not text:
                    continue
                fid = "f_" + hashlib.sha256((cat + "\n" + eid + "\n" + str(idx) + "\n" + text).encode("utf-8")).hexdigest()[:16]
                row = {"fact_id": fid, "category": cat, "repair_event_id": eid, "text": text, "evidence_quote": fact.get("evidence_quote")}
                if cat == "parts_replaced":
                    row["part_number"] = fact.get("part_number")
                    row["quantity"] = fact.get("quantity")
                bycat[cat].append(row)
    return bycat


def validate_clusters(parsed: Any, allowed_ids: set[str], id_field: str) -> List[Dict[str, Any]]:
    rows = parsed.get("clusters") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        return []
    used, out = set(), []
    for row in rows:
        if not isinstance(row, dict): continue
        label = normalized_ws(row.get("label"))
        ids = row.get(id_field)
        if not label or not isinstance(ids, list): continue
        clean = []
        for x in ids:
            sx = str(x)
            if sx in allowed_ids and sx not in used:
                clean.append(sx); used.add(sx)
        if clean:
            out.append({"label": label, id_field: clean})
    return out


def cluster_category(args: argparse.Namespace, category: str, facts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not facts:
        return []
    root = Path(args.output_root) / "corpus_reasoning" / category
    fact_by_id = {f["fact_id"]: f for f in facts}
    stage_clusters = []
    batches = [facts[i:i+args.cluster_batch_facts] for i in range(0, len(facts), args.cluster_batch_facts)]
    rinfo = model_info(args.reason_model)
    for bi, batch in enumerate(batches,1):
        compact = [{"fact_id":f["fact_id"],"event":f["repair_event_id"],"text":f["text"], **({"part_number":f.get("part_number")} if category=="parts_replaced" else {})} for f in batch]
        d = root / f"stage1_{bi:03d}"; d.mkdir(parents=True, exist_ok=True)
        parsed_path, run_path = d / "parsed.json", d / "run.json"
        input_hash = stable_json_hash(compact)
        cache_ok = False
        if parsed_path.exists() and run_path.exists() and not args.force_cluster:
            try:
                run=load_json(run_path); cache_ok = run.get("input_sha256")==input_hash and run.get("reason_model_digest")==rinfo.get("digest") and run.get("prompt_sha256")==sha256_text(CLUSTER_PROMPT)
            except Exception: cache_ok=False
        if cache_ok:
            parsed=load_json(parsed_path); action="cache"
        else:
            try:
                parsed, attempts = call_json(args.reason_model, CLUSTER_PROMPT + "\n\nCATEGORY: " + category + "\nFACTS:\n" + json.dumps(compact, ensure_ascii=False), num_ctx=args.reason_num_ctx, num_predict=args.cluster_num_predict, timeout=args.timeout, retries=1)
            except Exception as exc:
                parsed, attempts = {}, [{"ok":False,"error":str(exc)}]
            save_json(parsed_path, parsed); save_json(run_path,{"version":VERSION,"input_sha256":input_hash,"reason_model_digest":rinfo.get("digest"),"prompt_sha256":sha256_text(CLUSTER_PROMPT),"attempts":attempts})
            action="model_run"
        valid = validate_clusters(parsed, {f["fact_id"] for f in batch}, "member_ids")
        assigned = {x for c in valid for x in c["member_ids"]}
        for f in batch:
            if f["fact_id"] not in assigned:
                valid.append({"label":f["text"],"member_ids":[f["fact_id"]]})
        for c in valid:
            cid = "c_" + hashlib.sha256((category+"\n"+str(bi)+"\n"+"|".join(sorted(c["member_ids"]))).encode("utf-8")).hexdigest()[:16]
            stage_clusters.append({"cluster_id":cid,"label":c["label"],"member_ids":c["member_ids"]})
        print(f"[cluster {category} {bi}/{len(batches)}] facts={len(batch)} clusters={len(valid)} | {action}")
    if len(stage_clusters) <= 1:
        merged = stage_clusters
    else:
        compact = []
        for c in stage_clusters:
            events = sorted({fact_by_id[fid]["repair_event_id"] for fid in c["member_ids"] if fid in fact_by_id})
            examples = [fact_by_id[fid]["text"] for fid in c["member_ids"][:3] if fid in fact_by_id]
            compact.append({"cluster_id":c["cluster_id"],"label":c["label"],"event_count":len(events),"examples":examples})
        d=root/"merge"; d.mkdir(parents=True,exist_ok=True)
        input_hash=stable_json_hash(compact); parsed_path,run_path=d/"parsed.json",d/"run.json"
        cache_ok=False
        if parsed_path.exists() and run_path.exists() and not args.force_cluster:
            try:
                run=load_json(run_path); cache_ok=run.get("input_sha256")==input_hash and run.get("reason_model_digest")==rinfo.get("digest") and run.get("prompt_sha256")==sha256_text(MERGE_PROMPT)
            except Exception: cache_ok=False
        if cache_ok:
            parsed=load_json(parsed_path); action="cache"
        else:
            try:
                parsed,attempts=call_json(args.reason_model,MERGE_PROMPT+"\n\nCATEGORY: "+category+"\nCLUSTERS:\n"+json.dumps(compact,ensure_ascii=False),num_ctx=args.reason_num_ctx,num_predict=args.cluster_num_predict,timeout=args.timeout,retries=1)
            except Exception as exc:
                parsed,attempts={},[{"ok":False,"error":str(exc)}]
            save_json(parsed_path,parsed); save_json(run_path,{"version":VERSION,"input_sha256":input_hash,"reason_model_digest":rinfo.get("digest"),"prompt_sha256":sha256_text(MERGE_PROMPT),"attempts":attempts})
            action="model_run"
        valid=validate_clusters(parsed,{c["cluster_id"] for c in stage_clusters},"member_cluster_ids")
        bycid={c["cluster_id"]:c for c in stage_clusters}; used={x for c in valid for x in c["member_cluster_ids"]}
        merged=[]
        for row in valid:
            mids=[]
            for cid in row["member_cluster_ids"]: mids.extend(bycid[cid]["member_ids"])
            merged.append({"label":row["label"],"member_ids":sorted(set(mids))})
        for c in stage_clusters:
            if c["cluster_id"] not in used: merged.append({"label":c["label"],"member_ids":c["member_ids"]})
        print(f"[merge {category}] input={len(stage_clusters)} final={len(merged)} | {action}")
    result=[]
    for c in merged:
        rows=[fact_by_id[x] for x in c["member_ids"] if x in fact_by_id]
        events=sorted({r["repair_event_id"] for r in rows})
        pieces=0; unstated=0; pns=set()
        if category=="parts_replaced":
            for r in rows:
                if r.get("quantity") is None: unstated+=1
                else: pieces+=int(r["quantity"])
                if r.get("part_number"): pns.add(str(r["part_number"]))
        result.append({"category":category,"label":c["label"],"repair_event_count":len(events),"repair_event_ids":events,"fact_count":len(rows),"recorded_pieces":pieces if category=="parts_replaced" else None,"quantity_unstated_mentions":unstated if category=="parts_replaced" else None,"part_numbers":sorted(pns),"examples":[r["text"] for r in rows[:5]],"member_fact_ids":c["member_ids"]})
    result.sort(key=lambda r:(-r["repair_event_count"],-r["fact_count"],r["label"].casefold()))
    return result


def write_outputs(args: argparse.Namespace, selection: Dict[str, Any], events: Sequence[Dict[str, Any]], event_rows: Sequence[Dict[str, Any]], grouped: Dict[str,List[Dict[str,Any]]], vision_record_count: int) -> None:
    root=Path(args.output_root); root.mkdir(parents=True,exist_ok=True)
    save_json(root/"source_selection_v1_4_5.json",{"version":VERSION,"index_query":args.query,"raw_index_matches":selection["raw_index_matches"],"selected_document_count":selection["selected_document_count"],"excluded_counts":selection["excluded_counts"],"events":events,"accepted_facts":0,"qdrant_entries":0})
    save_json(root/"recurring_patterns_v1_4_5.json",{"version":VERSION,"categories":grouped,"accepted_facts":0,"qdrant_entries":0})
    with (root/"recurring_patterns_v1_4_5.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["category","rank","label","repair_event_count","fact_count","recorded_pieces","quantity_unstated_mentions","part_numbers","repair_event_ids"]); w.writeheader()
        for cat in CATEGORIES:
            for rank,row in enumerate(grouped.get(cat) or [],1):
                w.writerow({"category":cat,"rank":rank,"label":row["label"],"repair_event_count":row["repair_event_count"],"fact_count":row["fact_count"],"recorded_pieces":row.get("recorded_pieces"),"quantity_unstated_mentions":row.get("quantity_unstated_mentions"),"part_numbers":"; ".join(row.get("part_numbers") or []),"repair_event_ids":"; ".join(row["repair_event_ids"])})
    typed_events=sum(1 for e in events if e["typed_pair_optimization_applied"])
    support_docs=sum(len(e["supporting_documents"]) for e in events)
    primary_docs=sum(len(e["primary_documents"]) for e in events)
    fact_counts={cat:sum(len(ev["facts"].get(cat) or []) for ev in event_rows) for cat in CATEGORIES}
    lines=[
        "# Nova DRL Indexed Repair Event Intelligence v1.4.5","",
        "Operating mode: PROVISIONAL 80/20 VOLUME-BASED REPAIR INTELLIGENCE",
        f"DRL index query: {args.query}",f"Raw index matches: {selection['raw_index_matches']}",f"Selected Line Card documents: {selection['selected_document_count']}",f"Selector exclusions: {json.dumps(selection['excluded_counts'],sort_keys=True)}",
        f"Distinct repair events: {len(events)}",f"Primary Line Card documents selected for deep read: {primary_docs}",f"Supporting Line Cards retained without deep read: {support_docs}",f"Roger paired-card events optimized: {typed_events}",f"Vision records actually read: {vision_record_count}",
        "Vision model calls: only primary Line Cards; cached records reused","NAS discovery/rescan: 0 | SQLite index query only","Accepted facts: 0","Qdrant writes: OFF","Prior hosted benchmark runtime input: NONE","",
        "EVENT FACT COUNTS","-----------------"
    ]
    for cat in CATEGORIES: lines.append(f"{cat}: {fact_counts[cat]}")
    lines += ["","TOP RECURRING 80/20 PATTERNS","-----------------------------"]
    friendly={"reported_symptoms":"REPORTED SYMPTOMS","diagnostics":"DIAGNOSTICS / FINDINGS","repair_actions":"REPAIR ACTIONS","parts_replaced":"PARTS / ASSEMBLIES","adjustments_calibration":"ADJUSTMENTS / CALIBRATION / TEACH","testing_verification":"TEST / VERIFICATION","outcomes":"OUTCOMES"}
    for cat in CATEGORIES:
        lines += ["",friendly[cat]]
        recurring=[r for r in grouped.get(cat) or [] if r["repair_event_count"]>=2]
        if not recurring:
            lines.append("  No recurring 2+ event pattern identified.")
            continue
        for rank,row in enumerate(recurring[:20],1):
            extra=""
            if cat=="parts_replaced":
                pns=", ".join(row.get("part_numbers") or [])
                extra=f" | pieces={row.get('recorded_pieces',0)} | qty-unstated={row.get('quantity_unstated_mentions',0)}"+(f" | PN: {pns}" if pns else "")
            lines.append(f"{rank:2d}. {row['label']} | events={row['repair_event_count']} | mentions={row['fact_count']}{extra}")
    lines += ["","POLICY","------","80/20 rule: fixed default until Matt explicitly changes it","Roger (2) typed Line Card priority: applied only to Roger paired events","Roger (1) card: preserved as supporting evidence when (2) exists; not deep-read by default","Other engineers: no paired-card shortcut","Original source modified: NO","Low-frequency ambiguity: preserved for human exception review rather than driving pipeline development"]
    (root/"indexed_repair_intelligence_summary_v1_4_5.txt").write_text("\n".join(lines)+"\n",encoding="utf-8")
    save_json(root/"indexed_repair_intelligence_manifest_v1_4_5.json",{"version":VERSION,"query":args.query,"source_selection":selection,"repair_event_count":len(events),"primary_document_count":primary_docs,"supporting_document_count":support_docs,"roger_optimized_event_count":typed_events,"vision_record_count":vision_record_count,"event_fact_counts":fact_counts,"accepted_facts":0,"qdrant_entries":0})


def status(args: argparse.Namespace) -> int:
    print("# Nova DRL Indexed Repair Event Intelligence Status v1.4.5")
    print(f"DRL index:       {'FOUND' if Path(args.index_db).exists() else 'MISSING'} | {args.index_db}")
    print(f"Share root:      {'FOUND' if Path(args.share_root).exists() else 'MISSING'} | {args.share_root}")
    print(f"Index query:     {args.query}")
    try:
        selection=select_line_cards(Path(args.index_db),Path(args.share_root),args.query)
        events=build_event_plan(selection["selected_documents"],args.typed_pair_engineer)
        print(f"Raw matches:     {selection['raw_index_matches']}")
        print(f"Selected cards:  {selection['selected_document_count']}")
        print(f"Repair events:   {len(events)}")
        print(f"Roger optimized: {sum(1 for e in events if e['typed_pair_optimization_applied'])} events")
        print(f"Primary docs:    {sum(len(e['primary_documents']) for e in events)}")
        print(f"Supporting docs: {sum(len(e['supporting_documents']) for e in events)}")
    except Exception as exc:
        print(f"Index selection: ERROR | {exc}")
    vi,ri=model_info(args.vision_model),model_info(args.reason_model)
    print(f"Vision model:    {'FOUND' if vi.get('available') else 'MISSING'} | {args.vision_model}")
    print(f"Reason model:    {'FOUND' if ri.get('available') else 'MISSING'} | {args.reason_model}")
    print("NAS rescan:      OFF | persistent SQLite index only")
    print("80/20 rule:      FIXED DEFAULT")
    print("Accepted facts:  0")
    print("Qdrant:          OFF")
    return 0


def plan(args: argparse.Namespace) -> int:
    try:
        selection=select_line_cards(Path(args.index_db),Path(args.share_root),args.query)
        events=build_event_plan(selection["selected_documents"],args.typed_pair_engineer)
    except Exception as exc:
        print(f"ERROR: {exc}",file=sys.stderr); return 2
    primary_docs=sum(len(e["primary_documents"]) for e in events); support_docs=sum(len(e["supporting_documents"]) for e in events)
    pdf_pages=0; image_docs=0
    for e in events:
        for d in e["primary_documents"]:
            if d["extension"]==".pdf":
                try: pdf_pages += pdf_page_count(Path(d["absolute_path"]))
                except Exception: pdf_pages += 1
            else: image_docs += 1
    vision_records=image_docs+pdf_pages
    print("# Nova DRL Indexed Repair Event Intelligence v1.4.5 — PLAN ONLY")
    print(f"Index query:               {args.query}")
    print(f"Raw index matches:         {selection['raw_index_matches']}")
    print(f"Selected Line Card docs:   {selection['selected_document_count']}")
    print(f"Selector exclusions:       {json.dumps(selection['excluded_counts'],sort_keys=True)}")
    print(f"Distinct repair events:    {len(events)}")
    print(f"Valid 9-digit log events:  {sum(1 for e in events if e['log_number'])}")
    print(f"Legacy-token events:       {sum(1 for e in events if e['legacy_event_token'])}")
    print(f"Roger paired optimization: {sum(1 for e in events if e['typed_pair_optimization_applied'])} events")
    print(f"Primary docs deep-read:    {primary_docs}")
    print(f"Supporting docs retained:  {support_docs}")
    print(f"Planned vision records:    {vision_records} (PDFs expand to pages)")
    print(f"Engineer shortcut scope:   ONLY paths containing {args.typed_pair_engineer}; (2) primary, (1) support when pair exists")
    print("Other engineers:           all selected Line Cards remain primary")
    print("Vision task:               high-signal repair evidence, NOT perfect OCR")
    print("Event extraction:          one 14B structured repair record per event")
    print("Corpus reasoning:          category-wise 80/20 recurring pattern clustering")
    print("NAS discovery/rescan:      0 | SQLite index query only")
    print("Qdrant:                    OFF")
    print("Accepted facts:            0")
    return 0


def main() -> int:
    ap=argparse.ArgumentParser(description="Nova DRL Indexed Repair Event Intelligence v1.4.5")
    ap.add_argument("--query",default=DEFAULT_INDEX_QUERY,help=f"Everything-style DRL index query (default: {DEFAULT_INDEX_QUERY})")
    ap.add_argument("--index-db",default=str(DEFAULT_INDEX_DB))
    ap.add_argument("--share-root",default=str(DEFAULT_SHARE_ROOT))
    ap.add_argument("--output-root",default=None,help="Output directory; default is version output base + query slug")
    ap.add_argument("--typed-pair-engineer",default=DEFAULT_ENGINEER_TYPED_PAIR,help="Engineer name for the DRL-specific (2)=typed primary convention; default ROGER")
    ap.add_argument("--vision-model",default=DEFAULT_VISION_MODEL)
    ap.add_argument("--reason-model",default=DEFAULT_REASON_MODEL)
    ap.add_argument("--vision-num-ctx",type=int,default=16384)
    ap.add_argument("--vision-num-predict",type=int,default=2048)
    ap.add_argument("--reason-num-ctx",type=int,default=16384)
    ap.add_argument("--event-num-predict",type=int,default=3072)
    ap.add_argument("--cluster-num-predict",type=int,default=2048)
    ap.add_argument("--cluster-batch-facts",type=int,default=50)
    ap.add_argument("--render-dpi",type=int,default=300)
    ap.add_argument("--timeout",type=int,default=900)
    ap.add_argument("--status",action="store_true")
    ap.add_argument("--plan-only",action="store_true")
    ap.add_argument("--vision-only",action="store_true")
    ap.add_argument("--force-vision",action="store_true")
    ap.add_argument("--force-extraction",action="store_true")
    ap.add_argument("--force-cluster",action="store_true")
    args=ap.parse_args()
    if args.output_root is None:
        args.output_root = str(DEFAULT_OUTPUT_BASE / safe_slug(args.query))
    if args.status: return status(args)
    if args.plan_only: return plan(args)
    root=Path(args.output_root); root.mkdir(parents=True,exist_ok=True)
    try:
        selection=select_line_cards(Path(args.index_db),Path(args.share_root),args.query)
        events=build_event_plan(selection["selected_documents"],args.typed_pair_engineer)
    except Exception as exc:
        print(f"ERROR: {exc}",file=sys.stderr); return 2
    vi,ri=model_info(args.vision_model),model_info(args.reason_model)
    if not vi.get("available"):
        print(f"ERROR: vision model not available: {args.vision_model}",file=sys.stderr); return 3
    if not ri.get("available") and not args.vision_only:
        print(f"ERROR: reason model not available: {args.reason_model}",file=sys.stderr); return 3
    save_json(root/"source_selection_v1_4_5.json",{"version":VERSION,"index_query":args.query,"raw_index_matches":selection["raw_index_matches"],"selected_document_count":selection["selected_document_count"],"excluded_counts":selection["excluded_counts"],"events":events,"accepted_facts":0,"qdrant_entries":0})
    print("# Nova DRL Indexed Repair Event Intelligence v1.4.5")
    print("Operating mode: PROVISIONAL 80/20 — FIXED DEFAULT")
    print(f"Index query: {args.query}")
    print(f"Raw index matches: {selection['raw_index_matches']}")
    print(f"Selected Line Cards: {selection['selected_document_count']}")
    print(f"Repair events: {len(events)}")
    print(f"Roger paired-card optimizations: {sum(1 for e in events if e['typed_pair_optimization_applied'])}")
    print(f"Supporting (1) cards retained/no deep read: {sum(len(e['supporting_documents']) for e in events)}")
    print("Prior hosted benchmark runtime input: NONE")
    print("Accepted facts: 0")
    print("Qdrant: OFF")
    source_records,_=expand_primary_sources(events,root,args.render_dpi)
    vision_records=acquire_repair_evidence(args,source_records)
    if args.vision_only:
        print("Vision acquisition complete; stopping because --vision-only was requested.")
        return 0
    event_rows=extract_event_records(args,events,vision_records)
    facts=make_fact_candidates(event_rows)
    grouped={}
    for cat in CATEGORIES:
        grouped[cat]=cluster_category(args,cat,facts[cat])
    write_outputs(args,selection,events,event_rows,grouped,len(vision_records))
    print("\n# COMPLETE")
    print(f"Repair events:                 {len(events)}")
    print(f"Primary vision records:        {len(vision_records)}")
    print(f"Supporting Line Cards skipped: {sum(len(e['supporting_documents']) for e in events)}")
    print(f"Structured event facts:        {sum(len(v) for ev in event_rows for v in ev['facts'].values())}")
    print(f"Recurring 2+ event groups:     {sum(1 for cat in CATEGORIES for r in grouped[cat] if r['repair_event_count']>=2)}")
    print("Accepted facts:                0")
    print("Qdrant:                        OFF")
    print(f"Summary: {root/'indexed_repair_intelligence_summary_v1_4_5.txt'}")
    print(f"CSV:     {root/'recurring_patterns_v1_4_5.csv'}")
    print(f"Manifest:{root/'indexed_repair_intelligence_manifest_v1_4_5.json'}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
