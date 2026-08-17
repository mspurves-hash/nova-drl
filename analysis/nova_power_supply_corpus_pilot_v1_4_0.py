#!/usr/bin/env python3
"""
Nova DRL Power Supply Corpus Pilot v1.4.0

Blind-validation branch for PS-RCL1A-1D-W3 repair history.

Design:
- Never modifies the source PDF or image directory.
- Does not contain or read the prior hosted benchmark counts/answers.
- Qwen3-VL 8B performs page-level literal acquisition.
- Python proposes near-duplicate page pairs; Qwen2.5 14B adjudicates only those pairs.
- Qwen2.5 14B extracts replacement-part events from raw page transcription.
- Qwen2.5 14B groups obvious part aliases/families; Python owns all counting.
- Explicit quantities are summed; unspecified quantities remain unspecified.
- No Qdrant writes and no automatic approval.
- Every stage is resumable through file caches.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.4.0"
DEFAULT_SOURCE_PDF = Path("/opt/nova-drl/input/RCL1A-1D-W3 All Line Cards.pdf")
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/power_supply_corpus_pilot_v1_4_0")
DEFAULT_VISION_MODEL = "qwen3-vl-drl:8b-q8-16k"
DEFAULT_REASON_MODEL = "qwen25-drl:14b-q6-16k"
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

# Intentionally generic. Known benchmark part numbers/counts are not embedded here.
TRANSCRIPTION_PROMPT = """Transcribe this complete DRL power-supply repair record page as faithfully as possible.

RULES:
- Read the entire visible page, including printed, typed, stamped, and handwritten text.
- Return transcription only. Do not summarize, interpret, normalize, classify, or answer questions.
- Preserve part numbers, quantities, fuse ratings, component values, abbreviations, punctuation, and unusual spelling as actually read.
- Do not invent missing characters, quantities, or part numbers.
- Printed stock/reference text is still transcribed literally; do not decide whether it was used in this repair.
- If text cannot be read reliably, write [unclear].
- Do not repeat text unless it is actually repeated on the page.
"""

DUPLICATE_PROMPT = """You are adjudicating whether two transcribed scanned pages are duplicate scans of the SAME underlying DRL repair record.
Return JSON only:
{"duplicate": true|false, "confidence": "high|medium|low", "reason": "short reason"}

Rules:
- Duplicate means the same repair event/page scanned more than once, allowing OCR/transcription differences.
- Do NOT call pages duplicates merely because they share the same printed template, standard rebuild parts, or common wording.
- Compare event-specific handwritten repairs, quantities, dates, serial/log fields, notes, and unusual wording.
- If uncertain, return duplicate=false.
- Use only the two supplied transcriptions.
"""

EXTRACTION_PROMPT = """You are extracting replacement-part usage from ONE DRL power-supply repair record transcription.
Return JSON only with exactly this shape:
{
  "record_class": "repair_record|reference_or_stock_list|other_or_unclear",
  "replacements": [
    {
      "raw_quote": "exact text copied from the transcription",
      "part_number": "exact part number if explicitly present, otherwise null",
      "description": "short component/assembly description grounded in the quote",
      "quantity": 1,
      "quantity_text": "exact quantity wording if present, otherwise null",
      "action": "replaced|installed|used|rebuilt_assembly|other",
      "uncertain": false
    }
  ]
}

Rules:
- Extract ONLY components/assemblies actually replaced, installed, used, or explicitly rebuilt on this repair.
- Do not count a part merely because it appears in a printed stock list, standard kit, reference table, diagnostic note, or test instruction.
- Do not count actions such as cleaning, soldering, trace repair, inspection, adjustment, or testing as parts.
- raw_quote must be copied from the supplied transcription; do not paraphrase it.
- quantity must be an integer only when the source explicitly states or unmistakably enumerates it. Otherwise use null.
- Never convert words such as several, many, assorted, or unspecified into a numeric quantity.
- Preserve exact part-number characters. Do not silently correct OCR-sensitive part numbers.
- A complete board/assembly replacement may be included as an assembly if the repair text explicitly says it was replaced/installed/used.
- If there are no supported replacements, return an empty replacements array.
"""

NORMALIZE_PROMPT = """You are grouping extracted DRL power-supply replacement mentions into provisional SAME-PART or SAME-PART-FAMILY clusters.
Return JSON only:
{
  "clusters": [
    {
      "label": "short provisional family label",
      "member_descriptor_ids": ["d_...", "d_..."]
    }
  ]
}

Rules:
- Use only supplied descriptor IDs.
- Group obvious punctuation/case/spelling variants of the same part number.
- You may group equivalent wording for the same physical part when ratings/description strongly support it.
- Do NOT merge different voltage/current ratings, different component values, different board assemblies, or merely related parts.
- If uncertain, leave a descriptor ungrouped; Python will preserve it as a singleton.
- Do not use prior benchmark answers or outside stocking assumptions.
"""


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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_json_hash(value: Any) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def normalized_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def safe_slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return s[:100] or "unknown"


def model_info(model: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"requested_model": model, "available": False}
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        for item in data.get("models") or []:
            if item.get("name") == model or item.get("model") == model:
                out.update({
                    "available": True,
                    "resolved_name": item.get("name") or item.get("model"),
                    "digest": item.get("digest"),
                    "size_bytes": item.get("size"),
                    "details": item.get("details"),
                })
                break
    except Exception as exc:
        out["error"] = str(exc)
    return out


def call_ollama(model: str, prompt: str, *, image_path: Optional[Path], num_ctx: int, num_predict: int, timeout: int) -> str:
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "num_ctx": int(num_ctx), "num_predict": int(num_predict)},
    }
    if image_path is not None:
        payload["images"] = [base64.b64encode(image_path.read_bytes()).decode("ascii")]
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


def call_json_with_retry(model: str, prompt: str, *, num_ctx: int, num_predict: int, timeout: int, retries: int, cache_dir: Path) -> Tuple[Any, List[Dict[str, Any]]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    attempts: List[Dict[str, Any]] = []
    current = prompt
    for idx in range(1, retries + 2):
        t0 = time.time()
        raw = ""
        err: Optional[str] = None
        try:
            raw = call_ollama(model, current, image_path=None, num_ctx=num_ctx, num_predict=num_predict, timeout=timeout)
            (cache_dir / f"raw_attempt_{idx:02d}.txt").write_text(raw, encoding="utf-8")
            parsed = parse_json_response(raw)
            attempts.append({"attempt": idx, "elapsed_seconds": round(time.time() - t0, 3), "error": None})
            return parsed, attempts
        except Exception as exc:
            err = str(exc)
            if raw:
                (cache_dir / f"raw_attempt_{idx:02d}.txt").write_text(raw, encoding="utf-8")
            attempts.append({"attempt": idx, "elapsed_seconds": round(time.time() - t0, 3), "error": err})
            current = prompt + "\n\nYour previous response was invalid JSON. Return only valid JSON in the requested schema."
    raise RuntimeError(attempts[-1]["error"] if attempts else "model JSON call failed")


def require_executable(name: str) -> Optional[str]:
    return shutil.which(name)


def pdf_page_count(pdf: Path) -> int:
    exe = require_executable("pdfinfo")
    if not exe:
        raise RuntimeError("pdfinfo not found; install poppler-utils or use --source-images-root")
    p = subprocess.run([exe, str(pdf)], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "pdfinfo failed").strip())
    m = re.search(r"^Pages:\s+(\d+)\s*$", p.stdout, flags=re.M)
    if not m:
        raise RuntimeError("Could not parse PDF page count")
    return int(m.group(1))


def render_pdf_page(pdf: Path, page_num: int, dest: Path, dpi: int) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        return
    exe = require_executable("pdftoppm")
    if not exe:
        raise RuntimeError("pdftoppm not found; install poppler-utils or use --source-images-root")
    dest.parent.mkdir(parents=True, exist_ok=True)
    prefix = dest.with_suffix("")
    p = subprocess.run([
        exe, "-f", str(page_num), "-l", str(page_num), "-singlefile", "-jpeg", "-jpegopt", "quality=92", "-r", str(dpi), str(pdf), str(prefix)
    ], capture_output=True, text=True, timeout=180)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or f"pdftoppm failed page {page_num}").strip())
    produced = prefix.with_suffix(".jpg")
    if produced != dest:
        produced.replace(dest)


def discover_image_pages(root: Path) -> List[Path]:
    if not root.exists():
        raise FileNotFoundError(root)
    return [p for p in sorted(root.iterdir(), key=lambda x: x.name.lower()) if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]


def page_transcription_path(output_root: Path, page_num: int) -> Path:
    return output_root / "acquisition" / f"page_{page_num:04d}" / "transcription.txt"


def acquire_pages(args: argparse.Namespace, source_kind: str, pages: Sequence[Path] | int, source_sha: Optional[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    total = pages if isinstance(pages, int) else len(pages)
    for idx in range(1, int(total) + 1):
        page_dir = Path(args.output_root) / "acquisition" / f"page_{idx:04d}"
        page_dir.mkdir(parents=True, exist_ok=True)
        trans_path = page_dir / "transcription.txt"
        meta_path = page_dir / "page.json"
        if source_kind == "pdf":
            image = Path(args.output_root) / "rendered_pages" / f"page_{idx:04d}.jpg"
            render_pdf_page(Path(args.source_pdf), idx, image, int(args.render_dpi))
        else:
            image = pages[idx - 1]  # type: ignore[index]
        img_sha = sha256_file(image)
        cache_ok = False
        if trans_path.exists() and meta_path.exists() and not args.force_acquisition:
            try:
                meta = load_json(meta_path)
                cache_ok = (
                    meta.get("source_image_sha256") == img_sha
                    and meta.get("source_pdf_sha256") == source_sha
                    and meta.get("vision_model", {}).get("requested_model") == args.vision_model
                )
            except Exception:
                cache_ok = False
        if cache_ok:
            text = trans_path.read_text(encoding="utf-8")
            meta = load_json(meta_path)
            action = "cache"
        else:
            t0 = time.time()
            text = call_ollama(args.vision_model, TRANSCRIPTION_PROMPT, image_path=image, num_ctx=args.vision_num_ctx, num_predict=args.vision_num_predict, timeout=args.timeout)
            trans_path.write_text(text, encoding="utf-8")
            meta = {
                "version": VERSION,
                "page_number": idx,
                "source_kind": source_kind,
                "source_pdf": str(args.source_pdf) if source_kind == "pdf" else None,
                "source_pdf_sha256": source_sha,
                "source_image": str(image),
                "source_image_sha256": img_sha,
                "transcription_sha256": sha256_text(text),
                "vision_model": model_info(args.vision_model),
                "elapsed_seconds": round(time.time() - t0, 3),
                "accepted_facts": 0,
                "qdrant_entries": 0,
            }
            save_json(meta_path, meta)
            action = "model_run"
        row = {
            "page_number": idx,
            "source_image": str(image),
            "source_image_sha256": img_sha,
            "transcription_path": str(trans_path),
            "transcription_sha256": sha256_text(text),
            "character_count": len(text),
        }
        out.append(row)
        print(f"[acquire {idx}/{total}] chars={len(text)} | {action}")
    save_json(Path(args.output_root) / "page_manifest_v1_4_0.json", {"version": VERSION, "pages": out})
    return out


def duplicate_normalize(text: str) -> str:
    s = text.lower()
    s = re.sub(r"\[unclear\]", " ", s)
    s = re.sub(r"\bpage\s+\d+\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return normalized_ws(s)


def shingles(text: str, n: int = 3) -> set[Tuple[str, ...]]:
    toks = duplicate_normalize(text).split()
    if len(toks) < n:
        return {tuple(toks)} if toks else set()
    return {tuple(toks[i:i+n]) for i in range(len(toks) - n + 1)}


def jaccard(a: set[Any], b: set[Any]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def propose_duplicate_pairs(records: Sequence[Dict[str, Any]], *, threshold: float) -> List[Dict[str, Any]]:
    texts = {r["page_number"]: Path(r["transcription_path"]).read_text(encoding="utf-8") for r in records}
    sh = {k: shingles(v) for k, v in texts.items()}
    norm_hash: Dict[str, List[int]] = defaultdict(list)
    for p, t in texts.items():
        norm_hash[sha256_text(duplicate_normalize(t))].append(p)
    candidates: Dict[Tuple[int, int], Dict[str, Any]] = {}
    for group in norm_hash.values():
        if len(group) > 1:
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = sorted((group[i], group[j]))
                    candidates[(a, b)] = {"page_a": a, "page_b": b, "similarity": 1.0, "exact_normalized": True}
    nums = sorted(texts)
    for i, a in enumerate(nums):
        for b in nums[i+1:]:
            if (a, b) in candidates:
                continue
            ta, tb = duplicate_normalize(texts[a]), duplicate_normalize(texts[b])
            if not ta or not tb:
                continue
            lr = min(len(ta), len(tb)) / max(len(ta), len(tb))
            if lr < 0.55:
                continue
            sim = jaccard(sh[a], sh[b])
            if sim >= threshold:
                candidates[(a, b)] = {"page_a": a, "page_b": b, "similarity": round(sim, 4), "exact_normalized": False}
    return sorted(candidates.values(), key=lambda r: (-float(r["similarity"]), r["page_a"], r["page_b"]))


class UnionFind:
    def __init__(self, items: Iterable[int]):
        self.parent = {x: x for x in items}
    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x
    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def adjudicate_duplicates(args: argparse.Namespace, records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    output_root = Path(args.output_root)
    cache = output_root / "dedupe" / "duplicate_adjudication_v1_4_0.json"
    input_hash = stable_json_hash([(r["page_number"], r["transcription_sha256"]) for r in records])
    rinfo = model_info(args.reason_model)
    if cache.exists() and not args.force_dedupe:
        try:
            prior = load_json(cache)
            if (prior.get("input_transcription_manifest_sha256") == input_hash
                    and float(prior.get("candidate_threshold")) == float(args.duplicate_candidate_threshold)
                    and prior.get("reason_model_digest") == rinfo.get("digest")):
                return prior
        except Exception:
            pass
    by_page = {r["page_number"]: r for r in records}
    pairs = propose_duplicate_pairs(records, threshold=float(args.duplicate_candidate_threshold))
    decisions: List[Dict[str, Any]] = []
    uf = UnionFind(by_page)
    for idx, pair in enumerate(pairs, 1):
        a, b = int(pair["page_a"]), int(pair["page_b"])
        if pair.get("exact_normalized"):
            decision = {**pair, "duplicate": True, "confidence": "high", "reason": "identical normalized transcription", "action": "python_exact"}
        else:
            ta = Path(by_page[a]["transcription_path"]).read_text(encoding="utf-8")
            tb = Path(by_page[b]["transcription_path"]).read_text(encoding="utf-8")
            prompt = DUPLICATE_PROMPT + f"\n\nPAGE A ({a}):\n{ta[:7000]}\n\nPAGE B ({b}):\n{tb[:7000]}\n"
            cdir = output_root / "dedupe" / "pairs" / f"pair_{a:04d}_{b:04d}"
            try:
                parsed, attempts = call_json_with_retry(args.reason_model, prompt, num_ctx=args.reason_num_ctx, num_predict=768, timeout=args.timeout, retries=1, cache_dir=cdir)
                duplicate = bool(parsed.get("duplicate")) if isinstance(parsed, dict) else False
                decision = {**pair, "duplicate": duplicate, "confidence": parsed.get("confidence") if isinstance(parsed, dict) else "low", "reason": parsed.get("reason") if isinstance(parsed, dict) else "invalid", "attempts": attempts, "action": "model"}
            except Exception as exc:
                decision = {**pair, "duplicate": False, "confidence": "low", "reason": f"adjudication failed: {exc}", "action": "safe_unique_fallback"}
        if decision["duplicate"]:
            uf.union(a, b)
        decisions.append(decision)
        print(f"[dedupe {idx}/{len(pairs)}] pages {a}/{b} sim={pair['similarity']} duplicate={decision['duplicate']}")
    groups: Dict[int, List[int]] = defaultdict(list)
    for p in sorted(by_page):
        groups[uf.find(p)].append(p)
    duplicate_groups = [g for g in groups.values() if len(g) > 1]
    representatives = {p: min(groups[uf.find(p)]) for p in by_page}
    result = {
        "version": VERSION,
        "input_transcription_manifest_sha256": input_hash,
        "candidate_threshold": float(args.duplicate_candidate_threshold),
        "reason_model_digest": rinfo.get("digest"),
        "candidate_pair_count": len(pairs),
        "decisions": decisions,
        "duplicate_groups": duplicate_groups,
        "representative_by_page": {str(k): v for k, v in representatives.items()},
        "unique_representative_pages": sorted(set(representatives.values())),
        "source_page_count": len(records),
        "unique_page_count": len(set(representatives.values())),
        "duplicate_pages_excluded": len(records) - len(set(representatives.values())),
        "accepted_facts": 0,
        "qdrant_entries": 0,
    }
    save_json(cache, result)
    return result


def quote_bound(raw_quote: str, transcription: str) -> bool:
    q = normalized_ws(raw_quote)
    t = normalized_ws(transcription)
    if not q:
        return False
    if q in t:
        return True
    q2 = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9]+", " ", q)).strip().lower()
    t2 = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9]+", " ", t)).strip().lower()
    return bool(q2 and q2 in t2)


def validate_extraction(parsed: Any, page_num: int, transcription: str) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    record_class = "other_or_unclear"
    if isinstance(parsed, dict) and parsed.get("record_class") in {"repair_record", "reference_or_stock_list", "other_or_unclear"}:
        record_class = parsed["record_class"]
    rows = parsed.get("replacements") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        rows = []
    valid: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    seen: set[Tuple[str, Optional[int], str]] = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            rejected.append({"index": idx, "reason": "not_object"})
            continue
        raw = str(row.get("raw_quote") or "").strip()
        if not quote_bound(raw, transcription):
            rejected.append({"index": idx, "reason": "raw_quote_not_bound", "raw_quote": raw})
            continue
        qty = row.get("quantity")
        if isinstance(qty, bool):
            qty = None
        if qty is not None:
            try:
                qty = int(qty)
                if qty <= 0 or qty > 10000:
                    qty = None
            except Exception:
                qty = None
        pn = row.get("part_number")
        pn = str(pn).strip() if pn not in (None, "") else None
        desc = normalized_ws(row.get("description") or "")
        action = str(row.get("action") or "other")
        if action not in {"replaced", "installed", "used", "rebuilt_assembly", "other"}:
            action = "other"
        key = (normalized_ws(raw).lower(), qty, pn or "")
        if key in seen:
            continue
        seen.add(key)
        mention_id = "m_" + hashlib.sha256(f"{page_num}\n{idx}\n{raw}\n{pn}\n{qty}".encode("utf-8")).hexdigest()[:16]
        valid.append({
            "mention_id": mention_id,
            "page_number": page_num,
            "raw_quote": raw,
            "part_number": pn,
            "description": desc,
            "quantity": qty,
            "quantity_text": row.get("quantity_text"),
            "action": action,
            "uncertain": bool(row.get("uncertain")),
        })
    return record_class, valid, rejected


def extract_replacements(args: argparse.Namespace, records: Sequence[Dict[str, Any]], dedupe: Dict[str, Any]) -> Dict[str, Any]:
    output_root = Path(args.output_root)
    reps = set(int(x) for x in dedupe["unique_representative_pages"])
    all_mentions: List[Dict[str, Any]] = []
    page_rows: List[Dict[str, Any]] = []
    by_page = {r["page_number"]: r for r in records}
    for idx, page in enumerate(sorted(reps), 1):
        r = by_page[page]
        text = Path(r["transcription_path"]).read_text(encoding="utf-8")
        pdir = output_root / "extraction" / f"page_{page:04d}"
        parsed_path = pdir / "parsed.json"
        run_path = pdir / "run.json"
        rinfo = model_info(args.reason_model)
        cache_ok = False
        if parsed_path.exists() and run_path.exists() and not args.force_extraction:
            try:
                run = load_json(run_path)
                cache_ok = (
                    run.get("transcription_sha256") == r["transcription_sha256"]
                    and run.get("reason_model_digest") == rinfo.get("digest")
                    and run.get("prompt_sha256") == sha256_text(EXTRACTION_PROMPT)
                )
            except Exception:
                cache_ok = False
        if cache_ok:
            parsed = load_json(parsed_path)
            run = load_json(run_path)
            action = "cache"
        else:
            prompt = EXTRACTION_PROMPT + "\n\nTRANSCRIPTION:\n" + text
            try:
                parsed, attempts = call_json_with_retry(args.reason_model, prompt, num_ctx=args.reason_num_ctx, num_predict=args.extract_num_predict, timeout=args.timeout, retries=1, cache_dir=pdir)
                save_json(parsed_path, parsed)
                run = {"version": VERSION, "page_number": page, "model": rinfo, "reason_model_digest": rinfo.get("digest"), "transcription_sha256": r["transcription_sha256"], "attempts": attempts, "prompt_sha256": sha256_text(EXTRACTION_PROMPT), "accepted_facts": 0, "qdrant_entries": 0}
                save_json(run_path, run)
                action = "model_run"
            except Exception as exc:
                parsed = {"record_class": "other_or_unclear", "replacements": []}
                save_json(parsed_path, parsed)
                run = {"version": VERSION, "page_number": page, "model_error": str(exc), "reason_model_digest": rinfo.get("digest"), "transcription_sha256": r["transcription_sha256"], "prompt_sha256": sha256_text(EXTRACTION_PROMPT), "accepted_facts": 0, "qdrant_entries": 0}
                save_json(run_path, run)
                action = "safe_empty_fallback"
        record_class, mentions, rejected = validate_extraction(parsed, page, text)
        for m in mentions:
            m["record_class"] = record_class
        all_mentions.extend(mentions)
        page_rows.append({"page_number": page, "record_class": record_class, "replacement_count": len(mentions), "rejected_count": len(rejected), "rejected": rejected, "run_action": action})
        print(f"[extract {idx}/{len(reps)}] page={page} replacements={len(mentions)} rejected={len(rejected)} class={record_class} | {action}")
    write_jsonl(output_root / "replacement_mentions_v1_4_0.jsonl", all_mentions)
    save_json(output_root / "extraction_summary_v1_4_0.json", {"version": VERSION, "pages": page_rows, "mention_count": len(all_mentions)})
    return {"mentions": all_mentions, "pages": page_rows}


def descriptor_key(mention: Dict[str, Any]) -> str:
    pn = normalized_ws(mention.get("part_number") or "")
    desc = normalized_ws(mention.get("description") or "")
    if pn:
        return pn.upper()
    return desc.lower() or normalized_ws(mention.get("raw_quote") or "").lower()[:120]


def build_descriptors(mentions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for m in mentions:
        grouped[descriptor_key(m)].append(m)
    out: List[Dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        did = "d_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        part_numbers = sorted({str(x.get("part_number")) for x in rows if x.get("part_number")})
        descriptions = sorted({normalized_ws(x.get("description") or "") for x in rows if normalized_ws(x.get("description") or "")})
        examples = [x.get("raw_quote") for x in rows[:3]]
        out.append({"descriptor_id": did, "raw_key": key, "part_numbers": part_numbers, "descriptions": descriptions, "example_quotes": examples, "mention_ids": [x["mention_id"] for x in rows]})
    return out


def validate_normalization(parsed: Any, descriptors: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id = {d["descriptor_id"]: d for d in descriptors}
    assigned: set[str] = set()
    families: List[Dict[str, Any]] = []
    clusters = parsed.get("clusters") if isinstance(parsed, dict) else None
    if not isinstance(clusters, list):
        clusters = []
    for idx, c in enumerate(clusters):
        if not isinstance(c, dict):
            continue
        ids = []
        for x in c.get("member_descriptor_ids") or []:
            s = str(x)
            if s in by_id and s not in assigned:
                ids.append(s)
        ids = list(dict.fromkeys(ids))
        if len(ids) < 2:
            continue
        assigned.update(ids)
        label = normalized_ws(c.get("label") or "") or by_id[ids[0]]["raw_key"]
        fid = "pf_" + hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()[:16]
        families.append({"part_family_id": fid, "label": label, "member_descriptor_ids": ids, "origin": "model_cluster"})
    for d in descriptors:
        if d["descriptor_id"] not in assigned:
            fid = "pf_" + hashlib.sha256(d["descriptor_id"].encode("utf-8")).hexdigest()[:16]
            label = d["part_numbers"][0] if d["part_numbers"] else (d["descriptions"][0] if d["descriptions"] else d["raw_key"])
            families.append({"part_family_id": fid, "label": label, "member_descriptor_ids": [d["descriptor_id"]], "origin": "python_singleton"})
    return families


def normalize_part_families(args: argparse.Namespace, mentions: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    output_root = Path(args.output_root)
    cache = output_root / "part_family_map_v1_4_0.json"
    descriptors = build_descriptors(mentions)
    descriptor_hash = stable_json_hash([{k: d[k] for k in ("descriptor_id", "raw_key", "part_numbers", "descriptions", "mention_ids")} for d in descriptors])
    rinfo = model_info(args.reason_model)
    if cache.exists() and not args.force_normalize:
        try:
            prior = load_json(cache)
            if (prior.get("descriptor_manifest_sha256") == descriptor_hash
                    and prior.get("reason_model_digest") == rinfo.get("digest")
                    and prior.get("prompt_sha256") == sha256_text(NORMALIZE_PROMPT)):
                return prior
        except Exception:
            pass
    compact = [{"descriptor_id": d["descriptor_id"], "part_numbers": d["part_numbers"], "descriptions": d["descriptions"], "examples": d["example_quotes"]} for d in descriptors]
    prompt = NORMALIZE_PROMPT + "\n\nDESCRIPTORS:\n" + json.dumps(compact, ensure_ascii=False)
    parsed: Any = {"clusters": []}
    attempts: List[Dict[str, Any]] = []
    if descriptors:
        try:
            parsed, attempts = call_json_with_retry(args.reason_model, prompt, num_ctx=args.reason_num_ctx, num_predict=args.normalize_num_predict, timeout=args.timeout, retries=1, cache_dir=output_root / "normalization")
        except Exception:
            parsed = {"clusters": []}
    families = validate_normalization(parsed, descriptors)
    result = {"version": VERSION, "descriptor_manifest_sha256": descriptor_hash, "reason_model_digest": rinfo.get("digest"), "prompt_sha256": sha256_text(NORMALIZE_PROMPT), "descriptors": descriptors, "families": families, "attempts": attempts, "accepted_facts": 0, "qdrant_entries": 0}
    save_json(cache, result)
    return result


def count_frequencies(mentions: Sequence[Dict[str, Any]], family_map: Dict[str, Any]) -> List[Dict[str, Any]]:
    descriptor_by_mention: Dict[str, str] = {}
    for d in family_map.get("descriptors") or []:
        for mid in d.get("mention_ids") or []:
            descriptor_by_mention[str(mid)] = d["descriptor_id"]
    family_by_descriptor: Dict[str, Dict[str, Any]] = {}
    for fam in family_map.get("families") or []:
        for did in fam.get("member_descriptor_ids") or []:
            family_by_descriptor[str(did)] = fam
    buckets: Dict[str, Dict[str, Any]] = {}
    for m in mentions:
        did = descriptor_by_mention.get(m["mention_id"])
        fam = family_by_descriptor.get(did or "")
        if not fam:
            continue
        fid = fam["part_family_id"]
        b = buckets.setdefault(fid, {
            "part_family_id": fid,
            "label": fam["label"],
            "origin": fam.get("origin"),
            "pages": set(),
            "mention_ids": [],
            "part_numbers": set(),
            "descriptions": set(),
            "recorded_pieces": 0,
            "quantity_unstated_mentions": 0,
            "uncertain_mentions": 0,
        })
        b["pages"].add(int(m["page_number"]))
        b["mention_ids"].append(m["mention_id"])
        if m.get("part_number"):
            b["part_numbers"].add(str(m["part_number"]))
        if m.get("description"):
            b["descriptions"].add(str(m["description"]))
        if m.get("quantity") is None:
            b["quantity_unstated_mentions"] += 1
        else:
            b["recorded_pieces"] += int(m["quantity"])
        if m.get("uncertain"):
            b["uncertain_mentions"] += 1
    rows: List[Dict[str, Any]] = []
    for b in buckets.values():
        rows.append({
            "part_family_id": b["part_family_id"],
            "label": b["label"],
            "repairs_containing_part": len(b["pages"]),
            "recorded_pieces": b["recorded_pieces"],
            "quantity_unstated_mentions": b["quantity_unstated_mentions"],
            "uncertain_mentions": b["uncertain_mentions"],
            "part_numbers": sorted(b["part_numbers"]),
            "descriptions": sorted(b["descriptions"]),
            "representative_pages": sorted(b["pages"])[:20],
            "origin": b["origin"],
        })
    rows.sort(key=lambda x: (-x["repairs_containing_part"], -x["recorded_pieces"], str(x["label"]).lower()))
    return rows


def write_frequency_outputs(output_root: Path, rows: Sequence[Dict[str, Any]]) -> None:
    save_json(output_root / "part_frequency_v1_4_0.json", {"version": VERSION, "rows": list(rows)})
    with (output_root / "part_frequency_v1_4_0.csv").open("w", newline="", encoding="utf-8") as f:
        fieldnames = ["rank", "label", "repairs_containing_part", "recorded_pieces", "quantity_unstated_mentions", "uncertain_mentions", "part_numbers", "representative_pages", "part_family_id"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for idx, r in enumerate(rows, 1):
            w.writerow({
                "rank": idx,
                "label": r["label"],
                "repairs_containing_part": r["repairs_containing_part"],
                "recorded_pieces": r["recorded_pieces"],
                "quantity_unstated_mentions": r["quantity_unstated_mentions"],
                "uncertain_mentions": r["uncertain_mentions"],
                "part_numbers": "; ".join(r["part_numbers"]),
                "representative_pages": "; ".join(str(x) for x in r["representative_pages"]),
                "part_family_id": r["part_family_id"],
            })


def render_summary(args: argparse.Namespace, source_kind: str, source_count: int, dedupe: Dict[str, Any], extraction: Dict[str, Any], frequency: Sequence[Dict[str, Any]]) -> None:
    pages = extraction.get("pages") or []
    class_counts = Counter(str(x.get("record_class")) for x in pages)
    lines = [
        "# Nova DRL Power Supply Corpus Pilot v1.4.0",
        "",
        "Operating mode: BLIND PROVISIONAL 80/20",
        f"Source: {args.source_pdf if source_kind == 'pdf' else args.source_images_root}",
        f"Source pages/images: {source_count}",
        f"Duplicate pages excluded: {dedupe.get('duplicate_pages_excluded')}",
        f"Unique representative pages: {dedupe.get('unique_page_count')}",
        f"Extracted replacement mentions: {len(extraction.get('mentions') or [])}",
        f"Repair-record class pages: {class_counts.get('repair_record', 0)}",
        f"Reference/stock-list class pages: {class_counts.get('reference_or_stock_list', 0)}",
        f"Other/unclear class pages: {class_counts.get('other_or_unclear', 0)}",
        f"Provisional part families: {len(frequency)}",
        "Accepted facts: 0",
        "Qdrant writes: OFF",
        "Prior hosted benchmark read by runtime: NO",
        "",
        "TOP REPLACEMENT-PART FREQUENCIES — PROVISIONAL",
        "----------------------------------------------",
    ]
    for idx, r in enumerate(frequency[:40], 1):
        pn = ", ".join(r["part_numbers"]) if r["part_numbers"] else ""
        suffix = f" | PN: {pn}" if pn else ""
        lines.append(f"{idx:2d}. {r['label']} | repairs={r['repairs_containing_part']} | recorded pieces={r['recorded_pieces']} | qty-unstated mentions={r['quantity_unstated_mentions']}{suffix}")
    lines += [
        "",
        "POLICY",
        "------",
        "Original source modified: NO",
        "Raw page transcriptions modified after acquisition: NO",
        "Duplicate decisions: Python candidate generation + 14B adjudication; safe-unique on failure",
        "Replacement extraction: evidence-bound to raw transcription",
        "Part-family labels: provisional",
        "Frequency counts: Python-owned",
        "Unstated quantities converted to numbers: NO",
        "Automatic human approval: NO",
        "Qdrant writes: OFF",
        "Blind benchmark leakage into runtime: NO",
        "",
        "Freeze this output before comparing against any prior hosted parts-frequency report.",
    ]
    (Path(args.output_root) / "power_supply_pilot_summary_v1_4_0.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def status(args: argparse.Namespace) -> int:
    print("# Nova DRL Power Supply Corpus Pilot Status v1.4.0")
    if args.source_images_root:
        root = Path(args.source_images_root)
        try:
            n = len(discover_image_pages(root))
            print(f"Source images: FOUND | {root} | images={n}")
        except Exception as exc:
            print(f"Source images: ERROR | {root} | {exc}")
    else:
        pdf = Path(args.source_pdf)
        if pdf.exists():
            try:
                n = pdf_page_count(pdf)
                print(f"Source PDF:    FOUND | {pdf} | pages={n}")
            except Exception as exc:
                print(f"Source PDF:    FOUND but unreadable | {pdf} | {exc}")
        else:
            print(f"Source PDF:    NOT FOUND | {pdf}")
        print(f"Poppler:       pdfinfo={'FOUND' if require_executable('pdfinfo') else 'MISSING'} | pdftoppm={'FOUND' if require_executable('pdftoppm') else 'MISSING'}")
    vi = model_info(args.vision_model)
    ri = model_info(args.reason_model)
    print(f"Vision model:  {'FOUND' if vi.get('available') else 'MISSING'} | {args.vision_model}")
    print(f"Reason model:  {'FOUND' if ri.get('available') else 'MISSING'} | {args.reason_model}")
    print("Prior benchmark runtime input: NONE")
    print("Qdrant: OFF")
    return 0


def plan(args: argparse.Namespace) -> int:
    source_kind = "images" if args.source_images_root else "pdf"
    if source_kind == "images":
        pages = discover_image_pages(Path(args.source_images_root))
        count = len(pages)
        source = Path(args.source_images_root)
    else:
        source = Path(args.source_pdf)
        if not source.exists():
            print(f"ERROR: source PDF not found: {source}", file=sys.stderr)
            print("Place the original PDF there or pass --source-pdf /path/to/file.pdf", file=sys.stderr)
            return 2
        count = pdf_page_count(source)
    print("# Nova DRL Power Supply Corpus Pilot v1.4.0 — PLAN ONLY")
    print(f"Source kind:          {source_kind}")
    print(f"Source:               {source}")
    print(f"Pages/images:         {count}")
    print(f"Vision model:         {args.vision_model}")
    print(f"Reason model:         {args.reason_model}")
    print(f"Output root:          {args.output_root}")
    print("Acquisition calls:    one vision transcription per uncached page")
    print("Duplicate handling:   Python similarity candidates + 14B adjudication")
    print("Parts extraction:     one 14B extraction per unique representative page")
    print("Part normalization:   one provisional 14B corpus grouping call")
    print("Frequency counts:     Python")
    print("Prior benchmark read: NO")
    print("Accepted facts:       0")
    print("Qdrant:               OFF")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Nova DRL Power Supply Corpus Pilot v1.4.0 — blind parts-frequency validation")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--source-pdf", default=str(DEFAULT_SOURCE_PDF))
    src.add_argument("--source-images-root")
    ap.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    ap.add_argument("--vision-model", default=DEFAULT_VISION_MODEL)
    ap.add_argument("--reason-model", default=DEFAULT_REASON_MODEL)
    ap.add_argument("--vision-num-ctx", type=int, default=16384)
    ap.add_argument("--vision-num-predict", type=int, default=6144)
    ap.add_argument("--reason-num-ctx", type=int, default=16384)
    ap.add_argument("--extract-num-predict", type=int, default=2048)
    ap.add_argument("--normalize-num-predict", type=int, default=3072)
    ap.add_argument("--render-dpi", type=int, default=180)
    ap.add_argument("--duplicate-candidate-threshold", type=float, default=0.72)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--acquire-only", action="store_true")
    ap.add_argument("--force-acquisition", action="store_true")
    ap.add_argument("--force-dedupe", action="store_true")
    ap.add_argument("--force-extraction", action="store_true")
    ap.add_argument("--force-normalize", action="store_true")
    args = ap.parse_args()

    if args.status:
        return status(args)
    if args.plan_only:
        return plan(args)

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    source_kind = "images" if args.source_images_root else "pdf"
    source_sha: Optional[str] = None
    if source_kind == "images":
        pages: Sequence[Path] | int = discover_image_pages(Path(args.source_images_root))
        source_count = len(pages)
    else:
        pdf = Path(args.source_pdf)
        if not pdf.exists():
            print(f"ERROR: source PDF not found: {pdf}", file=sys.stderr)
            return 2
        source_sha = sha256_file(pdf)
        source_count = pdf_page_count(pdf)
        pages = source_count

    vi = model_info(args.vision_model)
    ri = model_info(args.reason_model)
    if not vi.get("available"):
        print(f"ERROR: vision model not available: {args.vision_model}", file=sys.stderr)
        return 3
    if not ri.get("available") and not args.acquire_only:
        print(f"ERROR: reason model not available: {args.reason_model}", file=sys.stderr)
        return 3

    print("# Nova DRL Power Supply Corpus Pilot v1.4.0")
    print("Operating mode: BLIND PROVISIONAL 80/20")
    print(f"Source pages/images: {source_count}")
    print(f"Vision model: {args.vision_model}")
    print(f"Reason model: {args.reason_model}")
    print("Prior hosted benchmark read by runtime: NO")
    print("Accepted facts: 0")
    print("Qdrant: OFF")

    records = acquire_pages(args, source_kind, pages, source_sha)
    if args.acquire_only:
        print("Acquisition complete; stopping because --acquire-only was requested.")
        return 0
    dedupe = adjudicate_duplicates(args, records)
    extraction = extract_replacements(args, records, dedupe)
    family_map = normalize_part_families(args, extraction["mentions"])
    freq = count_frequencies(extraction["mentions"], family_map)
    write_frequency_outputs(output_root, freq)
    render_summary(args, source_kind, source_count, dedupe, extraction, freq)
    manifest = {
        "version": VERSION,
        "source_kind": source_kind,
        "source": str(args.source_images_root or args.source_pdf),
        "source_sha256": source_sha,
        "source_page_count": source_count,
        "unique_page_count": dedupe.get("unique_page_count"),
        "duplicate_pages_excluded": dedupe.get("duplicate_pages_excluded"),
        "replacement_mention_count": len(extraction.get("mentions") or []),
        "part_family_count": len(freq),
        "vision_model": model_info(args.vision_model),
        "reason_model": model_info(args.reason_model),
        "prior_benchmark_read": False,
        "automatic_fact_acceptance": False,
        "qdrant_entries_created": 0,
        "summary": str(output_root / "power_supply_pilot_summary_v1_4_0.txt"),
        "frequency_json": str(output_root / "part_frequency_v1_4_0.json"),
        "frequency_csv": str(output_root / "part_frequency_v1_4_0.csv"),
    }
    save_json(output_root / "power_supply_pilot_manifest_v1_4_0.json", manifest)
    print("\n# COMPLETE")
    print(f"Source pages/images:       {source_count}")
    print(f"Unique representative:     {dedupe.get('unique_page_count')}")
    print(f"Duplicate pages excluded:  {dedupe.get('duplicate_pages_excluded')}")
    print(f"Replacement mentions:      {len(extraction.get('mentions') or [])}")
    print(f"Provisional part families: {len(freq)}")
    print("Accepted facts:            0")
    print("Qdrant:                    OFF")
    print(f"Summary: {output_root / 'power_supply_pilot_summary_v1_4_0.txt'}")
    print(f"CSV:     {output_root / 'part_frequency_v1_4_0.csv'}")
    print(f"Manifest:{output_root / 'power_supply_pilot_manifest_v1_4_0.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
