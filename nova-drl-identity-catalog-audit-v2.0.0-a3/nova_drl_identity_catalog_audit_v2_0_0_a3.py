#!/usr/bin/env python3
"""
NOVA DRL Identity Catalog Audit v2.0.0-a3

Read-only, type-first correction to the a2 candidate audit.

The frozen v1.6.0 corpus stores some co-occurring equipment labels in one
string, separated by ``|``.  Each segment follows the DRL label contract:

    EQUIPMENT TYPE - DRL PART NUMBER descriptive context

Version a3 splits compound labels before parsing identity and takes exactly
one identity candidate from each segment: the first token after the equipment
type boundary.  Descriptive tokens, OEM names, and a neighboring pipe segment
can never become identities for that segment.

Identity equality is the pair (canonical equipment type, exact Part #).
Punctuation-stripped keys are diagnostics only.  Cross-type Part # matches,
punctuation collisions, type typos, and malformed labels are never auto-merged.

This program creates no canonical catalog and approves no identity.  Frozen
inputs are opened read-only.  No database, corpus, source, launcher, or Qdrant
writes occur.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import nova_drl_identity_catalog_audit_v2_0_0_a2 as base


VERSION = "2.0.0-a3"
SCHEMA = "nova-drl-identity-catalog-audit-v2-a3"

DEFAULT_DB = base.DEFAULT_DB
DEFAULT_LOSSLESS_EVENTS = base.DEFAULT_LOSSLESS_EVENTS
DEFAULT_LAUNCHERS = base.DEFAULT_LAUNCHERS
DEFAULT_BENCHMARKS = base.DEFAULT_BENCHMARKS


# These equivalences came directly from the DRL type contract supplied for the
# audit.  Only user-confirmed aliases are canonicalized.  Other apparent type
# typos remain separate review candidates.
USER_CONFIRMED_TYPE_ALIASES: Mapping[str, str] = {
    "ALIGNER": "PREALIGNER",
    "PREALIGNER": "PREALIGNER",
    "CONTROLLER": "CNTL",
    "CNTL": "CNTL",
    "POWER SUPPLY": "PS",
    "PS": "PS",
    "RBT": "RBT",
    "ROBOT": "RBT",
    "RBT ARM": "RBT ARM",
    "ROBOT ARM": "RBT ARM",
    "SERVO DRIVER": "SVO DRV",
    "SVO DRIVER": "SVO DRV",
    "SVO DRV": "SVO DRV",
    "SVO-DRV": "SVO DRV",
}


# Known valid category labels observed in the frozen family-label vocabulary.
# They make conservative recovery possible when an otherwise recognizable
# label is missing the normal spaced dash.  The list does not grant identity
# approval; unknown explicit types are still inventoried and flagged.
KNOWN_CANONICAL_TYPES: Set[str] = {
    "AMP",
    "APERTURE ASSY",
    "BIBS",
    "BRD",
    "BRG ASSY",
    "BRUSH RING",
    "CABLE",
    "CHILLER",
    "CHUCK ASSY.",
    "COMPUTER",
    "CNTL",
    "CPU",
    "DAQ MODULE",
    "DIAGNOSTIC MODULE",
    "DISPLAY PANEL",
    "DOOR KIT",
    "DRILL ASSY",
    "ELEVATOR ASSEMBLY",
    "END EFFECTOR",
    "ENG SERVICES",
    "FLIP MODULE",
    "GEAR BOX",
    "HEAT EXCHANGER",
    "HUB",
    "INDEXER",
    "INVERTER",
    "LAMP DRIVER",
    "LASER",
    "LASER HEAD",
    "LASER WAFER MAPPER",
    "LIGHT SOURCE",
    "LIGHTHOUSE ASSY",
    "LINEAR TRACK",
    "LPT",
    "MOD",
    "MONITOR",
    "MR",
    "MTR",
    "PARTICLE COUNTER",
    "PENDANT",
    "PREALIGNER",
    "PUMP",
    "PUMP DRIVER",
    "RAIL",
    "RBT",
    "RBT ARM",
    "RBT ARM SPINDLE",
    "REGULATOR",
    "RF GEN",
    "RF MATCH",
    "SENSOR",
    "SHUTTER ASSY",
    "SOLENOID",
    "STAGE",
    "SVO AMP",
    "SVO DRV",
    "SVO MTR",
    "SWITCH",
    "THERM ARRAY",
    "THERM CNTL",
    "TRANSFORMER",
    "VALVE",
    "VALVE THROTTLE",
    "WAFER LIFT",
    "WELDER",
}


def clean(value: Any) -> str:
    return base.clean(value)


def part_comparison_key(value: Any) -> str:
    """Diagnostic-only PN key; never identity equality."""
    return base.norm(value)


def exact_part_key(value: Any) -> str:
    """Case-insensitive exact PN key that preserves punctuation."""
    return base.exact_token_key(value)


def exact_type_key(value: Any) -> str:
    """Case-insensitive exact type key that preserves internal punctuation."""
    return unicodedata.normalize("NFKC", clean(value)).upper()


def type_alias_lookup_key(value: Any) -> str:
    """Lookup key used only against the explicit alias table above."""
    text = unicodedata.normalize("NFKC", clean(value)).upper()
    text = re.sub(r"[_-]+", " ", text)
    return " ".join(text.split())


TYPE_ALIAS_LOOKUP: Dict[str, str] = {
    type_alias_lookup_key(alias): canonical
    for alias, canonical in USER_CONFIRMED_TYPE_ALIASES.items()
}


def canonicalize_equipment_type(raw_type: Any) -> Dict[str, Any]:
    exact = exact_type_key(raw_type)
    lookup = type_alias_lookup_key(raw_type)
    canonical = TYPE_ALIAS_LOOKUP.get(lookup)
    if canonical:
        status = "canonical"
        if exact != exact_type_key(canonical):
            status = "user_confirmed_alias"
        return {
            "raw_equipment_type": clean(raw_type),
            "equipment_type": canonical,
            "type_status": status,
            "type_recognized": True,
        }

    if exact in KNOWN_CANONICAL_TYPES:
        return {
            "raw_equipment_type": clean(raw_type),
            "equipment_type": exact,
            "type_status": "canonical",
            "type_recognized": True,
        }

    return {
        "raw_equipment_type": clean(raw_type),
        "equipment_type": exact,
        "type_status": "unrecognized_explicit_type",
        "type_recognized": False,
    }


def split_compound_family(value: Any) -> List[str]:
    """Split source co-occurrences before any identity parsing."""
    raw = clean(value)
    if not raw:
        return []
    return [clean(segment) for segment in re.split(r"\s*\|\s*", raw) if clean(segment)]


def _prefix_pattern(value: str) -> str:
    pieces = [re.escape(piece) for piece in clean(value).split()]
    return r"\s+".join(pieces)


KNOWN_TYPE_PREFIXES: Tuple[str, ...] = tuple(
    sorted(
        KNOWN_CANONICAL_TYPES.union(USER_CONFIRMED_TYPE_ALIASES),
        key=lambda value: (-len(value), value.casefold()),
    )
)


def _known_type_prefix_parse(segment: str) -> Optional[Tuple[str, str, str, List[str]]]:
    """Recover a missing/nonstandard delimiter only for a known type prefix."""
    for type_name in KNOWN_TYPE_PREFIXES:
        match = re.match(
            rf"^(?P<type>{_prefix_pattern(type_name)})(?P<sep>\s*[-–—]\s*|_+|\s+)(?P<rest>.+)$",
            segment,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        rest = clean(match.group("rest"))
        flags = ["nonstandard_type_part_delimiter"]
        if re.match(r"^0\s+\S+", rest):
            rest = clean(rest[1:])
            flags.append("ocr_zero_used_as_delimiter")
        return clean(match.group("type")), rest, "known_type_prefix_recovery", flags
    return None


def parse_family_segment(value: Any) -> Dict[str, Any]:
    """Parse one label segment into exactly one type-scoped Part # candidate."""
    segment = clean(value)
    result: Dict[str, Any] = {
        "segment": segment,
        "status": "unparsed",
        "reason": None,
        "raw_equipment_type": None,
        "equipment_type": None,
        "type_status": None,
        "type_recognized": False,
        "part_number": None,
        "part_number_key": None,
        "comparison_key": None,
        "description": "",
        "delimiter_style": None,
        "parse_flags": [],
    }
    if not segment:
        result["reason"] = "empty_family_segment"
        return result

    # The normal contract has whitespace before its boundary dash.  This also
    # permits legacy missing whitespace after the dash and Unicode dash forms.
    match = re.match(r"^(?P<type>.+?)\s+(?P<dash>[-–—])\s*(?P<rest>.*)$", segment)
    if match:
        raw_type = clean(match.group("type"))
        rest = clean(match.group("rest"))
        dash = match.group("dash")
        exact_standard = bool(re.match(r"^.+?\s+-\s+\S", segment))
        if dash != "-":
            delimiter_style = "unicode_dash"
            parse_flags = ["nonstandard_type_part_delimiter"]
        elif exact_standard:
            delimiter_style = "standard_spaced_dash"
            parse_flags = []
        else:
            delimiter_style = "dash_spacing_variant"
            parse_flags = ["nonstandard_type_part_delimiter"]
    else:
        recovered = _known_type_prefix_parse(segment)
        if recovered is None:
            result["reason"] = "missing_or_unrecognized_type_part_boundary"
            return result
        raw_type, rest, delimiter_style, parse_flags = recovered

    if not raw_type:
        result["reason"] = "missing_equipment_type"
        return result
    if not rest:
        result["reason"] = "missing_part_number"
        result["raw_equipment_type"] = raw_type
        return result

    pieces = rest.split(None, 1)
    part_number = pieces[0].strip("\"',:;[]{}")
    description = clean(pieces[1]) if len(pieces) > 1 else ""
    if not part_number or not part_comparison_key(part_number):
        result["reason"] = "missing_part_number"
        result["raw_equipment_type"] = raw_type
        return result

    type_result = canonicalize_equipment_type(raw_type)
    comparison_key = part_comparison_key(part_number)
    if not any(char.isalpha() for char in exact_type_key(raw_type)):
        parse_flags.append("invalid_equipment_type")
    elif not type_result["type_recognized"]:
        parse_flags.append("unrecognized_equipment_type")
    if type_result["type_status"] == "user_confirmed_alias":
        parse_flags.append("user_confirmed_type_alias")

    result.update(type_result)
    result.update(
        {
            "status": "parsed",
            "reason": None,
            "part_number": part_number,
            "part_number_key": exact_part_key(part_number),
            "comparison_key": comparison_key,
            "description": description,
            "delimiter_style": delimiter_style,
            "parse_flags": sorted(set(parse_flags)),
        }
    )
    return result


def event_family_values(row: Mapping[str, Any]) -> List[str]:
    return base.event_family_values(row)


def audit_lossless_events(path: Path) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "file": base.file_metadata(path, include_sha256=False),
        "status": "missing",
        "repair_event_rows": 0,
        "processed_fact_rows": 0,
        "missing_family_rows": 0,
        "invalid_json_rows": 0,
        "invalid_json_examples": [],
        "distinct_raw_family_labels": 0,
        "distinct_family_segments": 0,
        "compound_source_labels": 0,
        "compound_source_event_references": 0,
        "raw_family_counts": {},
        "family_counts": {},
        "family_event_ids": {},
        "segment_source_labels": {},
        "compound_label_counts": {},
        "sha256": None,
    }
    if not path.exists():
        return result

    raw_family_events: Dict[str, Set[str]] = defaultdict(set)
    segment_events: Dict[str, Set[str]] = defaultdict(set)
    segment_sources: Dict[str, Set[str]] = defaultdict(set)
    compound_events: Dict[str, Set[str]] = defaultdict(set)
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for line_number, raw_line in enumerate(stream, 1):
            digest.update(raw_line)
            if not raw_line.strip():
                continue
            result["repair_event_rows"] += 1
            try:
                row = json.loads(raw_line.decode("utf-8", errors="replace"))
            except Exception as exc:
                result["invalid_json_rows"] += 1
                if len(result["invalid_json_examples"]) < 5:
                    result["invalid_json_examples"].append(
                        {"line": line_number, "error": str(exc)}
                    )
                continue
            if not isinstance(row, dict):
                result["invalid_json_rows"] += 1
                continue
            if isinstance(row.get("facts"), dict):
                result["processed_fact_rows"] += 1
            raw_families = event_family_values(row)
            if not raw_families:
                result["missing_family_rows"] += 1
                continue

            event_id = clean(row.get("repair_event_id")) or f"line_{line_number}"
            for source_label in raw_families:
                raw_family_events[source_label].add(event_id)
                segments = split_compound_family(source_label)
                if len(segments) > 1:
                    compound_events[source_label].add(event_id)
                for segment in segments:
                    segment_events[segment].add(event_id)
                    segment_sources[segment].add(source_label)

    result["sha256"] = digest.hexdigest()
    result["raw_family_counts"] = {
        label: len(ids)
        for label, ids in sorted(raw_family_events.items(), key=lambda item: item[0].casefold())
    }
    result["family_counts"] = {
        segment: len(ids)
        for segment, ids in sorted(segment_events.items(), key=lambda item: item[0].casefold())
    }
    result["family_event_ids"] = {
        segment: sorted(ids)
        for segment, ids in sorted(segment_events.items(), key=lambda item: item[0].casefold())
    }
    result["segment_source_labels"] = {
        segment: sorted(labels, key=str.casefold)
        for segment, labels in sorted(segment_sources.items(), key=lambda item: item[0].casefold())
    }
    result["compound_label_counts"] = {
        label: len(ids)
        for label, ids in sorted(compound_events.items(), key=lambda item: item[0].casefold())
    }
    result["distinct_raw_family_labels"] = len(raw_family_events)
    result["distinct_family_segments"] = len(segment_events)
    result["compound_source_labels"] = len(compound_events)
    result["compound_source_event_references"] = sum(
        len(ids) for ids in compound_events.values()
    )
    result["status"] = (
        "complete_with_json_errors"
        if result["invalid_json_rows"]
        else "complete"
    )
    return result


def _candidate_identity_key(equipment_type: str, part_number_key: str) -> str:
    return f"{equipment_type}::{part_number_key}"


def family_identity_inventory(lossless_audit: Mapping[str, Any]) -> Dict[str, Any]:
    family_counts: Mapping[str, int] = lossless_audit.get("family_counts") or {}
    family_event_ids: Mapping[str, Sequence[str]] = (
        lossless_audit.get("family_event_ids") or {}
    )
    segment_sources: Mapping[str, Sequence[str]] = (
        lossless_audit.get("segment_source_labels") or {}
    )

    groups: Dict[str, Dict[str, Any]] = {}
    unparsed: List[Dict[str, Any]] = []
    parsed_segments: Dict[str, Dict[str, Any]] = {}

    for segment, count in family_counts.items():
        parsed = parse_family_segment(segment)
        parsed_segments[segment] = parsed
        if parsed["status"] != "parsed":
            unparsed.append(
                {
                    "equipment_family_segment": segment,
                    "repair_event_count": count,
                    "reason": parsed["reason"],
                    "source_labels": list(segment_sources.get(segment) or []),
                }
            )
            continue

        identity_key = _candidate_identity_key(
            parsed["equipment_type"], parsed["part_number_key"]
        )
        group = groups.setdefault(
            identity_key,
            {
                "identity_key": identity_key,
                "equipment_type": parsed["equipment_type"],
                "part_number_key": parsed["part_number_key"],
                "comparison_key": parsed["comparison_key"],
                "raw_equipment_types": set(),
                "raw_part_numbers": set(),
                "mapped_families": set(),
                "source_labels": set(),
                "source_compound_labels": set(),
                "event_ids": set(),
                "delimiter_styles": set(),
                "parse_flags": set(),
            },
        )
        group["raw_equipment_types"].add(parsed["raw_equipment_type"])
        group["raw_part_numbers"].add(parsed["part_number"])
        group["mapped_families"].add(segment)
        group["event_ids"].update(family_event_ids.get(segment) or [])
        group["delimiter_styles"].add(parsed["delimiter_style"])
        group["parse_flags"].update(parsed["parse_flags"])
        for source in segment_sources.get(segment) or [segment]:
            group["source_labels"].add(source)
            if len(split_compound_family(source)) > 1:
                group["source_compound_labels"].add(source)

    unparsed.sort(
        key=lambda row: (-row["repair_event_count"], row["equipment_family_segment"].casefold())
    )
    return {
        "groups": groups,
        "parsed_segments": parsed_segments,
        "unparsed": unparsed,
    }


def _sorted_families(values: Sequence[str], family_counts: Mapping[str, int]) -> List[str]:
    return sorted(
        set(values),
        key=lambda value: (-base.int_value(family_counts.get(value)), value.casefold()),
    )


def audit_identities(
    lossless_audit: Mapping[str, Any],
    benchmarks: Sequence[str],
    _oem_min_families: int = 3,
) -> Dict[str, Any]:
    family_counts: Mapping[str, int] = lossless_audit.get("family_counts") or {}
    inventory = family_identity_inventory(lossless_audit)
    groups: Mapping[str, Dict[str, Any]] = inventory["groups"]

    comparison_to_identities: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
    exact_part_to_identities: Dict[str, Set[str]] = defaultdict(set)
    for identity_key, group in groups.items():
        comparison_to_identities[
            (group["equipment_type"], group["comparison_key"])
        ].add(identity_key)
        exact_part_to_identities[group["part_number_key"]].add(identity_key)

    candidates: List[Dict[str, Any]] = []
    for identity_key, group0 in groups.items():
        group = dict(group0)
        family_segments = _sorted_families(group["mapped_families"], family_counts)
        same_type_collision_ids = sorted(
            comparison_to_identities[
                (group["equipment_type"], group["comparison_key"])
            ],
            key=str.casefold,
        )
        cross_type_ids = sorted(
            exact_part_to_identities[group["part_number_key"]] - {identity_key},
            key=str.casefold,
        )

        critical: List[str] = []
        review: List[str] = sorted(group["parse_flags"])
        if len(group["comparison_key"]) < 2:
            critical.append("part_number_too_short")
        if len(same_type_collision_ids) > 1:
            critical.append("normalization_collision_within_type")
        if cross_type_ids:
            critical.append("cross_type_part_number")
        if "invalid_equipment_type" in review:
            review.remove("invalid_equipment_type")
            critical.append("invalid_equipment_type")
        if len(family_segments) > 1:
            review.append("family_label_variants")
        if group["source_compound_labels"]:
            review.append("compound_source_cooccurrence")
        if len(group["raw_part_numbers"]) > 1:
            review.append("part_number_case_variants")
        if group["comparison_key"].isalpha():
            review.append("alphabetic_only_part_number")
        review = sorted(set(review))
        critical = sorted(set(critical))
        flags = critical + review

        score = int(round(10 * math.log10(len(group["event_ids"]) + 1)))
        weights = {
            "normalization_collision_within_type": 140,
            "cross_type_part_number": 130,
            "invalid_equipment_type": 120,
            "part_number_too_short": 110,
            "unrecognized_equipment_type": 70,
            "nonstandard_type_part_delimiter": 55,
            "ocr_zero_used_as_delimiter": 55,
            "family_label_variants": 35,
            "compound_source_cooccurrence": 25,
            "alphabetic_only_part_number": 20,
            "user_confirmed_type_alias": 10,
            "part_number_case_variants": 10,
        }
        score += sum(weights.get(flag, 0) for flag in flags)

        raw_part_numbers = sorted(group["raw_part_numbers"], key=str.casefold)
        candidate = {
            "identity_key": identity_key,
            "equipment_type": group["equipment_type"],
            "part_number": raw_part_numbers[0],
            "part_number_key": group["part_number_key"],
            "comparison_key": group["comparison_key"],
            "raw_equipment_types": sorted(group["raw_equipment_types"], key=str.casefold),
            "raw_part_numbers": raw_part_numbers,
            "mapped_families": family_segments,
            "observed_v1_6_0_families": family_segments,
            "source_compound_labels": sorted(
                group["source_compound_labels"], key=str.casefold
            ),
            "delimiter_styles": sorted(group["delimiter_styles"]),
            "observed_event_count": len(group["event_ids"]),
            "observed_event_weight": len(group["event_ids"]),
            "same_type_collision_identity_keys": same_type_collision_ids,
            "cross_type_identity_keys": cross_type_ids,
            "flags": flags,
            "critical_flags": critical,
            "review_flags": review,
            "disposition": "quarantine_candidate" if critical else "review_candidate",
            "review_score": score,
        }
        candidates.append(candidate)

    candidates.sort(
        key=lambda row: (
            -row["review_score"],
            -row["observed_event_count"],
            row["identity_key"].casefold(),
        )
    )
    by_identity = {row["identity_key"]: row for row in candidates}

    collisions: List[Dict[str, Any]] = []
    for (equipment_type, comparison_key), identity_keys0 in comparison_to_identities.items():
        identity_keys = sorted(identity_keys0, key=str.casefold)
        if len(identity_keys) < 2:
            continue
        rows = [by_identity[key] for key in identity_keys]
        families = _sorted_families(
            [family for row in rows for family in row["mapped_families"]],
            family_counts,
        )
        collisions.append(
            {
                "equipment_type": equipment_type,
                "comparison_key": comparison_key,
                "exact_part_numbers": sorted(
                    {row["part_number_key"] for row in rows}, key=str.casefold
                ),
                "identity_keys": identity_keys,
                "families": families,
                "observed_event_count": sum(row["observed_event_count"] for row in rows),
            }
        )
    collisions.sort(
        key=lambda row: (-row["observed_event_count"], row["equipment_type"], row["comparison_key"])
    )

    cross_type: List[Dict[str, Any]] = []
    for part_key, identity_keys0 in exact_part_to_identities.items():
        identity_keys = sorted(identity_keys0, key=str.casefold)
        types = sorted({by_identity[key]["equipment_type"] for key in identity_keys})
        if len(types) < 2:
            continue
        rows = [by_identity[key] for key in identity_keys]
        cross_type.append(
            {
                "part_number_key": part_key,
                "equipment_types": types,
                "identity_keys": identity_keys,
                "families": _sorted_families(
                    [family for row in rows for family in row["mapped_families"]],
                    family_counts,
                ),
                "observed_event_count": sum(row["observed_event_count"] for row in rows),
            }
        )
    cross_type.sort(
        key=lambda row: (-row["observed_event_count"], row["part_number_key"])
    )

    by_exact_part: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_comparison: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_exact_part[candidate["part_number_key"]].append(candidate)
        by_comparison[candidate["comparison_key"]].append(candidate)

    benchmark_rows: List[Dict[str, Any]] = []
    for benchmark in benchmarks:
        exact_key = exact_part_key(benchmark)
        comparison_key = part_comparison_key(benchmark)
        exact_rows = sorted(
            by_exact_part.get(exact_key, []), key=lambda row: row["identity_key"]
        )
        normalization_only = sorted(
            {
                row["identity_key"]
                for row in by_comparison.get(comparison_key, [])
                if row["part_number_key"] != exact_key
            },
            key=str.casefold,
        )
        exact_types = sorted({row["equipment_type"] for row in exact_rows})
        if len(exact_types) > 1:
            status = "observed_exact_cross_type_ambiguous"
        elif exact_rows and any(row["critical_flags"] for row in exact_rows):
            status = "observed_exact_quarantined"
        elif exact_rows:
            status = "observed_exact_pending_human_review"
        elif normalization_only:
            status = "normalization_only_match_not_accepted"
        else:
            status = "absent"
        benchmark_rows.append(
            {
                "part_number": benchmark,
                "part_number_key": exact_key,
                "comparison_key": comparison_key,
                "status": status,
                "equipment_types": exact_types,
                "identity_keys": [row["identity_key"] for row in exact_rows],
                "flags": sorted({flag for row in exact_rows for flag in row["flags"]}),
                "observed_v1_6_0_families": _sorted_families(
                    [family for row in exact_rows for family in row["mapped_families"]],
                    family_counts,
                ),
                "normalization_only_candidates": normalization_only,
            }
        )

    disposition_counts = Counter(row["disposition"] for row in candidates)
    flag_counts = Counter(flag for row in candidates for flag in row["flags"])
    unparsed = inventory["unparsed"]
    return {
        "raw_identity_candidates": len(candidates),
        "parsed_family_segments": len(inventory["parsed_segments"]) - len(unparsed),
        "unparsed_family_segment_count": len(unparsed),
        "disposition_counts": dict(disposition_counts),
        "flag_counts": dict(flag_counts),
        "normalization_collisions_within_type": collisions,
        "normalization_collisions": collisions,
        "cross_type_part_numbers": cross_type,
        "family_label_variant_candidates": [
            row for row in candidates if "family_label_variants" in row["flags"]
        ],
        "multi_family_mappings": [
            row for row in candidates if "family_label_variants" in row["flags"]
        ],
        "review_queue": candidates,
        "all_candidates": candidates,
        "unparsed_v1_6_0_family_segments": unparsed,
        "unmatched_v1_6_0_families": unparsed,
        "benchmarks": benchmark_rows,
        "policy": {
            "identity_candidate_source": (
                "first_token_after_type_boundary_in_split_v1_6_0_family_segment"
            ),
            "identity_key_fields": ["canonical_equipment_type", "exact_part_number"],
            "compound_labels_split_before_identity_parse": True,
            "description_tokens_are_identity_candidates": False,
            "sqlite_identity_authority": False,
            "punctuation_preserved_for_identity": True,
            "comparison_key_is_identity": False,
            "candidate_auto_approval": False,
            "normalization_collision_auto_merge": False,
            "cross_type_auto_merge": False,
            "family_label_variant_auto_merge": False,
            "user_confirmed_type_aliases_only": True,
            "review_strategy": "all_hazards_plus_ranked_recurring_80_20",
        },
    }


def build_audit(
    knowledge_db: Path,
    lossless_events: Path,
    launcher_paths: Sequence[Path],
    benchmarks: Sequence[str],
    oem_min_families: int = 3,
) -> Dict[str, Any]:
    launchers = base.audit_launchers(launcher_paths)
    sqlite_result = base.audit_sqlite(knowledge_db)
    lossless = audit_lossless_events(lossless_events)
    identities = audit_identities(lossless, benchmarks, oem_min_families)
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "built_at_utc": base.now_utc(),
        "status": base.overall_status(launchers, sqlite_result, lossless),
        "launchers": launchers,
        "sqlite": sqlite_result,
        "lossless_v1_6_0": lossless,
        "identity_audit": identities,
        "safety": {
            "source_files_opened_read_only": True,
            "database_identity_rows_used": 0,
            "database_writes": 0,
            "corpus_writes": 0,
            "source_traveler_writes": 0,
            "approved_identities_created": 0,
            "normalized_identity_merges": 0,
            "cross_type_identity_merges": 0,
            "accepted_facts_changed": 0,
            "qdrant": "OFF",
            "production_launcher_changed": False,
            "resolver_deployed": False,
        },
    }


def fmt_count(value: Any) -> str:
    return base.fmt_count(value)


def render_summary(report: Mapping[str, Any], review_limit: int) -> str:
    launchers = report["launchers"]
    sqlite_result = report["sqlite"]
    lossless = report["lossless_v1_6_0"]
    identities = report["identity_audit"]

    lines = [
        "=" * 96,
        f"NOVA DRL TYPE-FIRST IDENTITY CATALOG AUDIT  |  v{VERSION}",
        "=" * 96,
        f"Status: {report['status']}",
        "Mode: READ-ONLY AUDIT — ZERO IDENTITIES APPROVED",
        "",
        "PARSER CONTRACT",
        "---------------",
        "1. Split every pipe-joined source label into equipment segments.",
        "2. Parse TYPE - PART_NUMBER description within each segment.",
        "3. Take only the first post-type token as the DRL Part #.",
        "4. Fence identity by (canonical TYPE, exact punctuation-preserving Part #).",
        "5. Never auto-merge punctuation variants, type conflicts, or apparent typos.",
        "",
        "CURRENT LAUNCHERS",
        "-----------------",
    ]
    for row in launchers["paths"]:
        state = "FOUND" if row.get("exists") or row.get("is_symlink") else "MISSING"
        lines.append(f"{state:7} {row['path']}")
        if state == "FOUND":
            lines.append(f"        resolved: {row.get('resolved_path') or '-'}")
            lines.append(
                "        versions: "
                + (", ".join(row.get("version_mentions") or []) or "none detected")
            )
            for target in row.get("script_targets") or []:
                lines.append(f"        target:   {target}")

    lines.extend(
        [
            "",
            "SOURCE INVENTORY",
            "----------------",
            f"Knowledge DB: {sqlite_result['file']['path']}",
            f"DB status: {sqlite_result['status']} | mode={sqlite_result['open_mode']}",
            "DB rows: "
            + ", ".join(
                f"{key}={fmt_count(value)}"
                for key, value in sqlite_result.get("table_counts", {}).items()
            ),
            "SQLite identity role: INVENTORY ONLY — NOT AUTHORITY",
            f"Lossless events: {lossless['file']['path']}",
            f"Lossless status: {lossless['status']}",
            f"Lossless repair-event rows: {fmt_count(lossless['repair_event_rows'])}",
            f"Distinct raw family labels: {fmt_count(lossless['distinct_raw_family_labels'])}",
            f"Distinct split family segments: {fmt_count(lossless['distinct_family_segments'])}",
            f"Pipe-joined source labels split first: {fmt_count(lossless['compound_source_labels'])}",
            f"Missing-family rows: {fmt_count(lossless['missing_family_rows'])}",
            f"Invalid JSON rows: {fmt_count(lossless['invalid_json_rows'])}",
            "",
            "TYPE-FIRST IDENTITY CANDIDATE AUDIT",
            "-----------------------------------",
            "Candidate source: first token after TYPE boundary in each split family segment",
            f"Parsed family segments: {fmt_count(identities['parsed_family_segments'])}",
            f"Unparsed family segments: {fmt_count(identities['unparsed_family_segment_count'])}",
            f"Type-scoped identity candidates: {fmt_count(identities['raw_identity_candidates'])}",
        ]
    )
    for key in ("review_candidate", "quarantine_candidate"):
        lines.append(
            f"{key:32} {fmt_count(identities['disposition_counts'].get(key, 0))}"
        )

    lines.extend(["", "FLAG COUNTS", "-----------"])
    if not identities["flag_counts"]:
        lines.append("None.")
    else:
        for flag, count in sorted(
            identities["flag_counts"].items(), key=lambda item: (-item[1], item[0])
        ):
            lines.append(f"{flag:48} {count:,}")

    lines.extend(
        [
            "",
            "CROSS-TYPE EXACT PART NUMBERS — NEVER AUTO-MERGE",
            "------------------------------------------------",
            f"Total cross-type groups: {len(identities['cross_type_part_numbers']):,}",
        ]
    )
    for row in identities["cross_type_part_numbers"][:review_limit]:
        lines.append(
            f"{row['part_number_key']}: " + " | ".join(row["equipment_types"])
        )
    if not identities["cross_type_part_numbers"]:
        lines.append("None observed.")

    lines.extend(
        [
            "",
            "WITHIN-TYPE NORMALIZATION COLLISIONS — NEVER AUTO-MERGE",
            "-------------------------------------------------------",
            f"Total collision groups: {len(identities['normalization_collisions_within_type']):,}",
        ]
    )
    for row in identities["normalization_collisions_within_type"][:review_limit]:
        lines.append(
            f"{row['equipment_type']} / {row['comparison_key']}: "
            + " | ".join(row["exact_part_numbers"])
        )
    if not identities["normalization_collisions_within_type"]:
        lines.append("None observed.")

    lines.extend(
        [
            "",
            f"80/20 REVIEW QUEUE — TOP {review_limit}",
            "--------------------------------",
        ]
    )
    for index, row in enumerate(identities["review_queue"][:review_limit], 1):
        flags = ", ".join(row["flags"]) or "none"
        lines.append(
            f"{index:2}. {row['equipment_type']} - {row['part_number']} "
            f"| events={row['observed_event_count']} | {row['disposition']} | {flags}"
        )
    if not identities["review_queue"]:
        lines.append("No type-first identity candidates were observed.")

    lines.extend(["", "CRITICAL DRL BENCHMARKS", "-----------------------"])
    for row in identities["benchmarks"]:
        types = ", ".join(row["equipment_types"]) or "none"
        families = row["observed_v1_6_0_families"]
        family_text = families[0] if families else "not observed"
        if len(families) > 1:
            family_text += f" (+{len(families) - 1} more)"
        lines.append(
            f"{row['part_number']:<18} {row['status']:<44} "
            f"| type={types} | {family_text}"
        )

    unparsed = identities["unparsed_v1_6_0_family_segments"]
    lines.extend(
        [
            "",
            "UNPARSED / MALFORMED FAMILY SEGMENTS — NEVER GUESS",
            "--------------------------------------------------",
            f"Total unparsed segments: {len(unparsed):,}",
        ]
    )
    for row in unparsed[:20]:
        lines.append(
            f"{row['repair_event_count']:5}  {row['reason']} | {row['equipment_family_segment']}"
        )
    if not unparsed:
        lines.append("None observed.")

    lines.extend(
        [
            "",
            "SAFETY / DECISION",
            "-----------------",
            "Existing SQLite identities treated as authority: NO",
            "Description/OEM tokens admitted as identity candidates: NO",
            "Pipe-joined neighboring equipment merged into one identity: NO",
            "Canonical identities created: 0",
            "Normalized comparison key treated as identity: NO",
            "Normalization collisions auto-merged: 0",
            "Cross-type identities auto-merged: 0",
            "Database/corpus/source writes: 0",
            "Accepted facts changed: 0",
            "Qdrant: OFF",
            "Production launcher changed: NO",
            "",
            "NEXT: review this corrected a3 audit before building any v2 catalog or resolver.",
        ]
    )
    return "\n".join(lines) + "\n"


def compact_json_report(report: Mapping[str, Any]) -> Dict[str, Any]:
    lossless_excluded = {
        "raw_family_counts",
        "family_counts",
        "family_event_ids",
        "segment_source_labels",
        "compound_label_counts",
    }
    identities = report["identity_audit"]
    return {
        "schema": report["schema"],
        "version": report["version"],
        "built_at_utc": report["built_at_utc"],
        "status": report["status"],
        "launchers": report["launchers"],
        "sqlite": dict(report["sqlite"]),
        "lossless_v1_6_0": {
            key: value
            for key, value in report["lossless_v1_6_0"].items()
            if key not in lossless_excluded
        },
        "identity_audit": {
            key: value for key, value in identities.items() if key != "all_candidates"
        },
        "safety": report["safety"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only NOVA DRL type-first identity audit v2.0.0-a3"
    )
    parser.add_argument("--knowledge-db", default=str(DEFAULT_DB))
    parser.add_argument("--lossless-events", default=str(DEFAULT_LOSSLESS_EVENTS))
    parser.add_argument(
        "--launcher",
        action="append",
        help="Launcher path to inspect; repeatable. Defaults to known NOVA launchers.",
    )
    parser.add_argument(
        "--benchmark",
        action="append",
        help="Critical DRL Part # to audit; repeatable. Defaults to the standard set.",
    )
    parser.add_argument("--review-limit", type=int, default=30)
    parser.add_argument(
        "--oem-min-families",
        type=int,
        default=3,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.review_limit < 1:
        raise SystemExit("--review-limit must be >= 1")
    if args.oem_min_families < 2:
        raise SystemExit("--oem-min-families must be >= 2")

    launchers = (
        [Path(value) for value in args.launcher]
        if args.launcher
        else list(DEFAULT_LAUNCHERS)
    )
    benchmarks = args.benchmark if args.benchmark else list(DEFAULT_BENCHMARKS)
    report = build_audit(
        Path(args.knowledge_db),
        Path(args.lossless_events),
        launchers,
        benchmarks,
        args.oem_min_families,
    )

    if args.json:
        print(json.dumps(compact_json_report(report), indent=2, ensure_ascii=False))
    else:
        print(render_summary(report, args.review_limit), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
