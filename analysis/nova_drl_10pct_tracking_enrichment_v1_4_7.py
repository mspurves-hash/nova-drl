#!/usr/bin/env python3
"""
Nova DRL 10% Benchmark Tracking + Procurement Enrichment v1.4.7

Purpose
-------
Enrich the already-frozen v1.4.6 10% benchmark corpus with first-class tracking
and procurement metadata WITHOUT changing benchmark membership or rerunning the
original repair-history/parts extraction.

Adds:
- RMA numbers, event-linked and searchable.
- Distributor/procurement order references (Digi-Key, Mouser, etc.), kept
  separate from manufacturer part numbers.
- Optional visible description, manufacturer PN, and quantity when the source
  explicitly associates them with an order reference.
- Enriched replacement-mention output that prevents known distributor/order
  references from polluting the manufacturer-PN field.
- SQLite lookup DB + CLI lookups by RMA or order reference.

Standing DRL Nova policy
------------------------
- 80/20 is FIXED DEFAULT until Matt explicitly changes it.
- v1.4.6 remains the frozen benchmark baseline and is NEVER modified in place.
- Use the frozen v1.4.6 repair-event plan; do not resample and do not rediscover
  the NAS/index.
- RMA is a first-class repair tracking identifier.
- Procurement/order references are first-class searchable data, but are NOT
  manufacturer part numbers.
- Known DRL procurement-style prefixes include DGK, MSR, NWK, and DSK. DGK is
  treated as Digi-Key and MSR as Mouser when no explicit supplier label is
  visible. NWK/DSK remain supplier-unknown unless the source states otherwise.
- Never infer an actual manufacturer PN from a distributor/order reference.
- Preserve source evidence and original v1.4.6 data for human exception review.

No source writes. No Qdrant writes. No automatic human approval.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.4.7"
SOURCE_VERSION = "1.4.6"
DEFAULT_SOURCE_ROOT = Path("/opt/nova-drl/output/drl_10pct_benchmark_v1_4_6")
DEFAULT_SAMPLE_MANIFEST = Path("/opt/nova-drl/corpus/drl_10pct_benchmark_v1_4_6/sample_manifest_v1_4_6.json")
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/drl_10pct_tracking_enrichment_v1_4_7")
DEFAULT_VISION_MODEL = "qwen3-vl-drl:8b-q8-16k"
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
KNOWN_ORDER_PREFIX_SUPPLIER = {
    "DGK": "Digi-Key",
    "MSR": "Mouser",
    "NWK": None,
    "DSK": None,
}
KNOWN_ORDER_REF_RE = re.compile(r"^\s*(DGK|MSR|NWK|DSK)[\s._-]*(\d{4,})\s*$", re.I)

METADATA_PROMPT = r"""Read ONLY the tracking and procurement/order fields from this DRL Traveler / Line Card.
Use the standing 80/20 rule: get the useful tracking strings right; do not attempt a
full transcription and do not invent uncertain data.

Return JSON only with this exact shape:
{
  "rma_numbers": [
    {"value":"...", "evidence_quote":"..."}
  ],
  "procurement_refs": [
    {
      "order_ref":"...",
      "supplier":null,
      "description":null,
      "manufacturer_pn":null,
      "quantity":null,
      "evidence_quote":"..."
    }
  ]
}

Rules:
- RMA: capture a value only when it is visibly in an RMA / RMA# / Return Material
  Authorization field or is otherwise clearly labeled as RMA. Keep the exact visible
  value; do not turn PO, serial, DRL log, quote, or order numbers into RMA numbers.
- Procurement/order references: focus especially on the upper ordered-parts/order area.
  These are purchasing references, NOT manufacturer component part numbers.
- DRL historical procurement-style values such as DGK52102, MSR..., NWK56548,
  DSK520117 are distributor/order references when they occur in the order area.
- DGK-style references may be labeled Digi-Key and MSR-style references may be
  labeled Mouser. Use the visible supplier name when present. Do not guess a supplier
  for NWK/DSK unless it is visible.
- If the same order line visibly provides a real manufacturer part number, capture it
  separately in manufacturer_pn. NEVER derive or look up a manufacturer PN from an
  order_ref.
- description is a concise visible part description from the same order entry, if any.
- quantity is an integer only when explicitly visible for that order entry; else null.
- evidence_quote is a short visible text fragment supporting the item.
- Preserve useful uncertain strings as written rather than inventing exact characters.
- If nothing is visible, return empty arrays.
"""


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


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


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


def call_ollama(model: str, prompt: str, *, image_paths: Sequence[Path], num_ctx: int, num_predict: int, timeout: int) -> str:
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": num_predict},
        "images": [image_to_b64(p) for p in image_paths],
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


def call_vision_json(model: str, prompt: str, *, image_paths: Sequence[Path], num_ctx: int, num_predict: int, timeout: int, retries: int = 1) -> Tuple[Any, List[Dict[str, Any]], str]:
    attempts: List[Dict[str, Any]] = []
    current = prompt
    last_raw = ""
    for n in range(1, retries + 2):
        t0 = time.time()
        try:
            raw = call_ollama(model, current, image_paths=image_paths, num_ctx=num_ctx, num_predict=num_predict, timeout=timeout)
            last_raw = raw
            parsed = parse_json_response(raw)
            attempts.append({"attempt": n, "ok": True, "elapsed_seconds": round(time.time() - t0, 3)})
            return parsed, attempts, raw
        except Exception as exc:
            attempts.append({"attempt": n, "ok": False, "elapsed_seconds": round(time.time() - t0, 3), "error": str(exc), "raw_preview": last_raw[:500]})
            current = prompt + "\n\nPrevious response was invalid. Return ONLY valid JSON in the exact requested schema."
    raise RuntimeError(attempts[-1].get("error") or "vision JSON call failed")


def normalize_rma(value: Any) -> str:
    s = normalized_ws(value)
    s = re.sub(r"^RMA\s*#?\s*[:\-]?\s*", "", s, flags=re.I)
    return re.sub(r"[^A-Za-z0-9]+", "", s).upper()


def normalize_order_ref(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", normalized_ws(value)).upper()


def infer_supplier_from_order_ref(order_ref: Any) -> Optional[str]:
    m = KNOWN_ORDER_REF_RE.match(normalized_ws(order_ref))
    if not m:
        return None
    return KNOWN_ORDER_PREFIX_SUPPLIER.get(m.group(1).upper())


def is_known_order_ref(value: Any) -> bool:
    return KNOWN_ORDER_REF_RE.match(normalized_ws(value)) is not None


def validate_metadata_json(parsed: Any) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {"rma_numbers": [], "procurement_refs": []}
    if not isinstance(parsed, dict):
        return out

    seen_rma = set()
    rows = parsed.get("rma_numbers")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = normalized_ws(row.get("value"))
            norm = normalize_rma(value)
            quote = normalized_ws(row.get("evidence_quote"))
            if not value or not norm or len(norm) < 3:
                continue
            # Avoid obvious field labels accidentally returned as values.
            if norm in {"RMA", "RMANUMBER", "RETURNMATERIALAUTHORIZATION"}:
                continue
            if norm in seen_rma:
                continue
            seen_rma.add(norm)
            out["rma_numbers"].append({"value": value, "normalized": norm, "evidence_quote": quote})

    seen_order = set()
    rows = parsed.get("procurement_refs")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            order_ref = normalized_ws(row.get("order_ref"))
            norm = normalize_order_ref(order_ref)
            if not order_ref or not norm:
                continue
            supplier = normalized_ws(row.get("supplier")) or infer_supplier_from_order_ref(order_ref)
            desc = normalized_ws(row.get("description")) or None
            mpn = normalized_ws(row.get("manufacturer_pn")) or None
            quote = normalized_ws(row.get("evidence_quote"))
            qty = row.get("quantity")
            if isinstance(qty, bool):
                qty = None
            try:
                qty = int(qty) if qty is not None else None
                if qty is not None and (qty <= 0 or qty > 10000):
                    qty = None
            except Exception:
                qty = None
            key = (norm, (supplier or "").casefold(), (mpn or "").casefold(), (desc or "").casefold())
            if key in seen_order:
                continue
            seen_order.add(key)
            out["procurement_refs"].append({
                "order_ref": order_ref,
                "normalized": norm,
                "supplier": supplier or None,
                "description": desc,
                "manufacturer_pn": mpn,
                "quantity": qty,
                "evidence_quote": quote,
            })
    return out


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


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False


def make_upper_crop(source: Path, out_path: Path, fraction: float = 0.48, scale: float = 1.7) -> Optional[Path]:
    try:
        from PIL import Image
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as im:
            im = im.convert("RGB")
            w, h = im.size
            if w < 200 or h < 200:
                return None
            crop = im.crop((0, 0, w, max(1, int(h * fraction))))
            if scale > 1.0:
                crop = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))))
            crop.save(out_path, format="JPEG", quality=92)
        return out_path
    except Exception:
        return None


def load_source_corpus(source_root: Path) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    plan_path = source_root / "repair_event_plan_v1_4_6.json"
    events_path = source_root / "repair_events_v1_4_6.jsonl"
    parts_path = source_root / "replacement_mentions_v1_4_6.jsonl"
    if not plan_path.exists():
        raise FileNotFoundError(f"Frozen v1.4.6 event plan not found: {plan_path}")
    if not events_path.exists():
        raise FileNotFoundError(f"Frozen v1.4.6 repair events not found: {events_path}")
    if not parts_path.exists():
        raise FileNotFoundError(f"Frozen v1.4.6 replacement mentions not found: {parts_path}")
    plan = load_json(plan_path)
    if str(plan.get("version")) != SOURCE_VERSION:
        raise RuntimeError(f"Expected v{SOURCE_VERSION} repair event plan, found {plan.get('version')}")
    return list(plan.get("events") or []), read_jsonl(events_path), read_jsonl(parts_path)


def all_event_documents(event_plan: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    for event in event_plan:
        all_docs = event.get("all_documents")
        if not isinstance(all_docs, list):
            # Fallback for older plans: union primary + supporting.
            all_docs = list(event.get("primary_documents") or []) + list(event.get("supporting_documents") or [])
        for doc in all_docs:
            row = dict(doc)
            row["repair_event_id"] = event.get("repair_event_id")
            row["log_number"] = event.get("log_number")
            row["equipment_family"] = event.get("equipment_family")
            row["top_folders"] = event.get("top_folders")
            docs.append(row)
    # Deterministic order and dedupe by event/path.
    seen = set()
    out = []
    for d in sorted(docs, key=lambda x: (str(x.get("repair_event_id")), str(x.get("relative_path") or x.get("absolute_path")).casefold())):
        key = (str(d.get("repair_event_id")), str(d.get("absolute_path")))
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def expand_metadata_sources(docs: Sequence[Dict[str, Any]], output_root: Path, render_dpi: int) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for doc_i, doc in enumerate(docs, 1):
        path = Path(str(doc.get("absolute_path") or ""))
        if not path.exists() or not path.is_file():
            records.append({
                "record_status": "missing_source",
                "repair_event_id": doc.get("repair_event_id"),
                "source_path": str(path),
                "source_relative_path": doc.get("relative_path"),
                "equipment_family": doc.get("equipment_family"),
            })
            continue
        ext = str(doc.get("extension") or path.suffix).casefold()
        if ext in IMAGE_EXTENSIONS:
            file_sha = sha256_file(path)
            rid = "meta_" + hashlib.sha256((str(doc.get("relative_path")) + "\n" + file_sha).encode("utf-8")).hexdigest()[:16]
            records.append({
                "record_status": "ready",
                "source_record_id": rid,
                "repair_event_id": doc.get("repair_event_id"),
                "log_number": doc.get("log_number"),
                "equipment_family": doc.get("equipment_family"),
                "top_folders": doc.get("top_folders"),
                "source_path": str(path),
                "source_relative_path": doc.get("relative_path"),
                "source_image": str(path),
                "source_image_sha256": file_sha,
                "source_pdf_page": None,
            })
        elif ext == ".pdf":
            pages = pdf_page_count(path)
            pdf_sha = sha256_file(path)
            for page in range(1, pages + 1):
                rendered = output_root / "pdf_adapter" / safe_slug(doc.get("repair_event_id")) / safe_slug(path.stem) / f"page_{page:04d}.jpg"
                if not rendered.exists() or rendered.stat().st_size == 0:
                    render_pdf_page(path, page, rendered, render_dpi)
                    print(f"[pdf] event={doc.get('repair_event_id')} {path.name} page={page}/{pages} -> {rendered.name}")
                img_sha = sha256_file(rendered)
                rid = "metapdf_" + hashlib.sha256((str(doc.get("relative_path")) + f"\n{pdf_sha}\n{page}").encode("utf-8")).hexdigest()[:16]
                records.append({
                    "record_status": "ready",
                    "source_record_id": rid,
                    "repair_event_id": doc.get("repair_event_id"),
                    "log_number": doc.get("log_number"),
                    "equipment_family": doc.get("equipment_family"),
                    "top_folders": doc.get("top_folders"),
                    "source_path": f"{path}#page={page}",
                    "source_relative_path": str(doc.get("relative_path")) + f"#page={page}",
                    "source_image": str(rendered),
                    "source_image_sha256": img_sha,
                    "source_pdf_page": page,
                })
    return records


def acquire_tracking_metadata(args: argparse.Namespace, records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    root = Path(args.output_root) / "tracking_vision"
    vinfo = model_info(args.vision_model)
    out: List[Dict[str, Any]] = []
    ready = [r for r in records if r.get("record_status") == "ready"]
    total = len(ready)
    for i, src in enumerate(ready, 1):
        d = root / f"record_{i:05d}_{src['source_record_id'][-8:]}"
        d.mkdir(parents=True, exist_ok=True)
        parsed_path = d / "parsed.json"
        run_path = d / "run.json"
        raw_path = d / "raw.txt"
        crop_path = d / "upper_crop.jpg"
        cache_ok = False
        if parsed_path.exists() and run_path.exists() and not args.force_vision:
            try:
                run = load_json(run_path)
                cache_ok = (
                    run.get("source_image_sha256") == src.get("source_image_sha256")
                    and run.get("vision_model_digest") == vinfo.get("digest")
                    and run.get("prompt_sha256") == sha256_text(METADATA_PROMPT)
                )
            except Exception:
                cache_ok = False
        if cache_ok:
            parsed = load_json(parsed_path)
            action = "cache"
            crop_used = bool(load_json(run_path).get("upper_crop_used"))
        else:
            image_paths = [Path(src["source_image"])]
            crop = None
            if not args.no_upper_crop and pillow_available():
                crop = make_upper_crop(Path(src["source_image"]), crop_path)
                if crop:
                    image_paths.append(crop)
            try:
                parsed, attempts, raw = call_vision_json(
                    args.vision_model,
                    METADATA_PROMPT,
                    image_paths=image_paths,
                    num_ctx=args.vision_num_ctx,
                    num_predict=args.vision_num_predict,
                    timeout=args.timeout,
                    retries=1,
                )
            except Exception as exc:
                parsed, attempts, raw = {}, [{"ok": False, "error": str(exc)}], ""
            validated = validate_metadata_json(parsed)
            save_json(parsed_path, validated)
            raw_path.write_text(raw, encoding="utf-8")
            save_json(run_path, {
                "version": VERSION,
                "source_version": SOURCE_VERSION,
                "source_record_id": src.get("source_record_id"),
                "repair_event_id": src.get("repair_event_id"),
                "source_path": src.get("source_path"),
                "source_image_sha256": src.get("source_image_sha256"),
                "vision_model_digest": vinfo.get("digest"),
                "prompt_sha256": sha256_text(METADATA_PROMPT),
                "upper_crop_used": bool(crop),
                "attempts": attempts,
                "accepted_facts": 0,
                "qdrant_entries": 0,
            })
            parsed = validated
            action = "model_run"
            crop_used = bool(crop)
        validated = validate_metadata_json(parsed)
        row = dict(src)
        row["metadata"] = validated
        row["upper_crop_used"] = crop_used
        out.append(row)
        print(
            f"[track {i}/{total}] event={src.get('repair_event_id')} "
            f"rma={len(validated['rma_numbers'])} orders={len(validated['procurement_refs'])} "
            f"crop={'yes' if crop_used else 'no'} | {action}"
        )
    write_jsonl(Path(args.output_root) / "source_tracking_metadata_v1_4_7.jsonl", out)
    return out


def merge_event_tracking(event_plan: Sequence[Dict[str, Any]], tracking_rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_event: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in tracking_rows:
        by_event[str(row.get("repair_event_id"))].append(row)

    result: Dict[str, Dict[str, Any]] = {}
    for event in event_plan:
        eid = str(event.get("repair_event_id"))
        rmas: List[Dict[str, Any]] = []
        orders: List[Dict[str, Any]] = []
        seen_rma = set()
        seen_order = set()
        for src in by_event.get(eid, []):
            meta = src.get("metadata") or {}
            for r in meta.get("rma_numbers") or []:
                norm = normalize_rma(r.get("value"))
                if not norm or norm in seen_rma:
                    continue
                seen_rma.add(norm)
                rr = dict(r)
                rr["source_path"] = src.get("source_path")
                rmas.append(rr)
            for o in meta.get("procurement_refs") or []:
                norm = normalize_order_ref(o.get("order_ref"))
                key = (norm, (o.get("manufacturer_pn") or "").casefold(), (o.get("description") or "").casefold())
                if not norm or key in seen_order:
                    continue
                seen_order.add(key)
                oo = dict(o)
                oo["source_path"] = src.get("source_path")
                orders.append(oo)
        result[eid] = {
            "rma_numbers": rmas,
            "procurement_refs": orders,
        }
    return result


def order_lookup_by_event(event_tracking: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    out: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)
    for eid, meta in event_tracking.items():
        for row in meta.get("procurement_refs") or []:
            norm = normalize_order_ref(row.get("order_ref"))
            if norm and norm not in out[eid]:
                out[eid][norm] = row
    return out


def enrich_replacement_mentions(parts_rows: Sequence[Dict[str, Any]], event_tracking: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    order_map = order_lookup_by_event(event_tracking)
    out: List[Dict[str, Any]] = []
    reclassified = 0
    for row in parts_rows:
        item = dict(row)
        eid = str(row.get("repair_event_id"))
        original_pn = normalized_ws(row.get("part_number")) or None
        item["original_part_number_v1_4_6"] = original_pn
        item["manufacturer_part_number"] = original_pn
        item["distributor_order_ref"] = None
        item["supplier"] = None
        item["pn_classification"] = "manufacturer_part_number" if original_pn else "no_part_number"
        norm = normalize_order_ref(original_pn) if original_pn else ""
        matched = order_map.get(eid, {}).get(norm) if norm else None
        if matched or (original_pn and is_known_order_ref(original_pn)):
            reclassified += 1
            m = matched or {
                "order_ref": original_pn,
                "normalized": norm,
                "supplier": infer_supplier_from_order_ref(original_pn),
                "manufacturer_pn": None,
            }
            item["distributor_order_ref"] = m.get("order_ref") or original_pn
            item["supplier"] = m.get("supplier")
            actual_mpn = normalized_ws(m.get("manufacturer_pn")) or None
            item["manufacturer_part_number"] = actual_mpn
            item["part_number"] = actual_mpn
            item["pn_classification"] = "procurement_reference_reclassified"
        out.append(item)
    return out, reclassified


def enrich_repair_events(event_rows: Sequence[Dict[str, Any]], event_tracking: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for ev in event_rows:
        row = json.loads(json.dumps(ev))  # deep copy JSON-safe data
        row["tracking_metadata_v1_4_7"] = event_tracking.get(str(ev.get("repair_event_id")), {"rma_numbers": [], "procurement_refs": []})
        out.append(row)
    return out


def build_lookup_db(path: Path, enriched_events: Sequence[Dict[str, Any]], enriched_parts: Sequence[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            PRAGMA journal_mode=DELETE;
            CREATE TABLE events (
              repair_event_id TEXT PRIMARY KEY,
              log_number TEXT,
              equipment_family TEXT,
              top_folders_json TEXT,
              source_paths_json TEXT
            );
            CREATE TABLE rma_refs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              repair_event_id TEXT NOT NULL,
              rma_number TEXT NOT NULL,
              rma_normalized TEXT NOT NULL,
              evidence_quote TEXT,
              source_path TEXT
            );
            CREATE TABLE procurement_refs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              repair_event_id TEXT NOT NULL,
              supplier TEXT,
              order_ref TEXT NOT NULL,
              order_ref_normalized TEXT NOT NULL,
              description TEXT,
              manufacturer_pn TEXT,
              quantity INTEGER,
              evidence_quote TEXT,
              source_path TEXT
            );
            CREATE TABLE replacement_mentions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              repair_event_id TEXT NOT NULL,
              manufacturer_pn TEXT,
              distributor_order_ref TEXT,
              supplier TEXT,
              quantity INTEGER,
              text TEXT,
              evidence_quote TEXT
            );
            CREATE INDEX idx_rma_normalized ON rma_refs(rma_normalized);
            CREATE INDEX idx_order_ref_normalized ON procurement_refs(order_ref_normalized);
            CREATE INDEX idx_procurement_mpn ON procurement_refs(manufacturer_pn);
            CREATE INDEX idx_replacement_mpn ON replacement_mentions(manufacturer_pn);
            """
        )
        for ev in enriched_events:
            paths = list(ev.get("primary_source_paths") or []) + list(ev.get("supporting_source_paths") or [])
            conn.execute(
                "INSERT INTO events VALUES (?,?,?,?,?)",
                (
                    ev.get("repair_event_id"),
                    ev.get("log_number"),
                    ev.get("equipment_family"),
                    json.dumps(ev.get("top_folders") or [], ensure_ascii=False),
                    json.dumps(paths, ensure_ascii=False),
                ),
            )
            meta = ev.get("tracking_metadata_v1_4_7") or {}
            for r in meta.get("rma_numbers") or []:
                conn.execute(
                    "INSERT INTO rma_refs(repair_event_id,rma_number,rma_normalized,evidence_quote,source_path) VALUES (?,?,?,?,?)",
                    (ev.get("repair_event_id"), r.get("value"), normalize_rma(r.get("value")), r.get("evidence_quote"), r.get("source_path")),
                )
            for o in meta.get("procurement_refs") or []:
                conn.execute(
                    "INSERT INTO procurement_refs(repair_event_id,supplier,order_ref,order_ref_normalized,description,manufacturer_pn,quantity,evidence_quote,source_path) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        ev.get("repair_event_id"), o.get("supplier"), o.get("order_ref"), normalize_order_ref(o.get("order_ref")),
                        o.get("description"), o.get("manufacturer_pn"), o.get("quantity"), o.get("evidence_quote"), o.get("source_path"),
                    ),
                )
        for p in enriched_parts:
            conn.execute(
                "INSERT INTO replacement_mentions(repair_event_id,manufacturer_pn,distributor_order_ref,supplier,quantity,text,evidence_quote) VALUES (?,?,?,?,?,?,?)",
                (
                    p.get("repair_event_id"), p.get("manufacturer_part_number"), p.get("distributor_order_ref"), p.get("supplier"),
                    p.get("quantity"), p.get("text"), p.get("evidence_quote"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def write_outputs(args: argparse.Namespace, source_events: Sequence[Dict[str, Any]], source_parts: Sequence[Dict[str, Any]], tracking_rows: Sequence[Dict[str, Any]], event_tracking: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    out_root = Path(args.output_root)
    out_root.mkdir(parents=True, exist_ok=True)
    enriched_events = enrich_repair_events(source_events, event_tracking)
    enriched_parts, reclassified = enrich_replacement_mentions(source_parts, event_tracking)
    write_jsonl(out_root / "repair_events_enriched_v1_4_7.jsonl", enriched_events)
    write_jsonl(out_root / "replacement_mentions_enriched_v1_4_7.jsonl", enriched_parts)

    rma_rows = []
    order_rows = []
    for ev in enriched_events:
        meta = ev.get("tracking_metadata_v1_4_7") or {}
        for r in meta.get("rma_numbers") or []:
            rma_rows.append({
                "repair_event_id": ev.get("repair_event_id"),
                "log_number": ev.get("log_number"),
                "equipment_family": ev.get("equipment_family"),
                "rma_number": r.get("value"),
                "rma_normalized": normalize_rma(r.get("value")),
                "evidence_quote": r.get("evidence_quote"),
                "source_path": r.get("source_path"),
            })
        for o in meta.get("procurement_refs") or []:
            order_rows.append({
                "repair_event_id": ev.get("repair_event_id"),
                "log_number": ev.get("log_number"),
                "equipment_family": ev.get("equipment_family"),
                "supplier": o.get("supplier"),
                "order_ref": o.get("order_ref"),
                "order_ref_normalized": normalize_order_ref(o.get("order_ref")),
                "description": o.get("description"),
                "manufacturer_pn": o.get("manufacturer_pn"),
                "quantity": o.get("quantity"),
                "evidence_quote": o.get("evidence_quote"),
                "source_path": o.get("source_path"),
            })

    with (out_root / "rma_lookup_v1_4_7.csv").open("w", encoding="utf-8", newline="") as f:
        fields = ["repair_event_id", "log_number", "equipment_family", "rma_number", "rma_normalized", "evidence_quote", "source_path"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rma_rows)
    with (out_root / "procurement_refs_v1_4_7.csv").open("w", encoding="utf-8", newline="") as f:
        fields = ["repair_event_id", "log_number", "equipment_family", "supplier", "order_ref", "order_ref_normalized", "description", "manufacturer_pn", "quantity", "evidence_quote", "source_path"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(order_rows)
    with (out_root / "replacement_mentions_enriched_v1_4_7.csv").open("w", encoding="utf-8", newline="") as f:
        fields = ["repair_event_id", "log_number", "equipment_family", "manufacturer_part_number", "distributor_order_ref", "supplier", "quantity", "text", "evidence_quote", "pn_classification", "original_part_number_v1_4_6"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(enriched_parts)

    lookup_db = out_root / "tracking_lookup_v1_4_7.sqlite"
    build_lookup_db(lookup_db, enriched_events, enriched_parts)

    unique_rmas = {normalize_rma(r["rma_number"]) for r in rma_rows if normalize_rma(r["rma_number"])}
    unique_orders = {normalize_order_ref(o["order_ref"]) for o in order_rows if normalize_order_ref(o["order_ref"])}
    events_with_rma = len({r["repair_event_id"] for r in rma_rows})
    events_with_orders = len({o["repair_event_id"] for o in order_rows})
    supplier_counts = Counter(o.get("supplier") or "supplier not stated" for o in order_rows)

    summary_lines = [
        f"# Nova DRL 10% Tracking + Procurement Enrichment v{VERSION}",
        "",
        "Operating mode: ADDITIVE ENRICHMENT OF FROZEN v1.4.6 BENCHMARK",
        f"Source root: {args.source_root}",
        f"Source repair events: {len(source_events)}",
        f"Source replacement mentions: {len(source_parts)}",
        f"Tracking vision records: {len(tracking_rows)}",
        f"Events with RMA evidence: {events_with_rma}",
        f"Unique normalized RMA values: {len(unique_rmas)}",
        f"RMA evidence rows: {len(rma_rows)}",
        f"Events with procurement/order evidence: {events_with_orders}",
        f"Unique normalized procurement/order refs: {len(unique_orders)}",
        f"Procurement/order evidence rows: {len(order_rows)}",
        f"v1.4.6 replacement mentions reclassified from PN to order-ref: {reclassified}",
        "Accepted facts: 0",
        "Qdrant writes: OFF",
        "NAS discovery/rescan: 0 | frozen v1.4.6 event plan only",
        "Original v1.4.6 corpus modified: NO",
        "",
        "SUPPLIER / ORDER-REFERENCE SIGNALS",
        "----------------------------------",
    ]
    for supplier, count in supplier_counts.most_common(20):
        summary_lines.append(f"{supplier}: {count} order-reference rows")
    summary_lines.extend([
        "",
        "POLICY",
        "------",
        "80/20 rule: FIXED DEFAULT until Matt explicitly changes it",
        "RMA: first-class tracking field when visibly present",
        "Distributor/order reference: preserved separately from manufacturer PN",
        "DGK-style references: Digi-Key when supplier not otherwise visible",
        "MSR-style references: Mouser when supplier not otherwise visible",
        "NWK/DSK-style references: procurement refs; supplier left unknown unless visible",
        "Manufacturer PN: never inferred from a distributor/order reference",
        "Existing v1.4.6 replacement evidence: preserved; enriched copy written separately",
        "Original share modified: NO",
        "Automatic human approval: NO",
        "Qdrant writes: OFF",
    ])
    (out_root / "drl_10pct_tracking_enrichment_summary_v1_4_7.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    manifest = {
        "version": VERSION,
        "source_version": SOURCE_VERSION,
        "created_at": utc_now(),
        "source_root": str(args.source_root),
        "sample_manifest": str(args.sample_manifest),
        "source_repair_events": len(source_events),
        "source_replacement_mentions": len(source_parts),
        "tracking_vision_records": len(tracking_rows),
        "events_with_rma": events_with_rma,
        "unique_rmas": len(unique_rmas),
        "rma_rows": len(rma_rows),
        "events_with_procurement_refs": events_with_orders,
        "unique_procurement_refs": len(unique_orders),
        "procurement_rows": len(order_rows),
        "replacement_mentions_reclassified": reclassified,
        "accepted_facts": 0,
        "qdrant_entries": 0,
    }
    save_json(out_root / "tracking_enrichment_manifest_v1_4_7.json", manifest)
    return manifest


def lookup(args: argparse.Namespace) -> int:
    db = Path(args.output_root) / "tracking_lookup_v1_4_7.sqlite"
    if not db.exists():
        print(f"Lookup DB not found: {db}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        if args.lookup_rma:
            norm = normalize_rma(args.lookup_rma)
            rows = conn.execute(
                "SELECT r.*, e.log_number, e.equipment_family, e.top_folders_json FROM rma_refs r JOIN events e USING(repair_event_id) WHERE rma_normalized=? ORDER BY e.log_number",
                (norm,),
            ).fetchall()
            print(f'# Nova DRL RMA lookup v{VERSION} | query="{args.lookup_rma}" | matches={len(rows)}')
            for r in rows:
                print(f"{r['rma_number']} | log={r['log_number'] or '-'} | {r['equipment_family']} | event={r['repair_event_id']}")
                if r["source_path"]: print(f"  source: {r['source_path']}")
                if r["evidence_quote"]: print(f"  evidence: {r['evidence_quote']}")
            return 0
        if args.lookup_order:
            norm = normalize_order_ref(args.lookup_order)
            rows = conn.execute(
                "SELECT p.*, e.log_number, e.equipment_family FROM procurement_refs p JOIN events e USING(repair_event_id) WHERE p.order_ref_normalized=? ORDER BY e.log_number",
                (norm,),
            ).fetchall()
            print(f'# Nova DRL order-reference lookup v{VERSION} | query="{args.lookup_order}" | matches={len(rows)}')
            for r in rows:
                print(f"{r['order_ref']} | supplier={r['supplier'] or '-'} | log={r['log_number'] or '-'} | {r['equipment_family']}")
                print(f"  description: {r['description'] or '-'} | manufacturer PN: {r['manufacturer_pn'] or '-'} | qty={r['quantity'] if r['quantity'] is not None else 'unstated'}")
                if r["source_path"]: print(f"  source: {r['source_path']}")
                if r["evidence_quote"]: print(f"  evidence: {r['evidence_quote']}")
            return 0
    finally:
        conn.close()
    return 2


def status(args: argparse.Namespace) -> int:
    source_root = Path(args.source_root)
    sample_path = Path(args.sample_manifest)
    print(f"# Nova DRL 10% Tracking + Procurement Enrichment Status v{VERSION}")
    print(f"Frozen v1.4.6 source root: {'FOUND' if source_root.exists() else 'NOT FOUND'} | {source_root}")
    print(f"Frozen sample manifest:    {'FOUND' if sample_path.exists() else 'NOT FOUND'} | {sample_path}")
    try:
        plan, events, parts = load_source_corpus(source_root)
        docs = all_event_documents(plan)
        print(f"Source repair events:       {len(events)}")
        print(f"Source replacement mentions:{len(parts)}")
        print(f"Frozen source documents:    {len(docs)} | primary + supporting")
    except Exception as exc:
        print(f"Frozen corpus load: ERROR | {exc}")
    vi = model_info(args.vision_model)
    print(f"Vision model:               {'FOUND' if vi.get('available') else 'MISSING'} | {args.vision_model}")
    print(f"Pillow upper-crop assist:   {'FOUND' if pillow_available() else 'NOT FOUND'}")
    print("14B event calls:            0 | existing v1.4.6 event records reused")
    print("NAS discovery/rescan:       OFF | frozen event plan only")
    print("Original v1.4.6 modified:   NO")
    print("80/20 rule:                 FIXED DEFAULT")
    print("Accepted facts:             0")
    print("Qdrant:                     OFF")
    return 0


def plan_only(args: argparse.Namespace) -> int:
    source_root = Path(args.source_root)
    try:
        plan, events, parts = load_source_corpus(source_root)
        docs = all_event_documents(plan)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.limit_events:
        allowed = {str(e.get("repair_event_id")) for e in plan[: int(args.limit_events)]}
        plan = [e for e in plan if str(e.get("repair_event_id")) in allowed]
        docs = [d for d in docs if str(d.get("repair_event_id")) in allowed]
        events = [e for e in events if str(e.get("repair_event_id")) in allowed]
        parts = [p for p in parts if str(p.get("repair_event_id")) in allowed]
    ready = 0
    pdf_pages = 0
    missing = 0
    pdf_docs = 0
    for d in docs:
        p = Path(str(d.get("absolute_path") or ""))
        if not p.exists():
            missing += 1
            continue
        ext = str(d.get("extension") or p.suffix).casefold()
        if ext == ".pdf":
            pdf_docs += 1
            try: pdf_pages += pdf_page_count(p)
            except Exception: pdf_pages += 1
        else:
            ready += 1
    planned = ready + pdf_pages
    print(f"# Nova DRL 10% Tracking + Procurement Enrichment v{VERSION} — PLAN ONLY")
    print(f"Source benchmark version:      v{SOURCE_VERSION} frozen corpus")
    print(f"Source repair events:          {len(events)}")
    print(f"Source replacement mentions:   {len(parts)}")
    print(f"Frozen Line Card/Traveler docs:{len(docs)} | includes v1.4.6 supporting docs")
    print(f"PDF source docs:               {pdf_docs}")
    print(f"Missing frozen source docs:    {missing}")
    print(f"Planned metadata vision records:{planned} | PDFs expand to pages")
    print(f"Vision task:                   RMA + procurement/order metadata ONLY")
    print(f"Upper-area crop assist:        {'ON' if pillow_available() and not args.no_upper_crop else 'OFF'} | full source remains included")
    print(f"Planned 14B event calls:       0")
    print("Existing repair/parts extraction: REUSED; not rerun")
    print("DGK/MSR/NWK/DSK handling:      order refs, not manufacturer PNs")
    print("RMA handling:                  first-class event tracking")
    print("NAS discovery/rescan:          0 | frozen v1.4.6 event plan only")
    print("Original v1.4.6 corpus writes: 0")
    print("Accepted facts:                0")
    print("Qdrant:                        OFF")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=f"Nova DRL 10% Tracking + Procurement Enrichment v{VERSION}")
    ap.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT))
    ap.add_argument("--sample-manifest", default=str(DEFAULT_SAMPLE_MANIFEST))
    ap.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    ap.add_argument("--vision-model", default=DEFAULT_VISION_MODEL)
    ap.add_argument("--vision-num-ctx", type=int, default=16384)
    ap.add_argument("--vision-num-predict", type=int, default=1024)
    ap.add_argument("--render-dpi", type=int, default=300)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--limit-events", type=int, default=None, help="Smoke-test only: process first N frozen events")
    ap.add_argument("--force-vision", action="store_true")
    ap.add_argument("--no-upper-crop", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--lookup-rma")
    ap.add_argument("--lookup-order")
    args = ap.parse_args()

    if args.status:
        return status(args)
    if args.plan_only:
        return plan_only(args)
    if args.lookup_rma or args.lookup_order:
        return lookup(args)

    source_root = Path(args.source_root)
    try:
        event_plan, source_events, source_parts = load_source_corpus(source_root)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.limit_events:
        allowed = {str(e.get("repair_event_id")) for e in event_plan[: int(args.limit_events)]}
        event_plan = [e for e in event_plan if str(e.get("repair_event_id")) in allowed]
        source_events = [e for e in source_events if str(e.get("repair_event_id")) in allowed]
        source_parts = [p for p in source_parts if str(p.get("repair_event_id")) in allowed]

    print(f"# Nova DRL 10% Tracking + Procurement Enrichment v{VERSION}")
    print("Operating mode: ADDITIVE ENRICHMENT OF FROZEN v1.4.6 CORPUS")
    print(f"Source repair events: {len(source_events)}")
    print(f"Source replacement mentions: {len(source_parts)}")
    print("Original repair/parts extraction: REUSED")
    print("NAS discovery/rescan: OFF")
    print("Accepted facts: 0")
    print("Qdrant: OFF")

    docs = all_event_documents(event_plan)
    print(f"Frozen source documents selected: {len(docs)}")
    records = expand_metadata_sources(docs, Path(args.output_root), args.render_dpi)
    missing = sum(1 for r in records if r.get("record_status") == "missing_source")
    if missing:
        print(f"Frozen source documents missing at runtime: {missing}")
    tracking_rows = acquire_tracking_metadata(args, records)
    event_tracking = merge_event_tracking(event_plan, tracking_rows)
    manifest = write_outputs(args, source_events, source_parts, tracking_rows, event_tracking)

    print("\n# COMPLETE")
    print(f"Repair events enriched:        {manifest['source_repair_events']}")
    print(f"Tracking vision records:       {manifest['tracking_vision_records']}")
    print(f"Events with RMA evidence:      {manifest['events_with_rma']}")
    print(f"Unique RMA values:             {manifest['unique_rmas']}")
    print(f"Events with procurement refs: {manifest['events_with_procurement_refs']}")
    print(f"Unique procurement refs:       {manifest['unique_procurement_refs']}")
    print(f"Replacement PNs reclassified: {manifest['replacement_mentions_reclassified']}")
    print("Accepted facts:                0")
    print("Qdrant:                        OFF")
    print(f"Summary: {Path(args.output_root) / 'drl_10pct_tracking_enrichment_summary_v1_4_7.txt'}")
    print(f"RMA CSV: {Path(args.output_root) / 'rma_lookup_v1_4_7.csv'}")
    print(f"Orders:  {Path(args.output_root) / 'procurement_refs_v1_4_7.csv'}")
    print(f"Lookup:  {Path(args.output_root) / 'tracking_lookup_v1_4_7.sqlite'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
