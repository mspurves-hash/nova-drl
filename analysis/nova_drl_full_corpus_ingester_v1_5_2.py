#!/usr/bin/env python3
"""
Nova DRL Full Corpus Ingester v1.5.2

Production-scale continuation of the proven v1.4.6/v1.4.7 pipeline.

Purpose
-------
Freeze and ingest the full current DRL tech-scan repair-folder universe using the
persistent local DRL file index. Preserve the standing 80/20 rule, reuse the frozen
10% benchmark when the exact event/source set matches, and capture tracking/procurement
metadata in the SAME new-source vision pass so the remaining 90% does not require a
second metadata-only reread.

Key policy
----------
- 80/20 is FIXED DEFAULT until Matt explicitly changes it.
- Travelers/Line Cards are primarily repair-history + parts-used evidence.
- Perfect OCR is NOT the target.
- RMA and procurement/order references are literal tracking fields: no cross-event
  inference and no recurrence-based substitution.
- DGK = Digi-Key order reference; MSR = Mouser order reference. NWK/DSK are procurement
  refs with supplier unknown unless visible. These are NOT manufacturer PNs.
- Customer PO is stored separately from RMA and procurement references.
- Roger-only paired-card rule remains: (2) typed primary, (1) support when both exist.
- Unrelated equipment families remain separate.
- Original DRL share is never modified. Qdrant writes remain OFF.

Reuse
-----
If the frozen v1.4.7 enriched 10% corpus is present and an event's current primary and
supporting source-path sets exactly match, v1.5.2 reuses that event instead of re-reading
it. Reused tracking rows are revalidated literally so unsupported historical order/RMA
associations do not propagate.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import re
import sys
import time
import urllib.error
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.5.2"
SCRIPT_DIR = Path(__file__).resolve().parent
BASE_SCRIPT = SCRIPT_DIR / "nova_drl_10pct_corpus_ingester_v1_4_6.py"

DEFAULT_INDEX_DB = Path("/opt/nova-drl/index/drl_file_index.sqlite")
DEFAULT_SHARE_ROOT = Path("/mnt/drl")
DEFAULT_TECH_BASE = "000 folder for tech scans"
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/drl_full_corpus_v1_5_2")
DEFAULT_CORPUS_MANIFEST = Path("/opt/nova-drl/corpus/drl_full_corpus_v1_5_1/full_corpus_manifest_v1_5_1.json")
DEFAULT_CORPUS_SEED = "nova-drl-full-corpus-v1.5.1"
DEFAULT_VISION_MODEL = "qwen3-vl-drl:8b-q8-16k"
DEFAULT_REASON_MODEL = "qwen25-drl:14b-q6-16k"
DEFAULT_TYPED_PAIR_ENGINEER = "ROGER"
DEFAULT_REUSE_EVENT_FILE = Path("/opt/nova-drl/output/drl_10pct_tracking_enrichment_v1_4_7/repair_events_enriched_v1_4_7.jsonl")
DEFAULT_REUSE_PART_FILE = Path("/opt/nova-drl/output/drl_10pct_tracking_enrichment_v1_4_7/replacement_mentions_enriched_v1_4_7.jsonl")

KNOWN_ORDER_PREFIX_SUPPLIER = {
    "DGK": "Digi-Key",
    "MSR": "Mouser",
    "NWK": None,
    "DSK": None,
}
ORDER_PREFIX_RE = re.compile(r"\b(DGK|MSR|NWK|DSK)[\s._-]*([A-Za-z0-9-]{3,})\b", re.I)
RMA_LABEL_RE = re.compile(r"\bRMA\s*(?:#|NO\.?|NUMBER)?\s*[:#-]?\s*([A-Za-z0-9-]+)", re.I)
CUSTOMER_PO_LABEL_RE = re.compile(r"\b(?:CUST(?:OMER)?\s*)?PO\s*(?:#|NO\.?|NUMBER)?\s*[:#-]?\s*([A-Za-z0-9-]+)", re.I)

VISION_PROMPT = r"""Read this DRL Traveler / Line Card using the standing FIXED 80/20 rule.

This source is primarily REPAIR-HISTORY, PARTS-USAGE, and TRACKING evidence. Do NOT
attempt perfect OCR and do NOT transcribe the whole form. Capture the useful information
a veteran DRL technician would care about.

Return concise plain text only, using whichever headings actually have information:
TRACKING / ORDER METADATA:
BASIC REPORTED PROBLEM:
PARTS / ASSEMBLIES REPLACED OR USED:
OTHER REPAIR-HISTORY NOTES:
EXPLICIT TEST / OUTCOME NOTE:

Rules:
- Ground everything in what is visible on the source.
- Capture RMA when clearly labeled RMA/RMA#.
- Capture Customer PO separately when clearly labeled Cust PO / Customer PO / PO.
- Capture procurement/order references from the ordered-parts area. Historical DRL
  examples include DGK... (Digi-Key), MSR... (Mouser), NWK..., DSK....
- Procurement/order references are NOT manufacturer PNs.
- If a procurement line visibly includes a true manufacturer PN, preserve that PN too.
- Prioritize exact part numbers, assembly names, quantities, axis/component names,
  and replacement/rebuild wording when reasonably legible.
- Preserve a likely PN as written; do not spend effort guessing one uncertain character.
- Do not infer troubleshooting procedures or test methods that are not written.
- If the card contains almost no technical information, say so briefly rather than inventing content.
"""

EVENT_PROMPT = r"""Convert ONE DRL repair event into a concise structured Traveler-history record.
Evidence comes from one or more PRIMARY Line Cards/Travelers for the SAME event.
Return JSON only with this exact top-level shape:
{
  "basic_reported_problem": [{"text":"...","evidence_quote":"..."}],
  "parts_replaced": [{"text":"...","part_number":null,"quantity":null,"evidence_quote":"..."}],
  "repair_history_notes": [{"text":"...","evidence_quote":"..."}],
  "explicit_test_outcome": [{"text":"...","evidence_quote":"..."}],
  "rma_numbers": [{"value":"...","evidence_quote":"..."}],
  "customer_po_numbers": [{"value":"...","evidence_quote":"..."}],
  "procurement_refs": [{"order_ref":"...","supplier":null,"description":null,"manufacturer_pn":null,"quantity":null,"evidence_quote":"..."}]
}

Standing 80/20 rules:
- Travelers are primarily parts/repair-history evidence. Do not manufacture detailed
  diagnostics, procedures, calibration, or testing when absent.
- Capture actual replaced/installed/used/rebuilt components and assemblies.
- part_number is populated only when a manufacturer/component PN/string is actually
  present in the evidence.
- DGK/MSR/NWK/DSK order references are procurement references, NOT manufacturer PNs.
- RMA, Customer PO, and procurement/order refs are literal tracking fields: the value
  MUST be visibly supported by its own evidence_quote. Never borrow a tracking value
  from another line/event or normalize one order number into a different order number.
- DGK-style refs are Digi-Key and MSR-style refs are Mouser unless the evidence states
  otherwise. Do not guess supplier for NWK/DSK unless visible.
- quantity is an integer only when explicitly stated; otherwise null.
- evidence_quote must be copied from supplied evidence.
- Do not convert administrative/shop routing into technical repair facts.
- If a category has no useful evidence, return an empty list.
"""

TECH_CATEGORIES = ("basic_reported_problem", "parts_replaced", "repair_history_notes", "explicit_test_outcome")


def load_base():
    if not BASE_SCRIPT.exists():
        raise RuntimeError(f"Required proven base ingester not found: {BASE_SCRIPT}")
    spec = importlib.util.spec_from_file_location("nova_drl_v146_base", BASE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load base ingester: {BASE_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Patch proven base helpers to v1.5.2 policy/cache identity.
    mod.VERSION = VERSION
    mod.VISION_PROMPT = VISION_PROMPT
    return mod


base = load_base()


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not path.exists():
        return out
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


def norm_alnum(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def normalize_order_ref(value: Any) -> str:
    return norm_alnum(value)


def normalize_tracking_value(value: Any) -> str:
    return norm_alnum(value)


def infer_supplier(order_ref: Any, supplier: Any = None) -> Optional[str]:
    s = base.normalized_ws(supplier)
    if s:
        cf = s.casefold()
        if "digi" in cf:
            return "Digi-Key"
        if "mouser" in cf:
            return "Mouser"
        return s
    m = re.match(r"\s*(DGK|MSR|NWK|DSK)", str(order_ref or ""), re.I)
    return KNOWN_ORDER_PREFIX_SUPPLIER.get(m.group(1).upper()) if m else None


def literal_supported(value: Any, quote: Any) -> bool:
    nv = norm_alnum(value)
    nq = norm_alnum(quote)
    return bool(nv and nq and nv in nq)


def recover_order_from_quote(quote: str, supplier: Optional[str] = None) -> Optional[str]:
    m = ORDER_PREFIX_RE.search(quote or "")
    if m:
        return f"{m.group(1).upper()}{m.group(2)}"
    # If source clearly says Digi-Key/Mouser and then a visible number, preserve the
    # literal visible number rather than inventing a DGK/MSR-prefixed value.
    if supplier:
        cf = supplier.casefold()
        if "digi" in cf or "mouser" in cf:
            m2 = re.search(r"\b(?:digi\s*-?\s*key|digikey|mouser)\b[^A-Za-z0-9]{0,12}([A-Za-z0-9-]{4,})", quote or "", re.I)
            if m2:
                return m2.group(1)
    return None


def extract_order_from_part_row(row: Dict[str, Any]) -> Optional[Tuple[str, Optional[str]]]:
    blob = " ".join(str(row.get(k) or "") for k in ("text", "part_number", "evidence_quote"))
    m = ORDER_PREFIX_RE.search(blob)
    if not m:
        return None
    ref = f"{m.group(1).upper()}{m.group(2)}"
    return ref, KNOWN_ORDER_PREFIX_SUPPLIER.get(m.group(1).upper())


def _qty(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        v = int(value) if value is not None else None
        return v if v is None or (0 < v <= 10000) else None
    except Exception:
        return None


def validate_event_json(parsed: Any, evidence: Sequence[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {c: [] for c in TECH_CATEGORIES}
    out.update({"rma_numbers": [], "customer_po_numbers": [], "procurement_refs": []})
    if not isinstance(parsed, dict):
        return out

    # Technical categories use the proven v1.4.6 quote-bound approach.
    for cat in TECH_CATEGORIES:
        rows = parsed.get(cat)
        if not isinstance(rows, list):
            continue
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = base.normalized_ws(row.get("text"))
            quote = str(row.get("evidence_quote") or "").strip()
            if not text or not base.quote_bound(quote, evidence):
                continue
            key = (text.casefold(), quote.casefold())
            if key in seen:
                continue
            seen.add(key)
            item: Dict[str, Any] = {"text": text, "evidence_quote": quote}
            if cat == "parts_replaced":
                pn = base.normalized_ws(row.get("part_number")) or None
                item["part_number"] = pn
                item["quantity"] = _qty(row.get("quantity"))
                order_hit = extract_order_from_part_row(item)
                if order_hit:
                    ref, supplier = order_hit
                    out["procurement_refs"].append({
                        "order_ref": ref,
                        "supplier": supplier,
                        "description": text if norm_alnum(text) != norm_alnum(ref) else None,
                        "manufacturer_pn": None,
                        "quantity": item["quantity"],
                        "evidence_quote": quote,
                        "reclassified_from_parts": True,
                    })
                    continue
            out[cat].append(item)

    # Strict literal tracking fields.
    for key in ("rma_numbers", "customer_po_numbers"):
        rows = parsed.get(key)
        if not isinstance(rows, list):
            continue
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = base.normalized_ws(row.get("value"))
            quote = str(row.get("evidence_quote") or "").strip()
            if not value or not base.quote_bound(quote, evidence) or not literal_supported(value, quote):
                continue
            norm = normalize_tracking_value(value)
            if not norm or norm in seen:
                continue
            # Extra field-label guard: avoid obvious PO/RMA swaps.
            qcf = quote.casefold()
            if key == "rma_numbers" and "rma" not in qcf and not RMA_LABEL_RE.search(quote):
                continue
            if key == "customer_po_numbers" and "po" not in qcf and not CUSTOMER_PO_LABEL_RE.search(quote):
                continue
            seen.add(norm)
            out[key].append({"value": value, "normalized": norm, "evidence_quote": quote})

    rows = parsed.get("procurement_refs")
    if isinstance(rows, list):
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            ref = base.normalized_ws(row.get("order_ref"))
            quote = str(row.get("evidence_quote") or "").strip()
            supplier = infer_supplier(ref, row.get("supplier"))
            if not quote or not base.quote_bound(quote, evidence):
                continue
            if not ref or not literal_supported(ref, quote):
                recovered = recover_order_from_quote(quote, supplier)
                if not recovered:
                    continue
                ref = recovered
            norm = normalize_order_ref(ref)
            if not norm:
                continue
            mpn = base.normalized_ws(row.get("manufacturer_pn")) or None
            if mpn and not literal_supported(mpn, quote):
                mpn = None
            desc = base.normalized_ws(row.get("description")) or None
            q = _qty(row.get("quantity"))
            dedupe = (norm, norm_alnum(mpn), norm_alnum(desc))
            if dedupe in seen:
                continue
            seen.add(dedupe)
            out["procurement_refs"].append({
                "order_ref": ref,
                "normalized": norm,
                "supplier": supplier,
                "description": desc,
                "manufacturer_pn": mpn,
                "quantity": q,
                "evidence_quote": quote,
            })

    # Final de-dupe for procurement rows, including rows reclassified from parts.
    cleaned = []
    seen = set()
    for row in out["procurement_refs"]:
        norm = normalize_order_ref(row.get("order_ref"))
        quote = str(row.get("evidence_quote") or "")
        if not norm or not literal_supported(row.get("order_ref"), quote):
            continue
        k = (norm, norm_alnum(row.get("manufacturer_pn")), norm_alnum(row.get("description")))
        if k in seen:
            continue
        seen.add(k)
        row["normalized"] = norm
        row["supplier"] = infer_supplier(row.get("order_ref"), row.get("supplier"))
        cleaned.append(row)
    out["procurement_refs"] = cleaned
    return out


# Make base helpers use our validation when relevant.
base.validate_event_json = validate_event_json


def current_event_path_sets(event: Dict[str, Any]) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    p = tuple(sorted(str(d.get("absolute_path")) for d in event.get("primary_documents", [])))
    s = tuple(sorted(str(d.get("absolute_path")) for d in event.get("supporting_documents", [])))
    return p, s


def strictify_reused_tracking(meta: Dict[str, Any]) -> Dict[str, Any]:
    out = {"rma_numbers": [], "customer_po_numbers": [], "procurement_refs": []}
    seen = set()
    for r in meta.get("rma_numbers") or []:
        value = base.normalized_ws(r.get("value"))
        quote = str(r.get("evidence_quote") or "")
        if value and literal_supported(value, quote) and "rma" in quote.casefold():
            n = normalize_tracking_value(value)
            if n not in seen:
                seen.add(n); out["rma_numbers"].append({"value": value, "normalized": n, "evidence_quote": quote, "source_path": r.get("source_path")})
    seen = set()
    seen_po = set()
    for o in meta.get("procurement_refs") or []:
        ref = base.normalized_ws(o.get("order_ref"))
        quote = str(o.get("evidence_quote") or "")
        # v1.4.7 did not yet have a Customer PO field. Recover clearly labeled
        # customer POs from those old procurement rows instead of carrying them as orders.
        if ref and literal_supported(ref, quote) and CUSTOMER_PO_LABEL_RE.search(quote):
            npo = normalize_tracking_value(ref)
            if npo and npo not in seen_po:
                seen_po.add(npo)
                out["customer_po_numbers"].append({"value": ref, "normalized": npo, "evidence_quote": quote, "source_path": o.get("source_path")})
            continue
        supplier = infer_supplier(ref, o.get("supplier"))
        if not ref or not literal_supported(ref, quote):
            recovered = recover_order_from_quote(quote, supplier)
            if not recovered:
                continue
            ref = recovered
        n = normalize_order_ref(ref)
        if not n or n in seen:
            continue
        seen.add(n)
        mpn = base.normalized_ws(o.get("manufacturer_pn")) or None
        if mpn and not literal_supported(mpn, quote):
            mpn = None
        out["procurement_refs"].append({
            "order_ref": ref, "normalized": n, "supplier": supplier,
            "description": base.normalized_ws(o.get("description")) or None,
            "manufacturer_pn": mpn, "quantity": _qty(o.get("quantity")),
            "evidence_quote": quote, "source_path": o.get("source_path"),
        })
    return out


def load_reuse(args: argparse.Namespace, events: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    if args.no_reuse_benchmark:
        return {}, {}
    event_file = Path(args.reuse_event_file)
    part_file = Path(args.reuse_part_file)
    if not event_file.exists():
        return {}, {}
    old_events = {str(r.get("repair_event_id")): r for r in read_jsonl(event_file)}
    old_parts: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in read_jsonl(part_file):
        old_parts[str(p.get("repair_event_id"))].append(p)
    matched: Dict[str, Dict[str, Any]] = {}
    parts: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        eid = str(event.get("repair_event_id"))
        old = old_events.get(eid)
        if not old:
            continue
        cur_p, cur_s = current_event_path_sets(event)
        old_p = tuple(sorted(str(x) for x in old.get("primary_source_paths") or []))
        old_s = tuple(sorted(str(x) for x in old.get("supporting_source_paths") or []))
        if cur_p != old_p or cur_s != old_s:
            continue
        row = json.loads(json.dumps(old))
        facts = row.setdefault("facts", {})
        # Replace reused parts with the v1.4.7 enriched manufacturer-PN view.
        pr = []
        for p in old_parts.get(eid, []):
            mpn = base.normalized_ws(p.get("manufacturer_part_number") or p.get("part_number")) or None
            if not mpn:
                continue
            pr.append({
                "text": base.normalized_ws(p.get("text")),
                "part_number": mpn,
                "quantity": _qty(p.get("quantity")),
                "evidence_quote": str(p.get("evidence_quote") or ""),
            })
        facts["parts_replaced"] = pr
        tracking = strictify_reused_tracking(row.get("tracking_metadata_v1_4_7") or {})
        row["tracking"] = {"rma_numbers": tracking["rma_numbers"], "procurement_refs": tracking["procurement_refs"]}
        row["customer_po_numbers"] = tracking.get("customer_po_numbers", [])
        row["ingest_source"] = "reused_frozen_v1.4.7_10pct"
        matched[eid] = row
        parts[eid] = old_parts.get(eid, [])
    return matched, parts



def _http_error_detail(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
        except Exception:
            body = ""
        return f"HTTP {exc.code} {exc.reason}" + (f" | {body[:1000]}" if body else "")
    return f"{type(exc).__name__}: {exc}"


def normalize_image_for_vision(src: Path, dst: Path, *, max_side: int = 4096) -> Dict[str, Any]:
    """Re-encode an image into conservative RGB baseline JPEG for Ollama vision.

    This is a compatibility retry only. It does not replace or alter the DRL source.
    EXIF orientation is applied, metadata is stripped, color mode is normalized to RGB,
    and very large images are reduced only when a side exceeds max_side.
    """
    try:
        from PIL import Image, ImageOps
    except Exception as exc:
        raise RuntimeError(f"Pillow required for normalized vision retry: {exc}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        original = {"format": im.format, "mode": im.mode, "size": list(im.size)}
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            if im.mode in ("RGBA", "LA"):
                rgba = im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, "white")
                bg.paste(rgba, mask=rgba.getchannel("A"))
                im = bg
            else:
                im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > max_side:
            scale = max_side / float(max(w, h))
            im = im.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
        im.save(dst, format="JPEG", quality=92, optimize=False, progressive=False, subsampling=0)
        normalized = {"format": "JPEG", "mode": "RGB", "size": list(im.size)}
    return {"original": original, "normalized": normalized, "normalized_path": str(dst)}


def acquire_evidence_resilient(args: argparse.Namespace, records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """80/20 full-corpus vision acquisition.

    Try the original source first. On any vision/API rejection, re-encode to a standard
    RGB JPEG and retry once. If that also fails, preserve an exception record and keep
    the full corpus moving instead of aborting thousands of good Travelers.
    """
    root = Path(args.output_root) / "vision_evidence"
    normalized_root = Path(args.output_root) / "vision_normalized_retry"
    exception_root = Path(args.output_root) / "vision_exceptions"
    vinfo = base.model_info(args.vision_model)
    out: List[Dict[str, Any]] = []
    exceptions: List[Dict[str, Any]] = []
    total = len(records)

    for i, src in enumerate(records, 1):
        d = root / f"record_{i:05d}_{src['source_record_id'][-8:]}"
        d.mkdir(parents=True, exist_ok=True)
        txt, meta = d / "traveler_evidence.txt", d / "record.json"
        cache_ok = False
        if txt.exists() and meta.exists() and not args.force_vision:
            try:
                m = base.load_json(meta)
                cache_ok = (
                    m.get("status", "ok") == "ok"
                    and m.get("source_image_sha256") == src["source_image_sha256"]
                    and m.get("vision_model_digest") == vinfo.get("digest")
                    and m.get("prompt_sha256") == base.sha256_text(VISION_PROMPT)
                )
            except Exception:
                cache_ok = False

        vision_status = "ok"
        normalized_retry = False
        if cache_ok:
            evidence = txt.read_text(encoding="utf-8", errors="ignore")
            action = "cache"
        else:
            source_path = Path(src["source_image"])
            t0 = time.time()
            attempts: List[Dict[str, Any]] = []
            evidence = ""
            try:
                evidence = base.call_ollama(
                    args.vision_model, VISION_PROMPT,
                    image_paths=[source_path], num_ctx=args.vision_num_ctx,
                    num_predict=args.vision_num_predict, timeout=args.timeout,
                )
                attempts.append({"input": "original", "ok": True})
                action = "model_run"
            except Exception as exc1:
                attempts.append({"input": "original", "ok": False, "error": _http_error_detail(exc1)})
                normalized_path = normalized_root / f"record_{i:05d}_{src['source_record_id'][-8:]}.jpg"
                try:
                    norm_meta = normalize_image_for_vision(source_path, normalized_path)
                    normalized_retry = True
                    evidence = base.call_ollama(
                        args.vision_model, VISION_PROMPT,
                        image_paths=[normalized_path], num_ctx=args.vision_num_ctx,
                        num_predict=args.vision_num_predict, timeout=args.timeout,
                    )
                    attempts.append({"input": "normalized_rgb_jpeg", "ok": True, **norm_meta})
                    action = "normalized_retry"
                except Exception as exc2:
                    attempts.append({"input": "normalized_rgb_jpeg", "ok": False, "error": _http_error_detail(exc2)})
                    vision_status = "exception"
                    action = "exception_continued"
                    exception = {
                        "version": VERSION,
                        "source_record_id": src.get("source_record_id"),
                        "repair_event_id": src.get("repair_event_id"),
                        "equipment_family": src.get("equipment_family"),
                        "source_path": src.get("source_path"),
                        "source_image": src.get("source_image"),
                        "source_image_sha256": src.get("source_image_sha256"),
                        "attempts": attempts,
                        "policy": "preserve exception and continue under fixed 80/20 rule",
                    }
                    exceptions.append(exception)
                    exception_root.mkdir(parents=True, exist_ok=True)
                    base.save_json(exception_root / f"record_{i:05d}_{src['source_record_id'][-8:]}.json", exception)

            txt.write_text(evidence, encoding="utf-8")
            base.save_json(meta, {
                "version": VERSION,
                "status": vision_status,
                "source_record_id": src["source_record_id"],
                "repair_event_id": src["repair_event_id"],
                "source_path": src["source_path"],
                "source_image_sha256": src["source_image_sha256"],
                "vision_model_digest": vinfo.get("digest"),
                "prompt_sha256": base.sha256_text(VISION_PROMPT),
                "elapsed_seconds": round(time.time() - t0, 3),
                "normalized_retry": normalized_retry,
                "attempts": attempts,
                "accepted_facts": 0,
                "qdrant_entries": 0,
            })

        row = dict(src)
        row.update({
            "traveler_evidence_path": str(txt),
            "traveler_evidence_sha256": base.sha256_text(evidence),
            "traveler_evidence_chars": len(evidence),
            "vision_status": vision_status,
            "vision_normalized_retry": normalized_retry,
        })
        out.append(row)
        print(f"[vision {i}/{total}] event={src['repair_event_id']} family={src['equipment_family'][:42]} chars={len(evidence)} | {action}")

    write_jsonl(Path(args.output_root) / "vision_exceptions_v1_5_2.jsonl", exceptions)
    base.save_json(Path(args.output_root) / "vision_exception_summary_v1_5_2.json", {
        "version": VERSION,
        "vision_record_count": len(out),
        "normalized_retry_success_count": sum(1 for r in out if r.get("vision_status") == "ok" and r.get("vision_normalized_retry")),
        "vision_exception_count": len(exceptions),
        "accepted_facts": 0,
        "qdrant_entries": 0,
    })
    return out


def extract_new_events(args: argparse.Namespace, events: Sequence[Dict[str, Any]], vision_records: Sequence[Dict[str, Any]], reuse: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_event: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in vision_records:
        by_event[r["repair_event_id"]].append(r)
    rinfo = base.model_info(args.reason_model)
    out: List[Dict[str, Any]] = []
    root = Path(args.output_root) / "event_extraction"
    model_total = sum(1 for e in events if e["repair_event_id"] not in reuse)
    model_i = 0
    for i, event in enumerate(events, 1):
        eid = event["repair_event_id"]
        if eid in reuse:
            row = json.loads(json.dumps(reuse[eid]))
            out.append(row)
            print(f"[event {i}/{len(events)}] {eid} family={event['equipment_family'][:42]} | reuse-v1.4.7")
            continue
        model_i += 1
        recs = by_event.get(eid, [])
        blocks = [Path(r["traveler_evidence_path"]).read_text(encoding="utf-8", errors="ignore") for r in recs]
        manifest = [{"record": r["source_record_id"], "sha256": r["traveler_evidence_sha256"]} for r in recs]
        input_hash = base.stable_json_hash(manifest)
        d = root / f"event_{i:05d}_{base.safe_slug(eid)}"
        d.mkdir(parents=True, exist_ok=True)
        parsed_path, run_path = d / "parsed.json", d / "run.json"
        cache_ok = False
        if parsed_path.exists() and run_path.exists() and not args.force_extraction:
            try:
                run = base.load_json(run_path)
                cache_ok = run.get("evidence_manifest_sha256") == input_hash and run.get("reason_model_digest") == rinfo.get("digest") and run.get("prompt_sha256") == base.sha256_text(EVENT_PROMPT)
            except Exception:
                cache_ok = False
        if cache_ok:
            parsed = base.load_json(parsed_path); action = "cache"
        elif not any(b.strip() for b in blocks):
            parsed = {}
            attempts = [{"ok": False, "skipped": "no_usable_vision_evidence"}]
            action = "no_vision_evidence"
            base.save_json(parsed_path, parsed)
            base.save_json(run_path, {
                "version": VERSION, "repair_event_id": eid,
                "evidence_manifest_sha256": input_hash,
                "reason_model_digest": rinfo.get("digest"),
                "prompt_sha256": base.sha256_text(EVENT_PROMPT),
                "attempts": attempts, "accepted_facts": 0, "qdrant_entries": 0,
            })
        else:
            payload = "\n\n".join(f"EVIDENCE BLOCK {n}:\n{b}" for n, b in enumerate(blocks, 1))
            try:
                parsed, attempts = base.call_json(args.reason_model, EVENT_PROMPT + "\n\n" + payload, num_ctx=args.reason_num_ctx, num_predict=args.event_num_predict, timeout=args.timeout, retries=1)
            except Exception as exc:
                parsed, attempts = {}, [{"ok": False, "error": str(exc)}]
            base.save_json(parsed_path, parsed)
            base.save_json(run_path, {
                "version": VERSION, "repair_event_id": eid,
                "evidence_manifest_sha256": input_hash,
                "reason_model_digest": rinfo.get("digest"),
                "prompt_sha256": base.sha256_text(EVENT_PROMPT),
                "attempts": attempts, "accepted_facts": 0, "qdrant_entries": 0,
            })
            action = "model_run"
        facts = validate_event_json(parsed, blocks)
        tracking = {
            "rma_numbers": facts.pop("rma_numbers"),
            "procurement_refs": facts.pop("procurement_refs"),
        }
        customer_po = facts.pop("customer_po_numbers")
        row = {
            "repair_event_id": eid,
            "log_number": event.get("log_number"),
            "legacy_event_token": event.get("legacy_event_token"),
            "equipment_family": event.get("equipment_family"),
            "equipment_families": event.get("equipment_families"),
            "top_folders": event.get("top_folders"),
            "primary_source_paths": [d["absolute_path"] for d in event["primary_documents"]],
            "supporting_source_paths": [d["absolute_path"] for d in event["supporting_documents"]],
            "roger_pair_optimization_applied": bool(event.get("typed_pair_optimization_applied")),
            "facts": facts,
            "tracking": tracking,
            "customer_po_numbers": customer_po,
            "ingest_source": "v1.5.2_full_corpus_resilient",
        }
        out.append(row)
        count = sum(len(v) for v in facts.values())
        print(f"[event {i}/{len(events)}] {eid} family={event['equipment_family'][:42]} facts={count} parts={len(facts['parts_replaced'])} tracking={len(tracking['rma_numbers'])+len(tracking['procurement_refs'])} | {action}")
    write_jsonl(Path(args.output_root) / "repair_events_v1_5_2.jsonl", out)
    return out


def write_outputs(args: argparse.Namespace, corpus: Dict[str, Any], selection: Dict[str, Any], events: Sequence[Dict[str, Any]], event_rows: Sequence[Dict[str, Any]], vision_count: int, reused_events: int) -> None:
    out_root = Path(args.output_root)
    out_root.mkdir(parents=True, exist_ok=True)
    # Freeze/source manifests.
    base.save_json(out_root / "source_selection_v1_5_2.json", {
        "version": VERSION,
        "corpus_manifest": str(args.corpus_manifest),
        "folder_count_effective": selection["sample_folder_count_effective"],
        "selected_document_count": selection["selected_document_count"],
        "folder_exception_count": selection["folder_exception_count"],
        "folder_exceptions": selection["folder_exceptions"],
        "excluded_counts": selection["excluded_counts"],
        "selected_documents": selection["selected_documents"],
        "accepted_facts": 0, "qdrant_entries": 0,
    })
    base.save_json(out_root / "repair_event_plan_v1_5_2.json", {
        "version": VERSION,
        "repair_event_count": len(events),
        "roger_pair_optimized_events": sum(1 for e in events if e.get("typed_pair_optimization_applied")),
        "primary_documents": sum(len(e.get("primary_documents", [])) for e in events),
        "supporting_documents": sum(len(e.get("supporting_documents", [])) for e in events),
        "events": list(events), "accepted_facts": 0, "qdrant_entries": 0,
    })

    parts_rows: List[Dict[str, Any]] = []
    rma_rows: List[Dict[str, Any]] = []
    po_rows: List[Dict[str, Any]] = []
    order_rows: List[Dict[str, Any]] = []
    category_counts = Counter()
    for ev in event_rows:
        for cat in TECH_CATEGORIES:
            category_counts[cat] += len(ev.get("facts", {}).get(cat, []))
        for p in ev.get("facts", {}).get("parts_replaced", []):
            parts_rows.append({
                "repair_event_id": ev.get("repair_event_id"), "log_number": ev.get("log_number"),
                "equipment_family": ev.get("equipment_family"), "part_number": p.get("part_number"),
                "quantity": p.get("quantity"), "text": p.get("text"), "evidence_quote": p.get("evidence_quote"),
                "primary_source_paths": ev.get("primary_source_paths"),
            })
        for r in ev.get("tracking", {}).get("rma_numbers", []):
            rma_rows.append({"repair_event_id": ev.get("repair_event_id"), "log_number": ev.get("log_number"), "equipment_family": ev.get("equipment_family"), "rma_number": r.get("value"), "rma_normalized": normalize_tracking_value(r.get("value")), "evidence_quote": r.get("evidence_quote"), "source_path": r.get("source_path")})
        for po in ev.get("customer_po_numbers", []):
            po_rows.append({"repair_event_id": ev.get("repair_event_id"), "log_number": ev.get("log_number"), "equipment_family": ev.get("equipment_family"), "customer_po": po.get("value"), "customer_po_normalized": normalize_tracking_value(po.get("value")), "evidence_quote": po.get("evidence_quote")})
        for o in ev.get("tracking", {}).get("procurement_refs", []):
            order_rows.append({"repair_event_id": ev.get("repair_event_id"), "log_number": ev.get("log_number"), "equipment_family": ev.get("equipment_family"), "supplier": o.get("supplier"), "order_ref": o.get("order_ref"), "order_ref_normalized": normalize_order_ref(o.get("order_ref")), "description": o.get("description"), "manufacturer_pn": o.get("manufacturer_pn"), "quantity": o.get("quantity"), "evidence_quote": o.get("evidence_quote"), "source_path": o.get("source_path")})

    write_jsonl(out_root / "replacement_mentions_v1_5_2.jsonl", parts_rows)
    write_jsonl(out_root / "rma_refs_v1_5_2.jsonl", rma_rows)
    write_jsonl(out_root / "customer_po_refs_v1_5_2.jsonl", po_rows)
    write_jsonl(out_root / "procurement_refs_v1_5_2.jsonl", order_rows)

    def write_csv(name: str, rows: Sequence[Dict[str, Any]], cols: Sequence[str]) -> None:
        with (out_root / name).open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cols)); w.writeheader()
            for row in rows:
                w.writerow({c: row.get(c) for c in cols})
    write_csv("replacement_mentions_v1_5_2.csv", parts_rows, ["repair_event_id","log_number","equipment_family","part_number","quantity","text","evidence_quote"])
    write_csv("rma_lookup_v1_5_2.csv", rma_rows, ["repair_event_id","log_number","equipment_family","rma_number","evidence_quote","source_path"])
    write_csv("customer_po_lookup_v1_5_2.csv", po_rows, ["repair_event_id","log_number","equipment_family","customer_po","evidence_quote"])
    write_csv("procurement_refs_v1_5_2.csv", order_rows, ["repair_event_id","log_number","equipment_family","supplier","order_ref","description","manufacturer_pn","quantity","evidence_quote","source_path"])

    family_counts = Counter(e.get("equipment_family") for e in events)
    lines = [
        f"# Nova DRL Full Corpus Ingester v{VERSION}", "",
        "Operating mode: PRODUCTION FULL-CORPUS 80/20 INGESTION",
        f"Tech-scan folder universe at freeze: {corpus.get('all_top_level_folder_count')}",
        f"Frozen full-corpus folders: {corpus.get('sample_folder_count')}",
        f"Folders with no supported Line Card/Traveler detected: {selection.get('folder_exception_count')}",
        f"Selected Line Card/Traveler documents: {selection.get('selected_document_count')}",
        f"Distinct repair events: {len(events)}",
        f"Roger paired events optimized: {sum(1 for e in events if e.get('typed_pair_optimization_applied'))}",
        f"Vision records attempted this run: {vision_count}",
        f"Repair events reused from frozen 10% benchmark: {reused_events}",
        f"Structured event records: {len(event_rows)}",
        f"Events with replacement-part evidence: {sum(1 for e in event_rows if e.get('facts',{}).get('parts_replaced'))}",
        f"Extracted replacement mentions: {len(parts_rows)}",
        f"Events with RMA evidence: {len({r['repair_event_id'] for r in rma_rows})}",
        f"RMA evidence rows: {len(rma_rows)}",
        f"Events with Customer PO evidence: {len({r['repair_event_id'] for r in po_rows})}",
        f"Customer PO evidence rows: {len(po_rows)}",
        f"Events with procurement/order evidence: {len({r['repair_event_id'] for r in order_rows})}",
        f"Procurement/order evidence rows: {len(order_rows)}",
        "Accepted facts: 0", "Qdrant writes: OFF", "NAS discovery/rescan: 0 | persistent SQLite index only", "",
        "TOP EQUIPMENT FAMILIES — BY REPAIR EVENTS", "-----------------------------------------",
    ]
    for i, (fam, count) in enumerate(family_counts.most_common(30), 1):
        lines.append(f"{i:2d}. {fam} | repair events={count}")
    lines += ["", "STRUCTURED FACT COUNTS", "----------------------"]
    for cat in TECH_CATEGORIES:
        lines.append(f"{cat}: {category_counts[cat]}")
    lines += ["", "POLICY", "------",
              "80/20 rule: FIXED DEFAULT until Matt explicitly changes it",
              "Travelers/Line Cards: primary role is repair-history and parts-used evidence",
              "RMA/Customer PO/order refs: literal tracking fields; no recurrence-based substitution",
              "DGK=Digi-Key order ref; MSR=Mouser order ref; NWK/DSK=procurement refs unless supplier visible",
              "Procurement refs are excluded from manufacturer-PN replacement ranking",
              "Detailed fixes/testing: not inferred when absent; later Operations Checklists/manuals add depth",
              "Full-corpus membership: frozen at first production manifest; later growth handled incrementally",
              "Folder without detected Traveler: exception for review, NOT proof no Traveler exists",
              "Roger paired-card rule: ONLY Roger (2)=typed primary convention",
              "Original share modified: NO", "Perfect OCR required: NO", "Automatic human approval: NO", "Qdrant writes: OFF"]
    (out_root / "drl_full_corpus_summary_v1_5_2.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare(args: argparse.Namespace, *, persist: bool):
    by_folder, meta = base.load_tech_scan_rows(Path(args.index_db), args.tech_base)
    bound = meta.get("share_root") or meta.get("bound_share_root")
    if bound:
        try:
            if Path(bound).resolve() != Path(args.share_root).resolve():
                raise RuntimeError(f"DRL index is bound to {bound}, not requested share root {args.share_root}")
        except FileNotFoundError:
            raise RuntimeError(f"share root not resolvable: {args.share_root}")
    # Reuse the proven deterministic/frozen sample mechanism at 100%.
    corpus = base.get_or_create_sample(args, by_folder, meta, persist=persist)
    selection = base.select_sample_sources(args, by_folder, corpus)
    events = base.build_event_plan(selection["selected_documents"], args.typed_pair_engineer)
    return by_folder, meta, corpus, selection, events


def count_planned_vision(events: Sequence[Dict[str, Any]], reuse_ids: set[str], render_dpi: int) -> int:
    total = 0
    for e in events:
        if e["repair_event_id"] in reuse_ids:
            continue
        for d in e.get("primary_documents", []):
            if str(d.get("extension")) == ".pdf":
                try: total += base.pdf_page_count(Path(d["absolute_path"]))
                except Exception: total += 1
            else:
                total += 1
    return total


def status(args: argparse.Namespace) -> int:
    print(f"# Nova DRL Full Corpus Ingester Status v{VERSION}")
    print(f"DRL index:        {'FOUND' if Path(args.index_db).exists() else 'NOT FOUND'} | {args.index_db}")
    print(f"Share root:       {'FOUND' if Path(args.share_root).exists() else 'NOT FOUND'} | {args.share_root}")
    print(f"Tech scans base:  {args.tech_base}")
    print(f"Corpus manifest:  {'FROZEN' if Path(args.corpus_manifest).exists() else 'NOT FROZEN'} | {args.corpus_manifest}")
    try:
        _, _, corpus, selection, events = prepare(args, persist=False)
        reuse, _ = load_reuse(args, events)
        print(f"Repair folders in index: {corpus.get('all_top_level_folder_count')}")
        print(f"Full corpus folders:      {corpus.get('sample_folder_count')}")
        print(f"Selected source docs:     {selection.get('selected_document_count')}")
        print(f"Folder exceptions:        {selection.get('folder_exception_count')}")
        print(f"Repair events:            {len(events)}")
        print(f"Reusable 10% events:      {len(reuse)}")
        print(f"New events to ingest:     {len(events)-len(reuse)}")
        print(f"Roger optimized events:   {sum(1 for e in events if e.get('typed_pair_optimization_applied'))}")
        print(f"Primary docs:             {sum(len(e['primary_documents']) for e in events)}")
        print(f"Supporting docs:          {sum(len(e['supporting_documents']) for e in events)}")
    except Exception as exc:
        print(f"Corpus planning: ERROR | {exc}")
    vi, ri = base.model_info(args.vision_model), base.model_info(args.reason_model)
    print(f"Vision model:     {'FOUND' if vi.get('available') else 'MISSING'} | {args.vision_model}")
    print(f"Reason model:     {'FOUND' if ri.get('available') else 'MISSING'} | {args.reason_model}")
    print("NAS rescan:       OFF | persistent SQLite index only")
    print("80/20 rule:       FIXED DEFAULT")
    print("Accepted facts:   0")
    print("Qdrant:           OFF")
    return 0


def plan(args: argparse.Namespace) -> int:
    try:
        _, _, corpus, selection, events = prepare(args, persist=False)
        reuse, _ = load_reuse(args, events)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 2
    planned_vision = count_planned_vision(events, set(reuse), args.render_dpi)
    print(f"# Nova DRL Full Corpus Ingester v{VERSION} — PLAN ONLY")
    print(f"Tech-scan folder universe:       {corpus.get('all_top_level_folder_count')}")
    print("Corpus percent:                  100.0%")
    print(f"Frozen manifest exists:          {'YES (will reuse)' if Path(args.corpus_manifest).exists() and not args.force_corpus else 'NO (first real run will freeze)'}")
    print(f"Full-corpus folders:             {corpus.get('sample_folder_count')}")
    print(f"Selected Line Cards/Travelers:   {selection.get('selected_document_count')}")
    print(f"Folder exceptions:               {selection.get('folder_exception_count')} | retained for review")
    print(f"Selector exclusions:             {json.dumps(selection.get('excluded_counts'), sort_keys=True)}")
    print(f"Distinct repair events:          {len(events)}")
    print(f"Reusable frozen 10% events:      {len(reuse)}")
    print(f"New repair events to ingest:     {len(events)-len(reuse)}")
    print(f"Roger paired optimization:       {sum(1 for e in events if e.get('typed_pair_optimization_applied'))} events")
    print(f"Planned NEW vision records:      {planned_vision} (PDFs expand to pages)")
    print(f"Planned NEW 14B event calls:     {len(events)-len(reuse)} maximum before cache")
    print("Tracking in same pass:           RMA + Customer PO + procurement/order refs")
    print("DGK/MSR/NWK/DSK:                strict order refs, never manufacturer PNs")
    print("Corpus-wide clustering calls:    0 | ingest/structure first")
    print("Cross-model part ranking:        OFF | equipment identity preserved")
    print("NAS discovery/rescan:            0 | SQLite index only")
    print("Perfect OCR target:              NO | high-signal Traveler evidence")
    print("Vision failure policy:           original -> RGB-JPEG retry -> exception/continue")
    print("Accepted facts:                  0")
    print("Qdrant:                          OFF")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=f"Nova DRL Full Corpus Ingester v{VERSION}")
    ap.add_argument("--index-db", default=str(DEFAULT_INDEX_DB))
    ap.add_argument("--share-root", default=str(DEFAULT_SHARE_ROOT))
    ap.add_argument("--tech-base", default=DEFAULT_TECH_BASE)
    ap.add_argument("--sample-percent", type=float, default=100.0, help=argparse.SUPPRESS)
    ap.add_argument("--sample-seed", default=DEFAULT_CORPUS_SEED, help=argparse.SUPPRESS)
    ap.add_argument("--sample-manifest", dest="corpus_manifest", default=str(DEFAULT_CORPUS_MANIFEST), help=argparse.SUPPRESS)
    ap.add_argument("--corpus-manifest", dest="corpus_manifest", default=str(DEFAULT_CORPUS_MANIFEST))
    ap.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    ap.add_argument("--typed-pair-engineer", default=DEFAULT_TYPED_PAIR_ENGINEER)
    ap.add_argument("--vision-model", default=DEFAULT_VISION_MODEL)
    ap.add_argument("--reason-model", default=DEFAULT_REASON_MODEL)
    ap.add_argument("--vision-num-ctx", type=int, default=16384)
    ap.add_argument("--vision-num-predict", type=int, default=2048)
    ap.add_argument("--reason-num-ctx", type=int, default=16384)
    ap.add_argument("--event-num-predict", type=int, default=3072)
    ap.add_argument("--render-dpi", type=int, default=300)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--limit-sampled-folders", type=int, default=None, help="Smoke-test limit after frozen full-corpus order")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--manifest-only", action="store_true")
    ap.add_argument("--vision-only", action="store_true")
    ap.add_argument("--force-sample", dest="force_corpus", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--force-corpus", action="store_true", help="INTENTIONAL: regenerate frozen full-corpus membership from current index")
    ap.add_argument("--force-vision", action="store_true")
    ap.add_argument("--force-extraction", action="store_true")
    ap.add_argument("--no-reuse-benchmark", action="store_true")
    ap.add_argument("--reuse-event-file", default=str(DEFAULT_REUSE_EVENT_FILE))
    ap.add_argument("--reuse-part-file", default=str(DEFAULT_REUSE_PART_FILE))
    args = ap.parse_args()
    # Compatibility names used by proven base helpers.
    args.sample_manifest = args.corpus_manifest
    args.sample_percent = 100.0
    args.sample_seed = DEFAULT_CORPUS_SEED
    args.force_sample = args.force_corpus

    if args.status: return status(args)
    if args.plan_only: return plan(args)

    try:
        _, _, corpus, selection, events = prepare(args, persist=True)
        reuse, _ = load_reuse(args, events)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 2

    print(f"# Nova DRL Full Corpus Ingester v{VERSION}")
    print("Operating mode: PRODUCTION FULL-CORPUS 80/20 INGESTION")
    print(f"Folder universe at freeze: {corpus.get('all_top_level_folder_count')}")
    print(f"Frozen full-corpus folders: {corpus.get('sample_folder_count')}")
    print(f"Selected Line Cards/Travelers: {selection.get('selected_document_count')}")
    print(f"Folder exceptions: {selection.get('folder_exception_count')}")
    print(f"Repair events: {len(events)}")
    print(f"Reusable frozen 10% events: {len(reuse)}")
    print(f"New events to ingest: {len(events)-len(reuse)}")
    print("NAS rescan: OFF")
    print("Accepted facts: 0")
    print("Qdrant: OFF")

    # Save plan before expensive work.
    base.save_json(Path(args.output_root) / "full_corpus_manifest_snapshot_v1_5_2.json", corpus)
    base.save_json(Path(args.output_root) / "source_selection_v1_5_2.json", selection)
    base.save_json(Path(args.output_root) / "repair_event_plan_v1_5_2.json", {"version": VERSION, "events": events})
    if args.manifest_only:
        print("# MANIFEST COMPLETE"); return 0

    reuse_ids = set(reuse)
    new_events = [e for e in events if e["repair_event_id"] not in reuse_ids]
    source_records = base.expand_primary_sources(new_events, Path(args.output_root), args.render_dpi)
    print(f"NEW primary vision records after PDF expansion: {len(source_records)}")
    vision_rows = acquire_evidence_resilient(args, source_records)
    base.save_json(Path(args.output_root) / "vision_source_manifest_v1_5_2.json", {"version": VERSION, "records": vision_rows})
    if args.vision_only:
        print("# VISION-ONLY COMPLETE"); return 0

    event_rows = extract_new_events(args, events, vision_rows, reuse)
    write_outputs(args, corpus, selection, events, event_rows, len(vision_rows), len(reuse))
    print("\n# COMPLETE")
    print(f"Frozen full-corpus folders:      {corpus.get('sample_folder_count')}")
    print(f"Folder exceptions:               {selection.get('folder_exception_count')}")
    print(f"Selected source documents:       {selection.get('selected_document_count')}")
    print(f"Repair events:                   {len(events)}")
    print(f"Reused frozen 10% events:        {len(reuse)}")
    print(f"NEW vision records:              {len(vision_rows)}")
    print(f"Structured event records:        {len(event_rows)}")
    print(f"Vision normalized retries:       {sum(1 for r in vision_rows if r.get('vision_normalized_retry') and r.get('vision_status') == 'ok')}")
    print(f"Vision exceptions continued:     {sum(1 for r in vision_rows if r.get('vision_status') != 'ok')}")
    print("Accepted facts:                  0")
    print("Qdrant:                          OFF")
    print(f"Summary: {Path(args.output_root) / 'drl_full_corpus_summary_v1_5_2.txt'}")
    print(f"Events:  {Path(args.output_root) / 'repair_events_v1_5_2.jsonl'}")
    print(f"Parts:   {Path(args.output_root) / 'replacement_mentions_v1_5_2.jsonl'}")
    print(f"RMA:     {Path(args.output_root) / 'rma_lookup_v1_5_2.csv'}")
    print(f"Orders:  {Path(args.output_root) / 'procurement_refs_v1_5_2.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
