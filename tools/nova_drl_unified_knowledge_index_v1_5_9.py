#!/usr/bin/env python3
"""
Nova DRL 80/20 Reference-PN Resolver — Corpus-Only Product View v1.5.9

Engineer-facing presentation layer over the full v1.5.2 repair corpus and full DRL file index.

Key additions:
- DRL business rule: base product part number is the canonical repair identity; suffixes are metadata only,
- volume-based product resolver aggregates suffix/spelling/customer variants into one product view,
- structured-event parts aggregation uses every explicit facts.parts_replaced row, including replacement text when the PN field is blank,
- 80/20 Reference-PN resolver clusters recurring PN spellings/OCR variants by product and chooses a useful technician-facing reference from corpus recurrence,
- a recurring complete alphanumeric PN is preferred over a bare numeric fragment when the corpus supports it, while stable numeric cores such as 7800 remain valid when they clearly dominate,
- no expert/user PN mapping table is permitted; raw observed variants remain preserved underneath the reference PN,
- distinct repair-event union counting prevents double-counting when multiple PN spellings appear in one repair,
- Reported Failure is separated from technician Repair History,
- standard-font `Notes: FA - ...` is treated as customer-provided failure information,
- technician Repair History excludes database/admin/customer-requirement/test boilerplate,
- normal product view is minimal: product identity + recurring Parts Replaced + Reported Failure only,
- one-off part strings are suppressed from the 80/20 product list but remain searchable in the underlying corpus,
- HARD PROJECT INVARIANT: recurring product/parts knowledge is derived from corpus volume only; expert comments never project or alter counts unless explicitly promoted by Matt,
- TIMES REPLACED / TIMES SEEN render in isolated right-aligned columns on screen and PDF,
- Windows Engineer Client support for passwordless indexed queries and report transfer,
- machine-safe Base64 query modes for Windows client calls,
- server-side PDF-file generation without HTTP dependency for Windows transfer,
- clean grouped search presentation,
- strict identifier result linking (no unrelated filler for RMA/PN/order searches),
- customer PO shown separately from distributor procurement orders,
- all .picasa.ini and .picasaoriginals content hidden from normal search/report output,
- blue action hints for :pdf and :print in interactive terminals,
- clickable blue OSC-8 PDF links when the terminal supports them,
- an always-visible plain browser URL for older Windows consoles,
- LAN/IP URL preferred over hostname for workstation access,
- on-demand printable PDF reports with a local HTTP link,
- direct :print support through the server print system when available,
- no AI/LLM call for ordinary search, PDF generation, or printing.

The v1.5.3 engine rebuilds the local knowledge DB from the completed v1.5.2 full corpus. No AI/vision/NAS scan is used.
"""
from __future__ import annotations

import argparse
import base64
import collections
import difflib
import functools
import http.server
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import textwrap
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

VERSION = "1.5.9"
DEFAULT_DB = "/opt/nova-drl/index/drl_knowledge_index.sqlite"
DEFAULT_REPORTS_DIR = "/opt/nova-drl/reports"
DEFAULT_REPORT_PORT = 8765
BASE_SCRIPT = Path(__file__).with_name("nova_drl_unified_knowledge_engine_v1_5_3.py")


def load_base():
    if not BASE_SCRIPT.exists():
        raise FileNotFoundError(f"Required v1.5.3 full-corpus engine not found: {BASE_SCRIPT}")
    spec = importlib.util.spec_from_file_location("nova_drl_v153_engine", BASE_SCRIPT)
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load {BASE_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


base = load_base()


DISPLAY_GROUPS: List[Tuple[str, set[str]]] = [
    ("TRACKING / PROJECT", {"rma", "customer_po"}),
    ("PROCUREMENT / REORDER", {"order"}),
    ("EQUIPMENT / PRODUCT", {"product"}),
    ("PARTS REPLACED", {"product_part"}),
    ("REPORTED FAILURE", {"failure_pattern"}),
    ("REPAIR HISTORY", {"history_pattern", "event"}),
    ("PART OCCURRENCES", {"replacement"}),
    ("SOURCE FILES", {"file"}),
]


ANSI_BLUE = "\033[94m"
ANSI_RESET = "\033[0m"
OSC8_OPEN = "\033]8;;"
OSC8_CLOSE = "\033]8;;\033\\"
ST = "\033\\"


PRODUCT_HISTORY_LIMIT = 10
PRODUCT_FAILURE_LIMIT = 10
PRODUCT_PART_MIN_REPAIRS = 2

# DRL NOVA HARD PROJECT INVARIANT.
# These are deliberately code-level build guards, not user-tunable presentation settings.
DRL_80_20_HARD_INVARIANT = True
ALLOW_EXPERT_KNOWLEDGE_OVERRIDES = False
EXPERT_INPUT_ROLE = "sanity_check_only_unless_explicitly_promoted"

def enforce_drl_project_invariants() -> None:
    if DRL_80_20_HARD_INVARIANT is not True:
        raise RuntimeError("DRL Nova policy violation: 80/20 must remain a hard invariant")
    if ALLOW_EXPERT_KNOWLEDGE_OVERRIDES is not False:
        raise RuntimeError("DRL Nova policy violation: expert knowledge overrides are forbidden unless explicitly promoted by Matt")
    if PRODUCT_PART_MIN_REPAIRS < 2:
        raise RuntimeError("DRL Nova policy violation: normal 80/20 product parts must require recurrence")


def extract_model_token(family: str) -> str:
    """Extract the DRL product/model token from an equipment-family label.

    DRL repair grouping is based on the base product part number, not customer,
    engineer, serial, OEM spelling, or suffix metadata.  This deliberately does
    not attempt to understand every manufacturer's nomenclature.
    """
    text = compact(family)
    if not text:
        return ""
    m = re.match(r"^\s*[A-Za-z][A-Za-z0-9 /&().]*?\s*-\s*(\S+)", text)
    if m:
        return m.group(1).strip(" ,;:()[]{}")
    # Fallback for older/irregular labels without the normal "TYPE - PN" form.
    for token in text.split():
        clean = token.strip(" ,;:()[]{}")
        if len(clean) >= 4 and any(ch.isdigit() for ch in clean):
            return clean
    return ""


def _model_catalog(conn) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT equipment_family,repair_event_count,events_with_parts,indexed_component_count "
        "FROM product_families"
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        family = compact(r["equipment_family"])
        token = extract_model_token(family)
        if not token:
            continue
        out.append({
            "family": family,
            "model": token,
            "model_norm": alnum(token),
            "events": int(r["repair_event_count"] or 0),
            "events_with_parts": int(r["events_with_parts"] or 0),
            "components": int(r["indexed_component_count"] or 0),
        })
    return out


def resolve_base_product(conn, query: str) -> Optional[Dict[str, Any]]:
    """Resolve a search to a canonical DRL base product using corpus volume.

    If a suffix form is searched and the corpus also contains the shorter base
    product, the higher-volume shorter base wins.  Once the base is chosen, all
    observed model tokens equal to BASE or beginning BASE- are repair-equivalent.
    """
    q = compact(query)
    qn = alnum(q)
    if len(qn) < 3 or " " in q and len(q.split()) > 4:
        return None
    catalog = _model_catalog(conn)
    candidates = [c for c in catalog if qn in c["model_norm"] or c["model_norm"] in qn]
    if not candidates:
        return None

    # Prefer the highest-volume product token relevant to the query.  Length is
    # a tiebreaker so a stable full base PN beats an overly-short prefix.
    def score(c):
        rel = 2 if c["model_norm"] == qn else (1 if c["model_norm"] in qn else 0)
        return (c["events"], rel, len(c["model_norm"]))

    root_row = max(candidates, key=score)
    root = root_row["model"]
    root_cf = root.casefold()
    variants = [
        c for c in catalog
        if c["model"].casefold() == root_cf or c["model"].casefold().startswith(root_cf + "-")
    ]
    if not variants:
        variants = [root_row]
    families = sorted({c["family"] for c in variants}, key=str.casefold)
    # Prefer a high-volume exact-base family label for display. Manufacturer
    # spelling/customer suffixes are preserved through observed variants.
    exact = [c for c in variants if c["model"].casefold() == root_cf]
    display_row = max(exact or variants, key=lambda c: (c["events"], -len(c["family"])))
    return {
        "base_part_number": root,
        "display_family": display_row["family"],
        "families": families,
        "model_variants": sorted({c["model"] for c in variants}, key=str.casefold),
    }


def _sql_in(values: Sequence[str]) -> Tuple[str, List[str]]:
    vals = list(dict.fromkeys(values))
    return ",".join("?" for _ in vals), vals



def product_event_rows(conn, families: Sequence[str]) -> List[Dict[str, Any]]:
    if not families:
        return []
    marks, params = _sql_in(families)
    rows = conn.execute(
        f"SELECT repair_event_id,log_number,equipment_family,reported_problem_text AS reported_problem,repair_history_text AS repair_history,test_outcome_text AS test_outcome,source_paths_json "
        f"FROM repair_events WHERE equipment_family IN ({marks})",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


_PN_PLACEHOLDERS = {"", "NA", "N/A", "NONE", "NULL", "UNKNOWN", "UNK", "-"}
_PROCUREMENT_PREFIXES = ("DGK", "MSR", "NWK", "DSK")
_GENERIC_PN_WORDS = {
    "REPLACED", "REPLACE", "CHANGED", "CHANGE", "INSTALLED", "INSTALL", "SHOTGUNNED",
    "SHOTGUN", "BAD", "FAILED", "PART", "PARTS", "CAP", "CAPS", "CAPACITOR", "CAPACITORS",
    "MOSFET", "TRANSISTOR", "DIODE", "FUSE", "FUSES", "BOARD", "ASSEMBLY", "BEARING", "BELT",
    "MOTOR", "SEAL", "RESISTOR", "IC", "CHIP", "DRIVER", "RELAY", "ORDER", "EBAY", "PENS",
}


def _capacitance_core(value: Any) -> Optional[str]:
    """Return a stable capacitor value such as 33uF when visibly present."""
    text = compact(value).replace("µ", "u").replace("μ", "u")
    m = re.search(r"(?<![A-Za-z0-9])(\d{1,5}(?:\.\d+)?)\s*[uU]\s*[fF]\b", text)
    if not m:
        return None
    n = m.group(1)
    if n.endswith('.0'):
        n = n[:-2]
    return f"{n}uF"


def _capacitance_cores(value: Any) -> List[str]:
    text = compact(value).replace("µ", "u").replace("μ", "u")
    out = []
    for m in re.finditer(r"(?<![A-Za-z0-9])(\d{1,5}(?:\.\d+)?)\s*[uU]\s*[fF]\b", text):
        n = m.group(1)
        if n.endswith('.0'):
            n = n[:-2]
        out.append(f"{n}uF")
    return list(dict.fromkeys(out))


def _pn_clean(value: Any) -> str:
    return compact(value).upper().replace("µ", "U").replace("μ", "U").strip(" ,;:()[]{}")


def _pn_placeholder(value: Any) -> bool:
    return _pn_clean(value) in _PN_PLACEHOLDERS


def _pn_norm(value: Any) -> str:
    return alnum(_pn_clean(value))


def _pn_compare_norm(value: Any) -> str:
    """Comparison form used only for clustering, never as the displayed PN.

    A leading three-digit distributor/catalog prefix may be ignored for comparison
    when the remainder is itself a mixed alphanumeric PN.  The original spelling
    is always retained as evidence.
    """
    n = _pn_norm(value)
    m = re.match(r"^\d{3}([A-Z].*\d.*)$", n)
    if m and len(m.group(1)) >= 6:
        n = m.group(1)
    return n


def _pn_candidate_usable(value: Any) -> bool:
    s = _pn_clean(value)
    if not s or _pn_placeholder(s):
        return False
    n = _pn_norm(s)
    if len(n) < 3:
        return False
    if n.startswith(_PROCUREMENT_PREFIXES):
        return False
    # Reject whole descriptive phrases; a reference PN should look like a code.
    words = re.findall(r"[A-Z0-9./+-]+", s)
    if len(words) > 4:
        return False
    if words and all(w in _GENERIC_PN_WORDS for w in words):
        return False
    return any(ch.isdigit() for ch in n)


def _candidate_display(value: Any) -> str:
    s = _pn_clean(value)
    s = re.sub(r"^(?:P/?N|PART\s*(?:NO|NUMBER)?)\s*[:#-]*\s*", "", s, flags=re.I)
    s = re.sub(r"^X?\d+\s*[-X]\s*", "", s, flags=re.I)
    s = compact(s)
    # For compact IC/semiconductor PNs, spaces are usually OCR/layout noise.
    # Keep spacing for fuse/spec style references where it aids readability.
    if not re.search(r"\b(?:AMP|AMPS|VOLT|VOLTS|FUSE)\b", s, flags=re.I):
        s = re.sub(r"(?<=[A-Z0-9])\s+(?=[A-Z0-9])", "", s)
    return s.strip(" ,;:")


def _text_pn_candidates(text: Any, raw_norms: set[str]) -> List[str]:
    """Extract conservative PN-like tokens from explicit parts_replaced text.

    Text is used only because the structured event already says this is a
    replacement part.  Numeric-only text tokens are accepted only when the same
    number is supported by an observed raw PN elsewhere in this product.
    """
    s = _pn_clean(text)
    if not s:
        return []
    # Remove work/action words before PN token scanning so phrases such as
    # "Replaced 38AN08A1" cannot become the fake PN REPLACED38AN08A1.
    s = re.sub(r"\b(?:REPLACED?|CHANGED?|INSTALLED?|SHOTGUNNED?|BAD|FAILED|PARTS?|COMPONENTS?)\b\s*[:#=-]*\s*", " ", s, flags=re.I)
    s = compact(s)
    out: List[str] = []

    # Mixed alphanumeric candidates, including forms such as HCPL-7800,
    # FDH038AN08A1, IXFX 24N/100 Q3, 26C31 and 0325015.HXP.
    patterns = [
        r"\b(?:\d{3}-)?[A-Z]{1,8}[- ]?\d{2,}[A-Z0-9./-]*(?:\s+Q\d+|\s+IR|\s+TPI)?\b",
        r"\b\d{2,}[A-Z][A-Z0-9./-]*\b",
        r"\b\d{4,}\.[A-Z]{1,6}\b",
        r"\b0?[A-Z]{2,6}\d{2,}(?:\.[A-Z0-9]+)?\b",
    ]
    for pat in patterns:
        for m in re.finditer(pat, s):
            cand = _candidate_display(m.group(0))
            if _pn_candidate_usable(cand):
                n = _pn_norm(cand)
                # Unit/spec fragments alone are not useful PNs.
                if re.fullmatch(r"\d+(?:AMP|A|V|UF|PF)", n):
                    continue
                out.append(cand)

    # Numeric-only core tokens such as 7800 are allowed from text only when
    # they already exist as an explicit raw PN/inside an explicit raw PN in the
    # same product.  This blocks text-only OCR fragments such as 1002.
    for m in re.finditer(r"\b\d{4,8}\b", s):
        token = m.group(0)
        if 1900 <= int(token) <= 2099 and len(token) == 4:
            continue
        if any(token == rn or token in rn for rn in raw_norms):
            out.append(token)

    seen = set()
    clean: List[str] = []
    for cand in out:
        n = _pn_norm(cand)
        if n and n not in seen:
            seen.add(n)
            clean.append(cand)
    return clean


def _digit_signature(value: Any) -> str:
    return "".join(re.findall(r"\d", _pn_compare_norm(value)))


def _alpha_signature(value: Any) -> str:
    return "".join(re.findall(r"[A-Z]", _pn_compare_norm(value)))


def _variant_related(a: str, b: str) -> bool:
    """Product-local, corpus-only equivalence test for PN spellings."""
    an = _pn_compare_norm(a); bn = _pn_compare_norm(b)
    if not an or not bn:
        return False
    if an == bn:
        return True
    short, long = (an, bn) if len(an) <= len(bn) else (bn, an)
    if len(short) >= 4 and short in long and len(short) / max(1, len(long)) >= 0.45:
        return True

    ad, bd = _digit_signature(an), _digit_signature(bn)
    aa, ba = _alpha_signature(an), _alpha_signature(bn)
    seq = difflib.SequenceMatcher(None, an, bn).ratio()
    if ad and bd:
        if ad == bd and seq >= 0.72:
            return True
        if len(ad) == len(bd) and len(ad) >= 4 and _one_digit_apart(ad, bd) and seq >= 0.80:
            return True
    if aa and ba and seq >= 0.84:
        return True
    return False


def _one_digit_apart(a: str, b: str) -> bool:
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) <= 1


def _display_reference_pn(cluster: Dict[str, Any]) -> Optional[str]:
    """Choose one technician-facing REFERENCE PN from a recurrence cluster.

    The reference is never an expert mapping.  It is selected only from observed
    corpus variants.  A complete alphanumeric PN may replace a shorter observed
    form when it has meaningful recurring support; a strongly dominant stable
    numeric core (e.g. 7800) remains the reference when longer forms are sparse.
    """
    if cluster.get("kind") == "capacitance":
        return cluster.get("cap")

    stats = []
    for norm, info in cluster["variants"].items():
        support = len(info["events"])
        display = info["display"].most_common(1)[0][0]
        cmpn = _pn_compare_norm(display)
        if not cmpn or not _pn_candidate_usable(display):
            continue
        stats.append({
            "norm": norm, "cmp": cmpn, "display": _candidate_display(display),
            "support": support, "events": info["events"],
            "numeric": cmpn.isdigit(),
            "alpha_count": sum(ch.isalpha() for ch in cmpn),
            "length": len(cmpn),
        })
    if not stats:
        return None

    dominant = max(stats, key=lambda c: (c["support"], c["alpha_count"] > 0, c["length"]))
    chosen = dominant

    # Promote to a more complete recurring observed PN only when volume supports
    # the expansion.  Numeric cores demand stronger support so 7800 does not turn
    # into a rare HCPL/packaging variant merely because it is longer.
    threshold_ratio = 0.50 if dominant["numeric"] else 0.25
    threshold = max(2, int((dominant["support"] * threshold_ratio) + 0.9999))
    expansions = []
    for c in stats:
        if c["numeric"] or c["support"] < threshold:
            continue
        if dominant["cmp"] in c["cmp"] or _variant_related(dominant["display"], c["display"]):
            # Longer/completer forms are useful only when they actually add alpha
            # identity, not just arbitrary packaging characters.
            if c["length"] >= dominant["length"] and c["alpha_count"] >= dominant["alpha_count"]:
                expansions.append(c)
    if expansions:
        chosen = max(expansions, key=lambda c: (c["length"], c["alpha_count"], c["support"]))

    return chosen["display"]


def aggregate_product_parts(conn, families: Sequence[str], base_part_number: Optional[str] = None) -> List[Dict[str, Any]]:
    """80/20 Reference-PN ranking from explicit structured parts_replaced evidence.

    v1.5.9 keeps v1.5.8's correct event-level counting but replaces the overly
    literal numeric-core labels with product-local recurrence clustering:
    - all evidence comes from explicit structured `parts_replaced` rows;
    - raw PN and replacement text can both contribute PN signals;
    - obvious punctuation/OCR/prefix/suffix variants cluster within a product;
    - a complete recurring alphanumeric PN is preferred as REFERENCE PN when the
      corpus supports it;
    - a strongly dominant stable numeric core remains valid when it is the true
      recurring reference (e.g. 7800);
    - one-off noise is suppressed from the normal 80/20 view;
    - counts are DISTINCT repair-event unions;
    - no expert/user PN mappings or standard-kit overrides exist.
    """
    if not families:
        return []
    marks, params = _sql_in(families)
    rows = [dict(r) for r in conn.execute(
        f"SELECT repair_event_id,manufacturer_pn,quantity,text,evidence_quote "
        f"FROM replacement_mentions WHERE equipment_family IN ({marks}) "
        f"AND procurement_only_excluded=0",
        params,
    ).fetchall()]

    raw_norms: set[str] = set()
    for r in rows:
        pn = _pn_clean(r.get("manufacturer_pn"))
        if pn and not _pn_placeholder(pn) and _pn_candidate_usable(pn):
            raw_norms.add(_pn_norm(pn))

    # Candidate variant nodes.  Each node owns a set of repair events so final
    # counts can union events instead of summing already-aggregated numbers.
    nodes: Dict[str, Dict[str, Any]] = {}

    def add_signal(display: str, eid: str, *, kind: str = "pn") -> None:
        if not display or not eid:
            return
        if kind == "capacitance":
            key = f"CAP:{display.casefold()}"
            n = nodes.setdefault(key, {"kind": kind, "cap": display, "events": set(), "display": collections.Counter()})
        else:
            if not _pn_candidate_usable(display):
                return
            norm = _pn_norm(display)
            if not norm:
                return
            key = f"PN:{norm}"
            n = nodes.setdefault(key, {"kind": kind, "norm": norm, "events": set(), "display": collections.Counter()})
        n["events"].add(eid)
        n["display"][_candidate_display(display)] += 1

    for r in rows:
        eid = compact(r.get("repair_event_id"))
        if not eid:
            continue
        raw_pn = _pn_clean(r.get("manufacturer_pn"))
        text = compact(r.get("text"))
        for cap in _capacitance_cores(" ".join(x for x in (raw_pn, text) if x)):
            add_signal(cap, eid, kind="capacitance")
        if raw_pn and not _pn_placeholder(raw_pn):
            add_signal(raw_pn, eid)
        for cand in _text_pn_candidates(text, raw_norms):
            # Capacitance is already handled separately.
            if not _capacitance_core(cand):
                add_signal(cand, eid)

    keys = list(nodes)
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Exact capacitor values remain independent. PN candidates cluster by
    # product-local shape/similarity only.
    pn_keys = [k for k in keys if nodes[k]["kind"] == "pn"]
    for i, ka in enumerate(pn_keys):
        da = nodes[ka]["display"].most_common(1)[0][0]
        for kb in pn_keys[i+1:]:
            db = nodes[kb]["display"].most_common(1)[0][0]
            if _variant_related(da, db):
                union(ka, kb)

    # Low-volume numeric OCR mutations may join a much stronger numeric cluster
    # when they differ by a single digit. This is product-local 80/20 cleanup.
    numeric_keys = [k for k in pn_keys if _pn_compare_norm(nodes[k]["display"].most_common(1)[0][0]).isdigit()]
    for ka in numeric_keys:
        a = _pn_compare_norm(nodes[ka]["display"].most_common(1)[0][0])
        sa = len(nodes[ka]["events"])
        for kb in numeric_keys:
            if ka == kb:
                continue
            b = _pn_compare_norm(nodes[kb]["display"].most_common(1)[0][0])
            sb = len(nodes[kb]["events"])
            if len(a) == len(b) >= 4 and _one_digit_apart(a, b):
                if sa <= 3 and sb >= max(5, sa * 2):
                    union(kb, ka)

    clusters: Dict[str, Dict[str, Any]] = {}
    for key, node in nodes.items():
        root = find(key)
        c = clusters.setdefault(root, {
            "kind": node["kind"], "cap": node.get("cap"), "events": set(),
            "variants": {}, "evidence_nodes": 0,
        })
        c["events"].update(node["events"])
        c["evidence_nodes"] += 1
        if node["kind"] == "pn":
            norm = node["norm"]
            v = c["variants"].setdefault(norm, {"events": set(), "display": collections.Counter()})
            v["events"].update(node["events"])
            v["display"].update(node["display"])

    out: List[Dict[str, Any]] = []
    for root, c in clusters.items():
        repairs = len(c["events"])
        if repairs < PRODUCT_PART_MIN_REPAIRS:
            continue
        label = _display_reference_pn(c)
        if not label:
            continue
        observed = []
        for info in c["variants"].values():
            observed.extend([x for x, _ in info["display"].most_common()])
        observed = list(dict.fromkeys(observed))
        out.append({
            "item_type": "product_part",
            "item_key": f"reference_product_part:{alnum(label)}:{alnum(root)[:32]}",
            "primary_value": label,
            "title": label,
            "payload": {
                "pn": label,
                "reference_pn": label,
                "repairs": repairs,
                "explicit_repairs": repairs,
                "event_ids": sorted(c["events"]),
                "source_policy": "explicit_structured_parts_replaced_reference_pn_80_20",
                "observed_variants": observed,
            },
        })
    out.sort(key=lambda r: (-int(r["payload"]["repairs"]), compact(r["primary_value"]).casefold()))
    return out


_HISTORY_ADMIN_RE = re.compile(
    r"(?:remove\s+batter(?:y|ies)|return\s+without\s+batter|customer\s+requires|"
    r"fa\s+rpt|failure\s+analysis\s+report|required\s+electronically|"
    r"static\s+bag|p/?n\s+sticker|inside\s+of\s+box|outside\s+of\s+box|"
    r"vendor\s*(?:&|and)\s*date\s*stickers?|shipping|packaging|"
    r"warranty\s+type|warranty\s+per\b|final\s*o\.?\s*k\.?|"
    r"robot\s+fas?\s+(?:are\s+)?put\s+inside\s+packaging)", re.I,
)

_TEST_NOTE_RE = re.compile(
    r"(?:pass(?:ed)?\s+.*test|relay\s+test|burn[- ]?in|load\s+test|"
    r"functional\s+test|final\s+test|test(?:ed|ing)?\b|verification|"
    r"untestable|inspection\s+only|no\s+trouble\s+found)", re.I,
)

_REPAIR_ACTION_RE = re.compile(
    r"(?:replac(?:e|ed|ing)|chang(?:e|ed|ing)|repair(?:ed|ing)?|rebuild|rebuilt|"
    r"rework|resolder|solder(?:ed|ing)?|shotgun(?:ned)?|install(?:ed|ing)?|"
    r"clean(?:ed|ing)?|lub(?:e|ed)|lubricat(?:e|ed|ing)|greas(?:e|ed|ing)|"
    r"adjust(?:ed|ing)?|align(?:ed|ing)?|shim(?:med|ming)?|tighten(?:ed|ing)?|"
    r"tension(?:ed|ing)?|fix(?:ed|ing)?|swap(?:ped|ping)?|replac\w*|"
    r"trace\s+repair|reconstruct(?:ed|ion)?|reflow(?:ed|ing)?)", re.I,
)

_FA_PREFIX_RE = re.compile(r"^\s*(?:notes?\s*:\s*)?FA\s*[-:]+\s*(.+)$", re.I)


def _split_event_text(value: Any) -> List[str]:
    raw = compact(value)
    if not raw:
        return []
    return [compact(x) for x in re.split(r"\s*\|\s*|[\r\n]+", raw) if compact(x)]


def _strip_field_prefix(text: str) -> str:
    return compact(re.sub(
        r"^(?:repair\s+history|other\s+repair-history\s+notes?|basic\s+reported\s+problem|reported\s+problem|customer\s+complaint)\s*:\s*",
        "", text, flags=re.I,
    ))


def _history_norm(text: str) -> str:
    s = compact(text).casefold().replace("µ", "u").replace("μ", "u")
    # Collapse common 7800-family spellings before clustering; the generic
    # component-core resolver handles the Parts table independently.
    s = re.sub(r"\b(?:\d{3}-)?(?:hcpl|hcl|hcp1|hpc)[- ]*7800[a-z0-9-]*\b", "7800", s, flags=re.I)
    s = re.sub(r"\b7800a?\b", "7800", s, flags=re.I)
    s = re.sub(r"\b(?:caps?|capacitors?)\b", "capacitor", s, flags=re.I)
    s = re.sub(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", " ", s)
    s = re.sub(r"\b(?:19|20)\d{2}-\d{1,2}-\d{1,2}\b", " ", s)
    s = re.sub(r"\$\s*\d+(?:\.\d+)?", " ", s)
    s = re.sub(r"\b\d{6,}\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    stem = {
        "replaced": "replace", "replacing": "replace", "changed": "replace", "change": "replace",
        "rebuilt": "rebuild", "rebuilding": "rebuild", "cleaned": "clean", "cleaning": "clean",
        "lubed": "lube", "lubricated": "lube", "lubricating": "lube", "installed": "install",
        "adjusted": "adjust", "adjusting": "adjust", "belts": "belt", "bearings": "bearing",
        "capacitors": "capacitor", "motors": "motor", "seals": "seal", "fuses": "fuse",
        "boards": "board", "connectors": "connector",
    }
    toks = []
    for t in s.split():
        if t in {"the", "a", "an", "and", "to", "of", "for", "on", "with", "was", "were", "is", "are"}:
            continue
        toks.append(stem.get(t, t))
    return " ".join(toks)


def _reported_failure_snippets(row: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    # Primary source is the structured reported-problem field.
    for chunk in _split_event_text(row.get("reported_problem")):
        c = _strip_field_prefix(chunk)
        m = _FA_PREFIX_RE.match(c)
        if m:
            c = compact(m.group(1))
        if len(c) >= 5 and not _HISTORY_ADMIN_RE.search(c) and not _TEST_NOTE_RE.search(c):
            out.append(c)
    # Some old database-font customer failure notes were historically routed
    # into repair_history_notes.  `Notes: FA - ...` is explicitly reclassified.
    for chunk in _split_event_text(row.get("repair_history")):
        c = _strip_field_prefix(chunk)
        m = _FA_PREFIX_RE.match(c)
        if m:
            failure = compact(m.group(1))
            if len(failure) >= 5:
                out.append(failure)
    return list(dict.fromkeys(out))


def _technician_repair_snippets(row: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    test_norms = {_history_norm(x) for x in _split_event_text(row.get("test_outcome"))}
    for chunk in _split_event_text(row.get("repair_history")):
        c = _strip_field_prefix(chunk)
        if not c or _FA_PREFIX_RE.match(c):
            continue
        if _HISTORY_ADMIN_RE.search(c) or _TEST_NOTE_RE.search(c):
            continue
        if not _REPAIR_ACTION_RE.search(c):
            # Technician Repair History is work performed, not every database
            # note that happened to be stored beside it.
            continue
        n = _history_norm(c)
        if len(n) < 5 or n in test_norms:
            continue
        out.append(c)
    return list(dict.fromkeys(out))


def _aggregate_patterns(
    events: Sequence[Dict[str, Any]], extractor, *, item_type: str, payload_key: str, limit: int,
) -> List[Dict[str, Any]]:
    candidates: List[Tuple[str, str, str]] = []
    for ev in events:
        eid = compact(ev.get("repair_event_id"))
        for text in extractor(ev):
            norm = _history_norm(text)
            if eid and norm:
                candidates.append((eid, text, norm))

    clusters: List[Dict[str, Any]] = []
    for eid, text, norm in candidates:
        tokens = set(norm.split())
        best = None
        best_score = 0.0
        for c in clusters:
            ctoks = c["tokens"]
            anchors = {t for t in tokens if any(ch.isdigit() for ch in t)}
            canchors = {t for t in ctoks if any(ch.isdigit() for ch in t)}
            if anchors and canchors and anchors.isdisjoint(canchors):
                continue
            inter = len(tokens & ctoks) if tokens and ctoks else 0
            union = len(tokens | ctoks) if tokens and ctoks else 0
            jacc = inter / union if union else 0.0
            seq = difflib.SequenceMatcher(None, norm, c["norm"]).ratio()
            score = max(jacc, seq)
            if (jacc >= 0.62 or seq >= 0.82) and score > best_score:
                best, best_score = c, score
        if best is None:
            clusters.append({"norm": norm, "tokens": tokens, "events": {eid}, "labels": collections.Counter({text: 1})})
        else:
            best["events"].add(eid)
            best["labels"][text] += 1
            rep = min(best["labels"], key=lambda x: (len(x), x.casefold()))
            best["norm"] = _history_norm(rep)
            best["tokens"] = set(best["norm"].split())

    rows: List[Dict[str, Any]] = []
    for i, c in enumerate(clusters):
        count = len(c["events"])
        if count < 2:
            continue
        label = c["labels"].most_common(1)[0][0]
        rows.append({
            "item_type": item_type,
            "item_key": f"{item_type}:{i}",
            "primary_value": label,
            "title": label,
            "payload": {payload_key: label, "repairs": count, "event_ids": sorted(c["events"])},
        })
    rows.sort(key=lambda r: (-int(r["payload"]["repairs"]), len(compact(r["primary_value"])), compact(r["primary_value"]).casefold()))
    return rows[:limit]


def aggregate_reported_failures(events: Sequence[Dict[str, Any]], limit: int = PRODUCT_FAILURE_LIMIT) -> List[Dict[str, Any]]:
    return _aggregate_patterns(events, _reported_failure_snippets, item_type="failure_pattern", payload_key="failure", limit=limit)


def aggregate_repair_history(events: Sequence[Dict[str, Any]], limit: int = PRODUCT_HISTORY_LIMIT) -> List[Dict[str, Any]]:
    return _aggregate_patterns(events, _technician_repair_snippets, item_type="history_pattern", payload_key="history", limit=limit)


def product_view_groups(conn, query: str, generic_groups: List[Tuple[str, List[Dict[str, Any]]]]) -> Optional[List[Tuple[str, List[Dict[str, Any]]]]]:
    resolved = resolve_base_product(conn, query)
    if not resolved:
        return None
    events = product_event_rows(conn, resolved["families"])
    if not events:
        return None
    event_ids = {compact(e.get("repair_event_id")) for e in events if compact(e.get("repair_event_id"))}
    parts = aggregate_product_parts(conn, resolved["families"], resolved["base_part_number"])
    failures = aggregate_reported_failures(events)
    product = {
        "item_type": "product",
        "item_key": f"resolved_product:{alnum(resolved['base_part_number'])}",
        "primary_value": resolved["base_part_number"],
        "title": resolved["display_family"],
        "payload": {
            "base_part_number": resolved["base_part_number"],
            "repair_event_count": len(event_ids),
            "events_with_parts": len({eid for p in parts for eid in (p.get("payload") or {}).get("event_ids", [])}),
            "indexed_component_count": len(parts),
            "variant_count": len(resolved["model_variants"]),
            "model_variants": resolved["model_variants"],
        },
    }

    # Minimal product report for technicians. Tracking, procurement, individual
    # repair history and source files remain searchable by RMA/serial/log/PN but
    # are intentionally omitted from the normal product overview.
    out: List[Tuple[str, List[Dict[str, Any]]]] = []
    out.append(("EQUIPMENT / PRODUCT", [product]))
    if parts:
        out.append(("PARTS REPLACED", parts))
    if failures:
        out.append(("REPORTED FAILURE", failures))
    return out


def terminal_color_enabled(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM", "").casefold() == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def blue(text: str, *, force: Optional[bool] = None) -> str:
    enabled = terminal_color_enabled() if force is None else force
    return f"{ANSI_BLUE}{text}{ANSI_RESET}" if enabled else text


def terminal_link(url: str, label: Optional[str] = None, *, force: Optional[bool] = None) -> str:
    """Return a blue OSC-8 hyperlink when interactive; plain URL otherwise."""
    enabled = terminal_color_enabled() if force is None else force
    label = label or url
    if not enabled:
        return url if label == url else f"{label}: {url}"
    return f"{OSC8_OPEN}{url}{ST}{ANSI_BLUE}{label}{ANSI_RESET}{OSC8_CLOSE}"


def print_action_hint() -> None:
    print()
    print(f"Actions: {blue(':pdf')} create/open printable PDF   {blue(':print')} send current report to printer")


def compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def alnum(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", compact(value)).upper()


def is_picasa_irrelevant(result: Dict[str, Any]) -> bool:
    if result.get("item_type") != "file":
        return False
    p = result.get("payload") or {}
    path = compact(result.get("source_path") or p.get("relative_path") or result.get("title")).replace("\\", "/").casefold()
    name = compact(p.get("filename") or Path(path).name).casefold()
    return name == ".picasa.ini" or "/.picasaoriginals/" in path or path.endswith("/.picasa.ini")


def is_customer_po_result(result: Dict[str, Any]) -> bool:
    if result.get("item_type") != "order":
        return False
    p = result.get("payload") or {}
    evidence = compact(p.get("evidence_quote"))
    # Only explicit customer-PO wording is promoted. Generic PO/order language
    # remains procurement so we do not guess tracking semantics.
    return bool(re.search(r"\b(?:cust(?:omer)?\s*)?P\.?\s*O\.?\s*[:#-]?", evidence, flags=re.I) and re.search(r"\bcust(?:omer)?\b", evidence, flags=re.I))


def display_type(result: Dict[str, Any]) -> str:
    return "customer_po" if is_customer_po_result(result) else str(result.get("item_type") or "")


def identifier_style_query(query: str) -> bool:
    q = compact(query)
    a = alnum(q)
    if not a:
        return False
    if a.startswith(("DGK", "MSR", "NWK", "DSK", "RMA")) and len(a) >= 5:
        return True
    if a.isdigit() and len(a) >= 4:
        return True
    # Typical PN/model/serial tokens: letters + digits, not a sentence.
    if len(a) >= 5 and any(c.isalpha() for c in a) and any(c.isdigit() for c in a) and len(q.split()) <= 2:
        return True
    return False


def directly_matches(result: Dict[str, Any], query: str) -> bool:
    qa = alnum(query)
    if not qa:
        return False
    text = " ".join([
        compact(result.get("primary_value")), compact(result.get("title")),
        compact(result.get("subtitle")), compact(result.get("equipment_family")),
        compact(result.get("log_number")), compact(result.get("source_path")),
        compact(result.get("search_text")),
    ])
    return qa in alnum(text)


def strict_identifier_filter(results: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    if not identifier_style_query(query):
        return results
    direct = [r for r in results if directly_matches(r, query)]
    if not direct:
        return results
    event_ids = {compact(r.get("repair_event_id")) for r in direct if compact(r.get("repair_event_id"))}
    logs = {compact(r.get("log_number")) for r in direct if compact(r.get("log_number"))}
    keep: List[Dict[str, Any]] = []
    seen = set()
    for r in results:
        key = (r.get("item_type"), r.get("item_key"))
        if key in seen:
            continue
        direct_hit = directly_matches(r, query)
        linked = bool(
            (compact(r.get("repair_event_id")) and compact(r.get("repair_event_id")) in event_ids)
            or (compact(r.get("log_number")) and compact(r.get("log_number")) in logs)
        )
        if direct_hit or linked:
            keep.append(r)
            seen.add(key)
    return keep


def row_to_result(row) -> Dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:
        payload = {}
    return {k: row[k] for k in row.keys()} | {"payload": payload, "score": 0.0}


def linked_context_rows(conn, direct: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fetch records explicitly linked to literal identifier hits.

    FTS cannot return an event that does not itself contain an RMA/order value, so
    after a literal identifier hit we add only rows sharing that repair_event_id or
    DRL log. This is deterministic linkage, not fuzzy filler.
    """
    eids = sorted({compact(r.get("repair_event_id")) for r in direct if compact(r.get("repair_event_id"))})
    logs = sorted({compact(r.get("log_number")) for r in direct if compact(r.get("log_number"))})
    clauses = []
    params: List[Any] = []
    if eids:
        clauses.append("repair_event_id IN (%s)" % ",".join("?" * len(eids)))
        params.extend(eids)
    if logs:
        clauses.append("log_number IN (%s)" % ",".join("?" * len(logs)))
        params.extend(logs)
    if not clauses:
        return []
    rows = conn.execute("SELECT * FROM search_items WHERE " + " OR ".join(clauses), params).fetchall()
    return [row_to_result(r) for r in rows]


def search_clean(conn, query: str, *, candidate_limit: int = 800, result_limit: int = 160) -> List[Dict[str, Any]]:
    rows = base.search_db(conn, query, candidate_limit=candidate_limit, result_limit=max(result_limit, 160))
    rows = [dict(r) for r in rows if not is_picasa_irrelevant(r)]
    if identifier_style_query(query):
        direct = [r for r in rows if directly_matches(r, query)]
        if direct:
            linked = linked_context_rows(conn, direct)
            merged = direct + linked
            dedup: List[Dict[str, Any]] = []
            seen = set()
            for r in merged:
                if is_picasa_irrelevant(r):
                    continue
                key = (r.get("item_type"), r.get("item_key"))
                if key in seen:
                    continue
                seen.add(key)
                dedup.append(r)
            rows = dedup
        else:
            rows = strict_identifier_filter(rows, query)
    return rows[:result_limit]


def grouped_results(results: List[Dict[str, Any]], top: int) -> List[Tuple[str, List[Dict[str, Any]]]]:
    out: List[Tuple[str, List[Dict[str, Any]]]] = []
    for heading, types in DISPLAY_GROUPS:
        subset = [r for r in results if display_type(r) in types]
        if heading == "PARTS REPLACED":
            # Technician-facing stocking/history view: simple vertical ranking by
            # distinct repair events, highest first.  Keep the main list to PN +
            # times replaced; detailed variants remain indexed/searchable.
            subset = sorted(
                subset,
                key=lambda r: (
                    -int((r.get("payload") or {}).get("repairs") or 0),
                    compact((r.get("payload") or {}).get("pn") or r.get("primary_value") or r.get("title")).casefold(),
                ),
            )[:50]
        elif heading == "SOURCE FILES":
            # File metadata coverage is full DRL, but normal engineer output is a
            # concise source list rather than an Everything-style file dump.
            subset = subset[: min(top, 6)]
        else:
            subset = subset[:top]
        if subset:
            out.append((heading, subset))
    return out


def qty(value: Any) -> str:
    return str(value) if isinstance(value, int) else "unstated"


def item_lines(result: Dict[str, Any]) -> List[str]:
    t = display_type(result)
    p = result.get("payload") or {}
    family = compact(result.get("equipment_family")) or "-"
    log = compact(result.get("log_number")) or "-"
    lines: List[str] = []
    if t == "rma":
        lines.append(f"RMA {result.get('primary_value') or '-'}  |  DRL log {log}  |  {family}")
        if result.get("source_path"):
            lines.append(f"Source: {result['source_path']}")
    elif t == "customer_po":
        lines.append(f"Customer PO {result.get('primary_value') or '-'}  |  DRL log {log}  |  {family}")
        if p.get("evidence_quote"):
            lines.append(f"Evidence: {compact(p['evidence_quote'])}")
    elif t == "order":
        supplier = compact(result.get("subtitle")) or compact(p.get("supplier")) or "supplier not stated"
        lines.append(f"{supplier} order {result.get('primary_value') or '-'}  |  DRL log {log}  |  {family}")
        if p.get("description") or p.get("manufacturer_pn"):
            lines.append(f"Description: {p.get('description') or '-'}  |  Mfr PN: {p.get('manufacturer_pn') or '-'}  |  Qty: {qty(p.get('quantity'))}")
        if p.get("evidence_quote"):
            lines.append(f"Evidence: {compact(p['evidence_quote'])}")
    elif t == "product":
        lines.append(f"{result.get('title') or family}")
        
        if p.get('base_part_number'):
            lines.append(f"Base part number: {p.get('base_part_number')}  |  Indexed repair events: {p.get('repair_event_count',0)}  |  Indexed parts: {p.get('indexed_component_count',0)}")
            if int(p.get('variant_count') or 0) > 1:
                lines.append(f"Observed suffix/model variants preserved as metadata: {p.get('variant_count')}")
        else:
            lines.append(f"Indexed repair events: {p.get('repair_event_count',0)}  |  Events with parts: {p.get('events_with_parts',0)}  |  Indexed components: {p.get('indexed_component_count',0)}")
    elif t == "product_part":
        pn = compact(p.get("pn") or result.get("primary_value") or result.get("title")) or "-"
        lines.append(f"{pn}  |  {int(p.get('repairs') or 0)}")
    elif t == "failure_pattern":
        failure = compact(p.get('failure') or result.get('primary_value') or result.get('title')) or '-'
        lines.append(f"{failure}  |  {int(p.get('repairs') or 0)}")
    elif t == "history_pattern":
        history = compact(p.get('history') or result.get('primary_value') or result.get('title')) or '-'
        lines.append(f"{history}  |  {int(p.get('repairs') or 0)}")
    elif t == "event":
        lines.append(f"DRL log {log}  |  {family}")
        if p.get("reported_problem"):
            lines.append("Reported problem: " + compact(p["reported_problem"])[:500])
        if p.get("repair_history"):
            lines.append("Repair history: " + compact(p["repair_history"])[:700])
        if p.get("test_outcome"):
            lines.append("Test/outcome: " + compact(p["test_outcome"])[:500])
        paths = p.get("source_paths") or []
        if paths:
            lines.append("Source: " + str(paths[0]))
    elif t == "replacement":
        title = compact(result.get("title")) or "Replacement item"
        lines.append(f"{title}  |  DRL log {log}  |  {family}  |  Qty: {qty(p.get('quantity'))}")
        if p.get("text") and compact(p.get("text")) != title:
            lines.append("Recorded as: " + compact(p.get("text"))[:400])
    elif t == "file":
        line = compact(result.get("source_path") or result.get("title"))
        if log != "-":
            line += f"  |  DRL log {log}"
        lines.append(line)
    else:
        lines.append(compact(result.get("title")))
    return [x for x in lines if x]


def search_report(conn, query: str, args) -> Tuple[List[Dict[str, Any]], List[Tuple[str, List[Dict[str, Any]]]], float]:
    t0 = time.perf_counter()
    results = search_clean(conn, query, candidate_limit=args.candidate_limit, result_limit=max(args.top * 12, 160))
    generic = grouped_results(results, args.top)
    groups = product_view_groups(conn, query, generic) or generic
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return results, groups, elapsed_ms


def render_console(conn, query: str, args, *, show_pdf_hint: bool = True) -> int:
    results, groups, elapsed_ms = search_report(conn, query, args)
    print()
    print("=" * 86)
    print(f"NOVA DRL SEARCH  |  {query}  |  {elapsed_ms:.1f} ms")
    print("=" * 86)
    print("Coverage: file/path index = full DRL share | repair/parts/tracking knowledge = FULL v1.5.2 corpus")
    if not results:
        print("No indexed matches.")
        return 0
    for heading, subset in groups:
        print(f"\n{heading}")
        print("-" * len(heading))
        if heading == "PARTS REPLACED":
            print(f"{'REFERENCE PN':<52}    {'TIMES REPLACED':>16}")
            print(f"{'-' * 52}    {'-' * 16}")
            for result in subset:
                p = result.get("payload") or {}
                pn = compact(p.get("pn") or result.get("primary_value") or result.get("title")) or "-"
                repairs = int(p.get("repairs") or 0)
                print(f"{pn[:52]:<52}    {repairs:>16}")
            continue
        if heading == "REPORTED FAILURE" and subset and display_type(subset[0]) == "failure_pattern":
            print(f"{'REPORTED FAILURE':<64}    {'TIMES SEEN':>12}")
            print(f"{'-' * 64}    {'-' * 12}")
            for result in subset[:PRODUCT_FAILURE_LIMIT]:
                p = result.get("payload") or {}
                failure = compact(p.get("failure") or result.get("primary_value") or result.get("title")) or "-"
                repairs = int(p.get("repairs") or 0)
                print(f"{failure[:64]:<64}    {repairs:>12}")
            continue
        if heading == "REPAIR HISTORY" and subset and display_type(subset[0]) == "history_pattern":
            print(f"{'TOP TECHNICIAN REPAIR HISTORY':<68} {'TIMES SEEN':>10}")
            print(f"{'-' * 68} {'-' * 10}")
            for result in subset[:PRODUCT_HISTORY_LIMIT]:
                p = result.get("payload") or {}
                history = compact(p.get("history") or result.get("primary_value") or result.get("title")) or "-"
                repairs = int(p.get("repairs") or 0)
                print(f"{history[:68]:<68} {repairs:>10}")
            continue
        for i, result in enumerate(subset, 1):
            lines = item_lines(result)
            if not lines:
                continue
            print(f"{i}. {lines[0]}")
            for line in lines[1:]:
                print(f"   {line}")
    if show_pdf_hint:
        print_action_hint()
    return 0


# ----------------------------- PDF generation -----------------------------

def pdf_escape(text: str) -> str:
    s = text.encode("latin-1", "replace").decode("latin-1")
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def wrap_report_text(text: str, width: int = 96) -> List[str]:
    text = compact(text)
    if not text:
        return [""]
    return textwrap.wrap(text, width=width, break_long_words=True, break_on_hyphens=False) or [""]


def pdf_report_blocks(query: str, groups: List[Tuple[str, List[Dict[str, Any]]]], elapsed_ms: float) -> List[Tuple[str, str]]:
    blocks: List[Tuple[str, str]] = []
    blocks.append(("meta", f"Query: {query}"))
    blocks.append(("meta", f"Search time: {elapsed_ms:.1f} ms"))
    blocks.append(("meta", "Coverage: full DRL file/path index; repair/parts/RMA/PO/order knowledge from the full v1.5.2 corpus"))
    for heading, subset in groups:
        blocks.append(("heading", heading))
        if heading == "PARTS REPLACED":
            blocks.append(("parts_header", "REFERENCE PN\tTIMES REPLACED"))
            for r in subset:
                p = r.get("payload") or {}
                pn = compact(p.get("pn") or r.get("primary_value") or r.get("title")) or "-"
                repairs = int(p.get("repairs") or 0)
                blocks.append(("parts_row", f"{pn[:52]}\t{repairs}"))
        elif heading == "REPORTED FAILURE" and subset and display_type(subset[0]) == "failure_pattern":
            blocks.append(("failure_header", "REPORTED FAILURE\tTIMES SEEN"))
            for r in subset[:PRODUCT_FAILURE_LIMIT]:
                p = r.get("payload") or {}
                failure = compact(p.get("failure") or r.get("primary_value") or r.get("title")) or "-"
                repairs = int(p.get("repairs") or 0)
                blocks.append(("failure_row", f"{failure[:64]}\t{repairs}"))
        elif heading == "REPAIR HISTORY" and subset and display_type(subset[0]) == "history_pattern":
            blocks.append(("body", f"{'TOP TECHNICIAN REPAIR HISTORY':<70} TIMES SEEN"))
            blocks.append(("body", f"{'-' * 70} ----------"))
            for r in subset[:PRODUCT_HISTORY_LIMIT]:
                p = r.get("payload") or {}
                history = compact(p.get("history") or r.get("primary_value") or r.get("title")) or "-"
                repairs = int(p.get("repairs") or 0)
                blocks.append(("body", f"{history[:70]:<70} {repairs}"))
        else:
            for n, r in enumerate(subset, 1):
                lines = item_lines(r)
                for j, line in enumerate(lines):
                    prefix = f"{n}. " if j == 0 else "   "
                    blocks.append(("body", prefix + line))
        blocks.append(("spacer", ""))
    return blocks


def write_pdf(path: Path, query: str, groups: List[Tuple[str, List[Dict[str, Any]]]], elapsed_ms: float) -> None:
    """Dependency-free, print-friendly Letter PDF using built-in Helvetica fonts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = pdf_report_blocks(query, groups, elapsed_ms)
    page_width, page_height = 612.0, 792.0
    left, right, top, bottom = 42.0, 42.0, 42.0, 42.0
    max_y = page_height - top
    min_y = bottom + 20
    pages: List[List[Tuple[str, str]]] = []
    current: List[Tuple[str, str]] = []
    y = max_y - 34

    def needed(kind: str, text: str) -> Tuple[List[str], float]:
        if kind == "heading":
            lines = wrap_report_text(text, 82)
            return lines, 16.0 * len(lines) + 5
        if kind == "spacer":
            return [""], 7.0
        if kind in {"parts_header", "parts_row", "failure_header", "failure_row"}:
            return [text], 12.0
        lines = wrap_report_text(text, 98)
        return lines, 11.5 * len(lines)

    for kind, text in blocks:
        lines, h = needed(kind, text)
        if y - h < min_y and current:
            pages.append(current)
            current = []
            y = max_y - 34
        for line in lines:
            current.append((kind, line))
        y -= h
    if current or not pages:
        pages.append(current)

    objects: List[bytes] = []

    def add_obj(data: bytes) -> int:
        objects.append(data)
        return len(objects)

    # Reserve catalog/pages objects; fill later.
    catalog_id = add_obj(b"")
    pages_id = add_obj(b"")
    font_regular = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    font_bold = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    font_mono = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    font_mono_bold = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier-Bold >>")
    page_ids: List[int] = []

    for page_no, page_blocks in enumerate(pages, 1):
        cmds: List[str] = []
        # Header
        cmds.append("BT /F2 14 Tf 42 758 Td (NOVA DRL Search Report) Tj ET")
        cmds.append(f"BT /F1 8 Tf 42 744 Td (Generated by Unified Knowledge Search v{VERSION}) Tj ET")
        ycur = 716.0
        for kind, text in page_blocks:
            if kind == "heading":
                cmds.append(f"BT /F2 11 Tf 42 {ycur:.1f} Td ({pdf_escape(text)}) Tj ET")
                ycur -= 16.0
            elif kind == "spacer":
                ycur -= 7.0
            elif kind == "parts_header":
                left_text, right_text = (text.split("\t", 1) + [""])[:2]
                cmds.append(f"BT /F4 9 Tf 42 {ycur:.1f} Td ({pdf_escape(left_text)}) Tj ET")
                cmds.append(f"BT /F4 9 Tf 468 {ycur:.1f} Td ({pdf_escape(right_text)}) Tj ET")
                ycur -= 12.0
            elif kind == "parts_row":
                left_text, right_text = (text.split("\t", 1) + [""])[:2]
                rx = 545.0 - max(1, len(right_text)) * 5.4
                cmds.append(f"BT /F3 9 Tf 42 {ycur:.1f} Td ({pdf_escape(left_text)}) Tj ET")
                cmds.append(f"BT /F3 9 Tf {rx:.1f} {ycur:.1f} Td ({pdf_escape(right_text)}) Tj ET")
                ycur -= 12.0
            elif kind == "failure_header":
                left_text, right_text = (text.split("\t", 1) + [""])[:2]
                cmds.append(f"BT /F4 9 Tf 42 {ycur:.1f} Td ({pdf_escape(left_text)}) Tj ET")
                cmds.append(f"BT /F4 9 Tf 486 {ycur:.1f} Td ({pdf_escape(right_text)}) Tj ET")
                ycur -= 12.0
            elif kind == "failure_row":
                left_text, right_text = (text.split("\t", 1) + [""])[:2]
                rx = 545.0 - max(1, len(right_text)) * 5.4
                cmds.append(f"BT /F3 8.5 Tf 42 {ycur:.1f} Td ({pdf_escape(left_text)}) Tj ET")
                cmds.append(f"BT /F3 9 Tf {rx:.1f} {ycur:.1f} Td ({pdf_escape(right_text)}) Tj ET")
                ycur -= 12.0
            elif kind == "meta":
                cmds.append(f"BT /F1 8.5 Tf 42 {ycur:.1f} Td ({pdf_escape(text)}) Tj ET")
                ycur -= 11.5
            else:
                cmds.append(f"BT /F1 9 Tf 42 {ycur:.1f} Td ({pdf_escape(text)}) Tj ET")
                ycur -= 11.5
        footer = f"Page {page_no} of {len(pages)}"
        cmds.append(f"BT /F1 8 Tf 500 24 Td ({pdf_escape(footer)}) Tj ET")
        stream = "\n".join(cmds).encode("latin-1", "replace")
        content_id = add_obj(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_obj = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {page_width:.0f} {page_height:.0f}] "
            f"/Resources << /Font << /F1 {font_regular} 0 R /F2 {font_bold} 0 R /F3 {font_mono} 0 R /F4 {font_mono_bold} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("ascii")
        page_ids.append(add_obj(page_obj))

    objects[pages_id - 1] = (f"<< /Type /Pages /Kids [{' '.join(f'{x} 0 R' for x in page_ids)}] /Count {len(page_ids)} >>").encode("ascii")
    objects[catalog_id - 1] = (f"<< /Type /Catalog /Pages {pages_id} 0 R >>").encode("ascii")

    info_id = add_obj((f"<< /Title ({pdf_escape('NOVA DRL Search - ' + query)}) /Producer (Nova DRL v{VERSION}) >>").encode("latin-1", "replace"))
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects)+1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend((f"trailer\n<< /Size {len(objects)+1} /Root {catalog_id} 0 R /Info {info_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode("ascii"))
    path.write_bytes(out)


def slugify(query: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", compact(query)).strip("._-")
    return (s or "search")[:70]


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def report_server_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.35) as response:
            return b"Nova DRL printable PDF report server" in response.read(128)
    except Exception:
        return False


def start_report_server(args) -> bool:
    if report_server_ready(args.report_port):
        return True
    if port_open(args.report_port):
        # A different service owns this port; do not claim its URL as a Nova report link.
        return False
    Path(args.reports_dir).mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(Path(__file__).resolve()), "--serve-reports", "--reports-dir", args.reports_dir, "--report-port", str(args.report_port)]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except Exception:
        return False
    for _ in range(20):
        time.sleep(0.1)
        if report_server_ready(args.report_port):
            return True
    return False


def preferred_server_ip() -> Optional[str]:
    """Best LAN-facing IPv4 for engineer workstations; avoid loopback/container IPs when possible."""
    try:
        raw = subprocess.check_output(["ip", "-4", "route", "get", "1.1.1.1"], text=True, stderr=subprocess.DEVNULL, timeout=1)
        m = re.search(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)", raw)
        if m and not m.group(1).startswith("127."):
            return m.group(1)
    except Exception:
        pass
    try:
        raw = subprocess.check_output(["hostname", "-I"], text=True, stderr=subprocess.DEVNULL, timeout=1)
        ips = [x for x in raw.split() if ":" not in x and not x.startswith("127.")]
        if not ips:
            return None
        def rank(ip: str) -> tuple[int, str]:
            if ip.startswith("192.168."):
                return (0, ip)
            if ip.startswith("10."):
                return (1, ip)
            m = re.match(r"172\.(\d+)\.", ip)
            if m and 16 <= int(m.group(1)) <= 31:
                return (2, ip)
            return (3, ip)
        return sorted(ips, key=rank)[0]
    except Exception:
        return None


def report_urls(path: Path, port: int) -> List[str]:
    name = urllib.parse.quote(path.name)
    urls: List[str] = []
    ip = preferred_server_ip()
    if ip:
        urls.append(f"http://{ip}:{port}/{name}")
    host = socket.gethostname()
    if host:
        urls.append(f"http://{host}:{port}/{name}")
    return list(dict.fromkeys(urls))


def print_pdf_access(urls: List[str]) -> None:
    """Show both modern clickable link and old-console copy/paste URL."""
    if not urls:
        return
    preferred = urls[0]
    print()
    print("PDF READY FOR PRINTING")
    print("----------------------")
    print("Clickable link (supported terminals):")
    print("  " + terminal_link(preferred))
    print("COPY/PASTE INTO CHROME OR EDGE:")
    # Intentionally plain: no ANSI/OSC codes so classic Windows console users can copy it safely.
    print("  " + preferred)
    if len(urls) > 1:
        print("Alternate address:")
        print("  " + urls[1])


def create_pdf_for_query(conn, query: str, args) -> Path:
    _, groups, elapsed_ms = search_report(conn, query, args)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = Path(args.reports_dir) / f"NOVA_DRL_{slugify(query)}_{stamp}.pdf"
    write_pdf(path, query, groups, elapsed_ms)
    served = start_report_server(args)
    print(f"Printable PDF created: {path}")
    if served:
        urls = report_urls(path, args.report_port)
        if urls:
            print_pdf_access(urls)
        else:
            print(f"Report server is running on port {args.report_port}; use the server LAN IP with {path.name}.")
    else:
        print("PDF created, but the local report server could not be started. Use the file path above.")
    return path

def print_pdf_file(path: Path, args) -> bool:
    """Send PDF to configured/default CUPS printer. Return True on successful queueing."""
    printer = compact(getattr(args, "printer", None))
    lp = shutil.which("lp")
    lpr = shutil.which("lpr")
    if lp:
        cmd = [lp]
        if printer:
            cmd += ["-d", printer]
        cmd.append(str(path))
    elif lpr:
        cmd = [lpr]
        if printer:
            cmd += ["-P", printer]
        cmd.append(str(path))
    else:
        print("Direct print unavailable: neither 'lp' nor 'lpr' is installed/configured on this server.")
        print("Use the PDF browser URL above to print from the engineer workstation.")
        return False
    try:
        cp = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except Exception as exc:
        print(f"Print command failed: {exc}")
        print("Use the PDF browser URL above to print from the engineer workstation.")
        return False
    msg = compact(cp.stdout or cp.stderr)
    if cp.returncode != 0:
        print(f"Print command failed ({cp.returncode}): {msg or 'unknown printer/CUPS error'}")
        print("Use the PDF browser URL above to print from the engineer workstation.")
        return False
    target = printer or "default printer"
    print(f"Queued to {target}: {path.name}")
    if msg:
        print(msg)
    return True


def create_and_print_for_query(conn, query: str, args) -> Path:
    path = create_pdf_for_query(conn, query, args)
    print_pdf_file(path, args)
    return path


class PdfOnlyHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"", "/"}:
            body = b"Nova DRL printable PDF report server\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not parsed.path.casefold().endswith(".pdf"):
            self.send_error(404)
            return
        return super().do_GET()


def serve_reports(args) -> int:
    reports = Path(args.reports_dir)
    reports.mkdir(parents=True, exist_ok=True)
    handler = functools.partial(PdfOnlyHandler, directory=str(reports))
    server = http.server.ThreadingHTTPServer(("0.0.0.0", args.report_port), handler)
    print(f"Nova DRL PDF report server: {reports} -> port {args.report_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# ----------------------------- commands -----------------------------

def command_status(args) -> int:
    # v1.5.3 presentation/print layer uses the full-corpus v1.5.3 storage engine.
    base_args = argparse.Namespace(
        file_index=args.file_index, full_root=args.full_root,
        db=args.db, top=args.top, candidate_limit=args.candidate_limit, json=False,
        self_check_warn_ms=args.self_check_warn_ms,
    )
    rc = base.command_status(base_args)
    print(f"Presentation layer:    v{VERSION} | corpus-only recurring parts + reported failures")
    print("Picasa display policy: hidden | .picasa.ini and .picasaoriginals")
    print(f"Printable reports:     {args.reports_dir} | HTTP port {args.report_port}")
    print(f"Terminal actions:      blue :pdf / :print | clickable + plain browser PDF URL")
    print(f"Direct printer target: {args.printer or 'system default'} | lp/lpr used when available")
    return rc


def command_plan(args) -> int:
    base_args = argparse.Namespace(
        file_index=args.file_index, full_root=args.full_root,
        db=args.db, top=args.top, candidate_limit=args.candidate_limit, json=False,
        self_check_warn_ms=args.self_check_warn_ms,
    )
    rc = base.command_plan(base_args)
    print("v1.5.9: structured parts_replaced aggregation + corpus-only component-core resolver + minimal product view")
    print("Search/database schema:          v1.5.3 full-corpus DB reused; v1.5.9 resolves product + component cores from structured parts_replaced evidence at query time")
    print("80/20 enforcement:              HARD INVARIANT | expert overrides disabled")
    print("AI/LLM calls for search/PDF/print: 0")
    return rc


def command_build(args) -> int:
    # Rebuild with the v1.5.3 full-corpus engine.
    base.VERSION = VERSION
    base_args = argparse.Namespace(
        file_index=args.file_index, full_root=args.full_root,
        db=args.db, top=args.top, candidate_limit=args.candidate_limit, json=False,
        self_check_warn_ms=args.self_check_warn_ms,
    )
    return base.command_build(base_args)


def command_self_check(args) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: unified knowledge DB not built: {db}", file=sys.stderr)
        return 2
    conn = base.connect_ro(db)
    try:
        queries = ["RCL1A", "GB8", "DGK", "MSR", "Line Card"]
        r = conn.execute("SELECT rma_number FROM rma_refs LIMIT 1").fetchone()
        if r:
            queries.append(str(r[0]))
        po = conn.execute("SELECT customer_po FROM customer_po_refs LIMIT 1").fetchone()
        if po:
            queries.append(str(po[0]))
        p = conn.execute("SELECT manufacturer_pn FROM product_parts WHERE manufacturer_pn IS NOT NULL LIMIT 1").fetchone()
        if p:
            queries.append(str(p[0]))
        print(f"# Nova DRL Unified Knowledge Search Self-Check v{VERSION}")
        ok = True
        for q in queries:
            t0 = time.perf_counter()
            rows = search_clean(conn, q, candidate_limit=args.candidate_limit, result_limit=40)
            ms = (time.perf_counter() - t0) * 1000.0
            print(f"{q!r:30s} -> {len(rows):2d} clean results in {ms:7.2f} ms")
            if ms > args.self_check_warn_ms:
                ok = False
        print("Result:", "PASS" if ok else f"PASS WITH SPEED WARNING > {args.self_check_warn_ms:.0f} ms")
        return 0
    finally:
        conn.close()


def command_search(args, query: str) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: unified knowledge DB not built: {db}", file=sys.stderr)
        print("Run: python3 tools/nova_drl_unified_knowledge_index_v1_5_3.py --build")
        return 2
    conn = base.connect_ro(db)
    try:
        if args.json:
            results, groups, elapsed = search_report(conn, query, args)
            print(json.dumps({"version": VERSION, "query": query, "elapsed_ms": round(elapsed,3), "results": results}, indent=2, ensure_ascii=False))
            return 0
        return render_console(conn, query, args, show_pdf_hint=not getattr(args, "no_actions", False))
    finally:
        conn.close()


def decode_b64_query(value: str) -> str:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8").strip()
    except Exception as exc:
        raise ValueError(f"Invalid Base64 query: {exc}") from exc


def command_pdf_file(args, query: str) -> int:
    """Create a report file only; do not start HTTP server or invoke CUPS.

    This mode is intended for the Windows Engineer Client, which copies the PDF
    with SCP to the Windows-accessible DRL share and opens it locally.
    """
    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: unified knowledge DB not built: {db}", file=sys.stderr)
        return 2
    conn = base.connect_ro(db)
    try:
        _, groups, elapsed_ms = search_report(conn, query, args)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = Path(args.reports_dir) / f"NOVA_DRL_{slugify(query)}_{stamp}.pdf"
        write_pdf(path, query, groups, elapsed_ms)
        # Deliberately machine-readable single-line marker. The Windows client
        # does not need to parse ANSI text or a browser URL.
        print(f"NOVA_DRL_REPORT_PATH={path}")
        return 0
    finally:
        conn.close()


def command_pdf(args, query: str) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: unified knowledge DB not built: {db}", file=sys.stderr)
        return 2
    conn = base.connect_ro(db)
    try:
        create_pdf_for_query(conn, query, args)
        return 0
    finally:
        conn.close()


def command_print(args, query: str) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: unified knowledge DB not built: {db}", file=sys.stderr)
        return 2
    conn = base.connect_ro(db)
    try:
        create_and_print_for_query(conn, query, args)
        return 0
    finally:
        conn.close()


def command_prompt(args) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"Unified knowledge DB not built: {db}")
        print("Run the unified knowledge build first.")
        return 2
    conn = base.connect_ro(db)
    last_query: Optional[str] = None
    print(f"Nova DRL Full-Corpus Unified Knowledge Search v{VERSION}")
    print("Type any full/partial PN, model, serial, RMA, DRL log, customer PO, Digi-Key/Mouser order ref, or identifying text.")
    print(f"Simple lookup is local/indexed; no AI call. Commands: :help  :status  {blue(':pdf')}  {blue(':print')}  :quit")
    try:
        while True:
            try:
                q = input("NOVA-DRL> ").strip()
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                continue
            if not q:
                continue
            if q in {":q", ":quit", "quit", "exit"}:
                break
            if q == ":help":
                print("Search examples: BRD-1526990 | 1526990 | S07211 | 53434 | DGK52102 | MSR 56889 | IXFX24N100 | RCL1A")
                print(f"{blue(':pdf')}              Create a printable PDF and show clickable + plain browser URLs")
                print(f"{blue(':pdf <search>')}     Create a printable PDF for another search")
                print(f"{blue(':print')}            Create PDF and send it to the configured/default server printer")
                print(f"{blue(':print <search>')}   Print another search directly")
                print(":status           Show local index counts")
                print("Interpretive troubleshooting remains the AI layer above this fast retrieval index.")
                continue
            if q == ":status":
                c = base.db_counts(conn)
                print(f"files={c['files']:,} events={c['repair_events']:,} products={c['product_families']:,} product_parts={c['product_parts']:,} RMAs={c['rma_refs']:,} customer_POs={c.get('customer_po_refs',0):,} orders={c['procurement_refs']:,}")
                continue
            if q == ":pdf" or q.startswith(":pdf "):
                target = compact(q[5:]) if q.startswith(":pdf ") else last_query
                if not target:
                    print("No previous search. Type a search first, or use :pdf <search>.")
                    continue
                create_pdf_for_query(conn, target, args)
                continue
            if q == ":print" or q.startswith(":print "):
                target = compact(q[7:]) if q.startswith(":print ") else last_query
                if not target:
                    print("No previous search. Type a search first, or use :print <search>.")
                    continue
                create_and_print_for_query(conn, target, args)
                continue
            last_query = q
            render_console(conn, q, args)
    finally:
        conn.close()
    return 0


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=f"Nova DRL Unified Knowledge Search + Print v{VERSION}")
    p.add_argument("--file-index", default=base.DEFAULT_FILE_INDEX)
    p.add_argument("--full-root", default=base.DEFAULT_FULL_ROOT)
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--reports-dir", default=DEFAULT_REPORTS_DIR)
    p.add_argument("--report-port", type=int, default=DEFAULT_REPORT_PORT)
    p.add_argument("--printer", default=os.environ.get("NOVA_DRL_PRINTER"), help="Optional CUPS printer name for :print/--print; default uses the system printer")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--prompt", action="store_true")
    mode.add_argument("--self-check", action="store_true")
    mode.add_argument("--search")
    mode.add_argument("--search-b64", help="Base64 UTF-8 query for Windows Engineer Client")
    mode.add_argument("--pdf")
    mode.add_argument("--pdf-file-b64", help="Base64 UTF-8 query; create report file only and print NOVA_DRL_REPORT_PATH")
    mode.add_argument("--print", dest="print_query")
    mode.add_argument("--serve-reports", action="store_true")
    p.add_argument("--top", type=int, default=8, help="Maximum results per displayed/report section")
    p.add_argument("--candidate-limit", type=int, default=800)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-actions", action="store_true", help="Suppress :pdf/:print action hint (used by Windows Engineer Client)")
    p.add_argument("--self-check-warn-ms", type=float, default=250.0)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    enforce_drl_project_invariants()
    args = make_parser().parse_args(argv)
    if args.status:
        return command_status(args)
    if args.plan_only:
        return command_plan(args)
    if args.build or args.refresh:
        return command_build(args)
    if args.self_check:
        return command_self_check(args)
    if args.search is not None:
        return command_search(args, args.search)
    if args.search_b64 is not None:
        try:
            q = decode_b64_query(args.search_b64)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        return command_search(args, q)
    if args.pdf is not None:
        return command_pdf(args, args.pdf)
    if args.pdf_file_b64 is not None:
        try:
            q = decode_b64_query(args.pdf_file_b64)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        return command_pdf_file(args, q)
    if args.print_query is not None:
        return command_print(args, args.print_query)
    if args.serve_reports:
        return serve_reports(args)
    return command_prompt(args)


if __name__ == "__main__":
    raise SystemExit(main())
