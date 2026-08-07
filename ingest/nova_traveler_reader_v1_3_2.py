#!/usr/bin/env python3
"""
Nova DRL Traveler Reader v1.3.2 — Vision Transcriber

Reads the region crops and OCR metadata produced by v1.3.1, sends selected
crops to a local Ollama vision model, and saves Tesseract and vision output
side-by-side. It does not perform evidence fusion and does not write to Qdrant.

Source DRL files remain read-only. Outputs are written only into the local
v1.3.1 output tree under /opt/nova-drl/output.
"""

import argparse
import base64
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

VERSION = "1.3.2"
DEFAULT_MODEL = "minicpm-v:latest"
DEFAULT_REGIONS = ["repairs_replacements", "special_notes"]
ALL_REGIONS = [
    "identity",
    "packaging_status",
    "repairs_replacements",
    "special_notes",
    "final_test",
    "shipping_final_ok",
]

VISION_PROMPT = """You are transcribing a cropped region from a Direct Repair Laboratories repair traveler.

Transcribe only handwritten or technician-entered content visibly present in the image.

Rules:
1. Do not summarize.
2. Do not explain.
3. Do not infer intent.
4. Do not correct spelling unless the original text is clearly readable.
5. Preserve part names, axis names, error codes, numbers, initials, and dates exactly as visible.
6. Ignore preprinted form labels unless needed to understand placement.
7. If text is not confidently readable, write [unclear].
8. Do not invent dates, initials, parts, or repair details.
9. Return plain text only.

Region: {region}
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def ollama_tags() -> Optional[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:11434/api/tags", timeout=5
        ) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def resolve_model(requested: str) -> Optional[str]:
    data = ollama_tags()
    if not data:
        return None

    names = [m.get("name", "") for m in data.get("models", [])]
    if requested in names:
        return requested

    if ":" not in requested:
        for name in names:
            if name == requested or name.startswith(requested + ":"):
                return name
    return None


def image_to_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def call_ollama_vision(
    model: str,
    prompt: str,
    image_path: Path,
    timeout: int = 300,
) -> Dict[str, Any]:
    payload = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "images": [image_to_base64(image_path)],
            "stream": False,
            "options": {"temperature": 0},
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        "http://127.0.0.1:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        return {
            "status": "ok",
            "response": body.get("response", ""),
            "done_reason": body.get("done_reason"),
            "total_duration": body.get("total_duration"),
            "load_duration": body.get("load_duration"),
            "prompt_eval_count": body.get("prompt_eval_count"),
            "eval_count": body.get("eval_count"),
            "warning": None,
        }
    except Exception as exc:
        return {
            "status": "error",
            "response": "",
            "done_reason": None,
            "total_duration": None,
            "load_duration": None,
            "prompt_eval_count": None,
            "eval_count": None,
            "warning": str(exc),
        }


def discover_log_directories(root: Path, selected_logs: List[str]) -> List[Path]:
    if not root.exists() or not root.is_dir():
        raise ValueError(f"v1.3.1 output folder not found: {root}")

    selected = set(selected_logs)
    results = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or not re.fullmatch(r"\d{9}", child.name):
            continue
        if selected and child.name not in selected:
            continue
        results.append(child)
    return results


def transcribe_log(
    log_dir: Path,
    regions: List[str],
    model: str,
    timeout: int,
) -> Dict[str, Any]:
    prior = load_json(log_dir / "traveler_regions.json")
    if not prior:
        return {
            "reader_version": VERSION,
            "processed_at_utc": now_utc(),
            "log_number": log_dir.name,
            "status": "missing_v1_3_1_data",
            "regions": {},
        }

    region_outputs: Dict[str, Any] = {}
    for region_name in regions:
        prior_region = prior.get("regions", {}).get(region_name)
        if not prior_region:
            region_outputs[region_name] = {
                "status": "region_not_found",
                "vision_text": "",
            }
            continue

        crop_path = Path(prior_region.get("crop_path", ""))
        if not crop_path.exists():
            region_outputs[region_name] = {
                "status": "crop_not_found",
                "crop_path": str(crop_path),
                "vision_text": "",
            }
            continue

        prompt = VISION_PROMPT.format(region=region_name)
        result = call_ollama_vision(model, prompt, crop_path, timeout=timeout)

        region_outputs[region_name] = {
            "status": result["status"],
            "crop_path": str(crop_path),
            "model": model,
            "prompt": prompt,
            "tesseract_selected_psm": prior_region.get("selected_psm"),
            "tesseract_selected_score": prior_region.get("selected_score"),
            "tesseract_text": prior_region.get("selected_text", ""),
            "vision_text": result.get("response", ""),
            "ollama_meta": {
                "done_reason": result.get("done_reason"),
                "total_duration": result.get("total_duration"),
                "load_duration": result.get("load_duration"),
                "prompt_eval_count": result.get("prompt_eval_count"),
                "eval_count": result.get("eval_count"),
                "warning": result.get("warning"),
            },
        }

    return {
        "reader_version": VERSION,
        "processed_at_utc": now_utc(),
        "log_number": log_dir.name,
        "source_path": prior.get("source_path"),
        "relative_path": prior.get("relative_path"),
        "traveler_kind": prior.get("traveler_kind"),
        "warranty": prior.get("warranty"),
        "status": "ok",
        "regions": region_outputs,
    }


def write_log_outputs(log_dir: Path, record: Dict[str, Any]) -> None:
    (log_dir / "vision_transcription_v1_3_2.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines = [
        f"NOVA DRL TRAVELER READER v{VERSION}",
        f"Log: {record.get('log_number')}",
        f"Source: {record.get('source_path')}",
        "",
    ]

    for region_name, data in record.get("regions", {}).items():
        lines.extend(
            [
                "=" * 72,
                region_name.upper(),
                f"Status: {data.get('status')}",
                f"Model: {data.get('model')}",
                "-" * 72,
                "TESSERACT:",
                data.get("tesseract_text", "").rstrip(),
                "",
                "VISION:",
                data.get("vision_text", "").rstrip(),
                "",
            ]
        )

    (log_dir / "vision_transcription_v1_3_2.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def run_serial(
    root: Path,
    regions: List[str],
    model: str,
    selected_logs: List[str],
    timeout: int,
) -> Dict[str, Any]:
    log_dirs = discover_log_directories(root, selected_logs)
    records = []

    for log_dir in log_dirs:
        print(f"Processing log {log_dir.name} ...", flush=True)
        record = transcribe_log(log_dir, regions, model, timeout)
        if record.get("status") == "ok":
            write_log_outputs(log_dir, record)
        records.append(record)

    summary = {
        "reader_version": VERSION,
        "processed_at_utc": now_utc(),
        "form_output_root": str(root),
        "model": model,
        "regions_requested": regions,
        "selected_logs": selected_logs,
        "log_count": len(records),
        "logs_ok": sum(1 for r in records if r.get("status") == "ok"),
        "region_success_count": sum(
            1
            for record in records
            for data in record.get("regions", {}).values()
            if data.get("status") == "ok"
        ),
        "records": [
            {
                "log_number": r.get("log_number"),
                "status": r.get("status"),
                "regions": {
                    name: data.get("status")
                    for name, data in r.get("regions", {}).items()
                },
            }
            for r in records
        ],
    }

    (root / "vision_transcription_v1_3_2_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )

    text_lines = [
        f"NOVA DRL TRAVELER READER v{VERSION}",
        "=" * 72,
        f"Model: {model}",
        f"Logs found: {summary['log_count']}",
        f"Logs processed: {summary['logs_ok']}",
        f"Successful regions: {summary['region_success_count']}",
        f"Regions: {', '.join(regions)}",
        "",
    ]
    for row in summary["records"]:
        region_status = " ".join(
            f"{name}={status}" for name, status in row["regions"].items()
        )
        text_lines.append(
            f"{row['log_number']} status={row['status']} {region_status}"
        )

    (root / "vision_transcription_v1_3_2_summary.txt").write_text(
        "\n".join(text_lines) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Nova DRL Traveler Reader v1.3.2 — Vision Transcriber"
    )
    parser.add_argument(
        "form_output_root",
        help="The v1.3.1 output folder for one serial-number folder.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--regions",
        nargs="+",
        default=DEFAULT_REGIONS,
        choices=ALL_REGIONS,
    )
    parser.add_argument(
        "--log",
        action="append",
        default=[],
        dest="selected_logs",
        help="Process only this nine-digit log number. Repeat for multiple logs.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Per-region Ollama timeout in seconds. Default: 300",
    )
    args = parser.parse_args()

    model = resolve_model(args.model)
    if not model:
        print(f"ERROR: Ollama model not found: {args.model}", file=sys.stderr)
        print("Run: ollama list", file=sys.stderr)
        return 2

    invalid_logs = [x for x in args.selected_logs if not re.fullmatch(r"\d{9}", x)]
    if invalid_logs:
        print(
            "ERROR: --log requires nine digits: " + ", ".join(invalid_logs),
            file=sys.stderr,
        )
        return 2

    try:
        summary = run_serial(
            Path(args.form_output_root).expanduser().resolve(),
            args.regions,
            model,
            args.selected_logs,
            args.timeout,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print()
    print(f"Nova DRL Traveler Reader v{VERSION}")
    print("=" * 55)
    print(f"Model:              {summary['model']}")
    print(f"Logs found:         {summary['log_count']}")
    print(f"Logs processed:     {summary['logs_ok']}")
    print(f"Successful regions: {summary['region_success_count']}")
    print(f"Regions:            {', '.join(summary['regions_requested'])}")
    print()
    for row in summary["records"]:
        print(f"{row['log_number']} status={row['status']} {row['regions']}")
    print()
    print("VISION TRANSCRIPTION COMPLETE.")
    print("No DRL source files were changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
