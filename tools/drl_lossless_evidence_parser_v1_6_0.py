#!/usr/bin/env python3
"""Generic, lossless evidence parsing helpers for Nova DRL v1.6.0.

This module intentionally does NOT know any equipment family or component family.
It parses model output by evidence role and preserves raw strings. Normalization,
clustering, recurrence, and product-specific reasoning belong downstream.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.6.0"

ALLOWED_PROSPECT_KINDS = {
    "customer_requirement",
    "repair_or_service",
    "component_or_part",
    "diagnostic_or_failure",
    "testing_or_process",
    "shop_term_or_abbreviation",
    "part_number_or_identifier",
    "unclear_ocr",
    "other",
}

HIGH_RECALL_SECTION_MAP = {
    "REPORTED FAILURE / CUSTOMER COMPLAINT": "reported_failure",
    "EXPLICIT PARTS / COMPONENTS REPLACED, INSTALLED, SWAPPED, REBUILT OR USED": "parts_replaced",
    "PART / REFERENCE NUMBERS": "part_references",
    "OTHER TECHNICAL REPAIR / SERVICE ACTIONS": "repair_actions",
    "EXPLICIT TEST / OUTCOME": "explicit_test_outcome",
    "TRACKING / ORDER METADATA": "tracking_metadata",
}

# Alternate wording is intentionally generic and only affects heading recognition.
_HEADING_ALIASES = {
    "reported_failure": [
        "REPORTED FAILURE / CUSTOMER COMPLAINT",
        "REPORTED FAILURE",
        "CUSTOMER COMPLAINT",
        "BASIC REPORTED PROBLEM",
    ],
    "parts_replaced": [
        "EXPLICIT PARTS / COMPONENTS REPLACED, INSTALLED, SWAPPED, REBUILT OR USED",
        "PARTS / ASSEMBLIES REPLACED OR USED",
        "PARTS REPLACED",
        "COMPONENTS REPLACED",
    ],
    "part_references": ["PART / REFERENCE NUMBERS", "PART/REFERENCE NUMBERS", "REFERENCE NUMBERS"],
    "repair_actions": ["OTHER TECHNICAL REPAIR / SERVICE ACTIONS", "OTHER REPAIR-HISTORY NOTES", "REPAIR ACTIONS"],
    "explicit_test_outcome": ["EXPLICIT TEST / OUTCOME", "EXPLICIT TEST / OUTCOME NOTE", "TEST / OUTCOME"],
    "tracking_metadata": ["TRACKING / ORDER METADATA", "TRACKING/ORDER METADATA", "TRACKING METADATA"],
}

_BULLET_RE = re.compile(r"^\s*(?:[-*•]+|\d+[.)]|\[[ xX✓✔]?\])\s*")
_PART_LINE_RE = re.compile(r"^\s*PART\s*/?\s*REFERENCE\s*:\s*(.*?)\s*(?:\|\s*CONTEXT\s*:\s*(.*))?$", re.I)

ORDER_PREFIX_SUPPLIER = {"DGK": "Digi-Key", "MSR": "Mouser", "NWK": None, "DSK": None}
ORDER_PREFIX_RE = re.compile(r"\b(DGK|MSR|NWK|DSK)[\s._-]*([A-Za-z0-9-]{3,})\b", re.I)
RMA_LABEL_RE = re.compile(r"\bRMA\s*(?:#|NO\.?|NUMBER)?\s*[:#-]?\s*([A-Za-z0-9-]+)", re.I)
CUSTOMER_PO_LABEL_RE = re.compile(r"\b(?:CUST(?:OMER)?\s*)?PO\s*(?:#|NO\.?|NUMBER)?\s*[:#-]?\s*([A-Za-z0-9-]+)", re.I)

# Guard obvious non-component identifiers while preserving raw PN-hunter evidence.
_DATEISH_RE = re.compile(r"^(?:\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})$")
_PHONEISH_RE = re.compile(r"^\+?\d?[\s().-]*\d{3}[\s).-]*\d{3}[\s.-]*\d{4}$")


def normalized_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def norm_alnum(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def parse_json_loose(text: str) -> Dict[str, Any]:
    s = str(text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```$", "", s)
    try:
        value = json.loads(s)
        return value if isinstance(value, dict) else {}
    except Exception:
        a, b = s.find("{"), s.rfind("}")
        if a >= 0 and b > a:
            try:
                value = json.loads(s[a : b + 1])
                return value if isinstance(value, dict) else {}
            except Exception:
                return {}
        return {}


def _heading_match(line: str) -> Tuple[Optional[str], str]:
    clean = normalized_ws(line).rstrip(":")
    u = clean.upper()
    # Longest aliases first so specific headings win.
    aliases: List[Tuple[str, str]] = []
    for field, vals in _HEADING_ALIASES.items():
        for alias in vals:
            aliases.append((field, alias))
    aliases.sort(key=lambda x: len(x[1]), reverse=True)
    for field, alias in aliases:
        au = alias.upper().rstrip(":")
        if u == au:
            return field, ""
        # Accept heading + inline content.
        if u.startswith(au + ":"):
            return field, clean[len(alias.rstrip(':')) + 1 :].strip()
    return None, ""


def _clean_bullet(line: str) -> str:
    return normalized_ws(_BULLET_RE.sub("", line or ""))


def parse_high_recall_sections(text: str) -> Dict[str, List[str]]:
    """Parse only explicit section structure; never invent or normalize evidence.

    Unheaded text is returned under ``unassigned`` so it is preserved rather than lost.
    Continuation lines are joined to the prior bullet only when they do not look like a
    new bullet/heading and the prior line is clearly incomplete enough to need context.
    """
    out: Dict[str, List[str]] = {k: [] for k in list(HIGH_RECALL_SECTION_MAP.values()) + ["unassigned"]}
    current: Optional[str] = None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        field, inline = _heading_match(line)
        if field:
            current = field
            if inline:
                val = _clean_bullet(inline)
                if val:
                    out[field].append(val)
            continue
        val = _clean_bullet(line)
        if not val:
            continue
        target = current or "unassigned"
        out[target].append(val)
    return out


def parse_prospector(text: str, working_view: str) -> List[Dict[str, Any]]:
    parsed = parse_json_loose(text)
    rows: List[Dict[str, Any]] = []
    for item in parsed.get("candidates") or []:
        if not isinstance(item, dict):
            continue
        kind = normalized_ws(item.get("kind"))
        quote = str(item.get("raw_quote") or "").strip()
        if kind not in ALLOWED_PROSPECT_KINDS or not quote:
            continue
        rows.append(
            {
                "kind": kind,
                "raw_quote": quote,
                "quote_bound_exact": quote in str(working_view or ""),
            }
        )
    return rows


def parse_pn_focus(text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line or line.upper() == "NONE VISIBLE.":
            continue
        m = _PART_LINE_RE.match(line)
        if m:
            ref = normalized_ws(m.group(1))
            ctx = normalized_ws(m.group(2)) or None
        else:
            # Preserve nonconforming lines as raw candidates instead of discarding them.
            ref, ctx = normalized_ws(line), None
        if not ref:
            continue
        reason = component_reference_exclusion_reason(ref)
        rows.append(
            {
                "reference": ref,
                "context": ctx,
                "eligible_component_reference": reason is None,
                "exclusion_reason": reason,
                "raw_line": line,
            }
        )
    return rows


def component_reference_exclusion_reason(value: str) -> Optional[str]:
    s = normalized_ws(value)
    if not s:
        return "empty"
    if ORDER_PREFIX_RE.search(s):
        return "procurement_order_ref"
    if RMA_LABEL_RE.search(s):
        return "rma_labeled"
    if CUSTOMER_PO_LABEL_RE.search(s):
        return "customer_po_labeled"
    if _DATEISH_RE.fullmatch(s):
        return "date_like"
    if _PHONEISH_RE.fullmatch(s):
        return "phone_like"
    return None


def tracking_from_texts(texts: Sequence[Tuple[str, str]]) -> Dict[str, List[Dict[str, Any]]]:
    """Literal tracking extraction only. No cross-line or cross-event substitution."""
    rmas: Dict[str, Dict[str, Any]] = {}
    pos: Dict[str, Dict[str, Any]] = {}
    orders: Dict[str, Dict[str, Any]] = {}
    for source_role, text in texts:
        for raw in str(text or "").splitlines():
            line = normalized_ws(raw)
            if not line:
                continue
            for m in RMA_LABEL_RE.finditer(line):
                value = m.group(1)
                n = norm_alnum(value)
                if n:
                    rmas.setdefault(n, {"value": value, "normalized": n, "evidence_quote": line, "source_role": source_role})
            for m in CUSTOMER_PO_LABEL_RE.finditer(line):
                value = m.group(1)
                n = norm_alnum(value)
                if n:
                    pos.setdefault(n, {"value": value, "normalized": n, "evidence_quote": line, "source_role": source_role})
            for m in ORDER_PREFIX_RE.finditer(line):
                prefix, suffix = m.group(1).upper(), m.group(2)
                value = f"{prefix}{suffix}"
                n = norm_alnum(value)
                if n:
                    orders.setdefault(
                        n,
                        {
                            "order_ref": value,
                            "normalized": n,
                            "supplier": ORDER_PREFIX_SUPPLIER.get(prefix),
                            "evidence_quote": line,
                            "source_role": source_role,
                        },
                    )
    return {
        "rma_numbers": list(rmas.values()),
        "customer_po_numbers": list(pos.values()),
        "procurement_refs": list(orders.values()),
    }


def evidence_rows_for_record(
    source_record: Dict[str, Any],
    *,
    high_recall_text: str,
    prospector_text: str,
    prospector_working_view: str,
    pn_focus_text: str,
) -> List[Dict[str, Any]]:
    """Build an append-only evidence ledger for one source record."""
    base = {
        "version": VERSION,
        "repair_event_id": source_record.get("repair_event_id"),
        "source_record_id": source_record.get("source_record_id"),
        "source_path": source_record.get("source_path"),
        "source_relative_path": source_record.get("source_relative_path"),
        "source_image_sha256": source_record.get("source_image_sha256"),
        "equipment_family": source_record.get("equipment_family"),
        "line_card_sequence": source_record.get("line_card_sequence"),
        "selection_reason": source_record.get("selection_reason"),
    }
    rows: List[Dict[str, Any]] = []
    sections = parse_high_recall_sections(high_recall_text)
    for field, items in sections.items():
        for quote in items:
            rows.append(
                {
                    **base,
                    "evidence_source": "high_recall_direct",
                    "evidence_role": field,
                    "raw_quote": quote,
                    "authoritative_field_role": field != "unassigned",
                }
            )
    for item in parse_prospector(prospector_text, prospector_working_view):
        rows.append(
            {
                **base,
                "evidence_source": "historical_prospector",
                "evidence_role": item["kind"],
                "raw_quote": item["raw_quote"],
                "quote_bound_exact": item["quote_bound_exact"],
                "authoritative_field_role": False,
            }
        )
    for item in parse_pn_focus(pn_focus_text):
        rows.append(
            {
                **base,
                "evidence_source": "pn_focus",
                "evidence_role": "part_reference",
                "raw_quote": item["raw_line"],
                "part_reference": item["reference"],
                "context": item["context"],
                "eligible_component_reference": item["eligible_component_reference"],
                "exclusion_reason": item["exclusion_reason"],
                "authoritative_field_role": True,
            }
        )
    return rows


def unique_evidence(rows: Iterable[Dict[str, Any]], key_fields: Sequence[str]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for row in rows:
        key = tuple(normalized_ws(row.get(k)).casefold() for k in key_fields)
        if not any(key):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def derive_event_facts(rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Lossless derived view: direct section roles only; prospector remains supplemental.

    This deliberately avoids converting generic component mentions into replacements.
    PN-focused candidates remain part references, not replacement claims.
    """
    mapping = {
        "reported_failure": "reported_failure",
        "parts_replaced": "parts_replaced",
        "repair_actions": "repair_actions",
        "explicit_test_outcome": "explicit_test_outcome",
    }
    out: Dict[str, List[Dict[str, Any]]] = {k: [] for k in [*mapping.values(), "part_references", "prospector_candidates", "unassigned_high_recall"]}
    for r in rows:
        src, role = r.get("evidence_source"), r.get("evidence_role")
        if src == "high_recall_direct" and role in mapping:
            out[mapping[role]].append(
                {
                    "text": r.get("raw_quote"),
                    "evidence_quote": r.get("raw_quote"),
                    "source_record_id": r.get("source_record_id"),
                    "source_path": r.get("source_path"),
                    "evidence_source": src,
                }
            )
        elif src == "high_recall_direct" and role == "part_references":
            out["part_references"].append(
                {
                    "reference": r.get("raw_quote"),
                    "context": None,
                    "source_record_id": r.get("source_record_id"),
                    "source_path": r.get("source_path"),
                    "evidence_source": src,
                    "eligible_component_reference": True,
                }
            )
        elif src == "high_recall_direct" and role == "unassigned":
            out["unassigned_high_recall"].append(
                {
                    "text": r.get("raw_quote"),
                    "source_record_id": r.get("source_record_id"),
                    "source_path": r.get("source_path"),
                }
            )
        elif src == "pn_focus" and r.get("eligible_component_reference"):
            out["part_references"].append(
                {
                    "reference": r.get("part_reference"),
                    "context": r.get("context"),
                    "source_record_id": r.get("source_record_id"),
                    "source_path": r.get("source_path"),
                    "evidence_source": src,
                    "eligible_component_reference": True,
                }
            )
        elif src == "historical_prospector":
            out["prospector_candidates"].append(
                {
                    "kind": role,
                    "raw_quote": r.get("raw_quote"),
                    "quote_bound_exact": r.get("quote_bound_exact"),
                    "source_record_id": r.get("source_record_id"),
                    "source_path": r.get("source_path"),
                }
            )
    # Within the same event, de-dupe exact normalized duplicates without changing wording.
    out["reported_failure"] = unique_evidence(out["reported_failure"], ["text"])
    out["parts_replaced"] = unique_evidence(out["parts_replaced"], ["text"])
    out["repair_actions"] = unique_evidence(out["repair_actions"], ["text"])
    out["explicit_test_outcome"] = unique_evidence(out["explicit_test_outcome"], ["text"])
    out["part_references"] = unique_evidence(out["part_references"], ["reference", "context"])
    out["prospector_candidates"] = unique_evidence(out["prospector_candidates"], ["kind", "raw_quote"])
    out["unassigned_high_recall"] = unique_evidence(out["unassigned_high_recall"], ["text"])
    return out
