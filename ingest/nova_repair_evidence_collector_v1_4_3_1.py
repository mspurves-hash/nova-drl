#!/usr/bin/env python3
"""
Nova DRL Repair Evidence Collector v1.4.3.1
===========================================

Corrective scanned-document release built on the working v1.4.2 collector.

Why this corrective release exists
----------------------------------
The first v1.4.3 package was only a prototype inventory shell. It did not yet
perform the scanned-PDF page classification and OCR-quality gating described
for the release. v1.4.3.1 restores the complete v1.4.2 evidence collector and
adds the real page-classification/quality features.

New in v1.4.3.1
---------------
- Tries Tesseract PSM 3, 4, 6, and 11 for each scanned PDF page.
- Selects OCR using semantic quality rather than character quantity alone.
- Classifies known DRL scanned pages:
    * DRL internal robot checklist
    * DRL acceptance/robot test report
    * unknown scanned document
- Separates probable static-template content from event-field candidates.
- Extracts provisional serial, RMA, log, and complaint candidates.
- Marks low-quality OCR as review-required instead of simply "ok".
- Preserves every OCR pass, rendered page, selected text, and manifest.
- Keeps accepted_as_repair_fact=False and creates no Qdrant entry.

The production NAS remains read-only.
"""

import argparse
import importlib.util
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

VERSION = "1.4.3.1"

BASE_PATH = Path(__file__).resolve().with_name(
    "nova_repair_evidence_collector_v1_4_2.py"
)
if not BASE_PATH.exists():
    raise RuntimeError(
        "Required base module is missing: {}. "
        "Keep v1.4.2 in the ingest directory.".format(BASE_PATH)
    )

spec = importlib.util.spec_from_file_location("nova_v142_base", str(BASE_PATH))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

# Make every v1.4.2 output helper use the corrective release version.
base.VERSION = VERSION

VOWELS = set("aeiouy")
KNOWN_DRL_PHRASES = [
    "checklist for internal use at drl",
    "acceptance test report",
    "report genmark robot test",
    "customer problem",
    "customer fa",
    "serial number",
    "rma number",
    "log number",
    "initial checkout",
    "visual inspection",
    "functional test",
    "production information",
    "repair information",
    "page 1 of",
    "document drl",
]


def normalized_words(text):
    return re.findall(r"[A-Za-z][A-Za-z0-9#./+%()_'\-]{1,}", str(text or ""))


def suspicious_word(word):
    token = re.sub(r"[^a-z]", "", word.lower())
    if len(token) < 6:
        return False
    if not any(v in token for v in VOWELS):
        return True
    if len(token) >= 10 and len(set(token)) <= 4:
        return True
    if re.search(r"(.)\1{4,}", token):
        return True
    return False


def semantic_ocr_metrics(text):
    text = str(text or "")
    words = normalized_words(text)
    suspicious = [w for w in words if suspicious_word(w)]
    lower = text.lower()

    phrase_hits = sorted({
        phrase for phrase in KNOWN_DRL_PHRASES if phrase in lower
    })
    dotted_leaders = len(re.findall(r"\.{4,}", text))
    noisy_runs = len(re.findall(r"[^A-Za-z0-9\s.]{5,}", text))
    alpha_chars = sum(1 for c in text if c.isalpha())
    nonspace_chars = sum(1 for c in text if not c.isspace())
    alpha_ratio = alpha_chars / max(1, nonspace_chars)
    suspicious_ratio = len(suspicious) / max(1, len(words))
    line_count = sum(1 for line in text.splitlines() if line.strip())

    # Reward recognizable language and known DRL anchors. Penalize nonsense.
    score = (
        len(words) * 1.6
        + line_count * 1.0
        + len(phrase_hits) * 28
        + alpha_ratio * 40
        - len(suspicious) * 10
        - noisy_runs * 10
        - max(0, dotted_leaders - 10) * 0.5
    )

    if not text.strip():
        quality = "empty"
    elif len(words) >= 40 and suspicious_ratio <= 0.04 and alpha_ratio >= 0.55:
        quality = "good"
    elif len(words) >= 18 and suspicious_ratio <= 0.10 and alpha_ratio >= 0.45:
        quality = "usable"
    else:
        quality = "low"

    return {
        "semantic_score": round(score, 3),
        "quality": quality,
        "word_count": len(words),
        "line_count": line_count,
        "alpha_ratio": round(alpha_ratio, 4),
        "suspicious_word_count": len(suspicious),
        "suspicious_word_ratio": round(suspicious_ratio, 4),
        "suspicious_words": suspicious[:30],
        "known_phrase_hits": phrase_hits,
        "dotted_leader_count": dotted_leaders,
        "noisy_run_count": noisy_runs,
    }


def classify_scanned_page(text, page_number=None):
    lower = str(text or "").lower()

    if (
        "checklist for internal use at drl" in lower
        or ("rbt-gb8-mt" in lower and "initial checkout" in lower)
    ):
        family = "DRL_INTERNAL_CHECKLIST"
        if "initial checkout" in lower:
            page_type = "checklist_procedure_and_event_header"
        else:
            page_type = "checklist_page"
        static_template = True

    elif (
        "acceptance test report" in lower
        or "report genmark robot test" in lower
        or "drl148710" in lower
    ):
        family = "DRL_ACCEPTANCE_TEST_REPORT"
        if "contents" in lower and "production information" in lower:
            page_type = "test_report_front_matter_and_event_header"
        elif "functional test" in lower:
            page_type = "functional_test_page"
        elif "visual inspection" in lower:
            page_type = "visual_inspection_page"
        elif "completion" in lower:
            page_type = "completion_page"
        else:
            page_type = "test_report_page"
        static_template = True

    else:
        family = "UNKNOWN_SCANNED_DOCUMENT"
        page_type = "unknown_page"
        static_template = "unknown"

    return {
        "document_family": family,
        "page_type": page_type,
        "page_number": page_number,
        "static_template_content_present": static_template,
        "event_annotations_possible": True,
        "accepted_as_repair_fact": False,
    }


def _clean_field(value):
    value = re.sub(r"\s+", " ", str(value or "")).strip(" :|\t")
    return value or None


def extract_event_field_candidates(text, classification):
    """Conservative OCR candidates; none are accepted facts."""
    text = str(text or "")
    fields = []

    patterns = [
        ("serial_number", r"\bserial\s*(?:number|#)?\s*[:#]?\s*([A-Z0-9\-]{6,})"),
        ("rma_number", r"\brma\s*(?:number|#)?\s*[:#]?\s*([A-Z0-9\-]{3,})"),
        ("log_number", r"\b(?:log|traveler)\s*(?:number|#)?\s*[:#]?\s*(\d{9})"),
    ]

    for name, pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            fields.append({
                "field": name,
                "raw_value": _clean_field(match.group(1)),
                "source": "page_ocr",
                "confidence": "provisional",
                "human_review_required": True,
                "accepted_as_repair_fact": False,
            })

    # Capture a short candidate immediately after a complaint/FA label.
    complaint_patterns = [
        r"customer\s+problem(?:/symptom)?(?:\s+description)?\s*[:\-]?\s*([^\n]{3,160})",
        r"customer\s+fa\s*(?:\(summary\))?\s*[:\-]?\s*([^\n]{3,160})",
    ]
    for pattern in complaint_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = _clean_field(match.group(1))
            if value:
                fields.append({
                    "field": "customer_complaint",
                    "raw_value": value,
                    "source": "page_ocr",
                    "confidence": "provisional",
                    "human_review_required": True,
                    "accepted_as_repair_fact": False,
                })
                break

    return fields


def extract_scanned_pdf_text_v1431(
    path,
    artifact_dir,
    dpi=300,
    max_pages=50,
    document_role="document",
):
    """Render, classify, quality-gate, and OCR an image-only PDF."""
    path = Path(path)
    artifact_dir = Path(artifact_dir)
    deps = base.scanned_pdf_dependencies()
    missing = [
        name for name in ("pdftoppm", "tesseract") if not deps.get(name)
    ]
    if missing:
        return {
            "status": "dependency_missing",
            "method": "scanned_pdf_ocr",
            "text": "",
            "warning": "Missing required command(s): {}.".format(
                ", ".join(missing)
            ),
            "page_count": None,
            "pages_processed": 0,
            "page_records": [],
            "artifact_dir": None,
            "ocr_review_required": True,
        }

    total_pages = base.pdf_page_count(path)
    page_limit = max_pages if total_pages is None else min(total_pages, max_pages)

    ocr_dir = artifact_dir / "scanned_pdf_ocr"
    if ocr_dir.exists():
        shutil.rmtree(ocr_dir)
    pages_dir = ocr_dir / "pages"
    text_dir = ocr_dir / "page_text"
    passes_dir = ocr_dir / "ocr_passes"
    pages_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    passes_dir.mkdir(parents=True, exist_ok=True)

    prefix = pages_dir / "page"
    code, _, err = base.run_command(
        [
            "pdftoppm",
            "-f", "1",
            "-l", str(page_limit),
            "-r", str(dpi),
            "-gray",
            "-png",
            str(path),
            str(prefix),
        ],
        timeout=max(300, page_limit * 90),
    )
    if code != 0:
        return {
            "status": "error",
            "method": "scanned_pdf_ocr",
            "text": "",
            "warning": err.strip() or "pdftoppm failed.",
            "page_count": total_pages,
            "pages_processed": 0,
            "page_records": [],
            "artifact_dir": str(ocr_dir),
            "ocr_review_required": True,
        }

    page_images = sorted(
        pages_dir.glob("page-*.png"),
        key=base.page_number_from_rendered_name,
    )
    page_records = []
    combined_parts = []
    quality_counts = Counter()
    family_counts = Counter()
    all_candidates = []

    for page_image in page_images:
        page_number = base.page_number_from_rendered_name(page_image)
        passes = []

        for psm in (3, 4, 6, 11):
            code, text, stderr = base.run_command(
                [
                    "tesseract",
                    str(page_image),
                    "stdout",
                    "-l", "eng",
                    "--dpi", str(dpi),
                    "--psm", str(psm),
                ],
                timeout=240,
            )
            pass_path = passes_dir / "page_{:03d}_psm{}.txt".format(
                page_number, psm
            )
            pass_path.write_text(
                text if code == 0 else "",
                encoding="utf-8",
            )
            metrics = semantic_ocr_metrics(text if code == 0 else "")
            passes.append({
                "psm": psm,
                "status": "ok" if code == 0 else "error",
                "semantic_score": metrics["semantic_score"],
                "quality": metrics["quality"],
                "metrics": metrics,
                "text_path": str(pass_path),
                "warning": stderr.strip() or None,
                "text": text if code == 0 else "",
            })

        best = max(passes, key=lambda x: x["semantic_score"])
        classification = classify_scanned_page(
            best["text"],
            page_number=page_number,
        )
        candidates = extract_event_field_candidates(
            best["text"],
            classification,
        )

        selected_path = text_dir / "page_{:03d}.txt".format(page_number)
        selected_path.write_text(best["text"], encoding="utf-8")
        combined_parts.append(
            "\n===== PAGE {} | {} | {} =====\n{}".format(
                page_number,
                classification["document_family"],
                best["quality"],
                best["text"].rstrip(),
            )
        )

        quality_counts[best["quality"]] += 1
        family_counts[classification["document_family"]] += 1
        all_candidates.extend([
            {"page_number": page_number, **candidate}
            for candidate in candidates
        ])

        page_records.append({
            "page_number": page_number,
            "image_path": str(page_image),
            "selected_psm": best["psm"],
            "selected_semantic_score": best["semantic_score"],
            "selected_quality": best["quality"],
            "selected_metrics": best["metrics"],
            "selected_text_path": str(selected_path),
            "selected_char_count": len(best["text"]),
            "status": "ok" if best["text"].strip() else "empty",
            "classification": classification,
            "event_field_candidates": candidates,
            "eligible_for_evidence_comparison": (
                best["quality"] in ("good", "usable")
                and bool(candidates)
            ),
            "accepted_as_repair_fact": False,
            "passes": [
                {k: v for k, v in p.items() if k != "text"}
                for p in passes
            ],
        })

    combined_text = "\n".join(combined_parts).strip()
    combined_path = ocr_dir / "combined_ocr.txt"
    combined_path.write_text(
        combined_text + ("\n" if combined_text else ""),
        encoding="utf-8",
    )

    usable_pages = sum(
        quality_counts.get(key, 0) for key in ("good", "usable")
    )
    low_pages = quality_counts.get("low", 0)
    empty_pages = quality_counts.get("empty", 0)
    truncated = total_pages is not None and total_pages > page_limit

    if not combined_text:
        status = "empty_ocr"
        overall_quality = "empty"
    elif usable_pages:
        status = "ok"
        overall_quality = "usable"
    else:
        status = "review_required_low_quality"
        overall_quality = "low"

    semantics = base.document_semantics_for_role(document_role)
    manifest = {
        "collector_version": VERSION,
        "source_pdf": str(path),
        "document_role": document_role,
        "document_semantics": semantics,
        "dpi": dpi,
        "total_pages": total_pages,
        "pages_processed": len(page_records),
        "page_limit": max_pages,
        "truncated_by_page_limit": truncated,
        "combined_text_path": str(combined_path),
        "overall_ocr_quality": overall_quality,
        "quality_counts": dict(sorted(quality_counts.items())),
        "document_family_counts": dict(sorted(family_counts.items())),
        "event_field_candidates": all_candidates,
        "pages": page_records,
        "interpretation_status": "raw_ocr_and_provisional_candidates_only",
        "accepted_as_repair_fact": False,
        "qdrant_eligible": False,
    }
    manifest_path = ocr_dir / "scanned_pdf_ocr_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    warning_parts = [
        "Scanned-PDF OCR and extracted field candidates require human review."
    ]
    if low_pages or empty_pages:
        warning_parts.append(
            "{} low-quality and {} empty page(s) were blocked from automatic "
            "evidence fusion.".format(low_pages, empty_pages)
        )
    if semantics.get("static_template_content_present"):
        warning_parts.append(
            "Printed template instructions are not proof that work was completed."
        )
    if truncated:
        warning_parts.append(
            "Only the first {} of {} pages were processed.".format(
                page_limit, total_pages
            )
        )

    return {
        "status": status,
        "method": "scanned_pdf_ocr",
        "text": combined_text,
        "warning": " ".join(warning_parts),
        "page_count": total_pages,
        "pages_processed": len(page_records),
        "page_records": page_records,
        "artifact_dir": str(ocr_dir),
        "manifest_path": str(manifest_path),
        "combined_ocr_path": str(combined_path),
        "ocr_review_required": True,
        "truncated_by_page_limit": truncated,
        "overall_ocr_quality": overall_quality,
        "quality_counts": dict(sorted(quality_counts.items())),
        "document_family_counts": dict(sorted(family_counts.items())),
        "event_field_candidates": all_candidates,
    }


def write_extracted_text_v1431(base_dir, evidence_id, extraction):
    text = extraction.get("text", "")
    if not text.strip():
        return None
    if extraction.get("status") in {
        "error", "dependency_missing", "too_large",
        "unsupported", "not_attempted_image", "not_attempted_video",
        "excluded_system_metadata", "inventory_only", "deferred_by_log_filter",
    }:
        return None
    out_dir = Path(base_dir) / "extracted_text"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "{}.txt".format(evidence_id)
    path.write_text(text, encoding="utf-8")
    return str(path)


_original_render_event_text = base.render_event_text


def render_event_text_v1431(serial_meta, event):
    original = _original_render_event_text(serial_meta, event)
    lines = [
        "",
        "SCANNED DOCUMENT PAGE CLASSIFICATION AND QUALITY",
    ]
    found = False

    for record in event.get("evidence_files", []):
        extraction = record.get("extraction", {})
        if extraction.get("method") != "scanned_pdf_ocr":
            continue
        found = True
        lines.append("")
        lines.append("[{}] {}".format(
            record.get("role"),
            record.get("relative_path"),
        ))
        for page in extraction.get("page_records", []):
            classification = page.get("classification", {})
            lines.append(
                "  Page {}: family={} type={} OCR={} PSM={} score={}".format(
                    page.get("page_number"),
                    classification.get("document_family", "UNKNOWN"),
                    classification.get("page_type", "unknown"),
                    page.get("selected_quality", "unknown"),
                    page.get("selected_psm"),
                    page.get("selected_semantic_score"),
                )
            )
            lines.append(
                "    Static template present: {}".format(
                    classification.get("static_template_content_present")
                )
            )
            lines.append(
                "    Event candidates: {}".format(
                    len(page.get("event_field_candidates", []))
                )
            )
            for candidate in page.get("event_field_candidates", []):
                lines.append(
                    "      - {} = {} [PROVISIONAL; REVIEW REQUIRED]".format(
                        candidate.get("field"),
                        candidate.get("raw_value"),
                    )
                )
            lines.append(
                "    Eligible for evidence comparison: {}".format(
                    "YES" if page.get("eligible_for_evidence_comparison") else "NO"
                )
            )
            lines.append("    Accepted as repair fact: NO")

    if not found:
        lines.append("  None")

    marker = "\nSYSTEM METADATA (ACCOUNTED, EXCLUDED FROM REPAIR EVIDENCE)"
    insert = "\n".join(lines) + "\n"
    if marker in original:
        return original.replace(marker, insert + marker, 1)
    return original + insert


# Patch the proven v1.4.2 collector in memory.
base.extract_scanned_pdf_text = extract_scanned_pdf_text_v1431
base.write_extracted_text = write_extracted_text_v1431
base.render_event_text = render_event_text_v1431


def add_quality_summary(summary):
    quality = Counter()
    families = Counter()
    candidates = 0
    comparison_eligible = 0

    records = []
    for event in summary.get("repair_events", []):
        records.extend(event.get("evidence_files", []))
    records.extend(summary.get("unit_level_evidence", []))
    records.extend(summary.get("unresolved_evidence", []))

    for record in records:
        extraction = record.get("extraction", {})
        if extraction.get("method") != "scanned_pdf_ocr":
            continue
        for page in extraction.get("page_records", []):
            quality[page.get("selected_quality", "unknown")] += 1
            family = page.get("classification", {}).get(
                "document_family", "UNKNOWN"
            )
            families[family] += 1
            candidates += len(page.get("event_field_candidates", []))
            if page.get("eligible_for_evidence_comparison"):
                comparison_eligible += 1

    summary["scanned_page_quality_counts"] = dict(sorted(quality.items()))
    summary["scanned_page_family_counts"] = dict(sorted(families.items()))
    summary["provisional_event_field_candidate_count"] = candidates
    summary["evidence_comparison_eligible_page_count"] = comparison_eligible
    summary["qdrant_entry_created"] = False
    return summary


def validate_expectations(summary, args):
    return base.validate_expectations(summary, args)


def main():
    ap = argparse.ArgumentParser(
        description="Nova DRL Repair Evidence Collector v{}".format(VERSION)
    )
    ap.add_argument("serial_folder")
    ap.add_argument("--output-root")
    ap.add_argument(
        "--traveler-output-root",
        default="/opt/nova-drl/output/traveler_reader_v1_3_1",
    )
    ap.add_argument("--config")
    ap.add_argument("--inventory-only", action="store_true")
    ap.add_argument("--hash", action="store_true")
    ap.add_argument("--max-text-mb", type=int, default=25)
    ap.add_argument("--extract-log", action="append", default=[])
    ap.add_argument("--pdf-dpi", type=int, default=300)
    ap.add_argument("--max-pdf-pages", type=int, default=50)
    ap.add_argument("--no-scanned-pdf-ocr", action="store_true")
    ap.add_argument("--expect-events", type=int)
    ap.add_argument("--expect-files", type=int)
    ap.add_argument("--expect-event-assigned-files", type=int)
    ap.add_argument("--expect-event-evidence-files", type=int)
    ap.add_argument("--expect-warranty-events", type=int)
    ap.add_argument("--expect-missing-travelers", type=int)
    ap.add_argument("--expect-unit-items", type=int)
    ap.add_argument("--expect-system-metadata", type=int)
    args = ap.parse_args()

    source = Path(args.serial_folder).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        print(
            "ERROR: Serial folder not found: {}".format(source),
            file=sys.stderr,
        )
        return 2

    config_dir = (
        Path(args.config).resolve()
        if args.config
        else Path(__file__).resolve().parent.parent / "config"
    )
    refs = base.load_reference_config(config_dir)
    safe = base.safe_name(source.name)
    out = (
        Path(args.output_root).resolve()
        if args.output_root
        else Path.cwd() / "output"
        / "repair_evidence_collector_v1_4_3_1" / safe
    )

    try:
        summary = base.collect_evidence(
            source,
            out,
            refs,
            traveler_output_root=args.traveler_output_root,
            inventory_only=args.inventory_only,
            hash_files=args.hash,
            max_text_mb=args.max_text_mb,
            extract_logs=args.extract_log,
            enable_scanned_pdf_ocr=not args.no_scanned_pdf_ocr,
            pdf_dpi=args.pdf_dpi,
            max_pdf_pages=args.max_pdf_pages,
        )
        summary = add_quality_summary(summary)
        checks = validate_expectations(summary, args)
        summary["expectation_checks"] = checks
        base.write_outputs(summary, out)
    except Exception as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2

    print()
    print("Nova DRL Repair Evidence Collector v{}".format(VERSION))
    print("=" * 68)
    print("Model:                    {}".format(
        summary["serial_metadata"].get("model")
    ))
    print("Serial:                   {}".format(
        summary["serial_metadata"].get("serial_number")
    ))
    print("Repair events:            {}".format(
        summary["repair_event_count"]
    ))
    print("Original source files:    {}".format(
        summary["original_source_file_count"]
    ))
    print("Unaccounted files:        {}".format(
        summary["unaccounted_original_file_count"]
    ))
    print("Scanned-PDF OCR docs:     {}".format(
        summary.get("scanned_pdf_ocr_document_count", 0)
    ))
    print("Scanned-PDF OCR pages:    {}".format(
        summary.get("scanned_pdf_ocr_page_count", 0)
    ))
    print("Page quality counts:      {}".format(
        summary.get("scanned_page_quality_counts", {})
    ))
    print("Page family counts:       {}".format(
        summary.get("scanned_page_family_counts", {})
    ))
    print("Provisional event fields: {}".format(
        summary.get("provisional_event_field_candidate_count", 0)
    ))
    print("Comparison-eligible pages: {}".format(
        summary.get("evidence_comparison_eligible_page_count", 0)
    ))
    print("Inventory only:           {}".format(
        "YES" if summary["inventory_only"] else "NO"
    ))

    if checks:
        print("\nEXPECTED PILOT COUNTS")
        for check in checks:
            print(
                "  {:32} expected={} actual={} {}".format(
                    check["label"] + ":",
                    check["expected"],
                    check["actual"],
                    "PASS" if check["pass"] else "FAIL",
                )
            )

    print("\nReports: {}".format(out))
    print("READ-ONLY COMPLETE: No DRL source files were changed.")
    print("NO QDRANT ENTRY CREATED.")

    if (
        summary["unaccounted_original_file_count"] != 0
        or any(not check["pass"] for check in checks)
    ):
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
