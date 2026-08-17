#!/usr/bin/env python3
"""
Nova DRL Power Supply Focused Evidence Recovery v1.4.1

Production-shaped power-supply evidence recovery and parts-frequency pipeline.

Primary production source:
- Individual DRL Line Card / Traveler image files in repair folders.
- Source path, file hash, parent folder, and DRL log number (when available) are preserved.

Benchmark adapter:
- A combined PDF may be supplied; every PDF page is rendered to an image and then
  passed through the SAME image-first focused evidence pipeline.
- The prior hosted benchmark is never read by this runtime.
- v1.4.0 local whole-page transcriptions may be reused as AUXILIARY blind evidence
  only when their source PDF hash matches the current PDF.

Pipeline:
1) Discover individual source images (recursive) or render benchmark PDF pages.
2) Qwen3-VL 8B performs a parts-focused reread on each image at source resolution
   (PDF benchmark pages default to 300 DPI). Optional enlarged repair-region crop is
   included in the same vision call when Pillow is available.
3) Duplicate scan candidates use exact hashes + optional perceptual image hash +
   focused/auxiliary text similarity. 14B adjudicates ambiguous candidate pairs.
4) Same DRL log number is treated as one repair event even when multiple images exist.
5) 14B extracts replacement mentions from focused evidence (+ matching v1.4.0 blind
   whole-page transcription when available).
6) Python performs deterministic punctuation/case consolidation. 14B adjudicates
   fuzzy candidate descriptor blocks at corpus scale; unassigned descriptors remain.
7) Python owns repair-frequency and explicit-piece counting.

No automatic approval. No Qdrant writes. Original evidence is read-only.
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
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.4.1"
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/power_supply_focused_recovery_v1_4_1")
DEFAULT_V140_ROOT = Path("/opt/nova-drl/output/power_supply_corpus_pilot_v1_4_0")
DEFAULT_VISION_MODEL = "qwen3-vl-drl:8b-q8-16k"
DEFAULT_REASON_MODEL = "qwen25-drl:14b-q6-16k"
DEFAULT_IMAGE_NAME_REGEX = r"line\s*card"
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
LOG_RE = re.compile(r"(?<!\d)(\d{9})(?!\d)")

FOCUSED_PARTS_PROMPT = """Read this DRL Line Card / repair record specifically for PARTS AND REPLACEMENT EVIDENCE.

Image 1 is the complete source image. If Image 2 is present, it is an enlarged repair/replacement region from the same source.

Return a literal PARTS-FOCUSED TRANSCRIPTION only; do not return JSON.

Capture every visible line or fragment that may identify a component or assembly that was replaced, installed, used, rebuilt, or otherwise consumed in this repair. Include enough surrounding action wording to distinguish actual repair usage from a printed stock/reference list.

Prioritize:
- exact part numbers and handwritten part-number-like strings,
- quantities and quantity marks,
- fuse current/voltage/style ratings,
- transistor/MOSFET/rectifier/diode/IC/resistor/capacitor identifiers or values,
- board/assembly names,
- donor/inventory-used parts,
- words such as replaced, installed, used, robbed, rebuilt, new, changed.

Rules:
- Preserve characters exactly as you read them. Do NOT normalize or silently correct likely OCR/handwriting variants.
- Do NOT substitute a familiar part number for an uncertain one.
- If one character is uncertain, keep the surrounding text and use [unclear] only for the uncertain portion.
- Include suspicious or imperfect part-number readings rather than omitting them.
- Do not summarize, interpret, count across records, or decide equivalence.
- Printed reference/stock text may be included if visible, but preserve enough context for a later stage to decide whether it was actually used.
- Do not repeat a line unless it is genuinely repeated on the source.
"""

DUPLICATE_PROMPT = """You are deciding whether two source images/transcriptions represent DUPLICATE SCANS of the same underlying DRL repair record.
Return JSON only:
{"duplicate": true|false, "confidence": "high|medium|low", "reason": "short evidence-based reason"}

Rules:
- Duplicate scan means the same underlying repair record captured more than once.
- Common printed form text, common replacement parts, or a standard rebuild do NOT make records duplicates.
- Compare source metadata, DRL log number when present, unusual handwritten wording, quantities, dates, serial identifiers, and repair-specific combinations.
- Image/text similarity is only a candidate signal; make the final decision from event-specific evidence.
- If uncertain, duplicate=false.
"""

EXTRACTION_PROMPT = """You are extracting replacement-part usage from ONE DRL power-supply repair EVENT.
The supplied evidence may include a focused parts reread and an auxiliary whole-page blind transcription of the same source.
Return JSON only:
{
  "record_class": "repair_record|reference_or_stock_list|other_or_unclear",
  "replacements": [
    {
      "raw_quote": "exact text copied from one supplied evidence block",
      "part_number": "exact part number/string if explicitly present, otherwise null",
      "description": "short physical component/assembly description grounded in the quote",
      "quantity": 1,
      "quantity_text": "exact quantity wording if present, otherwise null",
      "action": "replaced|installed|used|rebuilt_assembly|other",
      "uncertain": false
    }
  ]
}

Rules:
- Extract components/assemblies actually replaced, installed, used, robbed from inventory/donor, or explicitly rebuilt on this repair.
- Do NOT count a part merely because it appears in printed stock/reference text or a diagnostic/test instruction.
- Do NOT count cleaning, soldering, trace repair, inspection, adjustment, or testing as parts.
- raw_quote must be copied from supplied evidence. Do not paraphrase the evidence quote.
- quantity is an integer only when explicitly stated or unmistakably enumerated. Otherwise null.
- Never convert several, many, assorted, or unstated into a numeric quantity.
- Preserve exact part-number characters from evidence. Do not silently correct OCR-sensitive strings.
- If focused and auxiliary evidence disagree on exact characters, preserve separate supported mentions or use the one tied to the actual replacement wording; do not invent a reconciliation.
- A complete board/assembly replacement may be included if explicitly replaced/installed/used.
"""

NORMALIZE_BLOCK_PROMPT = """You are performing PROVISIONAL corpus-level consolidation of extracted DRL replacement descriptors.
Each supplied block contains descriptors that Python considers plausibly related. Split or group them by SAME PHYSICAL PART / SAME REPLACEMENT FAMILY.
Return JSON only:
{"clusters":[{"label":"short useful provisional family label","member_descriptor_ids":["d_...","d_..."]}]}

Rules:
- Use only supplied descriptor IDs.
- Group punctuation/case/spacing variants and obvious OCR/handwriting variants when the full evidence strongly indicates the same physical part.
- Different current/voltage/value ratings remain separate unless the evidence clearly indicates synonymous notation.
- Different board assemblies remain separate.
- Related but physically different parts remain separate.
- A descriptor may appear at most once.
- candidate_component is a Python similarity hint. Prefer grouping within the same candidate_component; merge across components only when the supplied evidence makes same-part identity especially clear.
- You may return a one-member cluster when its identity is clear, but Python will preserve any omitted descriptor automatically.
- This is provisional normalization only; original mention strings remain authoritative evidence.
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


def normalized_ws(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def safe_slug(text: Any) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return s[:120] or "unknown"


def require_executable(name: str) -> Optional[str]:
    return shutil.which(name)


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False


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


def call_ollama(model: str, prompt: str, *, image_paths: Optional[Sequence[Path]], num_ctx: int, num_predict: int, timeout: int) -> str:
    payload: Dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "num_ctx": int(num_ctx), "num_predict": int(num_predict)},
    }
    if image_paths:
        payload["images"] = [base64.b64encode(p.read_bytes()).decode("ascii") for p in image_paths]
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
        try:
            raw = call_ollama(model, current, image_paths=None, num_ctx=num_ctx, num_predict=num_predict, timeout=timeout)
            (cache_dir / f"raw_attempt_{idx:02d}.txt").write_text(raw, encoding="utf-8")
            parsed = parse_json_response(raw)
            attempts.append({"attempt": idx, "elapsed_seconds": round(time.time() - t0, 3), "error": None})
            return parsed, attempts
        except Exception as exc:
            if raw:
                (cache_dir / f"raw_attempt_{idx:02d}.txt").write_text(raw, encoding="utf-8")
            attempts.append({"attempt": idx, "elapsed_seconds": round(time.time() - t0, 3), "error": str(exc)})
            current = prompt + "\n\nPrevious response was invalid. Return only valid JSON in the requested schema."
    raise RuntimeError(attempts[-1]["error"] if attempts else "model JSON call failed")


def pdf_page_count(pdf: Path) -> int:
    exe = require_executable("pdfinfo")
    if not exe:
        raise RuntimeError("pdfinfo not found; install poppler-utils")
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
        raise RuntimeError("pdftoppm not found; install poppler-utils")
    dest.parent.mkdir(parents=True, exist_ok=True)
    prefix = dest.with_suffix("")
    p = subprocess.run([
        exe, "-f", str(page_num), "-l", str(page_num), "-singlefile", "-jpeg", "-jpegopt", "quality=94", "-r", str(dpi), str(pdf), str(prefix)
    ], capture_output=True, text=True, timeout=240)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or f"pdftoppm failed page {page_num}").strip())
    produced = prefix.with_suffix(".jpg")
    if produced != dest:
        produced.replace(dest)


def create_focus_crop(src: Path, dest: Path, *, x_start: float = 0.34, y_start: float = 0.10, y_end: float = 0.92, upscale: float = 1.35) -> Optional[Path]:
    if not pillow_available():
        return None
    try:
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("RGB")
            w, h = im.size
            box = (int(w * x_start), int(h * y_start), w, int(h * y_end))
            crop = im.crop(box)
            if upscale > 1.0:
                crop = crop.resize((int(crop.width * upscale), int(crop.height * upscale)), Image.Resampling.LANCZOS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            crop.save(dest, format="JPEG", quality=94, optimize=True)
        return dest
    except Exception:
        return None


def dhash_image(path: Path, hash_size: int = 16) -> Optional[str]:
    if not pillow_available():
        return None
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im = ImageOps.grayscale(im).resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
            px = list(im.getdata())
        bits = []
        width = hash_size + 1
        for y in range(hash_size):
            row = y * width
            for x in range(hash_size):
                bits.append(1 if px[row + x] > px[row + x + 1] else 0)
        val = 0
        for bit in bits:
            val = (val << 1) | bit
        return f"{val:0{hash_size * hash_size // 4}x}"
    except Exception:
        return None


def hash_hamming(a: Optional[str], b: Optional[str]) -> Optional[int]:
    if not a or not b or len(a) != len(b):
        return None
    try:
        return (int(a, 16) ^ int(b, 16)).bit_count()
    except Exception:
        return None


def extract_log_number(*texts: str) -> Optional[str]:
    for text in texts:
        m = LOG_RE.search(str(text or ""))
        if m:
            return m.group(1)
    return None


def discover_line_card_images(root: Path, name_regex: str) -> List[Path]:
    if not root.exists():
        raise FileNotFoundError(root)
    rx = re.compile(name_regex, re.I)
    out = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS and rx.search(p.name):
            out.append(p)
    return sorted(out, key=lambda p: str(p).lower())


def make_source_records(args: argparse.Namespace) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    output_root = Path(args.output_root)
    if args.source_images_root:
        root = Path(args.source_images_root)
        images = discover_line_card_images(root, args.image_name_regex)
        records: List[Dict[str, Any]] = []
        for idx, image in enumerate(images, 1):
            rel = str(image.relative_to(root))
            img_sha = sha256_file(image)
            log = extract_log_number(image.name, str(image.parent), rel)
            rid = "src_" + hashlib.sha256((rel + "\n" + img_sha).encode("utf-8")).hexdigest()[:16]
            records.append({
                "source_index": idx,
                "source_record_id": rid,
                "source_kind": "image",
                "source_path": str(image),
                "source_relative_path": rel,
                "source_image": str(image),
                "source_image_sha256": img_sha,
                "source_pdf_page": None,
                "source_pdf_sha256": None,
                "log_number": log,
                "parent_folder": str(image.parent),
            })
        return "images", records, None

    if not args.source_pdf:
        raise RuntimeError("Supply --source-images-root or --source-pdf")
    pdf = Path(args.source_pdf)
    if not pdf.exists():
        raise FileNotFoundError(pdf)
    pdf_sha = sha256_file(pdf)
    count = pdf_page_count(pdf)
    records = []
    for idx in range(1, count + 1):
        image = output_root / "benchmark_pdf_adapter" / "rendered_300dpi" / f"page_{idx:04d}.jpg"
        before = image.exists() and image.stat().st_size > 0
        render_pdf_page(pdf, idx, image, int(args.render_dpi))
        if not before:
            print(f"[render {idx}/{count}] {args.render_dpi}dpi -> {image.name}")
        img_sha = sha256_file(image)
        rid = "pdf_" + hashlib.sha256((pdf_sha + f"\n{idx}").encode("utf-8")).hexdigest()[:16]
        records.append({
            "source_index": idx,
            "source_record_id": rid,
            "source_kind": "pdf_adapter",
            "source_path": f"{pdf}#page={idx}",
            "source_relative_path": f"page_{idx:04d}",
            "source_image": str(image),
            "source_image_sha256": img_sha,
            "source_pdf_page": idx,
            "source_pdf_sha256": pdf_sha,
            "log_number": None,
            "parent_folder": str(pdf.parent),
        })
    return "pdf_adapter", records, pdf_sha


def load_matching_v140_aux(v140_root: Path, current_pdf_sha: Optional[str]) -> Dict[int, str]:
    if not current_pdf_sha or not v140_root.exists():
        return {}
    manifest = v140_root / "power_supply_pilot_manifest_v1_4_0.json"
    if not manifest.exists():
        return {}
    try:
        m = load_json(manifest)
        if m.get("source_sha256") != current_pdf_sha:
            return {}
        out: Dict[int, str] = {}
        for p in sorted((v140_root / "acquisition").glob("page_*/transcription.txt")):
            try:
                page = int(p.parent.name.split("_")[1])
                out[page] = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
        return out
    except Exception:
        return {}


def acquire_focused_evidence(args: argparse.Namespace, source_records: Sequence[Dict[str, Any]], aux_by_page: Dict[int, str]) -> List[Dict[str, Any]]:
    output_root = Path(args.output_root)
    vinfo = model_info(args.vision_model)
    out: List[Dict[str, Any]] = []
    total = len(source_records)
    for idx, src in enumerate(source_records, 1):
        rid = src["source_record_id"]
        pdir = output_root / "focused_acquisition" / f"record_{idx:04d}_{rid[-8:]}"
        pdir.mkdir(parents=True, exist_ok=True)
        focus_path = pdir / "focused_transcription.txt"
        meta_path = pdir / "record.json"
        image = Path(src["source_image"])
        crop = create_focus_crop(image, pdir / "repair_region.jpg") if args.use_focus_crop else None
        image_paths = [image] + ([crop] if crop else [])
        image_hashes = [sha256_file(x) for x in image_paths]
        aux = aux_by_page.get(int(src["source_pdf_page"])) if src.get("source_pdf_page") else None
        aux_sha = sha256_text(aux) if aux else None
        cache_ok = False
        if focus_path.exists() and meta_path.exists() and not args.force_focused_acquisition:
            try:
                m = load_json(meta_path)
                cache_ok = (
                    m.get("source_image_sha256") == src["source_image_sha256"]
                    and m.get("vision_model_digest") == vinfo.get("digest")
                    and m.get("prompt_sha256") == sha256_text(FOCUSED_PARTS_PROMPT)
                    and m.get("input_image_sha256s") == image_hashes
                    and m.get("aux_v140_sha256") == aux_sha
                )
            except Exception:
                cache_ok = False
        if cache_ok:
            focused = focus_path.read_text(encoding="utf-8", errors="ignore")
            action = "cache"
        else:
            t0 = time.time()
            focused = call_ollama(args.vision_model, FOCUSED_PARTS_PROMPT, image_paths=image_paths, num_ctx=args.vision_num_ctx, num_predict=args.vision_num_predict, timeout=args.timeout)
            focus_path.write_text(focused, encoding="utf-8")
            save_json(meta_path, {
                "version": VERSION,
                "source_record_id": rid,
                "source_index": src["source_index"],
                "source_path": src["source_path"],
                "source_image": str(image),
                "source_image_sha256": src["source_image_sha256"],
                "input_image_sha256s": image_hashes,
                "focus_crop_used": bool(crop),
                "aux_v140_sha256": aux_sha,
                "vision_model": vinfo,
                "vision_model_digest": vinfo.get("digest"),
                "prompt_sha256": sha256_text(FOCUSED_PARTS_PROMPT),
                "elapsed_seconds": round(time.time() - t0, 3),
                "accepted_facts": 0,
                "qdrant_entries": 0,
            })
            action = "model_run"
        log = src.get("log_number") or extract_log_number(focused, aux or "")
        dh = dhash_image(image)
        row = dict(src)
        row.update({
            "focused_transcription_path": str(focus_path),
            "focused_transcription_sha256": sha256_text(focused),
            "focused_character_count": len(focused),
            "aux_v140_transcription": aux,
            "aux_v140_transcription_sha256": aux_sha,
            "log_number": log,
            "image_dhash": dh,
            "focus_crop_used": bool(crop),
        })
        out.append(row)
        print(f"[focus {idx}/{total}] chars={len(focused)} log={log or '-'} crop={'yes' if crop else 'no'} | {action}")
    save_json(output_root / "focused_source_manifest_v1_4_1.json", {"version": VERSION, "records": out})
    return out


def duplicate_normalize(text: str) -> str:
    s = str(text or "").lower()
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


def record_evidence_text(r: Dict[str, Any]) -> str:
    focused = Path(r["focused_transcription_path"]).read_text(encoding="utf-8", errors="ignore")
    aux = r.get("aux_v140_transcription") or ""
    return focused + ("\n\n" + aux if aux else "")


def propose_duplicate_pairs(records: Sequence[Dict[str, Any]], *, text_threshold: float, dhash_threshold: int) -> List[Dict[str, Any]]:
    candidates: Dict[Tuple[int, int], Dict[str, Any]] = {}
    texts = {i: record_evidence_text(r) for i, r in enumerate(records)}
    sh = {i: shingles(t) for i, t in texts.items()}
    for i in range(len(records)):
        a = records[i]
        for j in range(i + 1, len(records)):
            b = records[j]
            reasons = []
            exact = a["source_image_sha256"] == b["source_image_sha256"]
            if exact:
                reasons.append("exact_image_sha256")
            ham = hash_hamming(a.get("image_dhash"), b.get("image_dhash"))
            ta, tb = duplicate_normalize(texts[i]), duplicate_normalize(texts[j])
            text_sim = 0.0
            if ta and tb:
                lr = min(len(ta), len(tb)) / max(len(ta), len(tb))
                if lr >= 0.35:
                    text_sim = jaccard(sh[i], sh[j])
                    if text_sim >= text_threshold:
                        reasons.append("text_similarity")
            # Same-form Line Cards can have similar perceptual hashes. Never use
            # dHash alone unless the focused/aux text has at least weak overlap.
            if ham is not None and ham <= dhash_threshold and text_sim >= 0.18:
                reasons.append("perceptual_image_hash")
            if reasons:
                candidates[(i, j)] = {
                    "record_a_index": i,
                    "record_b_index": j,
                    "source_a": a["source_path"],
                    "source_b": b["source_path"],
                    "text_similarity": round(text_sim, 4),
                    "dhash_hamming": ham,
                    "exact_image_sha256": exact,
                    "candidate_reasons": reasons,
                }
    return sorted(candidates.values(), key=lambda x: (0 if x["exact_image_sha256"] else 1, x["dhash_hamming"] if x["dhash_hamming"] is not None else 9999, -x["text_similarity"]))


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
    cache = output_root / "dedupe" / "duplicate_adjudication_v1_4_1.json"
    input_hash = stable_json_hash([(r["source_record_id"], r["source_image_sha256"], r["focused_transcription_sha256"], r.get("aux_v140_transcription_sha256"), r.get("image_dhash")) for r in records])
    rinfo = model_info(args.reason_model)
    if cache.exists() and not args.force_dedupe:
        try:
            prior = load_json(cache)
            if (prior.get("input_manifest_sha256") == input_hash
                    and prior.get("reason_model_digest") == rinfo.get("digest")
                    and float(prior.get("text_threshold")) == float(args.duplicate_text_threshold)
                    and int(prior.get("dhash_threshold")) == int(args.duplicate_dhash_threshold)):
                return prior
        except Exception:
            pass
    pairs = propose_duplicate_pairs(records, text_threshold=float(args.duplicate_text_threshold), dhash_threshold=int(args.duplicate_dhash_threshold))
    uf = UnionFind(range(len(records)))
    decisions: List[Dict[str, Any]] = []
    for n, pair in enumerate(pairs, 1):
        ia, ib = int(pair["record_a_index"]), int(pair["record_b_index"])
        a, b = records[ia], records[ib]
        if pair["exact_image_sha256"]:
            decision = {**pair, "duplicate": True, "confidence": "high", "reason": "identical source image SHA256", "action": "python_exact_image"}
        else:
            ea = record_evidence_text(a)[:9000]
            eb = record_evidence_text(b)[:9000]
            prompt = DUPLICATE_PROMPT + "\n\nSOURCE A:\n" + json.dumps({"path": a["source_path"], "log": a.get("log_number"), "text": ea}, ensure_ascii=False) + "\n\nSOURCE B:\n" + json.dumps({"path": b["source_path"], "log": b.get("log_number"), "text": eb}, ensure_ascii=False)
            cdir = output_root / "dedupe" / "pairs" / f"pair_{ia+1:04d}_{ib+1:04d}"
            try:
                parsed, attempts = call_json_with_retry(args.reason_model, prompt, num_ctx=args.reason_num_ctx, num_predict=768, timeout=args.timeout, retries=1, cache_dir=cdir)
                dup = bool(parsed.get("duplicate")) if isinstance(parsed, dict) else False
                decision = {**pair, "duplicate": dup, "confidence": parsed.get("confidence") if isinstance(parsed, dict) else "low", "reason": parsed.get("reason") if isinstance(parsed, dict) else "invalid", "attempts": attempts, "action": "model"}
            except Exception as exc:
                decision = {**pair, "duplicate": False, "confidence": "low", "reason": f"adjudication failed: {exc}", "action": "safe_unique_fallback"}
        if decision["duplicate"]:
            uf.union(ia, ib)
        decisions.append(decision)
        print(f"[dedupe {n}/{len(pairs)}] {ia+1}/{ib+1} text={pair['text_similarity']:.3f} dh={pair['dhash_hamming']} duplicate={decision['duplicate']}")
    groups: Dict[int, List[int]] = defaultdict(list)
    for i in range(len(records)):
        groups[uf.find(i)].append(i)
    duplicate_groups = [[records[i]["source_record_id"] for i in g] for g in groups.values() if len(g) > 1]
    representative_index = {i: min(groups[uf.find(i)]) for i in range(len(records))}
    result = {
        "version": VERSION,
        "input_manifest_sha256": input_hash,
        "reason_model_digest": rinfo.get("digest"),
        "text_threshold": float(args.duplicate_text_threshold),
        "dhash_threshold": int(args.duplicate_dhash_threshold),
        "candidate_pair_count": len(pairs),
        "decisions": decisions,
        "duplicate_groups": duplicate_groups,
        "representative_index_by_record_id": {records[i]["source_record_id"]: records[rep]["source_record_id"] for i, rep in representative_index.items()},
        "unique_representative_record_ids": sorted({records[rep]["source_record_id"] for rep in representative_index.values()}),
        "source_record_count": len(records),
        "unique_record_count": len(set(representative_index.values())),
        "duplicate_records_excluded": len(records) - len(set(representative_index.values())),
        "accepted_facts": 0,
        "qdrant_entries": 0,
    }
    save_json(cache, result)
    return result


def quote_bound_any(raw_quote: str, evidence_blocks: Sequence[str]) -> bool:
    q = normalized_ws(raw_quote)
    if not q:
        return False
    q2 = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9]+", " ", q)).strip().lower()
    for block in evidence_blocks:
        t = normalized_ws(block)
        if q in t:
            return True
        t2 = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9]+", " ", t)).strip().lower()
        if q2 and q2 in t2:
            return True
    return False


def validate_extraction(parsed: Any, event_id: str, source_record_ids: Sequence[str], evidence_blocks: Sequence[str]) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
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
        if not quote_bound_any(raw, evidence_blocks):
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
        mention_id = "m_" + hashlib.sha256((event_id + f"\n{idx}\n{raw}\n{pn}\n{qty}").encode("utf-8")).hexdigest()[:16]
        valid.append({
            "mention_id": mention_id,
            "repair_event_id": event_id,
            "source_record_ids": list(source_record_ids),
            "raw_quote": raw,
            "part_number": pn,
            "description": desc,
            "quantity": qty,
            "quantity_text": row.get("quantity_text"),
            "action": action,
            "uncertain": bool(row.get("uncertain")),
        })
    return record_class, valid, rejected


def build_repair_events(records: Sequence[Dict[str, Any]], dedupe: Dict[str, Any]) -> List[Dict[str, Any]]:
    by_id = {r["source_record_id"]: r for r in records}
    rep_map = dedupe["representative_index_by_record_id"]
    # Duplicate scans are excluded from extraction. Only the representative scan
    # from each duplicate group is carried forward. Multiple NON-duplicate images
    # sharing the same DRL log are then merged as one repair event.
    unique_rep_ids = sorted(set(str(x) for x in rep_map.values()))
    event_buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for repid in unique_rep_ids:
        rep = by_id[repid]
        log = rep.get("log_number")
        event_key = f"log_{log}" if log else f"record_{repid}"
        event_buckets[event_key].append(rep)
    events = []
    for event_key, members in sorted(event_buckets.items()):
        unique_members = {m["source_record_id"]: m for m in members}
        members = sorted(unique_members.values(), key=lambda x: x["source_index"])
        events.append({
            "repair_event_id": event_key,
            "log_number": extract_log_number(event_key),
            "source_record_ids": [m["source_record_id"] for m in members],
            "source_paths": [m["source_path"] for m in members],
            "records": members,
        })
    return events


def extract_replacements(args: argparse.Namespace, records: Sequence[Dict[str, Any]], dedupe: Dict[str, Any]) -> Dict[str, Any]:
    output_root = Path(args.output_root)
    events = build_repair_events(records, dedupe)
    rinfo = model_info(args.reason_model)
    all_mentions: List[Dict[str, Any]] = []
    event_rows: List[Dict[str, Any]] = []
    for idx, event in enumerate(events, 1):
        blocks: List[str] = []
        block_meta = []
        for r in event["records"]:
            focused = Path(r["focused_transcription_path"]).read_text(encoding="utf-8", errors="ignore")
            blocks.append(focused)
            block_meta.append({"source": r["source_path"], "kind": "focused", "sha256": sha256_text(focused)})
            if r.get("aux_v140_transcription"):
                aux = str(r["aux_v140_transcription"])
                blocks.append(aux)
                block_meta.append({"source": r["source_path"], "kind": "v1.4.0_blind_whole_page_aux", "sha256": sha256_text(aux)})
        input_hash = stable_json_hash(block_meta)
        edir = output_root / "extraction" / f"event_{idx:04d}_{safe_slug(event['repair_event_id'])[:40]}"
        parsed_path = edir / "parsed.json"
        run_path = edir / "run.json"
        cache_ok = False
        if parsed_path.exists() and run_path.exists() and not args.force_extraction:
            try:
                run = load_json(run_path)
                cache_ok = (run.get("evidence_manifest_sha256") == input_hash and run.get("reason_model_digest") == rinfo.get("digest") and run.get("prompt_sha256") == sha256_text(EXTRACTION_PROMPT))
            except Exception:
                cache_ok = False
        if cache_ok:
            parsed = load_json(parsed_path)
            attempts = load_json(run_path).get("attempts") or []
            action = "cache"
        else:
            payload = []
            for n, b in enumerate(blocks, 1):
                payload.append(f"EVIDENCE BLOCK {n}:\n{b}")
            prompt = EXTRACTION_PROMPT + "\n\n" + "\n\n".join(payload)
            try:
                parsed, attempts = call_json_with_retry(args.reason_model, prompt, num_ctx=args.reason_num_ctx, num_predict=args.extract_num_predict, timeout=args.timeout, retries=1, cache_dir=edir)
            except Exception as exc:
                parsed, attempts = {"record_class": "other_or_unclear", "replacements": []}, [{"error": str(exc)}]
            save_json(parsed_path, parsed)
            save_json(run_path, {"version": VERSION, "repair_event_id": event["repair_event_id"], "evidence_manifest_sha256": input_hash, "reason_model_digest": rinfo.get("digest"), "prompt_sha256": sha256_text(EXTRACTION_PROMPT), "attempts": attempts, "accepted_facts": 0, "qdrant_entries": 0})
            action = "model_run"
        rc, mentions, rejected = validate_extraction(parsed, event["repair_event_id"], event["source_record_ids"], blocks)
        all_mentions.extend(mentions)
        event_rows.append({"repair_event_id": event["repair_event_id"], "log_number": event.get("log_number"), "source_record_ids": event["source_record_ids"], "source_paths": event["source_paths"], "record_class": rc, "replacement_count": len(mentions), "rejected_count": len(rejected), "run_action": action})
        print(f"[extract {idx}/{len(events)}] event={event['repair_event_id']} replacements={len(mentions)} rejected={len(rejected)} class={rc} | {action}")
    write_jsonl(output_root / "replacement_mentions_v1_4_1.jsonl", all_mentions)
    save_json(output_root / "extraction_summary_v1_4_1.json", {"version": VERSION, "events": event_rows, "event_count": len(events), "mention_count": len(all_mentions)})
    return {"mentions": all_mentions, "events": event_rows, "repair_events": events}


def canonical_descriptor_key(mention: Dict[str, Any]) -> str:
    pn = normalized_ws(mention.get("part_number") or "")
    if pn:
        compact = re.sub(r"[^A-Za-z0-9]+", "", pn).upper()
        return "PN:" + (compact or pn.upper())
    desc = normalized_ws(mention.get("description") or "")
    compact_desc = re.sub(r"[^a-z0-9]+", " ", desc.lower()).strip()
    return "DESC:" + (compact_desc or normalized_ws(mention.get("raw_quote") or "").lower()[:140])


def build_descriptors(mentions: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for m in mentions:
        grouped[canonical_descriptor_key(m)].append(m)
    out = []
    for key, rows in sorted(grouped.items()):
        did = "d_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        pns = sorted({str(x.get("part_number")) for x in rows if x.get("part_number")})
        descs = sorted({normalized_ws(x.get("description") or "") for x in rows if normalized_ws(x.get("description") or "")})
        quotes = []
        for x in rows:
            q = str(x.get("raw_quote") or "")
            if q and q not in quotes:
                quotes.append(q)
            if len(quotes) >= 5:
                break
        out.append({
            "descriptor_id": did,
            "canonical_key": key,
            "part_numbers": pns,
            "descriptions": descs,
            "example_quotes": quotes,
            "mention_ids": [x["mention_id"] for x in rows],
            "event_ids": sorted({x["repair_event_id"] for x in rows}),
        })
    return out


def token_set(text: str) -> set[str]:
    return {x for x in re.findall(r"[a-z0-9]+", text.lower()) if len(x) > 1}


def rating_tokens(text: str) -> set[str]:
    s = text.upper().replace("µ", "U")
    pats = [r"\b\d+(?:\.\d+)?\s*(?:A|AMP|AMPS)\b", r"\b\d+(?:\.\d+)?\s*V\b", r"\b\d+(?:\.\d+)?\s*(?:UF|PF|NF)\b", r"\b\d+(?:\.\d+)?\s*(?:OHM|OHMS)\b"]
    out = set()
    for p in pats:
        for m in re.findall(p, s):
            out.add(re.sub(r"\s+", "", m))
    return out


def descriptor_text(d: Dict[str, Any]) -> str:
    return " ".join(d.get("part_numbers") or []) + " " + " ".join(d.get("descriptions") or []) + " " + " ".join(d.get("example_quotes") or [])


def part_like_key(d: Dict[str, Any]) -> str:
    if d.get("part_numbers"):
        return re.sub(r"[^A-Z0-9]+", "", str(d["part_numbers"][0]).upper())
    return re.sub(r"[^A-Z0-9]+", "", str(d.get("canonical_key") or "").upper())


def descriptor_candidate_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ka, kb = part_like_key(a), part_like_key(b)
    seq = SequenceMatcher(None, ka, kb).ratio() if ka and kb else 0.0
    ta, tb = token_set(descriptor_text(a)), token_set(descriptor_text(b))
    jac = (len(ta & tb) / len(ta | tb)) if ta and tb else 0.0
    ra, rb = rating_tokens(descriptor_text(a)), rating_tokens(descriptor_text(b))
    rating_bonus = 0.12 if ra and rb and ra == rb else 0.0
    rating_conflict = 0.30 if ra and rb and ra.isdisjoint(rb) else 0.0
    pn_bonus = 0.10 if a.get("part_numbers") and b.get("part_numbers") else 0.0
    return max(seq + pn_bonus + rating_bonus - rating_conflict, jac + rating_bonus - rating_conflict)


def candidate_descriptor_components(descriptors: Sequence[Dict[str, Any]], threshold: float) -> List[List[str]]:
    uf = UnionFind(range(len(descriptors)))
    edge_count = [0] * len(descriptors)
    for i in range(len(descriptors)):
        for j in range(i + 1, len(descriptors)):
            s = descriptor_candidate_similarity(descriptors[i], descriptors[j])
            if s >= threshold:
                uf.union(i, j)
                edge_count[i] += 1
                edge_count[j] += 1
    groups: Dict[int, List[str]] = defaultdict(list)
    for i, d in enumerate(descriptors):
        groups[uf.find(i)].append(d["descriptor_id"])
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))


def pack_candidate_components(components: Sequence[List[str]], by_id: Dict[str, Dict[str, Any]], max_descriptors: int) -> List[List[str]]:
    batches: List[List[str]] = []
    current: List[str] = []
    for comp in components:
        if len(comp) > max_descriptors:
            if current:
                batches.append(current); current = []
            for i in range(0, len(comp), max_descriptors):
                batches.append(comp[i:i+max_descriptors])
            continue
        if current and len(current) + len(comp) > max_descriptors:
            batches.append(current); current = []
        current.extend(comp)
    if current:
        batches.append(current)
    return batches


def validate_normalization(parsed: Any, descriptor_ids: Sequence[str], by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    known = set(descriptor_ids)
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
            if s in known and s not in assigned:
                ids.append(s)
        ids = list(dict.fromkeys(ids))
        if not ids:
            continue
        assigned.update(ids)
        label = normalized_ws(c.get("label") or "") or (by_id[ids[0]]["part_numbers"][0] if by_id[ids[0]]["part_numbers"] else (by_id[ids[0]]["descriptions"][0] if by_id[ids[0]]["descriptions"] else by_id[ids[0]]["canonical_key"]))
        fid = "pf_" + hashlib.sha256("\n".join(sorted(ids)).encode("utf-8")).hexdigest()[:16]
        families.append({"part_family_id": fid, "label": label, "member_descriptor_ids": ids, "origin": "model_provisional_cluster", "model_cluster_index": idx})
    for did in descriptor_ids:
        if did not in assigned:
            d = by_id[did]
            label = d["part_numbers"][0] if d["part_numbers"] else (d["descriptions"][0] if d["descriptions"] else d["canonical_key"])
            fid = "pf_" + hashlib.sha256(did.encode("utf-8")).hexdigest()[:16]
            families.append({"part_family_id": fid, "label": label, "member_descriptor_ids": [did], "origin": "python_singleton"})
    return families


def normalize_part_families(args: argparse.Namespace, mentions: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    output_root = Path(args.output_root)
    cache = output_root / "part_family_map_v1_4_1.json"
    descriptors = build_descriptors(mentions)
    by_id = {d["descriptor_id"]: d for d in descriptors}
    descriptor_hash = stable_json_hash([{k: d[k] for k in ("descriptor_id", "canonical_key", "part_numbers", "descriptions", "mention_ids", "event_ids")} for d in descriptors])
    rinfo = model_info(args.reason_model)
    if cache.exists() and not args.force_normalize:
        try:
            prior = load_json(cache)
            if (prior.get("descriptor_manifest_sha256") == descriptor_hash
                    and prior.get("reason_model_digest") == rinfo.get("digest")
                    and prior.get("prompt_sha256") == sha256_text(NORMALIZE_BLOCK_PROMPT)
                    and float(prior.get("candidate_threshold")) == float(args.normalize_candidate_threshold)):
                return prior
        except Exception:
            pass
    components = candidate_descriptor_components(descriptors, float(args.normalize_candidate_threshold))
    batches = pack_candidate_components(components, by_id, int(args.normalize_batch_descriptors))
    all_families: List[Dict[str, Any]] = []
    run_meta = []
    for idx, ids in enumerate(batches, 1):
        compact = []
        component_by_id = {}
        for comp_idx, comp in enumerate(components, 1):
            for did0 in comp:
                component_by_id[did0] = comp_idx
        for did in ids:
            d = by_id[did]
            compact.append({"descriptor_id": did, "candidate_component": component_by_id.get(did), "part_numbers": d["part_numbers"], "descriptions": d["descriptions"], "examples": d["example_quotes"][:3], "repair_event_count": len(d["event_ids"])})
        prompt = NORMALIZE_BLOCK_PROMPT + "\n\nCANDIDATE DESCRIPTORS:\n" + json.dumps(compact, ensure_ascii=False)
        cdir = output_root / "normalization" / f"batch_{idx:04d}"
        parsed_path = cdir / "parsed.json"
        run_path = cdir / "run.json"
        batch_hash = stable_json_hash(compact)
        parsed = {"clusters": []}; attempts = []; action = "model_run"
        if parsed_path.exists() and run_path.exists() and not args.force_normalize:
            try:
                run = load_json(run_path)
                if run.get("batch_manifest_sha256") == batch_hash and run.get("reason_model_digest") == rinfo.get("digest") and run.get("prompt_sha256") == sha256_text(NORMALIZE_BLOCK_PROMPT):
                    parsed = load_json(parsed_path); attempts = run.get("attempts") or []; action = "cache"
            except Exception:
                pass
        if action != "cache":
            try:
                parsed, attempts = call_json_with_retry(args.reason_model, prompt, num_ctx=args.reason_num_ctx, num_predict=args.normalize_num_predict, timeout=args.timeout, retries=1, cache_dir=cdir)
            except Exception as exc:
                attempts = [{"error": str(exc)}]
                parsed = {"clusters": []}
            save_json(parsed_path, parsed)
            save_json(run_path, {"version": VERSION, "batch_manifest_sha256": batch_hash, "reason_model_digest": rinfo.get("digest"), "prompt_sha256": sha256_text(NORMALIZE_BLOCK_PROMPT), "attempts": attempts})
        fams = validate_normalization(parsed, ids, by_id)
        all_families.extend(fams)
        run_meta.append({"batch": idx, "descriptor_count": len(ids), "family_count": len(fams), "attempts": attempts, "run_action": action})
        print(f"[normalize {idx}/{len(batches)}] descriptors={len(ids)} families={len(fams)} | {action}")
    result = {
        "version": VERSION,
        "descriptor_manifest_sha256": descriptor_hash,
        "reason_model_digest": rinfo.get("digest"),
        "prompt_sha256": sha256_text(NORMALIZE_BLOCK_PROMPT),
        "candidate_threshold": float(args.normalize_candidate_threshold),
        "candidate_component_count": len(components),
        "normalization_batch_count": len(batches),
        "descriptors": descriptors,
        "families": all_families,
        "run_meta": run_meta,
        "accepted_facts": 0,
        "qdrant_entries": 0,
    }
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
        fam = family_by_descriptor.get(descriptor_by_mention.get(m["mention_id"], ""))
        if not fam:
            continue
        fid = fam["part_family_id"]
        b = buckets.setdefault(fid, {"part_family_id": fid, "label": fam["label"], "origin": fam.get("origin"), "repair_events": set(), "mention_ids": [], "part_numbers": set(), "descriptions": set(), "recorded_pieces": 0, "quantity_unstated_mentions": 0, "uncertain_mentions": 0})
        b["repair_events"].add(m["repair_event_id"])
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
    rows = []
    for b in buckets.values():
        rows.append({
            "part_family_id": b["part_family_id"],
            "label": b["label"],
            "repairs_containing_part": len(b["repair_events"]),
            "recorded_pieces": b["recorded_pieces"],
            "quantity_unstated_mentions": b["quantity_unstated_mentions"],
            "uncertain_mentions": b["uncertain_mentions"],
            "part_numbers": sorted(b["part_numbers"]),
            "descriptions": sorted(b["descriptions"]),
            "representative_repair_events": sorted(b["repair_events"])[:20],
            "origin": b["origin"],
        })
    rows.sort(key=lambda x: (-x["repairs_containing_part"], -x["recorded_pieces"], str(x["label"]).lower()))
    return rows


def write_frequency_outputs(output_root: Path, rows: Sequence[Dict[str, Any]]) -> None:
    save_json(output_root / "part_frequency_v1_4_1.json", {"version": VERSION, "rows": list(rows)})
    with (output_root / "part_frequency_v1_4_1.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["rank", "label", "repairs_containing_part", "recorded_pieces", "quantity_unstated_mentions", "uncertain_mentions", "part_numbers", "representative_repair_events", "part_family_id"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for idx, r in enumerate(rows, 1):
            w.writerow({
                "rank": idx,
                "label": r["label"],
                "repairs_containing_part": r["repairs_containing_part"],
                "recorded_pieces": r["recorded_pieces"],
                "quantity_unstated_mentions": r["quantity_unstated_mentions"],
                "uncertain_mentions": r["uncertain_mentions"],
                "part_numbers": "; ".join(r["part_numbers"]),
                "representative_repair_events": "; ".join(r["representative_repair_events"]),
                "part_family_id": r["part_family_id"],
            })


def render_summary(args: argparse.Namespace, source_kind: str, source_records: Sequence[Dict[str, Any]], dedupe: Dict[str, Any], extraction: Dict[str, Any], frequency: Sequence[Dict[str, Any]], aux_count: int) -> None:
    class_counts = Counter(str(x.get("record_class")) for x in extraction.get("events") or [])
    events = extraction.get("repair_events") or []
    lines = [
        "# Nova DRL Power Supply Focused Evidence Recovery v1.4.1",
        "",
        "Operating mode: BLIND PROVISIONAL 80/20",
        f"Source mode: {source_kind}",
        f"Source records/images: {len(source_records)}",
        f"Duplicate scan records excluded: {dedupe.get('duplicate_records_excluded')}",
        f"Unique scan representatives: {dedupe.get('unique_record_count')}",
        f"Distinct repair events after log grouping: {len(events)}",
        f"Focused vision records: {len(source_records)}",
        f"v1.4.0 blind whole-page auxiliary transcriptions reused: {aux_count}",
        f"Extracted replacement mentions: {len(extraction.get('mentions') or [])}",
        f"Repair-record class events: {class_counts.get('repair_record', 0)}",
        f"Reference/stock-list class events: {class_counts.get('reference_or_stock_list', 0)}",
        f"Other/unclear class events: {class_counts.get('other_or_unclear', 0)}",
        f"Provisional part families: {len(frequency)}",
        "Accepted facts: 0",
        "Qdrant writes: OFF",
        "Prior hosted benchmark read by runtime: NO",
        "",
        "TOP REPLACEMENT-PART FREQUENCIES — PROVISIONAL",
        "----------------------------------------------",
    ]
    for idx, r in enumerate(frequency[:50], 1):
        pn = ", ".join(r["part_numbers"])
        suffix = f" | PN: {pn}" if pn else ""
        lines.append(f"{idx:2d}. {r['label']} | repairs={r['repairs_containing_part']} | recorded pieces={r['recorded_pieces']} | qty-unstated mentions={r['quantity_unstated_mentions']}{suffix}")
    lines += [
        "",
        "POLICY",
        "------",
        "Production architecture source: individual Line Card images",
        "Benchmark PDF role: adapter only; each page converted to image and processed by same focused pipeline",
        "Original source modified: NO",
        "v1.4.0 blind baseline modified: NO",
        "Focused vision transcription modified after acquisition: NO",
        "Duplicate decisions: exact/perceptual/text candidate generation + 14B adjudication; safe-unique on failure",
        "Same DRL log number: one repair event for frequency counting",
        "Replacement extraction: evidence-bound to focused and optional matching v1.4.0 blind transcription",
        "Part-family labels: provisional corpus-level consolidation",
        "Frequency counts: Python-owned",
        "Unstated quantities converted to numbers: NO",
        "Automatic human approval: NO",
        "Qdrant writes: OFF",
        "Blind hosted benchmark leakage into runtime: NO",
    ]
    (Path(args.output_root) / "power_supply_focused_summary_v1_4_1.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def status(args: argparse.Namespace) -> int:
    print("# Nova DRL Power Supply Focused Evidence Recovery Status v1.4.1")
    if args.source_images_root:
        root = Path(args.source_images_root)
        try:
            imgs = discover_line_card_images(root, args.image_name_regex)
            print(f"Source images: FOUND | {root} | matching Line Cards={len(imgs)} | recursive=YES")
        except Exception as exc:
            print(f"Source images: ERROR | {root} | {exc}")
        print("Production source role: PRIMARY")
    elif args.source_pdf:
        pdf = Path(args.source_pdf)
        if pdf.exists():
            try:
                print(f"Benchmark PDF: FOUND | {pdf} | pages={pdf_page_count(pdf)}")
            except Exception as exc:
                print(f"Benchmark PDF: FOUND but unreadable | {pdf} | {exc}")
        else:
            print(f"Benchmark PDF: NOT FOUND | {pdf}")
        print(f"Poppler: pdfinfo={'FOUND' if require_executable('pdfinfo') else 'MISSING'} | pdftoppm={'FOUND' if require_executable('pdftoppm') else 'MISSING'}")
        print("Benchmark PDF role: ADAPTER ONLY")
    else:
        print("Source: NOT SPECIFIED")
    print(f"Pillow: {'FOUND' if pillow_available() else 'MISSING'} | optional crop/perceptual-hash enhancement")
    vi, ri = model_info(args.vision_model), model_info(args.reason_model)
    print(f"Vision model: {'FOUND' if vi.get('available') else 'MISSING'} | {args.vision_model}")
    print(f"Reason model: {'FOUND' if ri.get('available') else 'MISSING'} | {args.reason_model}")
    aux = load_matching_v140_aux(Path(args.reuse_v140_root), sha256_file(Path(args.source_pdf)) if args.source_pdf and Path(args.source_pdf).exists() else None)
    print(f"Matching v1.4.0 blind auxiliary: {'FOUND' if aux else 'NONE'} | records={len(aux)}")
    print("Prior hosted benchmark runtime input: NONE")
    print("Qdrant: OFF")
    return 0


def plan(args: argparse.Namespace) -> int:
    try:
        if args.source_images_root:
            source_kind = "images"
            records_count = len(discover_line_card_images(Path(args.source_images_root), args.image_name_regex))
            pdf_sha = None
        elif args.source_pdf:
            source_kind = "pdf_adapter"
            pdf = Path(args.source_pdf)
            if not pdf.exists():
                raise FileNotFoundError(pdf)
            records_count = pdf_page_count(pdf)
            pdf_sha = sha256_file(pdf)
        else:
            raise RuntimeError("Supply --source-images-root or --source-pdf")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    aux = load_matching_v140_aux(Path(args.reuse_v140_root), pdf_sha)
    print("# Nova DRL Power Supply Focused Evidence Recovery v1.4.1 — PLAN ONLY")
    print(f"Source mode:            {source_kind}")
    print(f"Source records/images:  {records_count}")
    print(f"Image discovery:        {'recursive Line Card filename filter' if source_kind == 'images' else 'PDF pages rendered as individual images'}")
    print(f"PDF render DPI:         {args.render_dpi if source_kind == 'pdf_adapter' else 'n/a (original image resolution)'}")
    print(f"Focused crop:           {'enabled when Pillow available' if args.use_focus_crop else 'disabled'}")
    print(f"v1.4.0 blind aux reuse: {len(aux)} matching whole-page transcriptions")
    print("Focused vision calls:   one per uncached source image/page; full source + optional enlarged repair region in same call")
    print("Duplicate handling:     exact image + optional perceptual hash + text candidates; 14B adjudication")
    print("Repair event identity:  same DRL 9-digit log => one event; otherwise deduped source record")
    print("Parts extraction:       one 14B extraction per repair event")
    print("Part normalization:     deterministic punctuation/case consolidation + fuzzy candidate blocks + 14B provisional grouping")
    print("Frequency counts:       Python; distinct repair events + explicit quantities")
    print("Prior hosted benchmark: NOT READ")
    print("Accepted facts:         0")
    print("Qdrant:                 OFF")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Nova DRL Power Supply Focused Evidence Recovery v1.4.1")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--source-images-root", help="Production mode: recursively discover individual Line Card images")
    src.add_argument("--source-pdf", help="Benchmark adapter only: render each PDF page to an image")
    ap.add_argument("--image-name-regex", default=DEFAULT_IMAGE_NAME_REGEX, help="Production image filename regex; default matches 'Line Card'")
    ap.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    ap.add_argument("--reuse-v140-root", default=str(DEFAULT_V140_ROOT), help="Optional blind v1.4.0 auxiliary transcription root; source hash must match")
    ap.add_argument("--vision-model", default=DEFAULT_VISION_MODEL)
    ap.add_argument("--reason-model", default=DEFAULT_REASON_MODEL)
    ap.add_argument("--vision-num-ctx", type=int, default=16384)
    ap.add_argument("--vision-num-predict", type=int, default=4096)
    ap.add_argument("--reason-num-ctx", type=int, default=16384)
    ap.add_argument("--extract-num-predict", type=int, default=3072)
    ap.add_argument("--normalize-num-predict", type=int, default=3072)
    ap.add_argument("--render-dpi", type=int, default=300)
    ap.add_argument("--use-focus-crop", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--duplicate-text-threshold", type=float, default=0.55)
    ap.add_argument("--duplicate-dhash-threshold", type=int, default=10)
    ap.add_argument("--normalize-candidate-threshold", type=float, default=0.72)
    ap.add_argument("--normalize-batch-descriptors", type=int, default=60)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--focused-acquire-only", action="store_true")
    ap.add_argument("--force-focused-acquisition", action="store_true")
    ap.add_argument("--force-dedupe", action="store_true")
    ap.add_argument("--force-extraction", action="store_true")
    ap.add_argument("--force-normalize", action="store_true")
    args = ap.parse_args()

    if args.status:
        return status(args)
    if args.plan_only:
        return plan(args)
    if not args.source_images_root and not args.source_pdf:
        print("ERROR: specify --source-images-root (production) or --source-pdf (benchmark adapter)", file=sys.stderr)
        return 2

    output_root = Path(args.output_root); output_root.mkdir(parents=True, exist_ok=True)
    try:
        source_kind, source_records, pdf_sha = make_source_records(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 2
    vi, ri = model_info(args.vision_model), model_info(args.reason_model)
    if not vi.get("available"):
        print(f"ERROR: vision model not available: {args.vision_model}", file=sys.stderr); return 3
    if not ri.get("available") and not args.focused_acquire_only:
        print(f"ERROR: reason model not available: {args.reason_model}", file=sys.stderr); return 3
    aux = load_matching_v140_aux(Path(args.reuse_v140_root), pdf_sha)

    print("# Nova DRL Power Supply Focused Evidence Recovery v1.4.1")
    print("Operating mode: BLIND PROVISIONAL 80/20")
    print(f"Source mode: {source_kind}")
    print(f"Source records/images: {len(source_records)}")
    print(f"Vision model: {args.vision_model}")
    print(f"Reason model: {args.reason_model}")
    print(f"Matching v1.4.0 blind auxiliary records: {len(aux)}")
    print("Prior hosted benchmark read by runtime: NO")
    print("Accepted facts: 0")
    print("Qdrant: OFF")

    records = acquire_focused_evidence(args, source_records, aux)
    if args.focused_acquire_only:
        print("Focused acquisition complete; stopping because --focused-acquire-only was requested.")
        return 0
    dedupe = adjudicate_duplicates(args, records)
    extraction = extract_replacements(args, records, dedupe)
    family_map = normalize_part_families(args, extraction["mentions"])
    frequency = count_frequencies(extraction["mentions"], family_map)
    write_frequency_outputs(output_root, frequency)
    render_summary(args, source_kind, records, dedupe, extraction, frequency, len(aux))
    manifest = {
        "version": VERSION,
        "source_mode": source_kind,
        "source": str(args.source_images_root or args.source_pdf),
        "source_record_count": len(records),
        "source_pdf_sha256": pdf_sha,
        "duplicate_records_excluded": dedupe.get("duplicate_records_excluded"),
        "unique_scan_representative_count": dedupe.get("unique_record_count"),
        "repair_event_count": len(extraction.get("repair_events") or []),
        "replacement_mention_count": len(extraction.get("mentions") or []),
        "part_family_count": len(frequency),
        "v140_blind_aux_count": len(aux),
        "vision_model": model_info(args.vision_model),
        "reason_model": model_info(args.reason_model),
        "prior_hosted_benchmark_read": False,
        "automatic_fact_acceptance": False,
        "qdrant_entries_created": 0,
        "summary": str(output_root / "power_supply_focused_summary_v1_4_1.txt"),
        "frequency_json": str(output_root / "part_frequency_v1_4_1.json"),
        "frequency_csv": str(output_root / "part_frequency_v1_4_1.csv"),
    }
    save_json(output_root / "power_supply_focused_manifest_v1_4_1.json", manifest)
    print("\n# COMPLETE")
    print(f"Source records/images:      {len(records)}")
    print(f"Duplicate scans excluded:   {dedupe.get('duplicate_records_excluded')}")
    print(f"Unique scan representatives:{dedupe.get('unique_record_count')}")
    print(f"Distinct repair events:     {len(extraction.get('repair_events') or [])}")
    print(f"Replacement mentions:       {len(extraction.get('mentions') or [])}")
    print(f"Provisional part families:  {len(frequency)}")
    print("Accepted facts:             0")
    print("Qdrant:                     OFF")
    print(f"Summary: {output_root / 'power_supply_focused_summary_v1_4_1.txt'}")
    print(f"CSV:     {output_root / 'part_frequency_v1_4_1.csv'}")
    print(f"Manifest:{output_root / 'power_supply_focused_manifest_v1_4_1.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
