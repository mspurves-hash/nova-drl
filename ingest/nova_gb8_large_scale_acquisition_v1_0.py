#!/usr/bin/env python3
"""Nova DRL GB8 Large-Scale Acquisition Launcher v1.0

Runs the frozen v1.3.5.1 Whole Traveler Corpus Collector across every
matching top-level GB8-family folder under the tech-scan root while producing
one unified corpus manifest. The collector itself is not modified.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

VERSION = "1.0"
DEFAULT_SOURCE_ROOT = Path("/mnt/drl/000 folder for tech scans")
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/whole_traveler_corpus_v1_3_5_1")
DEFAULT_FOLDER_PREFIX = "RBT - GB8"
DEFAULT_MODEL = "qwen3-vl-drl:8b-q8-16k"


def load_collector(repo_root: Path):
    path = repo_root / "ingest" / "nova_traveler_reader_v1_3_5_1.py"
    if not path.exists():
        raise FileNotFoundError(f"Required frozen collector not found: {path}")
    spec = importlib.util.spec_from_file_location("nova_traveler_reader_v1_3_5_1", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load collector module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def select_unit_folders(source_root: Path, prefix: str) -> List[Path]:
    pfx = prefix.upper()
    return sorted(
        [p for p in source_root.iterdir() if p.is_dir() and p.name.upper().startswith(pfx)],
        key=lambda p: p.name.upper(),
    )


def discover_all(collector, folders: List[Path]) -> Tuple[List[Dict[str, Any]], List[Tuple[str, int]]]:
    items: List[Dict[str, Any]] = []
    counts: List[Tuple[str, int]] = []
    for folder in folders:
        found = collector.discover_travelers(folder)
        # Keep the exact per-folder relative path behavior used by the original pilot,
        # which allows existing v1.3.5.1 evidence records to be reused unchanged.
        for item in found:
            item["unit_folder"] = folder.name
            item["collection_source_root"] = str(folder)
        items.extend(found)
        counts.append((folder.name, len(found)))
    items.sort(key=lambda x: (str(x.get("unit_folder", "")).upper(), str(x.get("source_path", "")).upper()))
    return items, counts


def write_launcher_summary(output_root: Path, source_root: Path, prefix: str, folder_counts, records, interrupted: bool) -> None:
    status = {}
    actions = {}
    for rec in records:
        s = str(rec.get("vision_status") or rec.get("collection_status") or "unknown")
        status[s] = status.get(s, 0) + 1
        a = str(rec.get("collection_action") or rec.get("collection_status") or "unknown")
        actions[a] = actions.get(a, 0) + 1
    zero = [name for name, count in folder_counts if count == 0]
    lines = [
        f"# Nova DRL GB8 Large-Scale Acquisition Launcher v{VERSION}",
        "",
        f"Master source root:             {source_root}",
        f"Folder prefix:                  {prefix}",
        f"GB8-family folders selected:    {len(folder_counts)}",
        f"Traveler images discovered:     {sum(c for _, c in folder_counts)}",
        f"Travelers recorded this run:     {len(records)}",
        f"Interrupted:                     {'YES' if interrupted else 'NO'}",
        f"Status counts:                   {status}",
        f"Action counts:                   {actions}",
        f"Zero-Traveler folders:           {len(zero)}",
        "Accepted repair facts:         0",
        "Qdrant entries created:        0",
        "",
        "ZERO-TRAVELER FOLDERS",
        "---------------------",
    ]
    lines.extend(zero or ["None"])
    lines += ["", "FOLDER COUNTS", "-------------"]
    lines.extend(f"{count:4d} | {name}" for name, count in folder_counts)
    (output_root / "gb8_large_scale_summary_v1_0.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Nova DRL GB8 Large-Scale Acquisition Launcher v{VERSION}")
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--folder-prefix", default=DEFAULT_FOLDER_PREFIX)
    parser.add_argument("--expect-folders", type=int)
    parser.add_argument("--expect-travelers", type=int)
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--num-predict", type=int, default=8192)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    source_root = Path(args.source_root)
    output_root = Path(args.output_root)

    if not source_root.exists():
        print(f"ERROR: source root not found: {source_root}", file=sys.stderr)
        return 2

    collector = load_collector(repo_root)
    if collector.source_is_under_output(source_root, output_root):
        print("ERROR: output root may not be inside the DRL source tree.", file=sys.stderr)
        return 2

    folders = select_unit_folders(source_root, args.folder_prefix)
    items, folder_counts = discover_all(collector, folders)

    if args.expect_folders is not None and len(folders) != args.expect_folders:
        print(f"ERROR: expected {args.expect_folders} folders but selected {len(folders)}. No vision calls were made.", file=sys.stderr)
        return 3
    if args.expect_travelers is not None and len(items) != args.expect_travelers:
        print(f"ERROR: expected {args.expect_travelers} Travelers but discovered {len(items)}. No vision calls were made.", file=sys.stderr)
        return 3

    if args.limit is not None:
        items = items[: max(0, args.limit)]

    output_root.mkdir(parents=True, exist_ok=True)
    model_info = collector.get_ollama_model_info(args.model)
    if not args.inventory_only and model_info.get("available") is False:
        print(f"ERROR: Ollama model is not installed: {args.model}", file=sys.stderr)
        return 4

    collector_args = SimpleNamespace(
        inventory_only=bool(args.inventory_only),
        model=args.model,
        num_ctx=args.num_ctx,
        num_predict=args.num_predict,
        timeout=args.timeout,
        force=bool(args.force),
    )

    print(f"# Nova DRL GB8 Large-Scale Acquisition Launcher v{VERSION}")
    print(f"Master source root: {source_root}")
    print(f"Folder prefix:      {args.folder_prefix}")
    print(f"Folders selected:   {len(folders)}")
    print(f"Travelers found:    {len(items)}")
    print(f"Output root:        {output_root}")
    print(f"Inventory only:     {'YES' if args.inventory_only else 'NO'}")
    print(f"Model:              {args.model}")
    print("Classification:     NONE")
    print("Qdrant:             OFF")
    print()

    records: List[Dict[str, Any]] = []
    interrupted = False
    try:
        total = len(items)
        for index, item in enumerate(items, 1):
            folder_root = Path(item.pop("collection_source_root"))
            print(f"[{index}/{total}] {item['unit_folder']} | {item['log_number']} {item['variant']} | {item['filename']}")
            rec = collector.collect_one(item, folder_root, output_root, model_info, collector_args)
            records.append(rec)
            print(f"    {rec.get('vision_status')} | {rec.get('collection_action') or rec.get('collection_status')}")
            # Evidence is written per record immediately. Refresh unified manifest every 10
            # records to reduce repeated large JSON rewrites during 461-record acquisition.
            if index % 10 == 0:
                collector.write_manifest(output_root, source_root, records, model_info, collector_args, interrupted=False)
                write_launcher_summary(output_root, source_root, args.folder_prefix, folder_counts, records, interrupted=False)
    except KeyboardInterrupt:
        interrupted = True
        print("\nInterrupted by user. Completed evidence records are preserved; rerun the same command to resume/reuse them.", file=sys.stderr)
    finally:
        manifest = collector.write_manifest(output_root, source_root, records, model_info, collector_args, interrupted=interrupted)
        write_launcher_summary(output_root, source_root, args.folder_prefix, folder_counts, records, interrupted=interrupted)

    print()
    print(f"Folders selected:                {len(folders)}")
    print(f"Travelers recorded:              {manifest['traveler_count']}")
    print(f"Status counts:                   {manifest['status_counts']}")
    print(f"Exact duplicate hash groups:     {manifest['exact_duplicate_hash_group_count']}")
    print("Accepted repair facts:           0")
    print("Qdrant entries created:          0")
    print(f"Unified manifest: {output_root / 'corpus_manifest_v1_3_5_1.json'}")
    print(f"Launcher summary: {output_root / 'gb8_large_scale_summary_v1_0.txt'}")
    return 130 if interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())
