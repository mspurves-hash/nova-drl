#!/usr/bin/env python3
"""
Nova DRL Whole Traveler Corpus Collector v1.3.5.1

Acquisition-first architecture:
- Discover every original/warranty Traveler image under a requested source tree.
- Preserve the ORIGINAL FULL image as the authority; never crop or modify source files.
- Send the complete source image directly to Qwen3-VL for literal whole-page transcription.
- Preserve the raw model response exactly as returned.
- Record source hashes, source paths, folder context, model/digest/context, and audit metrics.
- Flag exact duplicate source hashes, but do not suppress or classify them during acquisition.
- Perform NO repair-action, parts, terminology, diagnostic/root-cause, testing/final-result,
  or relevance classification in this collector.
- Accept zero facts automatically and create zero Qdrant entries.

The corpus is acquired first. Sorting/normalization/reasoning happen in a later corpus stage.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageOps

VERSION = "1.3.5.1"
DEFAULT_MODEL = "qwen3-vl-drl:8b-q8-16k"
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/whole_traveler_corpus_v1_3_5_1")
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
TRAVELER_RE = re.compile(r"(?i)^(?P<log>\d{9}).*line\s*card.*(?P<variant>original|warranty).*$")


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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_name(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text).strip())
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "unknown"


def source_is_under_output(source_root: Path, output_root: Path) -> bool:
    """Reject configurations that could write into the read-only source tree."""
    try:
        out = output_root.resolve(strict=False)
        src = source_root.resolve(strict=False)
        return out == src or src in out.parents
    except Exception:
        return False


def discover_travelers(source_root: Path, log_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    if not source_root.exists():
        raise FileNotFoundError(f"Source root not found: {source_root}")

    for path in sorted(source_root.rglob("*"), key=lambda p: str(p).lower()):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        m = TRAVELER_RE.match(path.name)
        if not m:
            continue
        log_number = m.group("log")
        if log_filter and log_number != str(log_filter):
            continue
        variant = m.group("variant").lower()
        try:
            rel = path.relative_to(source_root)
        except Exception:
            rel = Path(path.name)
        rel_parts = rel.parts
        if len(rel_parts) > 1:
            unit_folder = rel_parts[0]
        else:
            unit_folder = source_root.name
        records.append({
            "log_number": log_number,
            "variant": variant,
            "source_path": str(path),
            "source_relative_path": str(rel),
            "unit_folder": unit_folder,
            "parent_folder": path.parent.name,
            "filename": path.name,
        })
    return records


def read_image_metadata(path: Path) -> Dict[str, Any]:
    try:
        with Image.open(path) as im:
            original_size = list(im.size)
            fmt = im.format
            exif_orientation = None
            try:
                exif_orientation = im.getexif().get(274)
            except Exception:
                pass
            normalized = ImageOps.exif_transpose(im)
            normalized_size = list(normalized.size)
        return {
            "image_format": fmt,
            "source_image_size": original_size,
            "exif_orientation": exif_orientation,
            "exif_normalized_size_audit_only": normalized_size,
            "image_sent_to_model": "original_source_bytes",
            "crop_used": False,
            "derivative_used_for_model": False,
        }
    except Exception as exc:
        return {
            "image_metadata_error": str(exc),
            "image_sent_to_model": "original_source_bytes",
            "crop_used": False,
            "derivative_used_for_model": False,
        }


def ollama_version() -> Optional[str]:
    try:
        proc = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=10)
        text = (proc.stdout or proc.stderr or "").strip()
        return text or None
    except Exception:
        return None


def get_ollama_model_info(model: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {"requested_model": model, "ollama_version": ollama_version()}
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        models = data.get("models") or []
        selected = None
        for item in models:
            if item.get("name") == model or item.get("model") == model:
                selected = item
                break
        if selected:
            info.update({
                "resolved_name": selected.get("name") or selected.get("model"),
                "digest": selected.get("digest"),
                "size_bytes": selected.get("size"),
                "modified_at": selected.get("modified_at"),
                "details": selected.get("details"),
                "available": True,
            })
        else:
            info["available"] = False
    except Exception as exc:
        info.update({"available": None, "model_info_error": str(exc)})
    return info


def call_ollama_vision(image_path: Path, model: str, num_ctx: int, num_predict: int, timeout: int) -> str:
    """Send original image bytes directly to Qwen3-VL. Return response text unchanged."""
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {
        "model": model,
        "prompt": TRANSCRIPTION_PROMPT,
        "images": [encoded],
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


def transcription_audit(text: str) -> Dict[str, Any]:
    """Audit-only signals. Never alter or reject raw transcription from these metrics."""
    raw_lines = text.splitlines()
    nonempty = [x.strip() for x in raw_lines if x.strip()]
    counts = Counter(nonempty)
    most_common_line = None
    most_common_count = 0
    if counts:
        most_common_line, most_common_count = counts.most_common(1)[0]
    repeated_occurrences = sum(max(0, count - 1) for count in counts.values())
    repetition_fraction = (repeated_occurrences / len(nonempty)) if nonempty else 0.0
    repetition_flag = bool(most_common_count >= 5 or (len(nonempty) >= 10 and repetition_fraction >= 0.35))
    return {
        "character_count": len(text),
        "line_count": len(raw_lines),
        "nonempty_line_count": len(nonempty),
        "unclear_marker_count": text.lower().count("[unclear]"),
        "most_repeated_nonempty_line": most_common_line,
        "most_repeated_nonempty_line_count": most_common_count,
        "repeated_line_occurrence_fraction": round(repetition_fraction, 4),
        "possible_runaway_repetition": repetition_flag,
        "audit_only": True,
    }


def output_paths(output_root: Path, item: Dict[str, Any], source_sha: str) -> Dict[str, Path]:
    unit = safe_name(item["unit_folder"])
    log = item["log_number"]
    rid_basis = f"{item['source_relative_path']}\n{source_sha}"
    record_id = hashlib.sha256(rid_basis.encode("utf-8")).hexdigest()[:16]
    record_dir = output_root / unit / log / record_id
    return {
        "record_dir": record_dir,
        "json": record_dir / "traveler_evidence.json",
        "raw": record_dir / "raw_qwen3vl_transcription.txt",
        "source_ref": record_dir / "source_reference.txt",
    }


def existing_record_reusable(path: Path, source_sha: str, model: str, prompt_sha: str, num_ctx: int, num_predict: int) -> bool:
    if not path.exists():
        return False
    try:
        data = load_json(path)
    except Exception:
        return False
    if data.get("source_sha256") != source_sha:
        return False
    if data.get("model", {}).get("requested_model") != model:
        return False
    if data.get("prompt_sha256") != prompt_sha:
        return False
    if int(data.get("model", {}).get("num_ctx") or -1) != int(num_ctx):
        return False
    if int(data.get("model", {}).get("num_predict") or -1) != int(num_predict):
        return False
    raw_path = data.get("raw_transcription_path")
    return bool(data.get("vision_status") == "ok" and raw_path and Path(raw_path).exists())


def build_inventory_record(item: Dict[str, Any], source_root: Path, source_sha: str) -> Dict[str, Any]:
    p = Path(item["source_path"])
    return {
        "log_number": item["log_number"],
        "variant": item["variant"],
        "unit_folder": item["unit_folder"],
        "parent_folder": item["parent_folder"],
        "filename": item["filename"],
        "source_path": str(p),
        "source_relative_path": item["source_relative_path"],
        "source_root": str(source_root),
        "source_sha256": source_sha,
        "source_size_bytes": p.stat().st_size,
        **read_image_metadata(p),
    }


def write_manifest(output_root: Path, source_root: Path, records: Sequence[Dict[str, Any]], model_info: Dict[str, Any], args: argparse.Namespace, interrupted: bool = False) -> Dict[str, Any]:
    by_hash: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rec in records:
        if rec.get("source_sha256"):
            by_hash[rec["source_sha256"]].append(rec)
    duplicate_groups = []
    for sha, group in sorted(by_hash.items()):
        if len(group) > 1:
            duplicate_groups.append({
                "sha256": sha,
                "count": len(group),
                "sources": [x.get("source_path") for x in group],
                "logs": [x.get("log_number") for x in group],
                "policy": "flag_only_during_acquisition_do_not_suppress",
            })

    status_counts = Counter(str(x.get("vision_status") or x.get("collection_status") or "unknown") for x in records)
    manifest = {
        "collector_version": VERSION,
        "architecture": "acquire_complete_traveler_corpus_first_sort_later",
        "source_root": str(source_root),
        "output_root": str(output_root),
        "inventory_only": bool(args.inventory_only),
        "interrupted": bool(interrupted),
        "model": model_info,
        "num_ctx": args.num_ctx,
        "num_predict": args.num_predict,
        "prompt_sha256": sha256_text(TRANSCRIPTION_PROMPT),
        "traveler_count": len(records),
        "status_counts": dict(status_counts),
        "exact_duplicate_hash_group_count": len(duplicate_groups),
        "exact_duplicate_hash_groups": duplicate_groups,
        "records": list(records),
        "classification_performed": False,
        "automatic_fact_acceptance": False,
        "accepted_fact_count": 0,
        "qdrant_write_enabled": False,
        "qdrant_entries_created": 0,
        "downstream_policy": {
            "sort_after_collection": True,
            "preserve_raw_transcription": True,
            "preserve_unusual_shop_language": True,
            "do_not_infer_unstated_quantities": True,
            "deduplicate_before_future_frequency_counts": True,
            "keep_ambiguous_variants_separate_until_supported": True,
        },
    }
    save_json(output_root / "corpus_manifest_v1_3_5_1.json", manifest)
    with (output_root / "corpus_manifest_v1_3_5_1.jsonl").open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    lines = [
        f"# Nova DRL Whole Traveler Corpus Collector v{VERSION}",
        "",
        f"Source root:                    {source_root}",
        f"Output root:                    {output_root}",
        f"Inventory only:                 {'YES' if args.inventory_only else 'NO'}",
        f"Interrupted:                    {'YES' if interrupted else 'NO'}",
        f"Travelers acquired/inventory:  {len(records)}",
        f"Status counts:                  {dict(status_counts)}",
        f"Exact duplicate hash groups:    {len(duplicate_groups)}",
        f"Model:                          {model_info.get('requested_model')}",
        f"Model digest:                   {model_info.get('digest')}",
        f"Context:                        {args.num_ctx}",
        "Classification performed:       NO",
        "Accepted repair facts:          0",
        "Qdrant entries created:         0",
        "",
        "ACQUISITION POLICY",
        "------------------",
        "Original full Traveler is evidence authority: YES",
        "Source image cropped for model: NO",
        "Relevance boxes used: NO",
        "Repair/parts/diagnostic sorting during collection: NO",
        "Raw Qwen3-VL response preserved exactly: YES",
        "Duplicate scans suppressed during acquisition: NO",
        "Duplicate scans flagged by exact SHA-256: YES",
        "Future frequency counts exclude approved duplicate scans: POLICY YES",
        "Source files modified: NO",
        "",
        "RECORDS",
        "-------",
    ]
    for rec in records:
        lines.append(f"{rec.get('log_number')} | {rec.get('variant')} | {rec.get('vision_status') or rec.get('collection_status')} | {rec.get('source_path')}")
    (output_root / "corpus_summary_v1_3_5_1.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def collect_one(item: Dict[str, Any], source_root: Path, output_root: Path, model_info: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    source_path = Path(item["source_path"])
    source_sha = sha256_file(source_path)
    inventory = build_inventory_record(item, source_root, source_sha)
    paths = output_paths(output_root, item, source_sha)
    prompt_sha = sha256_text(TRANSCRIPTION_PROMPT)

    record: Dict[str, Any] = {
        "collector_version": VERSION,
        "architecture": "whole_traveler_corpus_acquisition_only",
        **inventory,
        "record_id": paths["record_dir"].name,
        "record_dir": str(paths["record_dir"]),
        "prompt_sha256": prompt_sha,
        "model": {
            **model_info,
            "num_ctx": args.num_ctx,
            "num_predict": args.num_predict,
            "temperature": 0,
        },
        "source_modified": False,
        "classification_performed": False,
        "automatic_fact_acceptance": False,
        "accepted_fact_count": 0,
        "qdrant_entries_created": 0,
    }

    paths["record_dir"].mkdir(parents=True, exist_ok=True)
    paths["source_ref"].write_text(str(source_path) + "\n" + source_sha + "\n", encoding="utf-8")

    if args.inventory_only:
        record.update({
            "collection_status": "inventory_only",
            "vision_status": "not_run_inventory_only",
            "raw_transcription_path": None,
            "raw_transcription_sha256": None,
            "transcription_audit": None,
        })
        save_json(paths["json"], record)
        return record

    if not args.force and existing_record_reusable(paths["json"], source_sha, args.model, prompt_sha, args.num_ctx, args.num_predict):
        old = load_json(paths["json"])
        old["collection_action"] = "reused_existing"
        return old

    started = time.time()
    try:
        raw = call_ollama_vision(source_path, args.model, args.num_ctx, args.num_predict, args.timeout)
        paths["raw"].write_text(raw, encoding="utf-8")
        record.update({
            "collection_status": "collected",
            "collection_action": "vision_run",
            "vision_status": "ok" if raw.strip() else "empty_response",
            "raw_transcription_path": str(paths["raw"]),
            "raw_transcription_sha256": sha256_text(raw),
            "transcription_audit": transcription_audit(raw),
            "elapsed_seconds": round(time.time() - started, 3),
        })
    except Exception as exc:
        record.update({
            "collection_status": "review_required",
            "collection_action": "vision_run",
            "vision_status": "vision_error",
            "vision_error": str(exc),
            "raw_transcription_path": None,
            "raw_transcription_sha256": None,
            "transcription_audit": None,
            "elapsed_seconds": round(time.time() - started, 3),
        })
    save_json(paths["json"], record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Nova DRL Whole Traveler Corpus Collector v{VERSION}")
    parser.add_argument("source_root", help="Read-only DRL source folder to scan recursively for *Line Card Original/Warranty* images")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT), help=f"Writable corpus output root (default: {DEFAULT_OUTPUT_ROOT})")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama multimodal model (default: {DEFAULT_MODEL})")
    parser.add_argument("--num-ctx", type=int, default=16384, help="Ollama context size (default: 16384)")
    parser.add_argument("--num-predict", type=int, default=8192, help="Maximum transcription output tokens (default: 8192)")
    parser.add_argument("--timeout", type=int, default=1800, help="Per-Traveler Ollama request timeout seconds (default: 1800)")
    parser.add_argument("--inventory-only", "--discover-only", dest="inventory_only", action="store_true", help="Discover/hash Travelers only; do not run Qwen3-VL")
    parser.add_argument("--expect-travelers", type=int, help="Fail before vision if discovered Traveler count does not match")
    parser.add_argument("--log", help="Optional single DRL log filter for validation")
    parser.add_argument("--limit", type=int, help="Optional maximum number of discovered Travelers to process")
    parser.add_argument("--force", action="store_true", help="Re-run vision even when matching raw evidence already exists")
    args = parser.parse_args()

    source_root = Path(args.source_root)
    output_root = Path(args.output_root)

    if not source_root.exists():
        print(f"ERROR: source root not found: {source_root}", file=sys.stderr)
        return 2
    if source_is_under_output(source_root, output_root):
        print("ERROR: output root may not be inside the DRL source tree.", file=sys.stderr)
        return 2

    items = discover_travelers(source_root, args.log)
    if args.limit is not None:
        items = items[: max(0, args.limit)]

    if args.expect_travelers is not None and len(items) != args.expect_travelers:
        print(f"ERROR: expected {args.expect_travelers} Travelers but discovered {len(items)}. No vision calls were made.", file=sys.stderr)
        for item in items:
            print(f"  {item['log_number']} | {item['variant']} | {item['source_path']}", file=sys.stderr)
        return 3

    output_root.mkdir(parents=True, exist_ok=True)
    model_info = get_ollama_model_info(args.model)
    if not args.inventory_only and model_info.get("available") is False:
        print(f"ERROR: Ollama model is not installed: {args.model}", file=sys.stderr)
        return 4

    print(f"# Nova DRL Whole Traveler Corpus Collector v{VERSION}")
    print(f"Source root:       {source_root}")
    print(f"Output root:       {output_root}")
    print(f"Travelers found:   {len(items)}")
    print(f"Inventory only:    {'YES' if args.inventory_only else 'NO'}")
    print(f"Model:             {args.model}")
    print("Classification:    NONE")
    print("Qdrant:            OFF")
    print()

    records: List[Dict[str, Any]] = []
    interrupted = False
    try:
        for index, item in enumerate(items, 1):
            print(f"[{index}/{len(items)}] {item['log_number']} {item['variant']} | {item['filename']}")
            rec = collect_one(item, source_root, output_root, model_info, args)
            records.append(rec)
            print(f"    {rec.get('vision_status')} | {rec.get('collection_action') or rec.get('collection_status')}")
            write_manifest(output_root, source_root, records, model_info, args, interrupted=False)
    except KeyboardInterrupt:
        interrupted = True
        print("\nInterrupted by user. Completed records are preserved.", file=sys.stderr)
    finally:
        manifest = write_manifest(output_root, source_root, records, model_info, args, interrupted=interrupted)

    print()
    print(f"Travelers recorded:             {manifest['traveler_count']}")
    print(f"Status counts:                  {manifest['status_counts']}")
    print(f"Exact duplicate hash groups:    {manifest['exact_duplicate_hash_group_count']}")
    print("Accepted repair facts:          0")
    print("Qdrant entries created:         0")
    print(f"Manifest: {output_root / 'corpus_manifest_v1_3_5_1.json'}")
    print(f"Summary:  {output_root / 'corpus_summary_v1_3_5_1.txt'}")
    return 130 if interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())
