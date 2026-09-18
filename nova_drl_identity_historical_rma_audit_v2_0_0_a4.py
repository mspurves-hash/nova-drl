#!/usr/bin/env python3
"""
Nova DRL Historical RMA Identity Corroboration Audit v2.0.0-a4.

This is a read-only, evidence-only audit. It recovers same-row RMA-to-part
associations from the PDF-derived XLSX where part text drifted across columns
C:G, compares conservative exact Part # tokens with the v2.0.0-a3 identity
audit, and writes two new artifacts:

  1. an a4 corroboration audit JSON; and
  2. a deduplicated historical RMA reference JSONL.

It does not modify SQLite, the frozen corpus, launchers, accepted facts,
production identity behavior, or Qdrant. It never fuzzy-matches, completes a
truncated Part #, or approves an identity.

Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple
from xml.etree import ElementTree as ET


VERSION = "2.0.0-a4"
SCHEMA = "nova-drl-identity-historical-rma-audit-v2-a4"
REFERENCE_SCHEMA = "nova-drl-historical-rma-part-reference-v1"

DEFAULT_XLSX_CANDIDATES = (
    Path("/opt/nova-drl/input/00001 ReportGenReport.xlsx"),
    Path("/opt/nova-drl/00001 ReportGenReport.xlsx"),
    Path("00001 ReportGenReport.xlsx"),
)
DEFAULT_A3_CANDIDATES = (
    Path("/opt/nova-drl/output/nova_drl_identity_catalog_audit_v2_0_0_a3.json"),
    Path("nova_drl_identity_catalog_audit_v2_0_0_a3.json"),
)
DEFAULT_OUTPUT_DIR = Path("/opt/nova-drl/output")

PART_COLUMNS = ("C", "D", "E", "F", "G")
EXPECTED_SPLIT_PATTERN = ("F", "G")

TYPE_ALIASES: Dict[str, str] = {
    "P/S": "PS",
    "P_S": "PS",
    "SVO-DRV": "SVO DRV",
    "SVR DRV": "SVO DRV",
    "SVP DRV": "SVO DRV",
    "SVO DRVB": "SVO DRV",
    "PREALGINER": "PREALIGNER",
    "PREAIGNER": "PREALIGNER",
    "GEAR BOS": "GEAR BOX",
}

GENERIC_OR_INCOMPLETE_TOKENS: Set[str] = {
    "AIR",
    "ASSEMBLY",
    "AUTOLOADER",
    "BOARD",
    "CARTRIDGE",
    "CD",
    "DIAGNOSTIC",
    "ELEVATOR",
    "ENGINEERING",
    "FAN",
    "HEAD",
    "INPUT",
    "LAMP",
    "MODULE",
    "MONITOR",
    "OPERATION",
    "PREALIGNER",
    "RENT",
    "SENSOR",
    "SERVICE",
    "SERVICES",
    "STAGE",
    "THERM",
    "TRAY",
    "XY",
}

TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/#-]*", re.IGNORECASE)
CELL_REF_RE = re.compile(r"^([A-Z]+)([0-9]+)$")


@dataclass
class SourceAssociation:
    row_number: int
    rma: str
    part_label: str
    source_cells: List[Dict[str, str]]

    @property
    def column_pattern(self) -> str:
        return "+".join(cell["column"] for cell in self.source_cells)


@dataclass
class DedupedAssociation:
    rma: str
    part_label: str
    source_rows: List[int] = field(default_factory=list)
    column_patterns: Set[str] = field(default_factory=set)


@dataclass
class ParsedLabel:
    equipment_type: str
    raw_token: str
    normalized_part_number: str
    parse_status: str
    flags: List[str]


@dataclass
class HistoricalTokenEvidence:
    normalized_part_number: str
    source_row_count: int = 0
    rmas: Set[str] = field(default_factory=set)
    labels: Counter = field(default_factory=Counter)
    type_rows: Counter = field(default_factory=Counter)
    type_rmas: Dict[str, Set[str]] = field(default_factory=lambda: defaultdict(set))
    untyped_rows: int = 0
    untyped_rmas: Set[str] = field(default_factory=set)
    flagged_rows: Counter = field(default_factory=Counter)


def normalize_whitespace(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_part_number(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", normalize_whitespace(value).upper())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_path_label(path: Path) -> str:
    """Keep normal server paths, but do not embed transient workspace paths."""
    server_root = Path("/opt/nova-drl")
    try:
        path.relative_to(server_root)
        return str(path)
    except ValueError:
        return path.name


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def column_letters(cell_reference: str) -> str:
    match = CELL_REF_RE.match(cell_reference)
    return match.group(1) if match else ""


def resolve_first_existing(explicit: Optional[Path], candidates: Sequence[Path], label: str) -> Path:
    if explicit is not None:
        path = explicit.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"{label} not found: {path}")
        return path
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    tried = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"{label} not found. Tried: {tried}")


def normalize_zip_target(base: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return str(PurePosixPath(base).parent.joinpath(target))


def first_worksheet_path(archive: zipfile.ZipFile) -> str:
    workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
    first_sheet = next(
        (element for element in workbook_root.iter() if local_name(element.tag) == "sheet"),
        None,
    )
    if first_sheet is None:
        raise ValueError("XLSX has no worksheets")

    relationship_id = None
    for key, value in first_sheet.attrib.items():
        if local_name(key) == "id":
            relationship_id = value
            break
    if not relationship_id:
        raise ValueError("Unable to resolve first worksheet relationship")

    rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relationship in rels_root.iter():
        if local_name(relationship.tag) != "Relationship":
            continue
        if relationship.attrib.get("Id") == relationship_id:
            target = relationship.attrib.get("Target", "")
            resolved = normalize_zip_target("xl/workbook.xml", target)
            if resolved not in archive.namelist():
                raise ValueError(f"Resolved worksheet is missing from XLSX: {resolved}")
            return resolved
    raise ValueError("First worksheet relationship target was not found")


def load_shared_strings(archive: zipfile.ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    shared: List[str] = []
    for item in root.iter():
        if local_name(item.tag) != "si":
            continue
        pieces = [element.text or "" for element in item.iter() if local_name(element.tag) == "t"]
        shared.append("".join(pieces))
    return shared


def cell_text(cell: ET.Element, shared_strings: Sequence[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(
            element.text or "" for element in cell.iter() if local_name(element.tag) == "t"
        )

    raw_value = ""
    for child in cell:
        if local_name(child.tag) == "v":
            raw_value = child.text or ""
            break
    if not raw_value:
        return ""
    if cell_type == "s":
        index = int(raw_value)
        if index < 0 or index >= len(shared_strings):
            raise ValueError(f"Shared-string index out of range: {index}")
        return shared_strings[index]
    if cell_type == "b":
        return "TRUE" if raw_value == "1" else "FALSE"
    if cell_type in {"str", "e"}:
        return raw_value

    # Numeric identifiers are kept as text. Excel may serialize whole numbers
    # with a trailing .0; remove it without converting through float.
    if re.fullmatch(r"[+-]?[0-9]+\.0+", raw_value):
        return raw_value.split(".", 1)[0]
    return raw_value


def iter_first_sheet_rows(xlsx_path: Path) -> Iterator[Tuple[int, Dict[str, str]]]:
    with zipfile.ZipFile(xlsx_path, "r") as archive:
        shared_strings = load_shared_strings(archive)
        worksheet_path = first_worksheet_path(archive)
        with archive.open(worksheet_path, "r") as worksheet_stream:
            for _event, element in ET.iterparse(worksheet_stream, events=("end",)):
                if local_name(element.tag) != "row":
                    continue
                row_number = int(element.attrib.get("r", "0") or 0)
                cells: Dict[str, str] = {}
                for cell in element:
                    if local_name(cell.tag) != "c":
                        continue
                    column = column_letters(cell.attrib.get("r", ""))
                    if column in {"A", *PART_COLUMNS}:
                        value = normalize_whitespace(cell_text(cell, shared_strings))
                        if value:
                            cells[column] = value
                yield row_number, cells
                element.clear()


def extract_associations(xlsx_path: Path) -> Tuple[List[SourceAssociation], List[DedupedAssociation], Dict[str, object]]:
    source: List[SourceAssociation] = []
    missing_part: List[Dict[str, object]] = []
    orphan_part: List[Dict[str, object]] = []
    split_rows: List[Dict[str, object]] = []
    unexpected_multi_cell: List[Dict[str, object]] = []
    column_counts: Counter = Counter()
    max_xml_row = 0
    max_data_row = 0
    nonblank_rows = 0

    for row_number, cells in iter_first_sheet_rows(xlsx_path):
        max_xml_row = max(max_xml_row, row_number)
        if cells:
            nonblank_rows += 1
            max_data_row = max(max_data_row, row_number)
        rma = normalize_whitespace(cells.get("A", ""))
        part_cells = [
            {"column": column, "value": cells[column]}
            for column in PART_COLUMNS
            if normalize_whitespace(cells.get(column, ""))
        ]

        if not rma and not part_cells:
            continue
        if not rma:
            orphan_part.append({"row": row_number, "source_cells": part_cells})
            continue
        if not part_cells:
            missing_part.append({"row": row_number, "rma": rma})
            continue

        for item in part_cells:
            column_counts[item["column"]] += 1
        if len(part_cells) > 1:
            detail = {"row": row_number, "rma": rma, "source_cells": part_cells}
            split_rows.append(detail)
            if tuple(item["column"] for item in part_cells) != EXPECTED_SPLIT_PATTERN:
                unexpected_multi_cell.append(detail)

        part_label = normalize_whitespace(" ".join(item["value"] for item in part_cells))
        source.append(
            SourceAssociation(
                row_number=row_number,
                rma=rma,
                part_label=part_label,
                source_cells=part_cells,
            )
        )

    deduped_by_key: Dict[Tuple[str, str], DedupedAssociation] = {}
    for row in source:
        key = (row.rma, row.part_label)
        record = deduped_by_key.setdefault(
            key,
            DedupedAssociation(rma=row.rma, part_label=row.part_label),
        )
        record.source_rows.append(row.row_number)
        record.column_patterns.add(row.column_pattern)

    deduped = sorted(
        deduped_by_key.values(),
        key=lambda item: (int(item.rma) if item.rma.isdigit() else sys.maxsize, item.rma, item.part_label),
    )
    unique_rmas = {row.rma for row in source}
    unique_labels = {row.part_label for row in source}
    alignment_complete = not missing_part and not orphan_part and not unexpected_multi_cell

    summary: Dict[str, object] = {
        "worksheet_effective_extent_rows": max_data_row,
        "xml_row_extent_including_trailing_formatting": max_xml_row,
        "blank_or_separator_rows": max(0, max_data_row - nonblank_rows),
        "rma_rows": len(source) + len(missing_part),
        "rows_with_same_row_rma_part_evidence": len(source),
        "rows_with_exactly_one_part_cell": len(source) - len(split_rows),
        "rows_with_split_part_description": len(split_rows),
        "rma_rows_missing_part_text": len(missing_part),
        "part_rows_missing_rma": len(orphan_part),
        "unexpected_multi_cell_rows": len(unexpected_multi_cell),
        "part_cell_counts_by_column": dict(sorted(column_counts.items())),
        "unique_rmas": len(unique_rmas),
        "unique_raw_part_labels": len(unique_labels),
        "distinct_rma_part_associations": len(deduped),
        "exact_duplicate_rows_removed": len(source) - len(deduped),
        "source_alignment_status": (
            "complete_same_row_pairing" if alignment_complete else "blocked_source_alignment"
        ),
        "split_row_examples": split_rows[:20],
        "missing_part_examples": missing_part[:20],
        "orphan_part_examples": orphan_part[:20],
        "unexpected_multi_cell_examples": unexpected_multi_cell[:20],
    }
    return source, deduped, summary


def load_a3(a3_path: Path) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    with a3_path.open("r", encoding="utf-8") as handle:
        audit = json.load(handle)
    if not isinstance(audit, dict):
        raise ValueError("a3 audit root must be an object")
    identity_audit = audit.get("identity_audit")
    if not isinstance(identity_audit, dict):
        raise ValueError("a3 audit is missing identity_audit")
    queue = identity_audit.get("review_queue")
    if not isinstance(queue, list):
        raise ValueError("a3 audit is missing identity_audit.review_queue")
    required = {"identity_key", "equipment_type", "part_number"}
    for index, row in enumerate(queue):
        if not isinstance(row, dict) or not required.issubset(row):
            raise ValueError(f"Invalid a3 review_queue row at index {index}")
    return audit, queue


def build_type_aliases(review_queue: Sequence[Mapping[str, object]]) -> Dict[str, str]:
    aliases = dict(TYPE_ALIASES)
    for row in review_queue:
        equipment_type = normalize_whitespace(row.get("equipment_type", "")).upper()
        if equipment_type:
            aliases.setdefault(equipment_type, equipment_type)
    return aliases


def parse_historical_label(label: str, type_aliases: Mapping[str, str]) -> ParsedLabel:
    normalized_label = normalize_whitespace(label)
    payload = normalized_label
    equipment_type = ""
    for raw_type in sorted(type_aliases, key=lambda value: (-len(value), value)):
        pattern = re.compile(rf"^{re.escape(raw_type)}\s*-\s*", re.IGNORECASE)
        if pattern.search(normalized_label):
            equipment_type = type_aliases[raw_type]
            payload = pattern.sub("", normalized_label, count=1).strip()
            break

    match = TOKEN_RE.match(payload)
    raw_token = (match.group(0) if match else "").rstrip(".,;:()[]{}")
    normalized = normalize_part_number(raw_token)
    flags: List[str] = []
    upper_token = raw_token.upper()

    if not raw_token:
        return ParsedLabel(equipment_type, "", "", "rejected_no_part_token", flags)
    if len(normalized) < 4:
        return ParsedLabel(equipment_type, raw_token, normalized, "rejected_token_too_short", flags)
    if not any(character.isdigit() for character in normalized):
        return ParsedLabel(equipment_type, raw_token, normalized, "rejected_no_digit", flags)
    if upper_token in GENERIC_OR_INCOMPLETE_TOKENS:
        return ParsedLabel(equipment_type, raw_token, normalized, "rejected_generic_token", flags)

    if raw_token.endswith(("-", "/", ".")) or normalized_label.endswith(("(", "-")):
        flags.append("possibly_truncated_source_label")
    if not equipment_type:
        flags.append("no_explicit_equipment_type")
    return ParsedLabel(equipment_type, raw_token, normalized, "conservative_part_token", flags)


def build_token_evidence(
    source_rows: Sequence[SourceAssociation],
    type_aliases: Mapping[str, str],
) -> Tuple[Dict[str, HistoricalTokenEvidence], Counter]:
    evidence: Dict[str, HistoricalTokenEvidence] = {}
    rejection_counts: Counter = Counter()
    for row in source_rows:
        parsed = parse_historical_label(row.part_label, type_aliases)
        if parsed.parse_status != "conservative_part_token":
            rejection_counts[parsed.parse_status] += 1
            continue
        bucket = evidence.setdefault(
            parsed.normalized_part_number,
            HistoricalTokenEvidence(parsed.normalized_part_number),
        )
        bucket.source_row_count += 1
        bucket.rmas.add(row.rma)
        bucket.labels[row.part_label] += 1
        for flag in parsed.flags:
            bucket.flagged_rows[flag] += 1
        if parsed.equipment_type:
            bucket.type_rows[parsed.equipment_type] += 1
            bucket.type_rmas[parsed.equipment_type].add(row.rma)
        else:
            bucket.untyped_rows += 1
            bucket.untyped_rmas.add(row.rma)
    return evidence, rejection_counts


def candidate_comparison_key(row: Mapping[str, object]) -> str:
    key = normalize_part_number(row.get("comparison_key", ""))
    return key or normalize_part_number(row.get("part_number", ""))


def evidence_summary(bucket: HistoricalTokenEvidence) -> Dict[str, object]:
    return {
        "normalized_part_number": bucket.normalized_part_number,
        "source_row_count": bucket.source_row_count,
        "distinct_rma_count": len(bucket.rmas),
        "typed_row_counts": dict(sorted(bucket.type_rows.items())),
        "typed_rma_counts": {
            key: len(value) for key, value in sorted(bucket.type_rmas.items())
        },
        "untyped_row_count": bucket.untyped_rows,
        "untyped_rma_count": len(bucket.untyped_rmas),
        "flagged_row_counts": dict(sorted(bucket.flagged_rows.items())),
        "sample_labels": [label for label, _count in bucket.labels.most_common(8)],
    }


def build_corroboration(
    review_queue: Sequence[Mapping[str, object]],
    historical: Mapping[str, HistoricalTokenEvidence],
    type_aliases: Mapping[str, str],
) -> Dict[str, object]:
    current_by_part: Dict[str, List[Mapping[str, object]]] = defaultdict(list)
    for row in review_queue:
        current_by_part[candidate_comparison_key(row)].append(row)

    identity_evidence: List[Dict[str, object]] = []
    matched_token_records: List[Dict[str, object]] = []
    cross_type_review: List[Dict[str, object]] = []
    matched_identity_keys: Set[str] = set()
    disposition_counts: Counter = Counter()

    for normalized_part in sorted(set(current_by_part).intersection(historical)):
        bucket = historical[normalized_part]
        current_rows = current_by_part[normalized_part]
        matched_token_records.append(
            {
                **evidence_summary(bucket),
                "current_identity_keys": sorted(str(row["identity_key"]) for row in current_rows),
            }
        )
        if len(current_rows) > 1 or len(bucket.type_rows) > 1:
            cross_type_review.append(
                {
                    **evidence_summary(bucket),
                    "current_identity_keys": sorted(str(row["identity_key"]) for row in current_rows),
                    "decision": "keep_type_scoped_pending_human_review",
                }
            )

        typed_total = sum(bucket.type_rows.values())
        for current in current_rows:
            identity_key = str(current["identity_key"])
            observed_current_type = normalize_whitespace(current["equipment_type"]).upper()
            comparison_type = type_aliases.get(observed_current_type, observed_current_type)
            same_rows = int(bucket.type_rows.get(comparison_type, 0))
            same_rmas = len(bucket.type_rmas.get(comparison_type, set()))
            other_rows = typed_total - same_rows
            other_rmas_set: Set[str] = set()
            for observed_type, rmas in bucket.type_rmas.items():
                if observed_type != comparison_type:
                    other_rmas_set.update(rmas)

            if same_rows and not other_rows:
                disposition = (
                    "type_alias_corroboration_not_approved"
                    if observed_current_type != comparison_type
                    else "corroborated_same_type_not_approved"
                )
            elif same_rows and other_rows:
                disposition = "mixed_type_historical_evidence_review"
            elif other_rows:
                disposition = "historical_type_conflict_review"
            else:
                disposition = "part_number_only_corroboration_not_approved"
            disposition_counts[disposition] += 1
            matched_identity_keys.add(identity_key)
            identity_evidence.append(
                {
                    "identity_key": identity_key,
                    "equipment_type": observed_current_type,
                    "canonical_equipment_type_for_comparison": comparison_type,
                    "part_number": current["part_number"],
                    "normalized_part_number": normalized_part,
                    "historical_same_type_rows": same_rows,
                    "historical_same_type_rmas": same_rmas,
                    "historical_other_type_rows": other_rows,
                    "historical_other_type_rmas": len(other_rmas_set),
                    "historical_other_types": sorted(
                        observed_type
                        for observed_type in bucket.type_rows
                        if observed_type != comparison_type
                    ),
                    "historical_untyped_rows": bucket.untyped_rows,
                    "historical_untyped_rmas": len(bucket.untyped_rmas),
                    "sample_labels": [label for label, _count in bucket.labels.most_common(8)],
                    "disposition": disposition,
                    "approved": False,
                }
            )

    identity_evidence.sort(
        key=lambda item: (
            -int(item["historical_same_type_rmas"]),
            -int(item["historical_untyped_rmas"]),
            str(item["identity_key"]),
        )
    )
    matched_token_records.sort(
        key=lambda item: (-int(item["distinct_rma_count"]), str(item["normalized_part_number"]))
    )
    cross_type_review.sort(
        key=lambda item: (-int(item["distinct_rma_count"]), str(item["normalized_part_number"]))
    )

    historical_only: List[Dict[str, object]] = []
    for normalized_part in sorted(set(historical).difference(current_by_part)):
        bucket = historical[normalized_part]
        historical_only.append(
            {
                **evidence_summary(bucket),
                "disposition": "preserved_historical_only_not_promoted",
                "approved": False,
            }
        )
    historical_only.sort(
        key=lambda item: (-int(item["distinct_rma_count"]), str(item["normalized_part_number"]))
    )

    matched_historical_rows = sum(
        historical[key].source_row_count for key in set(current_by_part).intersection(historical)
    )
    total_historical_rows = sum(bucket.source_row_count for bucket in historical.values())
    return {
        "current_a3_identity_candidates": len(review_queue),
        "current_a3_identity_candidates_with_historical_match": len(matched_identity_keys),
        "historical_conservative_part_tokens": len(historical),
        "historical_tokens_matching_a3": len(set(current_by_part).intersection(historical)),
        "historical_tokens_not_in_a3": len(set(historical).difference(current_by_part)),
        "historical_rows_matching_a3": matched_historical_rows,
        "historical_rows_not_matching_a3": total_historical_rows - matched_historical_rows,
        "disposition_counts": dict(sorted(disposition_counts.items())),
        "identity_evidence": identity_evidence,
        "matched_part_number_evidence": matched_token_records,
        "cross_type_review": cross_type_review,
        "historical_only_part_numbers": historical_only,
    }


def benchmark_record(
    normalized_part: str,
    historical: Mapping[str, HistoricalTokenEvidence],
    review_queue: Sequence[Mapping[str, object]],
) -> Dict[str, object]:
    bucket = historical.get(normalized_part)
    current = [
        str(row["identity_key"])
        for row in review_queue
        if candidate_comparison_key(row) == normalized_part
    ]
    if not bucket:
        return {
            "normalized_part_number": normalized_part,
            "status": "not_observed_in_historical_reference",
            "current_identity_keys": sorted(current),
        }
    return {
        **evidence_summary(bucket),
        "status": "observed_historical_reference_not_approved",
        "current_identity_keys": sorted(current),
    }


def build_reference_records(
    deduped: Sequence[DedupedAssociation],
    type_aliases: Mapping[str, str],
    review_queue: Sequence[Mapping[str, object]],
) -> List[Dict[str, object]]:
    current_by_part: Dict[str, List[str]] = defaultdict(list)
    for row in review_queue:
        current_by_part[candidate_comparison_key(row)].append(str(row["identity_key"]))

    records: List[Dict[str, object]] = []
    for item in deduped:
        parsed = parse_historical_label(item.part_label, type_aliases)
        records.append(
            {
                "schema": REFERENCE_SCHEMA,
                "rma": item.rma,
                "raw_part_label": item.part_label,
                "source_rows": sorted(item.source_rows),
                "source_row_count": len(item.source_rows),
                "source_column_patterns": sorted(item.column_patterns),
                "parsed_equipment_type": parsed.equipment_type,
                "parsed_part_token": parsed.raw_token,
                "normalized_part_number": parsed.normalized_part_number,
                "parse_status": parsed.parse_status,
                "flags": parsed.flags,
                "a3_identity_keys": sorted(
                    current_by_part.get(parsed.normalized_part_number, [])
                    if parsed.parse_status == "conservative_part_token"
                    else []
                ),
                "identity_authority": False,
                "approved": False,
            }
        )
    return records


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temp_path, path)
    try:
        path.chmod(0o664)
    except OSError:
        pass


def atomic_write_jsonl(path: Path, records: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    os.replace(temp_path, path)
    try:
        path.chmod(0o664)
    except OSError:
        pass


def default_output_paths() -> Tuple[Path, Path]:
    output_dir = DEFAULT_OUTPUT_DIR if DEFAULT_OUTPUT_DIR.is_dir() else Path("output")
    return (
        output_dir / "nova_drl_identity_historical_rma_audit_v2_0_0_a4.json",
        output_dir / "nova_drl_historical_rma_part_reference_v1_0_0.jsonl",
    )


def build_audit(
    xlsx_path: Path,
    a3_path: Path,
    reference_output: Path,
) -> Tuple[Dict[str, object], List[Dict[str, object]]]:
    source_rows, deduped, extraction = extract_associations(xlsx_path)
    a3, review_queue = load_a3(a3_path)
    type_aliases = build_type_aliases(review_queue)
    historical, rejection_counts = build_token_evidence(source_rows, type_aliases)
    corroboration = build_corroboration(review_queue, historical, type_aliases)
    reference_records = build_reference_records(deduped, type_aliases, review_queue)

    source_complete = extraction["source_alignment_status"] == "complete_same_row_pairing"
    status = "audit_complete_no_approvals" if source_complete else "blocked_source_alignment"
    audit: Dict[str, object] = {
        "schema": SCHEMA,
        "version": VERSION,
        "built_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "mode": "read_only_historical_reference_audit",
        "source_inventory": {
            "historical_xlsx": {
                "path": source_path_label(xlsx_path),
                "sha256": sha256_file(xlsx_path),
                "role": "historical_rma_reference_not_identity_authority",
            },
            "identity_a3": {
                "path": source_path_label(a3_path),
                "sha256": sha256_file(a3_path),
                "schema": a3.get("schema"),
                "version": a3.get("version"),
                "status": a3.get("status"),
                "role": "read_only_identity_candidate_audit",
            },
            "reference_output": source_path_label(reference_output),
        },
        "extraction": extraction,
        "conservative_token_extraction": {
            "rows_yielding_conservative_part_token": sum(
                bucket.source_row_count for bucket in historical.values()
            ),
            "unique_conservative_part_tokens": len(historical),
            "rejected_row_counts": dict(sorted(rejection_counts.items())),
            "rules": [
                "Use explicit known TYPE - PART token boundaries only.",
                "Otherwise inspect only the first compact Part #-like token.",
                "Require at least four normalized characters and at least one digit.",
                "Never complete a truncated prefix or suffix.",
                "Never fuzzy-match or infer an equipment type from description text.",
            ],
        },
        "corroboration": corroboration,
        "benchmarks": {
            "BM23995": benchmark_record("BM23995", historical, review_queue),
            "KD200-414": benchmark_record("KD200414", historical, review_queue),
            "UTC800P": benchmark_record("UTC800P", historical, review_queue),
            "GB8-MT": benchmark_record("GB8MT", historical, review_queue),
            "3795-18-1": benchmark_record("3795181", historical, review_queue),
            "XU-RCM7231": benchmark_record("XURCM7231", historical, review_queue),
        },
        "policy": {
            "historical_reference_is_identity_authority": False,
            "exact_part_number_comparison_only": True,
            "equipment_type_fence_preserved": True,
            "fuzzy_matching": False,
            "prefix_completion": False,
            "suffix_completion": False,
            "normalization_collisions_auto_merged": False,
            "cross_type_identities_auto_merged": False,
            "historical_only_part_numbers_promoted": False,
            "human_verification_required": True,
            "eighty_twenty_priority": True,
        },
        "safety": {
            "canonical_identities_created": 0,
            "identities_approved": 0,
            "accepted_facts_changed": 0,
            "database_writes": 0,
            "corpus_writes": 0,
            "launcher_changes": 0,
            "qdrant": "OFF",
            "production_resolver_changed": False,
        },
        "next": (
            "Human-review high-value same-type corroboration and cross-type exceptions; "
            "do not promote historical-only or truncated labels automatically."
        ),
    }
    return audit, reference_records


def render_summary(audit: Mapping[str, object], output: Path, reference_output: Path) -> str:
    extraction = audit["extraction"]
    corroboration = audit["corroboration"]
    token = audit["conservative_token_extraction"]
    lines = [
        "=" * 92,
        f"NOVA DRL HISTORICAL RMA IDENTITY CORROBORATION AUDIT  |  v{VERSION}",
        "=" * 92,
        f"Status: {audit['status']}",
        "Mode: READ-ONLY AUDIT — ZERO IDENTITIES APPROVED",
        "",
        "SOURCE RECOVERY",
        "---------------",
        f"RMA rows: {extraction['rma_rows']:,}",
        f"Same-row RMA/part rows: {extraction['rows_with_same_row_rma_part_evidence']:,}",
        f"Distinct RMA/part associations: {extraction['distinct_rma_part_associations']:,}",
        f"Exact duplicate rows removed: {extraction['exact_duplicate_rows_removed']:,}",
        f"Split F+G continuation rows: {extraction['rows_with_split_part_description']:,}",
        f"Alignment: {extraction['source_alignment_status']}",
        "",
        "A3 CORROBORATION",
        "----------------",
        f"Conservative historical Part # tokens: {token['unique_conservative_part_tokens']:,}",
        f"Historical tokens matching a3: {corroboration['historical_tokens_matching_a3']:,}",
        f"a3 type-scoped identities with a historical match: "
        f"{corroboration['current_a3_identity_candidates_with_historical_match']:,}",
        f"Historical-only tokens preserved/unpromoted: "
        f"{corroboration['historical_tokens_not_in_a3']:,}",
        "",
        "SAFETY",
        "------",
        "Canonical identities created: 0",
        "Database/corpus/launcher writes: 0",
        "Qdrant: OFF",
        "Production resolver changed: NO",
        "",
        f"Audit JSON: {output}",
        f"Reference JSONL: {reference_output}",
    ]
    return "\n".join(lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    default_output, default_reference = default_output_paths()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xlsx", type=Path, help="PDF-derived historical RMA XLSX")
    parser.add_argument("--a3", type=Path, help="v2.0.0-a3 identity audit JSON")
    parser.add_argument("--output", type=Path, default=default_output, help="a4 audit JSON output")
    parser.add_argument(
        "--reference-output",
        type=Path,
        default=default_reference,
        help="deduplicated historical RMA reference JSONL output",
    )
    parser.add_argument("--version", action="version", version=VERSION)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        xlsx_path = resolve_first_existing(args.xlsx, DEFAULT_XLSX_CANDIDATES, "Historical XLSX")
        a3_path = resolve_first_existing(args.a3, DEFAULT_A3_CANDIDATES, "a3 audit JSON")
        output = args.output.expanduser().resolve()
        reference_output = args.reference_output.expanduser().resolve()
        if output in {xlsx_path, a3_path} or reference_output in {xlsx_path, a3_path}:
            raise ValueError("Output paths must not overwrite either input")
        if output == reference_output:
            raise ValueError("Audit JSON and reference JSONL outputs must be different")

        audit, reference_records = build_audit(xlsx_path, a3_path, reference_output)
        atomic_write_json(output, audit)
        atomic_write_jsonl(reference_output, reference_records)
        print(render_summary(audit, output, reference_output))
        return 0 if audit["status"] == "audit_complete_no_approvals" else 2
    except (FileNotFoundError, ValueError, OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
