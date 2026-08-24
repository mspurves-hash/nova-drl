#!/usr/bin/env python3
"""
Nova DRL Full Repair-History Corpus Ingester v1.5.0

Purpose
-------
Ingest the full indexed DRL tech-scan repair-folder universe into one evidence-
preserving repair-history corpus. This is the production-scale successor to the
frozen v1.4.6 10% benchmark.

Standing DRL Nova policy
------------------------
- 80/20 is FIXED DEFAULT until Matt explicitly changes it.
- Travelers / Line Cards primarily provide high-volume repair-history and parts-used
  evidence. Do not force detailed procedures/testing when the source does not contain them.
- RMA, Customer PO, distributor/order refs, DRL logs, and similar tracking fields are
  LITERAL identifiers: evidence required; no recurrence-based guessing.
- DGK = Digi-Key order reference, MSR = Mouser order reference. NWK/DSK are procurement
  refs with supplier left unknown unless visible. Procurement refs are NOT manufacturer PNs.
- Preserve original source paths, evidence, and raw model output for human exception review.
- .picasa.ini and .picasaoriginals are irrelevant to repair knowledge and excluded.
- Roger-only paired-card convention: (2) typed card is primary; (1) retained supporting.
- Unrelated equipment families remain separate. No cross-model parts ranking in ingestion.

Scale / reuse
-------------
- Uses the persistent DRL File Index; never recursively scans the NAS.
- First persisted run freezes the current top-level repair-folder universe into a full-corpus
  manifest. --refresh-manifest intentionally adds/removes top-level folders later.
- Existing v1.4.6 technical vision evidence and v1.4.7 tracking evidence are reused when
  source-image identity matches, avoiding unnecessary reruns of the frozen 10% benchmark.
- Existing v1.4.7 enriched event facts may be reused for matching events.
- New/changed sources are processed once with a unified vision pass capturing both high-signal
  repair evidence and tracking/procurement metadata, followed by one 14B event-structuring call.

No source writes. No automatic approval. No Qdrant writes.
"""
from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import hashlib
import json
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import nova_drl_10pct_corpus_ingester_v1_4_6 as base
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import nova_drl_10pct_corpus_ingester_v1_4_6 as base

VERSION = "1.5.0"
DEFAULT_INDEX_DB = Path("/opt/nova-drl/index/drl_file_index.sqlite")
DEFAULT_SHARE_ROOT = Path("/mnt/drl")
DEFAULT_TECH_BASE = "000 folder for tech scans"
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/drl_full_corpus_v1_5_0")
DEFAULT_MANIFEST = Path("/opt/nova-drl/corpus/drl_full_corpus_v1_5_0/full_corpus_manifest_v1_5_0.json")
DEFAULT_VISION_MODEL = "qwen3-vl-drl:8b-q8-16k"
DEFAULT_REASON_MODEL = "qwen25-drl:14b-q6-16k"
DEFAULT_TYPED_PAIR_ENGINEER = "ROGER"
DEFAULT_REUSE_V146_ROOT = Path("/opt/nova-drl/output/drl_10pct_benchmark_v1_4_6")
DEFAULT_REUSE_V147_ROOT = Path("/opt/nova-drl/output/drl_10pct_tracking_enrichment_v1_4_7")
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
IMAGE_EXTENSIONS = base.IMAGE_EXTENSIONS
CATEGORIES = base.CATEGORIES
KNOWN_ORDER_PREFIX_SUPPLIER = {"DGK": "Digi-Key", "MSR": "Mouser", "NWK": None, "DSK": None}
ORDER_PREFIX_RE = re.compile(r"\b(DGK|MSR|NWK|DSK)[\s._-]*([A-Z0-9-]{3,})\b", re.I)
CUSTOMER_PO_LABEL_RE = re.compile(r"\b(?:cust(?:omer)?\s*P\.?O\.?|customer\s+purchase\s+order|PO\s*#)\b", re.I)
RMA_LABEL_RE = re.compile(r"\bRMA\s*#?\b|return\s+material\s+authorization", re.I)
SUPPLIER_RE = re.compile(r"\b(Digi[- ]?Key|Mouser)\b", re.I)

UNIFIED_VISION_PROMPT = r"""Read this DRL Traveler / Line Card using the FIXED 80/20 rule.
Do NOT transcribe the whole form. Capture high-value repair-history / parts evidence AND literal
tracking/procurement fields in one pass.

Return JSON only with this exact shape:
{
  "technical_evidence": "concise plain text using the headings below",
  "rma_numbers": [{"value":"...","evidence_quote":"..."}],
  "customer_po_numbers": [{"value":"...","evidence_quote":"..."}],
  "procurement_refs": [{
      "order_ref":"...","supplier":null,"description":null,
      "manufacturer_pn":null,"quantity":null,"evidence_quote":"..."
  }]
}

technical_evidence should use only headings that actually have useful information:
BASIC REPORTED PROBLEM:
PARTS / ASSEMBLIES REPLACED OR USED:
OTHER REPAIR-HISTORY NOTES:
EXPLICIT TEST / OUTCOME NOTE:

Rules:
- Travelers are mainly parts/replacement and repair-history evidence. Do not invent procedures.
- Prioritize actual replaced/installed/rebuilt parts, assembly names, quantities and likely PNs.
- DGK..., MSR..., NWK..., DSK... in order areas are PROCUREMENT refs, not replacement-part PNs.
  DGK means Digi-Key; MSR means Mouser. Do not put those refs in technical_evidence as parts unless
  a separate actual manufacturer PN/part is visibly identified as installed/replaced.
- RMA: only values visibly labeled RMA/RMA#/Return Material Authorization.
- Customer PO: only values visibly labeled Customer PO/Cust PO/PO# in customer tracking context.
- Procurement: use the visible supplier/order area. If the same line visibly provides a real
  manufacturer PN, capture it separately; NEVER infer manufacturer PN from an order_ref.
- quantity is integer only when explicit, else null.
- evidence_quote must be a short visible fragment that literally supports the tracking/order item.
- Preserve useful uncertain technical fragments rather than inventing exact characters.
- If nothing is present for a tracking list, return an empty array.
"""

EVENT_PROMPT = base.EVENT_PROMPT + r"""

Additional strict rule for this full-corpus release:
- Lines that are only purchasing references (DGK..., MSR..., NWK..., DSK..., Digi-Key/Mouser order
  numbers, Customer PO) are NOT parts_replaced. Only actual installed/replaced parts belong there.
"""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def normalized_ws(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def upper_id(v: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", normalized_ws(v)).upper()


def save_json(path: Path, value: Any) -> None:
    base.save_json(path, value)


def load_json(path: Path) -> Any:
    return base.load_json(path)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    base.write_jsonl(path, rows)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return base.sha256_file(path)


def stable_json_hash(value: Any) -> str:
    return base.stable_json_hash(value)


def safe_slug(value: Any) -> str:
    return base.safe_slug(value)


def model_info(model: str) -> Dict[str, Any]:
    return base.model_info(model)


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
    req = urllib.request.Request(
        OLLAMA_GENERATE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return str(json.loads(response.read().decode("utf-8")).get("response") or "")


def parse_json_response(text: str) -> Any:
    return base.parse_json_response(text)


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


def make_upper_crop(source: Path, out_path: Path, fraction: float = 0.48, scale: float = 1.6) -> Optional[Path]:
    try:
        from PIL import Image
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as im:
            im = im.convert("RGB")
            w, h = im.size
            if w < 200 or h < 200:
                return None
            crop = im.crop((0, 0, w, max(1, int(h * fraction))))
            if scale > 1:
                crop = crop.resize((int(crop.width * scale), int(crop.height * scale)))
            crop.save(out_path, format="JPEG", quality=91)
        return out_path
    except Exception:
        return None


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False


def manifest_from_index(args: argparse.Namespace, by_folder: Dict[str, List[Dict[str, Any]]], meta: Dict[str, str], *, persist: bool) -> Dict[str, Any]:
    path = Path(args.manifest)
    if path.exists() and not args.refresh_manifest:
        manifest = load_json(path)
        if str(manifest.get("version")) != VERSION:
            raise RuntimeError(f"full-corpus manifest version mismatch: {manifest.get('version')} != {VERSION}")
        return manifest
    folders = sorted(by_folder, key=str.casefold)
    manifest = {
        "version": VERSION,
        "created_at": utc_now(),
        "manifest_policy": "full indexed top-level tech-scan folder snapshot",
        "tech_base": args.tech_base,
        "index_db": str(args.index_db),
        "index_share_root": meta.get("share_root") or meta.get("bound_share_root"),
        "folder_count": len(folders),
        "folders": [{"folder": f, "ordinal": i + 1} for i, f in enumerate(folders)],
        "accepted_facts": 0,
        "qdrant_entries": 0,
    }
    if persist:
        save_json(path, manifest)
    return manifest


def prepare(args: argparse.Namespace, *, persist_manifest: bool = False):
    by_folder, meta = base.load_tech_scan_rows(Path(args.index_db), args.tech_base)
    bound = meta.get("share_root") or meta.get("bound_share_root")
    if bound and Path(bound).resolve() != Path(args.share_root).resolve():
        raise RuntimeError(f"index bound to {bound}, not {args.share_root}")
    manifest = manifest_from_index(args, by_folder, meta, persist=persist_manifest)
    full_like = {
        "sampled_folders": [{"folder": x["folder"]} for x in manifest.get("folders", [])],
        "sample_folder_count": manifest.get("folder_count"),
    }
    args.limit_sampled_folders = args.limit_folders
    selection = base.select_sample_sources(args, by_folder, full_like)
    events = base.build_event_plan(selection["selected_documents"], args.typed_pair_engineer)
    return by_folder, meta, manifest, selection, events


def expand_all_sources(events: Sequence[Dict[str, Any]], output_root: Path, render_dpi: int) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    idx = 0
    for event in events:
        primary_paths = {str(d.get("absolute_path")) for d in event.get("primary_documents", [])}
        docs = list(event.get("all_documents") or (list(event.get("primary_documents", [])) + list(event.get("supporting_documents", []))))
        seen = set()
        for doc in docs:
            path = Path(str(doc.get("absolute_path") or ""))
            if not path.exists() or not path.is_file():
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            is_primary = key in primary_paths
            ext = str(doc.get("extension") or path.suffix).casefold()
            if ext in IMAGE_EXTENSIONS:
                idx += 1
                file_sha = sha256_file(path)
                rid = "full_" + hashlib.sha256((str(doc.get("relative_path")) + "\n" + file_sha).encode("utf-8")).hexdigest()[:16]
                records.append({
                    "source_index": idx,
                    "source_record_id": rid,
                    "repair_event_id": event["repair_event_id"],
                    "log_number": event.get("log_number"),
                    "equipment_family": event.get("equipment_family"),
                    "top_folders": event.get("top_folders"),
                    "source_path": str(path),
                    "source_relative_path": doc.get("relative_path"),
                    "source_image": str(path),
                    "source_image_sha256": file_sha,
                    "source_pdf_page": None,
                    "line_card_sequence": doc.get("line_card_sequence"),
                    "selection_reason": doc.get("selection_reason"),
                    "technical_primary": is_primary,
                })
            elif ext == ".pdf":
                pages = base.pdf_page_count(path)
                pdf_sha = sha256_file(path)
                for page in range(1, pages + 1):
                    idx += 1
                    rendered = output_root / "pdf_adapter" / safe_slug(event["repair_event_id"]) / safe_slug(path.stem) / f"page_{page:04d}.jpg"
                    if not rendered.exists() or rendered.stat().st_size == 0:
                        base.render_pdf_page(path, page, rendered, render_dpi)
                        print(f"[pdf] event={event['repair_event_id']} {path.name} page={page}/{pages} -> {rendered.name}")
                    img_sha = sha256_file(rendered)
                    rid = "fullpdf_" + hashlib.sha256((str(doc.get("relative_path")) + f"\n{pdf_sha}\n{page}").encode("utf-8")).hexdigest()[:16]
                    records.append({
                        "source_index": idx,
                        "source_record_id": rid,
                        "repair_event_id": event["repair_event_id"],
                        "log_number": event.get("log_number"),
                        "equipment_family": event.get("equipment_family"),
                        "top_folders": event.get("top_folders"),
                        "source_path": f"{path}#page={page}",
                        "source_relative_path": str(doc.get("relative_path")) + f"#page={page}",
                        "source_image": str(rendered),
                        "source_image_sha256": img_sha,
                        "source_pdf_page": page,
                        "line_card_sequence": doc.get("line_card_sequence"),
                        "selection_reason": doc.get("selection_reason"),
                        "technical_primary": is_primary,
                    })
    return records


def normalized_source_key(path: Any) -> str:
    return str(path or "").replace("\\", "/").casefold()


def load_reuse_maps(args: argparse.Namespace) -> Dict[str, Any]:
    technical: Dict[str, Dict[str, Any]] = {}
    tracking: Dict[str, Dict[str, Any]] = {}
    events: Dict[str, Dict[str, Any]] = {}
    v146 = Path(args.reuse_v146_root)
    v147 = Path(args.reuse_v147_root)
    vm = v146 / "vision_source_manifest_v1_4_6.json"
    if vm.exists():
        try:
            for row in load_json(vm).get("records") or []:
                technical[normalized_source_key(row.get("source_path"))] = row
        except Exception:
            pass
    for row in read_jsonl(v147 / "source_tracking_metadata_v1_4_7.jsonl"):
        tracking[normalized_source_key(row.get("source_path"))] = row
    for row in read_jsonl(v147 / "repair_events_enriched_v1_4_7.jsonl"):
        events[str(row.get("repair_event_id"))] = row
    return {"technical": technical, "tracking": tracking, "events": events}


def source_quote_contains(value: Any, quote: Any) -> bool:
    v = upper_id(value)
    q = upper_id(quote)
    return bool(v and q and v in q)


def supplier_from_ref_or_quote(order_ref: str, supplier: Any, quote: str) -> Optional[str]:
    s = normalized_ws(supplier)
    if s:
        if re.search(r"digi", s, re.I):
            return "Digi-Key"
        if re.search(r"mouser", s, re.I):
            return "Mouser"
        return s
    m = ORDER_PREFIX_RE.search(order_ref)
    if m:
        return KNOWN_ORDER_PREFIX_SUPPLIER.get(m.group(1).upper())
    sm = SUPPLIER_RE.search(quote)
    if sm:
        return "Digi-Key" if re.search(r"digi", sm.group(1), re.I) else "Mouser"
    return None


def recover_customer_po_from_quote(quote: str) -> Optional[str]:
    if not CUSTOMER_PO_LABEL_RE.search(quote):
        return None
    m = re.search(r"(?:Cust(?:omer)?\s*P\.?O\.?|PO\s*#)\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})", quote, re.I)
    return normalized_ws(m.group(1)) if m else None


def recover_order_from_quote(quote: str, supplier: Optional[str]) -> Optional[str]:
    m = ORDER_PREFIX_RE.search(quote)
    if m:
        return normalized_ws(m.group(0))
    if supplier or SUPPLIER_RE.search(quote):
        # Prefer a token directly after a visible supplier name, including
        # wording such as "Parts ordered DigiKey 55516".
        m = re.search(r"(?:Digi[- ]?Key|Mouser)\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})", quote, re.I)
        if m and not CUSTOMER_PO_LABEL_RE.search(quote):
            return normalized_ws(m.group(1))
        # Then accept generic order wording only when the next token is not a supplier label.
        m = re.search(r"parts?\s+order(?:ed)?\s*[:#-]?\s*(?!Digi[- ]?Key\b|Mouser\b)([A-Z0-9][A-Z0-9._/-]{3,})", quote, re.I)
        if m and not CUSTOMER_PO_LABEL_RE.search(quote):
            return normalized_ws(m.group(1))
    return None


def validate_tracking(parsed: Any) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {"rma_numbers": [], "customer_po_numbers": [], "procurement_refs": []}
    if not isinstance(parsed, dict):
        return out

    seen = set()
    for row in parsed.get("rma_numbers") or []:
        if not isinstance(row, dict):
            continue
        value = normalized_ws(row.get("value"))
        quote = normalized_ws(row.get("evidence_quote"))
        if not value or not quote or not RMA_LABEL_RE.search(quote) or not source_quote_contains(value, quote):
            continue
        norm = upper_id(value)
        key = (norm, quote.casefold())
        if key in seen:
            continue
        seen.add(key)
        out["rma_numbers"].append({"value": value, "normalized": norm, "evidence_quote": quote})

    seen = set()
    for row in parsed.get("customer_po_numbers") or []:
        if not isinstance(row, dict):
            continue
        value = normalized_ws(row.get("value"))
        quote = normalized_ws(row.get("evidence_quote"))
        recovered = recover_customer_po_from_quote(quote)
        if not value or not quote or not CUSTOMER_PO_LABEL_RE.search(quote):
            if recovered:
                value = recovered
            else:
                continue
        if not source_quote_contains(value, quote):
            if recovered:
                value = recovered
            else:
                continue
        norm = upper_id(value)
        key = (norm, quote.casefold())
        if key in seen:
            continue
        seen.add(key)
        out["customer_po_numbers"].append({"value": value, "normalized": norm, "evidence_quote": quote})

    seen = set()
    for row in parsed.get("procurement_refs") or []:
        if not isinstance(row, dict):
            continue
        quote = normalized_ws(row.get("evidence_quote"))
        ref = normalized_ws(row.get("order_ref"))
        supplier = supplier_from_ref_or_quote(ref, row.get("supplier"), quote)
        if CUSTOMER_PO_LABEL_RE.search(quote) and not (SUPPLIER_RE.search(quote) or ORDER_PREFIX_RE.search(quote)):
            continue
        if not ref or not quote or not source_quote_contains(ref, quote):
            recovered = recover_order_from_quote(quote, supplier)
            if recovered:
                ref = recovered
            else:
                continue
        supplier = supplier_from_ref_or_quote(ref, supplier, quote)
        norm = upper_id(ref)
        desc = normalized_ws(row.get("description")) or None
        mpn = normalized_ws(row.get("manufacturer_pn")) or None
        qty = row.get("quantity")
        try:
            qty = int(qty) if qty is not None and not isinstance(qty, bool) else None
        except Exception:
            qty = None
        if qty is not None and not (1 <= qty <= 10000):
            qty = None
        key = (norm, (supplier or "").casefold(), (mpn or "").casefold(), quote.casefold())
        if key in seen:
            continue
        seen.add(key)
        out["procurement_refs"].append({
            "order_ref": ref,
            "normalized": norm,
            "supplier": supplier,
            "description": desc,
            "manufacturer_pn": mpn,
            "quantity": qty,
            "evidence_quote": quote,
        })
    return out


def classify_v147_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    pseudo = {"rma_numbers": metadata.get("rma_numbers") or [], "customer_po_numbers": [], "procurement_refs": []}
    for row in metadata.get("procurement_refs") or []:
        quote = normalized_ws(row.get("evidence_quote"))
        po = recover_customer_po_from_quote(quote)
        if po:
            pseudo["customer_po_numbers"].append({"value": po, "evidence_quote": quote})
        else:
            pseudo["procurement_refs"].append(row)
    return {"technical_evidence": "", "tracking": validate_tracking(pseudo)}


def parse_unified_vision(parsed: Any) -> Dict[str, Any]:
    if not isinstance(parsed, dict):
        parsed = {}
    technical = normalized_ws(parsed.get("technical_evidence"))
    return {"technical_evidence": technical, "tracking": validate_tracking(parsed)}


def acquire_unified_evidence(args: argparse.Namespace, records: Sequence[Dict[str, Any]], reuse: Dict[str, Any]) -> List[Dict[str, Any]]:
    root = Path(args.output_root) / "unified_vision"
    vinfo = model_info(args.vision_model)
    out: List[Dict[str, Any]] = []
    for i, src in enumerate(records, 1):
        folder = root / f"record_{i:05d}_{src['source_record_id'][-8:]}"
        folder.mkdir(parents=True, exist_ok=True)
        parsed_path = folder / "parsed.json"
        run_path = folder / "run.json"
        raw_path = folder / "raw.txt"
        crop_path = folder / "upper_crop.jpg"
        result: Optional[Dict[str, Any]] = None
        action: Optional[str] = None
        crop_used = False

        if parsed_path.exists() and run_path.exists() and not args.force_vision:
            try:
                run = load_json(run_path)
                if (
                    run.get("source_image_sha256") == src.get("source_image_sha256")
                    and run.get("vision_model_digest") == vinfo.get("digest")
                    and run.get("prompt_sha256") == sha256_text(UNIFIED_VISION_PROMPT)
                ):
                    result = load_json(parsed_path)
                    action = "cache"
                    crop_used = bool(run.get("upper_crop_used"))
            except Exception:
                pass

        if result is None and not args.no_reuse_10pct:
            key = normalized_source_key(src.get("source_path"))
            tracking_row = reuse["tracking"].get(key)
            technical_row = reuse["technical"].get(key)
            tracking_ok = bool(tracking_row and tracking_row.get("source_image_sha256") == src.get("source_image_sha256"))
            technical_path = Path(str((technical_row or {}).get("traveler_evidence_path") or ""))
            technical_ok = bool(
                technical_row
                and technical_row.get("source_image_sha256") == src.get("source_image_sha256")
                and technical_path.exists()
            )
            if tracking_ok and (technical_ok or not src.get("technical_primary")):
                tracking = classify_v147_metadata(tracking_row.get("metadata") or {})["tracking"]
                technical = technical_path.read_text(encoding="utf-8", errors="ignore") if technical_ok else ""
                result = {"technical_evidence": technical, "tracking": tracking}
                action = "reuse_v146_v147" if technical_ok else "reuse_v147_tracking"
                save_json(parsed_path, result)
                raw_path.write_text("REUSED FROM FROZEN v1.4.6/v1.4.7\n", encoding="utf-8")
                save_json(run_path, {
                    "version": VERSION,
                    "source_image_sha256": src.get("source_image_sha256"),
                    "vision_model_digest": vinfo.get("digest"),
                    "prompt_sha256": sha256_text(UNIFIED_VISION_PROMPT),
                    "reuse_mode": action,
                    "upper_crop_used": False,
                    "accepted_facts": 0,
                    "qdrant_entries": 0,
                })

        if result is None:
            images = [Path(src["source_image"])]
            crop = None
            if not args.no_upper_crop and pillow_available():
                crop = make_upper_crop(images[0], crop_path)
                if crop:
                    images.append(crop)
                    crop_used = True
            try:
                parsed, attempts, raw = call_vision_json(
                    args.vision_model,
                    UNIFIED_VISION_PROMPT,
                    image_paths=images,
                    num_ctx=args.vision_num_ctx,
                    num_predict=args.vision_num_predict,
                    timeout=args.timeout,
                    retries=1,
                )
                result = parse_unified_vision(parsed)
            except Exception as exc:
                result = {"technical_evidence": "", "tracking": {"rma_numbers": [], "customer_po_numbers": [], "procurement_refs": []}}
                attempts = [{"ok": False, "error": str(exc)}]
                raw = ""
            save_json(parsed_path, result)
            raw_path.write_text(raw, encoding="utf-8")
            save_json(run_path, {
                "version": VERSION,
                "source_image_sha256": src.get("source_image_sha256"),
                "vision_model_digest": vinfo.get("digest"),
                "prompt_sha256": sha256_text(UNIFIED_VISION_PROMPT),
                "upper_crop_used": crop_used,
                "attempts": attempts,
                "accepted_facts": 0,
                "qdrant_entries": 0,
            })
            action = "model_run"

        row = dict(src)
        row["technical_evidence"] = normalized_ws(result.get("technical_evidence"))
        row["tracking"] = result.get("tracking") or {"rma_numbers": [], "customer_po_numbers": [], "procurement_refs": []}
        row["cache_action"] = action
        out.append(row)
        trk = row["tracking"]
        print(
            f"[vision {i}/{len(records)}] event={src['repair_event_id']} "
            f"tech={'yes' if row['technical_evidence'] else 'no'} "
            f"rma={len(trk['rma_numbers'])} po={len(trk['customer_po_numbers'])} "
            f"orders={len(trk['procurement_refs'])} | {action}"
        )
    write_jsonl(Path(args.output_root) / "source_unified_evidence_v1_5_0.jsonl", out)
    return out


def procurement_only_part(item: Dict[str, Any]) -> bool:
    pn = normalized_ws(item.get("part_number"))
    text = normalized_ws(item.get("text"))
    quote = normalized_ws(item.get("evidence_quote"))
    blob = " ".join([text, quote])
    pnu = upper_id(pn)
    if pnu.startswith(tuple(KNOWN_ORDER_PREFIX_SUPPLIER)):
        return True
    if CUSTOMER_PO_LABEL_RE.search(blob):
        return True
    match = ORDER_PREFIX_RE.search(blob)
    if match:
        if not pn:
            return True
        ordernorm = upper_id(match.group(0))
        digits = upper_id(match.group(2))
        if pnu in {ordernorm, digits}:
            return True
    if re.search(r"\b(?:Digi[- ]?Key|Mouser)\b", blob, re.I) and re.search(r"\b(?:parts?\s+order(?:ed)?|order\s*#?)\b", blob, re.I):
        if not pn or pnu.isdigit():
            return True
    return False


def merge_event_tracking(events: Sequence[Dict[str, Any]], vision_rows: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_event: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in vision_rows:
        by_event[str(row.get("repair_event_id"))].append(row)
    result: Dict[str, Dict[str, Any]] = {}
    for event in events:
        eid = str(event.get("repair_event_id"))
        merged: Dict[str, List[Dict[str, Any]]] = {"rma_numbers": [], "customer_po_numbers": [], "procurement_refs": []}
        seen = {k: set() for k in merged}
        for src in by_event.get(eid, []):
            tracking = src.get("tracking") or {}
            for key in merged:
                for row in tracking.get(key) or []:
                    item = dict(row)
                    item["source_path"] = src.get("source_path")
                    identity = (
                        item.get("normalized") or upper_id(item.get("value") or item.get("order_ref")),
                        normalized_ws(item.get("evidence_quote")).casefold(),
                    )
                    if identity in seen[key]:
                        continue
                    seen[key].add(identity)
                    merged[key].append(item)
        result[eid] = merged
    return result


def load_reuse_event_facts(reuse: Dict[str, Any], event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    old = reuse["events"].get(str(event.get("repair_event_id")))
    if not old:
        return None
    old_paths = set(map(str, old.get("primary_source_paths") or []))
    new_paths = set(str(d.get("absolute_path")) for d in event.get("primary_documents", []))
    if old_paths != new_paths:
        return None
    facts = old.get("facts")
    return json.loads(json.dumps(facts)) if isinstance(facts, dict) else None


def extract_events(args: argparse.Namespace, events: Sequence[Dict[str, Any]], vision_rows: Sequence[Dict[str, Any]], tracking: Dict[str, Dict[str, Any]], reuse: Dict[str, Any]) -> List[Dict[str, Any]]:
    by_event: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in vision_rows:
        if row.get("technical_primary") and row.get("technical_evidence"):
            by_event[str(row.get("repair_event_id"))].append(row)
    reason_info = model_info(args.reason_model)
    root = Path(args.output_root) / "event_extraction"
    out: List[Dict[str, Any]] = []

    for i, event in enumerate(events, 1):
        eid = str(event["repair_event_id"])
        records = by_event.get(eid, [])
        blocks = [r["technical_evidence"] for r in records]
        facts: Optional[Dict[str, Any]] = None
        action: Optional[str] = None

        if not args.force_extraction and not args.no_reuse_10pct:
            facts = load_reuse_event_facts(reuse, event)
            if facts is not None:
                action = "reuse_v147_event"

        folder = root / f"event_{i:05d}_{safe_slug(eid)}"
        folder.mkdir(parents=True, exist_ok=True)
        parsed_path = folder / "parsed.json"
        run_path = folder / "run.json"
        manifest = [{"source": r.get("source_path"), "evidence_sha": sha256_text(r.get("technical_evidence") or "")} for r in records]
        input_hash = stable_json_hash(manifest)

        if facts is None and parsed_path.exists() and run_path.exists() and not args.force_extraction:
            try:
                run = load_json(run_path)
                if (
                    run.get("evidence_manifest_sha256") == input_hash
                    and run.get("reason_model_digest") == reason_info.get("digest")
                    and run.get("prompt_sha256") == sha256_text(EVENT_PROMPT)
                ):
                    facts = load_json(parsed_path)
                    action = "cache"
            except Exception:
                pass

        if facts is None:
            payload = "\n\n".join(f"EVIDENCE BLOCK {n}:\n{block}" for n, block in enumerate(blocks, 1))
            if not blocks:
                parsed, attempts = {}, []
            else:
                try:
                    parsed, attempts = base.call_json(
                        args.reason_model,
                        EVENT_PROMPT + "\n\n" + payload,
                        num_ctx=args.reason_num_ctx,
                        num_predict=args.event_num_predict,
                        timeout=args.timeout,
                        retries=1,
                    )
                except Exception as exc:
                    parsed, attempts = {}, [{"ok": False, "error": str(exc)}]
            facts = base.validate_event_json(parsed, blocks)
            facts["parts_replaced"] = [p for p in facts.get("parts_replaced", []) if not procurement_only_part(p)]
            save_json(parsed_path, facts)
            save_json(run_path, {
                "version": VERSION,
                "evidence_manifest_sha256": input_hash,
                "reason_model_digest": reason_info.get("digest"),
                "prompt_sha256": sha256_text(EVENT_PROMPT),
                "attempts": attempts,
                "accepted_facts": 0,
                "qdrant_entries": 0,
            })
            action = "model_run"
        else:
            facts.setdefault("parts_replaced", [])
            facts["parts_replaced"] = [p for p in facts.get("parts_replaced", []) if not procurement_only_part(p)]

        row = {
            "repair_event_id": eid,
            "log_number": event.get("log_number"),
            "legacy_event_token": event.get("legacy_event_token"),
            "equipment_family": event.get("equipment_family"),
            "equipment_families": event.get("equipment_families"),
            "top_folders": event.get("top_folders"),
            "primary_source_paths": [d["absolute_path"] for d in event.get("primary_documents", [])],
            "supporting_source_paths": [d["absolute_path"] for d in event.get("supporting_documents", [])],
            "roger_pair_optimization_applied": bool(event.get("typed_pair_optimization_applied")),
            "tracking": tracking.get(eid, {"rma_numbers": [], "customer_po_numbers": [], "procurement_refs": []}),
            "facts": facts,
        }
        out.append(row)
        print(f"[event {i}/{len(events)}] {eid} family={str(event.get('equipment_family'))[:42]} parts={len(facts.get('parts_replaced', []))} | {action}")

    write_jsonl(Path(args.output_root) / "repair_events_v1_5_0.jsonl", out)
    return out


def write_outputs(args: argparse.Namespace, manifest: Dict[str, Any], selection: Dict[str, Any], events: Sequence[Dict[str, Any]], event_rows: Optional[Sequence[Dict[str, Any]]], vision_rows: Optional[Sequence[Dict[str, Any]]]) -> None:
    out = Path(args.output_root)
    out.mkdir(parents=True, exist_ok=True)
    save_json(out / "source_selection_v1_5_0.json", {
        "version": VERSION,
        "manifest": str(args.manifest),
        **selection,
        "accepted_facts": 0,
        "qdrant_entries": 0,
    })
    save_json(out / "repair_event_plan_v1_5_0.json", {
        "version": VERSION,
        "repair_event_count": len(events),
        "events": list(events),
        "accepted_facts": 0,
        "qdrant_entries": 0,
    })
    if event_rows is None:
        return

    parts: List[Dict[str, Any]] = []
    rmas: List[Dict[str, Any]] = []
    customer_pos: List[Dict[str, Any]] = []
    orders: List[Dict[str, Any]] = []
    for event in event_rows:
        eid = event["repair_event_id"]
        for p in event.get("facts", {}).get("parts_replaced", []):
            parts.append({
                "repair_event_id": eid,
                "log_number": event.get("log_number"),
                "equipment_family": event.get("equipment_family"),
                "part_number": p.get("part_number"),
                "manufacturer_part_number": p.get("part_number"),
                "quantity": p.get("quantity"),
                "text": p.get("text"),
                "evidence_quote": p.get("evidence_quote"),
                "primary_source_paths": event.get("primary_source_paths"),
            })
        tracking = event.get("tracking") or {}
        for row in tracking.get("rma_numbers") or []:
            rmas.append({"repair_event_id": eid, "log_number": event.get("log_number"), "equipment_family": event.get("equipment_family"), **row})
        for row in tracking.get("customer_po_numbers") or []:
            customer_pos.append({"repair_event_id": eid, "log_number": event.get("log_number"), "equipment_family": event.get("equipment_family"), **row})
        for row in tracking.get("procurement_refs") or []:
            orders.append({"repair_event_id": eid, "log_number": event.get("log_number"), "equipment_family": event.get("equipment_family"), **row})

    write_jsonl(out / "replacement_mentions_v1_5_0.jsonl", parts)
    write_jsonl(out / "rma_refs_v1_5_0.jsonl", rmas)
    write_jsonl(out / "customer_po_refs_v1_5_0.jsonl", customer_pos)
    write_jsonl(out / "procurement_refs_v1_5_0.jsonl", orders)

    def write_csv(path: Path, rows: Sequence[Dict[str, Any]], fields: Sequence[str]) -> None:
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    write_csv(out / "rma_refs_v1_5_0.csv", rmas, ["repair_event_id", "log_number", "equipment_family", "value", "normalized", "evidence_quote", "source_path"])
    write_csv(out / "customer_po_refs_v1_5_0.csv", customer_pos, ["repair_event_id", "log_number", "equipment_family", "value", "normalized", "evidence_quote", "source_path"])
    write_csv(out / "procurement_refs_v1_5_0.csv", orders, ["repair_event_id", "log_number", "equipment_family", "supplier", "order_ref", "normalized", "description", "manufacturer_pn", "quantity", "evidence_quote", "source_path"])
    write_csv(out / "replacement_mentions_v1_5_0.csv", parts, ["repair_event_id", "log_number", "equipment_family", "part_number", "quantity", "text", "evidence_quote"])

    family_counts = Counter(event.get("equipment_family") for event in event_rows)
    category_counts = Counter()
    for event in event_rows:
        for category in CATEGORIES:
            category_counts[category] += len(event.get("facts", {}).get(category, []))
    reuse_counts = Counter(row.get("cache_action") for row in (vision_rows or []))

    lines = [
        f"# Nova DRL Full Repair-History Corpus Ingester v{VERSION}",
        "",
        "Operating mode: FULL DRL TRAVELER CORPUS — FIXED 80/20",
        f"Top-level repair folders in frozen full-corpus manifest: {manifest.get('folder_count')}",
        f"Effective folders processed: {selection.get('sample_folder_count_effective')}",
        f"Folder exceptions: {selection.get('folder_exception_count')}",
        f"Selected Line Card/Traveler documents: {selection.get('selected_document_count')}",
        f"Distinct repair events: {len(events)}",
        f"Vision records: {len(vision_rows or [])}",
        f"Structured event records: {len(event_rows)}",
        f"Events with parts: {sum(1 for e in event_rows if e.get('facts', {}).get('parts_replaced'))}",
        f"Replacement mentions: {len(parts)}",
        f"Events with RMA: {len({r['repair_event_id'] for r in rmas})}",
        f"RMA evidence rows: {len(rmas)}",
        f"Events with Customer PO: {len({r['repair_event_id'] for r in customer_pos})}",
        f"Customer PO evidence rows: {len(customer_pos)}",
        f"Events with procurement/order refs: {len({r['repair_event_id'] for r in orders})}",
        f"Procurement/order rows: {len(orders)}",
        f"Vision cache/reuse modes: {dict(reuse_counts)}",
        "Accepted facts: 0",
        "Qdrant writes: OFF",
        "NAS discovery/rescan: 0 | persistent SQLite index only",
        "",
        "TOP EQUIPMENT FAMILIES — BY REPAIR EVENTS",
        "-----------------------------------------",
    ]
    for i, (family, count) in enumerate(family_counts.most_common(30), 1):
        lines.append(f"{i:2d}. {family} | repair events={count}")
    lines += ["", "STRUCTURED FACT COUNTS", "----------------------"]
    for category in CATEGORIES:
        lines.append(f"{category}: {category_counts[category]}")
    lines += [
        "",
        "POLICY",
        "------",
        "80/20 rule: FIXED DEFAULT until Matt explicitly changes it",
        "Travelers/Line Cards: repair-history + parts-used evidence first",
        "RMA / Customer PO / procurement: literal evidence required; no recurrence guessing",
        "DGK=Digi-Key order ref; MSR=Mouser order ref; NWK/DSK procurement refs unless supplier visible",
        "Procurement-only strings excluded from manufacturer-PN parts knowledge",
        ".picasa.ini and .picasaoriginals excluded from repair knowledge",
        "Roger (2) typed card primary; Roger (1) supporting when pair exists",
        "Operations Checklists/manuals remain later procedural knowledge layers",
        "Original share modified: NO",
        "Perfect OCR required: NO",
        "Automatic human approval: NO",
        "Qdrant writes: OFF",
    ]
    (out / "drl_full_corpus_summary_v1_5_0.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plan_counts(args: argparse.Namespace, events: Sequence[Dict[str, Any]], reuse: Dict[str, Any]) -> Tuple[int, int, int, int, int]:
    docs: List[Dict[str, Any]] = []
    for event in events:
        docs.extend(event.get("all_documents") or list(event.get("primary_documents", [])) + list(event.get("supporting_documents", [])))
    unique = {str(d.get("absolute_path")): d for d in docs}
    image_count = 0
    pdf_count = 0
    pdf_pages = 0
    for doc in unique.values():
        ext = str(doc.get("extension") or Path(str(doc.get("absolute_path"))).suffix).casefold()
        if ext == ".pdf":
            pdf_count += 1
            try:
                pdf_pages += base.pdf_page_count(Path(doc["absolute_path"]))
            except Exception:
                pdf_pages += 1
        else:
            image_count += 1
    planned = image_count + pdf_pages
    reuse_possible = sum(1 for doc in unique.values() if normalized_source_key(doc.get("absolute_path")) in reuse["tracking"])
    event_reuse = sum(1 for event in events if str(event.get("repair_event_id")) in reuse["events"])
    return planned, pdf_count, pdf_pages, reuse_possible, event_reuse


def status(args: argparse.Namespace) -> int:
    print(f"# Nova DRL Full Repair-History Corpus Ingester Status v{VERSION}")
    print(f"DRL index:       {'FOUND' if Path(args.index_db).exists() else 'NOT FOUND'} | {args.index_db}")
    print(f"Share root:      {'FOUND' if Path(args.share_root).exists() else 'NOT FOUND'} | {args.share_root}")
    print(f"Full manifest:   {'FROZEN' if Path(args.manifest).exists() else 'NOT FROZEN'} | {args.manifest}")
    try:
        _, _, manifest, selection, events = prepare(args, persist_manifest=False)
        print(f"Repair folders:  {manifest.get('folder_count')}")
        print(f"Selected docs:   {selection.get('selected_document_count')}")
        print(f"Exceptions:      {selection.get('folder_exception_count')}")
        print(f"Repair events:   {len(events)}")
    except Exception as exc:
        print(f"Corpus planning: ERROR | {exc}")
    vision_info, reason_info = model_info(args.vision_model), model_info(args.reason_model)
    print(f"Vision model:    {'FOUND' if vision_info.get('available') else 'MISSING'} | {args.vision_model}")
    print(f"Reason model:    {'FOUND' if reason_info.get('available') else 'MISSING'} | {args.reason_model}")
    print(f"v1.4.6 reuse:    {'FOUND' if Path(args.reuse_v146_root).exists() else 'MISSING'} | {args.reuse_v146_root}")
    print(f"v1.4.7 reuse:    {'FOUND' if Path(args.reuse_v147_root).exists() else 'MISSING'} | {args.reuse_v147_root}")
    print("NAS rescan:      OFF | persistent SQLite index only")
    print("80/20 rule:      FIXED DEFAULT")
    print("Accepted facts:  0")
    print("Qdrant:          OFF")
    return 0


def plan(args: argparse.Namespace) -> int:
    try:
        _, _, manifest, selection, events = prepare(args, persist_manifest=False)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    reuse = load_reuse_maps(args)
    planned, pdfs, pdfpages, reuse_possible, event_reuse = plan_counts(args, events, reuse)
    family_counts = Counter(event.get("equipment_family") for event in events)
    print(f"# Nova DRL Full Repair-History Corpus Ingester v{VERSION} — PLAN ONLY")
    print(f"Top-level repair folders:      {manifest.get('folder_count')}")
    print(f"Full manifest exists:          {'YES' if Path(args.manifest).exists() and not args.refresh_manifest else 'NO (first run will freeze current universe)'}")
    print(f"Selected Line Cards/Travelers: {selection.get('selected_document_count')}")
    print(f"Folder exceptions:             {selection.get('folder_exception_count')} | retained for review")
    print(f"Selector exclusions:           {json.dumps(selection.get('excluded_counts'), sort_keys=True)}")
    print(f"Distinct repair events:        {len(events)}")
    print(f"Equipment-family keys:         {len(family_counts)}")
    print(f"Roger paired events:           {sum(1 for e in events if e.get('typed_pair_optimization_applied'))}")
    print(f"Planned unified vision records:{planned} | PDFs={pdfs} pages={pdfpages}")
    print(f"Prior 10% source docs with possible tracking-cache reuse: {reuse_possible}")
    print(f"Prior 10% events with possible structured-event reuse:     {event_reuse}")
    print(f"Maximum new 14B event calls:   {len(events) - event_reuse} before v1.5.0 cache")
    print("Vision pass captures:          repair evidence + RMA + Customer PO + procurement in one pass")
    print("Tracking policy:               literal evidence only; no 80/20 guessing for identifiers")
    print("Cross-model part ranking:      OFF")
    print("NAS discovery/rescan:          0 | SQLite index only")
    print("Accepted facts:                0")
    print("Qdrant:                        OFF")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=f"Nova DRL Full Repair-History Corpus Ingester v{VERSION}")
    ap.add_argument("--index-db", default=str(DEFAULT_INDEX_DB))
    ap.add_argument("--share-root", default=str(DEFAULT_SHARE_ROOT))
    ap.add_argument("--tech-base", default=DEFAULT_TECH_BASE)
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    ap.add_argument("--typed-pair-engineer", default=DEFAULT_TYPED_PAIR_ENGINEER)
    ap.add_argument("--vision-model", default=DEFAULT_VISION_MODEL)
    ap.add_argument("--reason-model", default=DEFAULT_REASON_MODEL)
    ap.add_argument("--vision-num-ctx", type=int, default=16384)
    ap.add_argument("--vision-num-predict", type=int, default=2048)
    ap.add_argument("--reason-num-ctx", type=int, default=16384)
    ap.add_argument("--event-num-predict", type=int, default=2048)
    ap.add_argument("--render-dpi", type=int, default=300)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--reuse-v146-root", default=str(DEFAULT_REUSE_V146_ROOT))
    ap.add_argument("--reuse-v147-root", default=str(DEFAULT_REUSE_V147_ROOT))
    ap.add_argument("--no-reuse-10pct", action="store_true")
    ap.add_argument("--limit-folders", type=int, default=None, help="development/smoke-test limit applied to frozen manifest folder order")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--manifest-only", action="store_true")
    ap.add_argument("--vision-only", action="store_true")
    ap.add_argument("--refresh-manifest", action="store_true", help="INTENTIONAL: regenerate full-corpus top-level folder snapshot from current DRL index")
    ap.add_argument("--force-vision", action="store_true")
    ap.add_argument("--force-extraction", action="store_true")
    ap.add_argument("--no-upper-crop", action="store_true")
    args = ap.parse_args()
    args.limit_sampled_folders = args.limit_folders

    if args.status:
        return status(args)
    if args.plan_only:
        return plan(args)

    try:
        _, _, manifest, selection, events = prepare(args, persist_manifest=True)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"# Nova DRL Full Repair-History Corpus Ingester v{VERSION}")
    print("Operating mode: FULL DRL TRAVELER CORPUS — FIXED 80/20")
    print(f"Frozen top-level folders: {manifest.get('folder_count')}")
    print(f"Selected docs: {selection.get('selected_document_count')}")
    print(f"Folder exceptions: {selection.get('folder_exception_count')}")
    print(f"Repair events: {len(events)}")
    print("NAS rescan: OFF")
    print("Accepted facts: 0")
    print("Qdrant: OFF")

    write_outputs(args, manifest, selection, events, None, None)
    if args.manifest_only:
        print("# MANIFEST COMPLETE")
        print(f"Full manifest: {args.manifest}")
        return 0

    records = expand_all_sources(events, Path(args.output_root), args.render_dpi)
    reuse = load_reuse_maps(args)
    print(f"Unified vision records after PDF expansion: {len(records)}")
    vision_rows = acquire_unified_evidence(args, records, reuse)
    if args.vision_only:
        print("# VISION-ONLY COMPLETE")
        return 0

    tracking = merge_event_tracking(events, vision_rows)
    event_rows = extract_events(args, events, vision_rows, tracking, reuse)
    write_outputs(args, manifest, selection, events, event_rows, vision_rows)
    replacement_count = sum(len(e.get("facts", {}).get("parts_replaced", [])) for e in event_rows)

    print("\n# COMPLETE")
    print(f"Repair events:        {len(event_rows)}")
    print(f"Vision records:       {len(vision_rows)}")
    print(f"Replacement mentions: {replacement_count}")
    print(f"Summary: {Path(args.output_root) / 'drl_full_corpus_summary_v1_5_0.txt'}")
    print(f"Events:  {Path(args.output_root) / 'repair_events_v1_5_0.jsonl'}")
    print(f"Parts:   {Path(args.output_root) / 'replacement_mentions_v1_5_0.jsonl'}")
    print(f"RMA:     {Path(args.output_root) / 'rma_refs_v1_5_0.csv'}")
    print(f"PO:      {Path(args.output_root) / 'customer_po_refs_v1_5_0.csv'}")
    print(f"Orders:  {Path(args.output_root) / 'procurement_refs_v1_5_0.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
