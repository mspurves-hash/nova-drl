#!/usr/bin/env python3
"""
Nova DRL 10% Benchmark Corpus Ingester v1.4.6

Purpose
-------
Create and ingest a fixed, deterministic 10% benchmark sample from the DRL repair
folder universe under `/mnt/drl/000 folder for tech scans`, using the persistent
Nova DRL File Index rather than rescanning the NAS.

Standing DRL Nova policy
------------------------
- 80/20 is FIXED DEFAULT until Matt explicitly changes it.
- Travelers / Line Cards are primarily high-volume repair-history and parts-used
  evidence. Do not force them to provide detailed procedures/testing they do not
  contain.
- Prefer high-signal recurring repair information over perfect OCR.
- Preserve source paths and raw acquired evidence so Matt can perform exception /
  forensic review only when something materially looks wrong.
- Do not aggregate unrelated equipment families into one repair-pattern answer.

Benchmark sample
----------------
- Enumerate top-level repair/equipment folders directly below the configured tech
  scans base using the SQLite index only.
- Rank folders by SHA256(seed + folder name) and take exactly ceil(N * percent/100).
  This gives a deterministic sample spread across the alphabetic folder universe.
- On first real run the sample is frozen to a manifest. Later index growth does NOT
  silently change the benchmark. Use --force-sample only when intentionally creating
  a new sample baseline.

Source selection
----------------
- Within sampled folders, select indexed image/PDF files whose filename contains
  Line Card / Linecard / Traveler.
- Exclude .picasaoriginals backup paths and combined "All Line Cards" benchmark PDFs.
- A sampled folder with no detected source document is an EXCEPTION TO REVIEW, not
  proof that the folder lacks a repair record.
- Valid DRL YYMMDD### logs group documents into repair events.
- Roger-only paired-card convention from DRL: when a ROGER event has both (1) and
  (2), (2) is primary typed evidence, (1) is retained supporting evidence; (3+) is
  also primary. This is NOT generalized to other engineers.

Ingestion
---------
1. Qwen3-VL 8B reads primary Line Cards for concise high-signal Traveler evidence.
2. Qwen2.5 14B converts each repair event into structured evidence categories:
   basic reported symptom, repair-history notes, parts/assemblies replaced/used,
   and any explicit outcome/test note that happens to be present.
3. Python owns provenance, event IDs, explicit quantities, counts, and manifests.
4. No corpus-wide LLM clustering is performed in v1.4.6. This release establishes
   the reusable benchmark corpus. Model/family-specific intelligence comes later.

No source writes. No automatic approval. No Qdrant writes.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.4.6"
DEFAULT_INDEX_DB = Path("/opt/nova-drl/index/drl_file_index.sqlite")
DEFAULT_SHARE_ROOT = Path("/mnt/drl")
DEFAULT_TECH_BASE = "000 folder for tech scans"
DEFAULT_SAMPLE_PERCENT = 10.0
DEFAULT_SAMPLE_SEED = "nova-drl-fixed-10pct-v1.4.6"
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/drl_10pct_benchmark_v1_4_6")
DEFAULT_SAMPLE_MANIFEST = Path("/opt/nova-drl/corpus/drl_10pct_benchmark_v1_4_6/sample_manifest_v1_4_6.json")
DEFAULT_VISION_MODEL = "qwen3-vl-drl:8b-q8-16k"
DEFAULT_REASON_MODEL = "qwen25-drl:14b-q6-16k"
DEFAULT_TYPED_PAIR_ENGINEER = "ROGER"
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf"}
LOG_RE = re.compile(r"(?<!\d)(\d{9})(?!\d)")
LEGACY_EVENT_RE = re.compile(r"(?<!\d)(\d{8,12})(?!\d)")
SOURCE_NAME_RE = re.compile(r"(?:line\s*card|linecard|traveler)", re.I)
SEQ_RE = re.compile(r"\((\d+)\)(?=\s*\.[^.]+$|\s*$)", re.I)
SERIAL_SPLIT_RE = re.compile(r"\s+(?:S/?N|SN|SERIAL\s*(?:NO\.?|NUMBER)?)\s*[:#-]?\s*", re.I)

VISION_PROMPT = """Read this DRL Traveler / Line Card using the standing 80/20 rule.

This source is primarily REPAIR-HISTORY AND PARTS-USAGE evidence. Do NOT attempt
perfect OCR and do NOT transcribe the whole form. Capture the useful information a
veteran DRL technician would care about, with strongest priority on what was replaced,
installed, rebuilt, used, or consumed.

Return concise plain text only, using whichever headings actually have information:
BASIC REPORTED PROBLEM:
PARTS / ASSEMBLIES REPLACED OR USED:
OTHER REPAIR-HISTORY NOTES:
EXPLICIT TEST / OUTCOME NOTE:

Rules:
- Ground everything in what is visible on the source.
- Prioritize exact part numbers, assembly names, quantities, axis/component names,
  and replacement/rebuild wording when reasonably legible.
- Preserve a likely PN as written; do not spend effort guessing a single uncertain
  character. Keep an uncertain but useful fragment rather than inventing precision.
- Do not infer troubleshooting procedures or test methods that are not written.
- Administrative fields may be omitted unless they materially identify the repair.
- If the card contains almost no technical information, say so briefly rather than
  inventing content.
"""

EVENT_PROMPT = """Convert ONE DRL repair event into a concise structured Traveler-history record.
Evidence comes from one or more PRIMARY Line Cards/Travelers for the SAME event.
Return JSON only with this exact top-level shape:
{
  "basic_reported_problem": [{"text":"...","evidence_quote":"..."}],
  "parts_replaced": [{"text":"...","part_number":null,"quantity":null,"evidence_quote":"..."}],
  "repair_history_notes": [{"text":"...","evidence_quote":"..."}],
  "explicit_test_outcome": [{"text":"...","evidence_quote":"..."}]
}

Standing 80/20 rules:
- Travelers are primarily parts/repair-history evidence. Do not manufacture detailed
  diagnostics, procedures, calibration, or testing when the evidence does not contain them.
- Capture actual replaced/installed/used/rebuilt components and assemblies.
- part_number is populated only when a PN/string is actually present in the evidence.
- quantity is an integer only when explicitly stated; otherwise null.
- evidence_quote must be copied from supplied evidence.
- Do not convert administrative/shop routing into technical repair facts.
- If a category has no useful evidence, return an empty list.
"""

CATEGORIES = ("basic_reported_problem", "parts_replaced", "repair_history_notes", "explicit_test_outcome")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def normalized_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def safe_slug(value: Any) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
    return s[:100] or "item"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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


def model_info(model: str) -> Dict[str, Any]:
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        for item in data.get("models", []):
            if item.get("name") == model or item.get("model") == model:
                return {"available": True, "digest": item.get("digest"), "size": item.get("size")}
        return {"available": False, "digest": None}
    except Exception as exc:
        return {"available": False, "digest": None, "error": str(exc)}


def image_to_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def call_ollama(model: str, prompt: str, *, image_paths: Optional[Sequence[Path]], num_ctx: int, num_predict: int, timeout: int) -> str:
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": num_predict},
    }
    if image_paths:
        payload["images"] = [image_to_b64(p) for p in image_paths]
    req = urllib.request.Request(OLLAMA_GENERATE_URL, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data.get("response") or "")


def parse_json_response(text: str) -> Any:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        first, last = s.find("{"), s.rfind("}")
        if first >= 0 and last > first:
            return json.loads(s[first:last + 1])
        raise


def call_json(model: str, prompt: str, *, num_ctx: int, num_predict: int, timeout: int, retries: int = 1) -> Tuple[Any, List[Dict[str, Any]]]:
    attempts: List[Dict[str, Any]] = []
    current = prompt
    for n in range(1, retries + 2):
        raw = ""
        t0 = time.time()
        try:
            raw = call_ollama(model, current, image_paths=None, num_ctx=num_ctx, num_predict=num_predict, timeout=timeout)
            parsed = parse_json_response(raw)
            attempts.append({"attempt": n, "ok": True, "elapsed_seconds": round(time.time() - t0, 3)})
            return parsed, attempts
        except Exception as exc:
            attempts.append({"attempt": n, "ok": False, "elapsed_seconds": round(time.time() - t0, 3), "error": str(exc), "raw_preview": raw[:500]})
            current = prompt + "\n\nPrevious response was invalid. Return only valid JSON in the exact requested schema."
    raise RuntimeError(attempts[-1].get("error") or "model JSON call failed")


def index_meta(conn: sqlite3.Connection) -> Dict[str, str]:
    try:
        return {str(r[0]): str(r[1]) for r in conn.execute("SELECT key,value FROM meta")}
    except sqlite3.OperationalError:
        return {}


def load_tech_scan_rows(index_db: Path, tech_base: str) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, str]]:
    if not index_db.exists():
        raise FileNotFoundError(f"DRL index DB not found: {index_db}")
    prefix = tech_base.strip("/") + "/"
    conn = sqlite3.connect(str(index_db), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        meta = index_meta(conn)
        rows = conn.execute(
            "SELECT relative_path,filename,parent_path,extension,size,mtime_ns,detected_log,file_kind "
            "FROM files WHERE relative_path LIKE ? ORDER BY relative_path COLLATE NOCASE",
            (prefix + "%",),
        )
        by_folder: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for rr in rows:
            row = dict(rr)
            rel = str(row.get("relative_path") or "")
            rest = rel[len(prefix):] if rel.casefold().startswith(prefix.casefold()) else ""
            if "/" not in rest:
                continue
            top = rest.split("/", 1)[0]
            row["top_folder"] = top
            by_folder[top].append(row)
        return dict(by_folder), meta
    finally:
        conn.close()


def sample_hash(folder: str, seed: str) -> str:
    return hashlib.sha256((seed + "\0" + folder).encode("utf-8")).hexdigest()


def deterministic_sample(folders: Sequence[str], percent: float, seed: str) -> List[Dict[str, Any]]:
    if percent <= 0 or percent > 100:
        raise ValueError("sample percent must be >0 and <=100")
    ranked = sorted(((sample_hash(f, seed), f) for f in folders), key=lambda x: (x[0], x[1].casefold()))
    count = int(math.ceil(len(ranked) * percent / 100.0)) if ranked else 0
    return [{"sample_rank": i + 1, "folder": f, "sample_hash": h} for i, (h, f) in enumerate(ranked[:count])]


def get_or_create_sample(args: argparse.Namespace, by_folder: Dict[str, List[Dict[str, Any]]], meta: Dict[str, str], *, persist: bool) -> Dict[str, Any]:
    manifest_path = Path(args.sample_manifest)
    if manifest_path.exists() and not args.force_sample:
        manifest = load_json(manifest_path)
        if str(manifest.get("version")) != VERSION:
            raise RuntimeError(f"sample manifest version mismatch: {manifest.get('version')} != {VERSION}; use --force-sample intentionally")
        return manifest
    selected = deterministic_sample(sorted(by_folder, key=str.casefold), args.sample_percent, args.sample_seed)
    manifest = {
        "version": VERSION,
        "created_at": utc_now(),
        "sample_policy": "sha256-ranked deterministic exact percentage; frozen after first persisted run",
        "sample_percent": float(args.sample_percent),
        "sample_seed": args.sample_seed,
        "tech_base": args.tech_base,
        "index_db": str(args.index_db),
        "index_software_version": meta.get("software_version"),
        "index_share_root": meta.get("share_root") or meta.get("bound_share_root"),
        "all_top_level_folder_count": len(by_folder),
        "sample_folder_count": len(selected),
        "sampled_folders": selected,
        "accepted_facts": 0,
        "qdrant_entries": 0,
    }
    if persist:
        save_json(manifest_path, manifest)
    return manifest


def equipment_family_from_folder(folder: str) -> str:
    # Conservative deterministic identity: keep the folder prefix before its serial
    # number marker. We do NOT force EG-300B and EG-300B-009 together here; later
    # family intelligence may do so when volume/evidence supports it.
    parts = SERIAL_SPLIT_RE.split(folder, maxsplit=1)
    family = normalized_ws(parts[0]) if parts else normalized_ws(folder)
    return family or normalized_ws(folder)


def extract_log(*texts: str) -> Optional[str]:
    for text in texts:
        for m in LOG_RE.finditer(str(text or "")):
            token = m.group(1)
            try:
                dt.date(2000 + int(token[:2]), int(token[2:4]), int(token[4:6]))
                if int(token[6:]) > 0:
                    return token
            except Exception:
                continue
    return None


def extract_legacy_token(filename: str) -> Optional[str]:
    prefix = SOURCE_NAME_RE.split(filename, maxsplit=1)[0]
    vals = LEGACY_EVENT_RE.findall(prefix)
    return vals[-1] if vals else None


def card_sequence(filename: str) -> Optional[int]:
    m = SEQ_RE.search(Path(filename).name)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def engineer_matches(doc: Dict[str, Any], engineer: str) -> bool:
    if not engineer:
        return False
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(engineer)}(?![A-Za-z0-9])", str(doc.get("relative_path") or ""), re.I) is not None


def select_sample_sources(args: argparse.Namespace, by_folder: Dict[str, List[Dict[str, Any]]], sample_manifest: Dict[str, Any]) -> Dict[str, Any]:
    share_root = Path(args.share_root)
    selected: List[Dict[str, Any]] = []
    exceptions: List[Dict[str, Any]] = []
    excluded = Counter()
    sampled = [str(x["folder"]) for x in sample_manifest.get("sampled_folders", [])]
    if args.limit_sampled_folders:
        sampled = sampled[: int(args.limit_sampled_folders)]
    for folder in sampled:
        rows = by_folder.get(folder, [])
        folder_selected = []
        source_name_candidates = []
        for row in rows:
            rel = str(row.get("relative_path") or "")
            name = str(row.get("filename") or Path(rel).name)
            ext = str(row.get("extension") or Path(name).suffix).casefold()
            parts_cf = [p.casefold() for p in Path(rel).parts]
            if SOURCE_NAME_RE.search(name):
                source_name_candidates.append({"relative_path": rel, "filename": name, "extension": ext, "file_kind": row.get("file_kind")})
            if ".picasaoriginals" in parts_cf:
                if SOURCE_NAME_RE.search(name): excluded["picasa_backup"] += 1
                continue
            if re.search(r"\ball\s+line\s+cards?\b", name, re.I):
                excluded["combined_all_line_cards"] += 1
                continue
            if not SOURCE_NAME_RE.search(name):
                continue
            if ext not in SUPPORTED_EXTENSIONS:
                excluded["source_name_unsupported_extension"] += 1
                continue
            abs_path = share_root / rel
            if not abs_path.exists() or not abs_path.is_file():
                excluded["stale_or_missing_index_entry"] += 1
                continue
            log = row.get("detected_log") or extract_log(name, rel)
            doc = dict(row)
            doc.update({
                "absolute_path": str(abs_path),
                "relative_path": rel,
                "filename": name,
                "extension": ext,
                "top_folder": folder,
                "equipment_family": equipment_family_from_folder(folder),
                "log_number": log,
                "legacy_event_token": extract_legacy_token(name) if not log else None,
                "line_card_sequence": card_sequence(name),
            })
            selected.append(doc)
            folder_selected.append(doc)
        if not folder_selected:
            exceptions.append({
                "top_folder": folder,
                "equipment_family": equipment_family_from_folder(folder),
                "indexed_file_count": len(rows),
                "source_name_candidate_count": len(source_name_candidates),
                "source_name_candidates": source_name_candidates[:20],
                "reason": "no_supported_line_card_or_traveler_detected",
            })
    return {
        "sample_folder_count_effective": len(sampled),
        "selected_document_count": len(selected),
        "selected_documents": selected,
        "folder_exceptions": exceptions,
        "folder_exception_count": len(exceptions),
        "excluded_counts": dict(sorted(excluded.items())),
    }


def event_identity(doc: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[str]]:
    log = doc.get("log_number")
    if log:
        return f"log_{log}", str(log), None
    legacy = doc.get("legacy_event_token")
    if legacy:
        rid = hashlib.sha256((str(doc.get("top_folder")) + "\n" + str(legacy)).encode("utf-8")).hexdigest()[:8]
        return f"legacy_{legacy}_{rid}", None, str(legacy)
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
    for eid, docs in sorted(buckets.items(), key=lambda kv: kv[0]):
        docs = sorted(docs, key=lambda d: (d.get("line_card_sequence") is None, d.get("line_card_sequence") or 999, str(d.get("relative_path") or "").casefold()))
        is_roger = any(engineer_matches(d, typed_pair_engineer) for d in docs)
        has_seq2 = is_roger and any(d.get("line_card_sequence") == 2 for d in docs)
        primary, supporting = [], []
        if has_seq2:
            for d in docs:
                dd = dict(d)
                if d.get("line_card_sequence") == 1:
                    dd["selection_reason"] = f"{typed_pair_engineer}_paired_supporting_(1)"
                    supporting.append(dd)
                else:
                    dd["selection_reason"] = f"{typed_pair_engineer}_typed_primary_(2+)_or_unnumbered"
                    primary.append(dd)
        else:
            for d in docs:
                dd = dict(d); dd["selection_reason"] = "normal_traveler_primary"; primary.append(dd)
        log, legacy = ids[eid]
        folder_names = sorted({str(d.get("top_folder")) for d in docs})
        families = sorted({str(d.get("equipment_family")) for d in docs})
        events.append({
            "repair_event_id": eid,
            "log_number": log,
            "legacy_event_token": legacy,
            "top_folders": folder_names,
            "equipment_families": families,
            "equipment_family": families[0] if len(families) == 1 else " | ".join(families),
            "typed_pair_engineer_match": is_roger,
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
    cmd = ["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile", "-jpeg", "-r", str(dpi), str(pdf), str(prefix)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    generated = prefix.with_suffix(".jpg")
    if generated != out_jpg and generated.exists():
        generated.replace(out_jpg)


def expand_primary_sources(events: Sequence[Dict[str, Any]], output_root: Path, render_dpi: int) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    index = 0
    for event in events:
        for doc in event["primary_documents"]:
            path = Path(doc["absolute_path"])
            ext = str(doc.get("extension") or path.suffix).casefold()
            if ext in IMAGE_EXTENSIONS:
                index += 1
                file_sha = sha256_file(path)
                rid = "src_" + hashlib.sha256((str(doc["relative_path"]) + "\n" + file_sha).encode("utf-8")).hexdigest()[:16]
                records.append({
                    "source_index": index,
                    "source_record_id": rid,
                    "repair_event_id": event["repair_event_id"],
                    "equipment_family": event["equipment_family"],
                    "top_folders": event["top_folders"],
                    "source_path": str(path),
                    "source_relative_path": doc["relative_path"],
                    "source_image": str(path),
                    "source_image_sha256": file_sha,
                    "source_pdf_page": None,
                    "line_card_sequence": doc.get("line_card_sequence"),
                    "selection_reason": doc.get("selection_reason"),
                })
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
                    records.append({
                        "source_index": index,
                        "source_record_id": rid,
                        "repair_event_id": event["repair_event_id"],
                        "equipment_family": event["equipment_family"],
                        "top_folders": event["top_folders"],
                        "source_path": f"{path}#page={page}",
                        "source_relative_path": str(doc["relative_path"]) + f"#page={page}",
                        "source_image": str(rendered),
                        "source_image_sha256": img_sha,
                        "source_pdf_page": page,
                        "line_card_sequence": doc.get("line_card_sequence"),
                        "selection_reason": doc.get("selection_reason"),
                    })
    return records


def acquire_evidence(args: argparse.Namespace, records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    root = Path(args.output_root) / "vision_evidence"
    vinfo = model_info(args.vision_model)
    out = []
    total = len(records)
    for i, src in enumerate(records, 1):
        d = root / f"record_{i:05d}_{src['source_record_id'][-8:]}"
        d.mkdir(parents=True, exist_ok=True)
        txt, meta = d / "traveler_evidence.txt", d / "record.json"
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
            save_json(meta, {
                "version": VERSION,
                "source_record_id": src["source_record_id"],
                "repair_event_id": src["repair_event_id"],
                "source_path": src["source_path"],
                "source_image_sha256": src["source_image_sha256"],
                "vision_model_digest": vinfo.get("digest"),
                "prompt_sha256": sha256_text(VISION_PROMPT),
                "elapsed_seconds": round(time.time() - t0, 3),
                "accepted_facts": 0,
                "qdrant_entries": 0,
            })
            action = "model_run"
        row = dict(src)
        row.update({"traveler_evidence_path": str(txt), "traveler_evidence_sha256": sha256_text(evidence), "traveler_evidence_chars": len(evidence)})
        out.append(row)
        print(f"[vision {i}/{total}] event={src['repair_event_id']} family={src['equipment_family'][:42]} chars={len(evidence)} | {action}")
    save_json(Path(args.output_root) / "vision_source_manifest_v1_4_6.json", {"version": VERSION, "records": out})
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


def validate_event_json(parsed: Any, evidence: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
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
            key = (text.casefold(), quote.casefold())
            if key in seen:
                continue
            seen.add(key)
            item: Dict[str, Any] = {"text": text, "evidence_quote": quote}
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


def extract_events(args: argparse.Namespace, events: Sequence[Dict[str, Any]], vision_records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_event: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in vision_records:
        by_event[r["repair_event_id"]].append(r)
    rinfo = model_info(args.reason_model)
    out = []
    root = Path(args.output_root) / "event_extraction"
    for i, event in enumerate(events, 1):
        recs = by_event.get(event["repair_event_id"], [])
        blocks = [Path(r["traveler_evidence_path"]).read_text(encoding="utf-8", errors="ignore") for r in recs]
        manifest = [{"record": r["source_record_id"], "sha256": r["traveler_evidence_sha256"]} for r in recs]
        input_hash = stable_json_hash(manifest)
        d = root / f"event_{i:05d}_{safe_slug(event['repair_event_id'])}"
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
            payload = "\n\n".join(f"EVIDENCE BLOCK {n}:\n{b}" for n, b in enumerate(blocks, 1))
            try:
                parsed, attempts = call_json(args.reason_model, EVENT_PROMPT + "\n\n" + payload, num_ctx=args.reason_num_ctx, num_predict=args.event_num_predict, timeout=args.timeout, retries=1)
            except Exception as exc:
                parsed, attempts = {}, [{"ok": False, "error": str(exc)}]
            save_json(parsed_path, parsed)
            save_json(run_path, {
                "version": VERSION,
                "repair_event_id": event["repair_event_id"],
                "evidence_manifest_sha256": input_hash,
                "reason_model_digest": rinfo.get("digest"),
                "prompt_sha256": sha256_text(EVENT_PROMPT),
                "attempts": attempts,
                "accepted_facts": 0,
                "qdrant_entries": 0,
            })
            action = "model_run"
        facts = validate_event_json(parsed, blocks)
        row = {
            "repair_event_id": event["repair_event_id"],
            "log_number": event.get("log_number"),
            "legacy_event_token": event.get("legacy_event_token"),
            "equipment_family": event.get("equipment_family"),
            "equipment_families": event.get("equipment_families"),
            "top_folders": event.get("top_folders"),
            "primary_source_paths": [d["absolute_path"] for d in event["primary_documents"]],
            "supporting_source_paths": [d["absolute_path"] for d in event["supporting_documents"]],
            "roger_pair_optimization_applied": bool(event.get("typed_pair_optimization_applied")),
            "facts": facts,
        }
        out.append(row)
        count = sum(len(v) for v in facts.values())
        print(f"[event {i}/{len(events)}] {event['repair_event_id']} family={event['equipment_family'][:42]} facts={count} parts={len(facts['parts_replaced'])} | {action}")
    write_jsonl(Path(args.output_root) / "repair_events_v1_4_6.jsonl", out)
    return out


def write_inventory_outputs(args: argparse.Namespace, sample: Dict[str, Any], selection: Dict[str, Any], events: Sequence[Dict[str, Any]], event_rows: Optional[Sequence[Dict[str, Any]]] = None, vision_count: Optional[int] = None) -> None:
    out_root = Path(args.output_root)
    out_root.mkdir(parents=True, exist_ok=True)
    family_folder_counts = Counter()
    for item in sample.get("sampled_folders", [])[: selection.get("sample_folder_count_effective", 0)]:
        family_folder_counts[equipment_family_from_folder(str(item["folder"]))] += 1
    event_family_counts = Counter(e.get("equipment_family") for e in events)
    typed_count = sum(1 for e in events if e.get("typed_pair_optimization_applied"))
    primary_docs = sum(len(e.get("primary_documents", [])) for e in events)
    support_docs = sum(len(e.get("supporting_documents", [])) for e in events)

    save_json(out_root / "source_selection_v1_4_6.json", {
        "version": VERSION,
        "sample_manifest": str(args.sample_manifest),
        "sample_folder_count_effective": selection["sample_folder_count_effective"],
        "selected_document_count": selection["selected_document_count"],
        "folder_exception_count": selection["folder_exception_count"],
        "folder_exceptions": selection["folder_exceptions"],
        "excluded_counts": selection["excluded_counts"],
        "selected_documents": selection["selected_documents"],
        "accepted_facts": 0,
        "qdrant_entries": 0,
    })
    save_json(out_root / "repair_event_plan_v1_4_6.json", {
        "version": VERSION,
        "repair_event_count": len(events),
        "roger_pair_optimized_events": typed_count,
        "primary_documents": primary_docs,
        "supporting_documents": support_docs,
        "events": list(events),
        "accepted_facts": 0,
        "qdrant_entries": 0,
    })

    with (out_root / "sampled_folders_v1_4_6.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sample_rank", "folder", "equipment_family", "sample_hash"])
        for item in sample.get("sampled_folders", [])[: selection.get("sample_folder_count_effective", 0)]:
            w.writerow([item["sample_rank"], item["folder"], equipment_family_from_folder(item["folder"]), item["sample_hash"]])

    with (out_root / "folder_exceptions_v1_4_6.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["top_folder", "equipment_family", "indexed_file_count", "source_name_candidate_count", "reason"])
        for x in selection["folder_exceptions"]:
            w.writerow([x["top_folder"], x["equipment_family"], x["indexed_file_count"], x["source_name_candidate_count"], x["reason"]])

    if event_rows is not None:
        parts_rows = []
        for ev in event_rows:
            for p in ev["facts"].get("parts_replaced", []):
                parts_rows.append({
                    "repair_event_id": ev["repair_event_id"],
                    "log_number": ev.get("log_number"),
                    "equipment_family": ev.get("equipment_family"),
                    "top_folders": ev.get("top_folders"),
                    "part_number": p.get("part_number"),
                    "quantity": p.get("quantity"),
                    "text": p.get("text"),
                    "evidence_quote": p.get("evidence_quote"),
                    "primary_source_paths": ev.get("primary_source_paths"),
                })
        write_jsonl(out_root / "replacement_mentions_v1_4_6.jsonl", parts_rows)

        with (out_root / "replacement_mentions_v1_4_6.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["repair_event_id", "log_number", "equipment_family", "part_number", "quantity", "text", "evidence_quote"])
            for p in parts_rows:
                w.writerow([p["repair_event_id"], p["log_number"], p["equipment_family"], p["part_number"], p["quantity"], p["text"], p["evidence_quote"]])

        parts_events = sum(1 for ev in event_rows if ev["facts"].get("parts_replaced"))
        total_parts = len(parts_rows)
        category_counts = Counter()
        for ev in event_rows:
            for cat in CATEGORIES:
                category_counts[cat] += len(ev["facts"].get(cat, []))
    else:
        parts_events = total_parts = 0
        category_counts = Counter()

    top_family_lines = []
    for i, (fam, count) in enumerate(event_family_counts.most_common(25), 1):
        top_family_lines.append(f"{i:2d}. {fam} | repair events={count} | sampled folders={family_folder_counts.get(fam, 0)}")

    lines = [
        f"# Nova DRL 10% Benchmark Corpus Ingester v{VERSION}",
        "",
        "Operating mode: FIXED 80/20 BENCHMARK CORPUS",
        f"Tech-scan folder universe at sample freeze: {sample.get('all_top_level_folder_count')}",
        f"Frozen benchmark sample percent: {sample.get('sample_percent')}%",
        f"Frozen sampled folders: {sample.get('sample_folder_count')}",
        f"Effective sampled folders this run: {selection.get('sample_folder_count_effective')}",
        f"Folders with no supported Line Card/Traveler detected: {selection.get('folder_exception_count')}",
        f"Selected Line Card/Traveler documents: {selection.get('selected_document_count')}",
        f"Distinct repair events: {len(events)}",
        f"Roger paired events optimized: {typed_count}",
        f"Primary documents selected for deep read: {primary_docs}",
        f"Supporting documents retained without deep read: {support_docs}",
        f"Vision records actually read: {vision_count if vision_count is not None else 'NOT RUN'}",
        f"Structured event records: {len(event_rows) if event_rows is not None else 'NOT RUN'}",
        f"Events with replacement-part evidence: {parts_events if event_rows is not None else 'NOT RUN'}",
        f"Extracted replacement mentions: {total_parts if event_rows is not None else 'NOT RUN'}",
        "Accepted facts: 0",
        "Qdrant writes: OFF",
        "NAS discovery/rescan: 0 | persistent SQLite index only",
        "",
        "TOP EQUIPMENT FAMILIES IN THE 10% SAMPLE — BY REPAIR EVENTS",
        "------------------------------------------------------------",
        *top_family_lines,
    ]
    if event_rows is not None:
        lines += ["", "STRUCTURED FACT COUNTS", "----------------------"]
        for cat in CATEGORIES:
            lines.append(f"{cat}: {category_counts[cat]}")
    lines += [
        "",
        "POLICY",
        "------",
        "80/20 rule: FIXED DEFAULT until Matt explicitly changes it",
        "Travelers/Line Cards: primary role is repair-history and parts-used evidence",
        "Detailed fixes/testing: not inferred when absent; later knowledge layers include Operations Checklists and manuals",
        "Sample: deterministic and frozen; later index growth does not silently change benchmark membership",
        "Folder without detected Traveler: exception for review, NOT proof no Traveler exists",
        "Roger paired-card rule: applied ONLY to Roger (2)=typed primary convention",
        "Unrelated equipment families: preserved separately; no global mixed-model part ranking",
        "Original share modified: NO",
        "Perfect OCR required: NO",
        "Automatic human approval: NO",
        "Qdrant writes: OFF",
    ]
    (out_root / "drl_10pct_benchmark_summary_v1_4_6.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare(args: argparse.Namespace, *, persist_sample: bool) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, str], Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    by_folder, meta = load_tech_scan_rows(Path(args.index_db), args.tech_base)
    bound = meta.get("share_root") or meta.get("bound_share_root")
    if bound:
        try:
            if Path(bound).resolve() != Path(args.share_root).resolve():
                raise RuntimeError(f"DRL index is bound to {bound}, not requested share root {args.share_root}")
        except FileNotFoundError:
            raise RuntimeError(f"share root not resolvable: {args.share_root}")
    sample = get_or_create_sample(args, by_folder, meta, persist=persist_sample)
    selection = select_sample_sources(args, by_folder, sample)
    events = build_event_plan(selection["selected_documents"], args.typed_pair_engineer)
    return by_folder, meta, sample, selection, events


def status(args: argparse.Namespace) -> int:
    print(f"# Nova DRL 10% Benchmark Corpus Ingester Status v{VERSION}")
    print(f"DRL index:        {'FOUND' if Path(args.index_db).exists() else 'NOT FOUND'} | {args.index_db}")
    print(f"Share root:       {'FOUND' if Path(args.share_root).exists() else 'NOT FOUND'} | {args.share_root}")
    print(f"Tech scans base:  {args.tech_base}")
    print(f"Sample manifest:  {'FROZEN' if Path(args.sample_manifest).exists() else 'NOT FROZEN'} | {args.sample_manifest}")
    try:
        _, _, sample, selection, events = prepare(args, persist_sample=False)
        print(f"Repair folders in index: {sample.get('all_top_level_folder_count')}")
        print(f"10% sample folders:      {sample.get('sample_folder_count')}")
        print(f"Selected source docs:    {selection.get('selected_document_count')}")
        print(f"Folder exceptions:       {selection.get('folder_exception_count')}")
        print(f"Repair events:           {len(events)}")
        print(f"Roger optimized events:  {sum(1 for e in events if e.get('typed_pair_optimization_applied'))}")
        print(f"Primary docs:            {sum(len(e['primary_documents']) for e in events)}")
        print(f"Supporting docs:         {sum(len(e['supporting_documents']) for e in events)}")
    except Exception as exc:
        print(f"Corpus planning: ERROR | {exc}")
    vi, ri = model_info(args.vision_model), model_info(args.reason_model)
    print(f"Vision model:     {'FOUND' if vi.get('available') else 'MISSING'} | {args.vision_model}")
    print(f"Reason model:     {'FOUND' if ri.get('available') else 'MISSING'} | {args.reason_model}")
    print("NAS rescan:       OFF | persistent SQLite index only")
    print("80/20 rule:       FIXED DEFAULT")
    print("Accepted facts:   0")
    print("Qdrant:           OFF")
    return 0


def plan(args: argparse.Namespace) -> int:
    try:
        _, _, sample, selection, events = prepare(args, persist_sample=False)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    primary_docs = sum(len(e["primary_documents"]) for e in events)
    support_docs = sum(len(e["supporting_documents"]) for e in events)
    pdf_docs = [d for e in events for d in e["primary_documents"] if str(d.get("extension")) == ".pdf"]
    planned_vision = primary_docs - len(pdf_docs)
    pdf_pages = 0
    for d in pdf_docs:
        try:
            pages = pdf_page_count(Path(d["absolute_path"]))
        except Exception:
            pages = 1
        pdf_pages += pages
    planned_vision += pdf_pages
    families = Counter(e["equipment_family"] for e in events)
    print(f"# Nova DRL 10% Benchmark Corpus Ingester v{VERSION} — PLAN ONLY")
    print(f"Tech-scan folder universe:    {sample.get('all_top_level_folder_count')}")
    print(f"Deterministic sample percent: {sample.get('sample_percent')}%")
    print(f"Sample seed:                  {sample.get('sample_seed')}")
    print(f"Frozen manifest exists:       {'YES (will reuse)' if Path(args.sample_manifest).exists() and not args.force_sample else 'NO (first full/manifest run will freeze)'}")
    print(f"Sample folders:               {sample.get('sample_folder_count')}")
    if args.limit_sampled_folders:
        print(f"Effective folders (limit):    {selection.get('sample_folder_count_effective')}")
    print(f"Selected Line Cards/Travelers:{selection.get('selected_document_count')}")
    print(f"Folder exceptions:            {selection.get('folder_exception_count')} | retained for review")
    print(f"Selector exclusions:          {json.dumps(selection.get('excluded_counts'), sort_keys=True)}")
    print(f"Distinct repair events:       {len(events)}")
    print(f"Equipment-family keys:        {len(families)}")
    print(f"Roger paired optimization:    {sum(1 for e in events if e.get('typed_pair_optimization_applied'))} events")
    print(f"Primary docs deep-read:       {primary_docs}")
    print(f"Supporting docs retained:     {support_docs}")
    print(f"Planned vision records:       {planned_vision} (PDFs expand to pages)")
    print(f"Planned 14B event calls:      {len(events)} maximum before cache")
    print("Corpus-wide clustering calls: 0 | v1.4.6 ingests/structures first")
    print("Cross-model part ranking:     OFF | equipment identity preserved")
    print("NAS discovery/rescan:         0 | SQLite index only")
    print("Perfect OCR target:           NO | high-signal Traveler evidence")
    print("Accepted facts:               0")
    print("Qdrant:                       OFF")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=f"Nova DRL 10% Benchmark Corpus Ingester v{VERSION}")
    ap.add_argument("--index-db", default=str(DEFAULT_INDEX_DB))
    ap.add_argument("--share-root", default=str(DEFAULT_SHARE_ROOT))
    ap.add_argument("--tech-base", default=DEFAULT_TECH_BASE)
    ap.add_argument("--sample-percent", type=float, default=DEFAULT_SAMPLE_PERCENT)
    ap.add_argument("--sample-seed", default=DEFAULT_SAMPLE_SEED)
    ap.add_argument("--sample-manifest", default=str(DEFAULT_SAMPLE_MANIFEST))
    ap.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    ap.add_argument("--typed-pair-engineer", default=DEFAULT_TYPED_PAIR_ENGINEER)
    ap.add_argument("--vision-model", default=DEFAULT_VISION_MODEL)
    ap.add_argument("--reason-model", default=DEFAULT_REASON_MODEL)
    ap.add_argument("--vision-num-ctx", type=int, default=16384)
    ap.add_argument("--vision-num-predict", type=int, default=1536)
    ap.add_argument("--reason-num-ctx", type=int, default=16384)
    ap.add_argument("--event-num-predict", type=int, default=2048)
    ap.add_argument("--render-dpi", type=int, default=300)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--limit-sampled-folders", type=int, default=None, help="Development/smoke-test limit applied after frozen sample order")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--manifest-only", action="store_true", help="Freeze sample + source/event manifests without model calls")
    ap.add_argument("--vision-only", action="store_true", help="Run/fill vision cache and stop before 14B event extraction")
    ap.add_argument("--force-sample", action="store_true", help="INTENTIONAL: regenerate frozen 10% sample from current index")
    ap.add_argument("--force-vision", action="store_true")
    ap.add_argument("--force-extraction", action="store_true")
    args = ap.parse_args()

    if args.status:
        return status(args)
    if args.plan_only:
        return plan(args)

    # First real action freezes the sample. Later runs reuse it automatically.
    try:
        _, _, sample, selection, events = prepare(args, persist_sample=True)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"# Nova DRL 10% Benchmark Corpus Ingester v{VERSION}")
    print("Operating mode: FIXED 80/20 BENCHMARK CORPUS")
    print(f"Folder universe at sample freeze: {sample.get('all_top_level_folder_count')}")
    print(f"Frozen sampled folders: {sample.get('sample_folder_count')}")
    print(f"Selected Line Cards/Travelers: {selection.get('selected_document_count')}")
    print(f"Folder exceptions: {selection.get('folder_exception_count')}")
    print(f"Repair events: {len(events)}")
    print(f"Roger paired optimizations: {sum(1 for e in events if e.get('typed_pair_optimization_applied'))}")
    print("NAS rescan: OFF")
    print("Accepted facts: 0")
    print("Qdrant: OFF")

    write_inventory_outputs(args, sample, selection, events)
    if args.manifest_only:
        print("# MANIFEST COMPLETE")
        print(f"Frozen sample: {args.sample_manifest}")
        print(f"Source selection: {Path(args.output_root) / 'source_selection_v1_4_6.json'}")
        print(f"Event plan: {Path(args.output_root) / 'repair_event_plan_v1_4_6.json'}")
        return 0

    source_records = expand_primary_sources(events, Path(args.output_root), args.render_dpi)
    print(f"Primary vision records after PDF expansion: {len(source_records)}")
    vision_rows = acquire_evidence(args, source_records)
    if args.vision_only:
        write_inventory_outputs(args, sample, selection, events, event_rows=None, vision_count=len(vision_rows))
        print("# VISION-ONLY COMPLETE")
        return 0

    event_rows = extract_events(args, events, vision_rows)
    write_inventory_outputs(args, sample, selection, events, event_rows=event_rows, vision_count=len(vision_rows))
    parts_mentions = sum(len(ev["facts"].get("parts_replaced", [])) for ev in event_rows)
    parts_events = sum(1 for ev in event_rows if ev["facts"].get("parts_replaced"))
    print("\n# COMPLETE")
    print(f"Frozen sampled folders:       {sample.get('sample_folder_count')}")
    print(f"Effective folders processed:  {selection.get('sample_folder_count_effective')}")
    print(f"Folder exceptions:            {selection.get('folder_exception_count')}")
    print(f"Selected source documents:    {selection.get('selected_document_count')}")
    print(f"Repair events:                {len(events)}")
    print(f"Vision records:               {len(vision_rows)}")
    print(f"Events with replacement data: {parts_events}")
    print(f"Replacement mentions:         {parts_mentions}")
    print("Accepted facts:               0")
    print("Qdrant:                       OFF")
    print(f"Summary: {Path(args.output_root) / 'drl_10pct_benchmark_summary_v1_4_6.txt'}")
    print(f"Events:  {Path(args.output_root) / 'repair_events_v1_4_6.jsonl'}")
    print(f"Parts:   {Path(args.output_root) / 'replacement_mentions_v1_4_6.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
