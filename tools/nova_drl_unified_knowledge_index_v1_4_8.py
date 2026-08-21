#!/usr/bin/env python3
"""
Nova DRL Unified Knowledge Index v1.4.8

Fast local retrieval layer across:
- the full persistent DRL file metadata index (v1.4.2), and
- AI-ingested repair knowledge from the frozen 10% benchmark/enrichment
  (v1.4.6 + v1.4.7).

This layer does NOT call an LLM. It is the Everything-style retrieval substrate.
AI reasoning belongs above this index for interpretive questions.

Normal engineer workflow after installing the wrapper:
    nova-drl
    NOVA-DRL> BRD-1526990
    NOVA-DRL> 1526990
    NOVA-DRL> S07211
    NOVA-DRL> 53434
    NOVA-DRL> DGK52102
    NOVA-DRL> IXFX24N100

Search is case-insensitive and partial. Exact/strong identifier matches are ranked
before broader partial/file-path matches. SQLite FTS5 trigram search provides fast
substring retrieval without model calls or NAS traversal.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import os
import re
import shlex
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

VERSION = "1.4.8"
SCHEMA_VERSION = 1

DEFAULT_FILE_INDEX = "/opt/nova-drl/index/drl_file_index.sqlite"
DEFAULT_TRACKING_ROOT = "/opt/nova-drl/output/drl_10pct_tracking_enrichment_v1_4_7"
DEFAULT_BENCHMARK_ROOT = "/opt/nova-drl/output/drl_10pct_benchmark_v1_4_6"
DEFAULT_KNOWLEDGE_DB = "/opt/nova-drl/index/drl_knowledge_index.sqlite"
DEFAULT_SHARE_ROOT = "/mnt/drl"
KNOWLEDGE_SCOPE = "frozen_10pct_v1_4_6_plus_v1_4_7"

KNOWN_PROCUREMENT_PREFIXES = ("DGK", "MSR", "NWK", "DSK")
SUPPLIER_FOR_PREFIX = {"DGK": "Digi-Key", "MSR": "Mouser"}

FACT_CATEGORIES = (
    "basic_reported_problem",
    "parts_replaced",
    "repair_history_notes",
    "explicit_test_outcome",
)

ITEM_TYPE_BONUS = {
    "rma": 150.0,
    "order": 145.0,
    "product_part": 130.0,
    "product": 120.0,
    "replacement": 105.0,
    "event": 95.0,
    "file": 25.0,
}

GROUP_ORDER = [
    ("TRACKING", {"rma", "order"}),
    ("PRODUCT KNOWLEDGE", {"product"}),
    ("PART USAGE", {"product_part"}),
    ("REPAIR HISTORY", {"event", "replacement"}),
    ("FILES", {"file"}),
]


@dataclass
class BuildCounts:
    files: int = 0
    events: int = 0
    rmas: int = 0
    rmas_rejected: int = 0
    orders: int = 0
    orders_rejected: int = 0
    orders_recovered_from_evidence: int = 0
    replacements: int = 0
    procurement_only_replacements_excluded: int = 0
    products: int = 0
    product_parts: int = 0
    search_items: int = 0


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def compact_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def norm_text(value: Any) -> str:
    s = compact_ws(value).casefold().replace("\\", "/")
    return s


def alnum_norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", norm_text(value))


def upper_id(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", compact_ws(value).upper())


def display_quantity(value: Any) -> str:
    return str(value) if value is not None else "unstated"


def sha1_short(value: str, n: int = 16) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:n]


def source_mtime_ns(path: Path) -> int:
    try:
        return int(path.stat().st_mtime_ns)
    except Exception:
        return 0


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def connect_ro(path: Path) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def file_index_root(conn: sqlite3.Connection) -> str:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='share_root'").fetchone()
        return str(row[0]) if row else DEFAULT_SHARE_ROOT
    except Exception:
        return DEFAULT_SHARE_ROOT


def enriched_paths(tracking_root: Path, benchmark_root: Path) -> Dict[str, Path]:
    events = tracking_root / "repair_events_enriched_v1_4_7.jsonl"
    parts = tracking_root / "replacement_mentions_enriched_v1_4_7.jsonl"
    if not events.exists():
        events = benchmark_root / "repair_events_v1_4_6.jsonl"
    if not parts.exists():
        parts = benchmark_root / "replacement_mentions_v1_4_6.jsonl"
    return {
        "tracking_db": tracking_root / "tracking_lookup_v1_4_7.sqlite",
        "events": events,
        "parts": parts,
    }


def fact_text(event: Dict[str, Any], category: str) -> str:
    facts = (event.get("facts") or {}).get(category) or []
    chunks = []
    for item in facts:
        if isinstance(item, dict):
            for key in ("text", "evidence_quote", "part_number"):
                v = compact_ws(item.get(key))
                if v:
                    chunks.append(v)
        else:
            v = compact_ws(item)
            if v:
                chunks.append(v)
    return " | ".join(dict.fromkeys(chunks))


def event_source_paths(event: Dict[str, Any]) -> List[str]:
    paths = list(event.get("primary_source_paths") or []) + list(event.get("supporting_source_paths") or [])
    return [compact_ws(x) for x in paths if compact_ws(x)]


def tracking_meta(event: Dict[str, Any]) -> Dict[str, Any]:
    meta = event.get("tracking_metadata_v1_4_7")
    return meta if isinstance(meta, dict) else {"rma_numbers": [], "procurement_refs": []}


def evidence_contains_identifier(evidence: Any, identifier: Any) -> bool:
    e = alnum_norm(evidence)
    i = alnum_norm(identifier)
    return bool(i and i in e)


def normalize_rma_ref(value: Any) -> str:
    s = compact_ws(value)
    s = re.sub(r"^RMA\s*#?\s*[:\-]?\s*", "", s, flags=re.I)
    return re.sub(r"[^A-Za-z0-9]+", "", s).upper()


def infer_supplier_from_ref(ref: str) -> Optional[str]:
    u = upper_id(ref)
    for prefix, supplier in SUPPLIER_FOR_PREFIX.items():
        if u.startswith(prefix):
            return supplier
    return None


def extract_visible_order_from_evidence(evidence: str, supplier: Optional[str]) -> Optional[Tuple[str, Optional[str]]]:
    """Strictly recover a visible procurement reference from evidence text.

    No cross-event inference. We only return characters present in the evidence.
    For supplier-name forms such as "DigiKey 55516", the visible ref is "55516"
    (we do not manufacture a DGK prefix that is absent from evidence).
    """
    text = compact_ws(evidence)
    if not text:
        return None

    m = re.search(r"\b(DGK|MSR|NWK|DSK)\s*[-:#]?\s*([A-Z0-9][A-Z0-9._/-]{2,})\b", text, flags=re.I)
    if m:
        prefix = m.group(1).upper()
        token = m.group(2).rstrip(".,;:")
        ref = prefix + token if not token.upper().startswith(prefix) else token
        return ref, SUPPLIER_FOR_PREFIX.get(prefix) or supplier

    supplier_patterns = [
        (r"\bDigi[ -]?Key\b\s*(?:order|ord|#|no\.?|ref)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{2,})", "Digi-Key"),
        (r"\bMouser\b\s*(?:order|ord|#|no\.?|ref)?\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{2,})", "Mouser"),
    ]
    for pat, sup in supplier_patterns:
        m = re.search(pat, text, flags=re.I)
        if m:
            token = m.group(1).rstrip(".,;:")
            # Avoid capturing a generic word after supplier name.
            if re.fullmatch(r"(?i)(parts?|ordered?|order|from)", token):
                continue
            return token, sup
    return None


def strict_procurement_rows(events: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int, int]:
    """Return evidence-grounded order refs, rejected count, recovered-from-evidence count."""
    out: List[Dict[str, Any]] = []
    rejected = 0
    recovered = 0
    seen = set()
    for ev in events:
        eid = compact_ws(ev.get("repair_event_id"))
        log = compact_ws(ev.get("log_number")) or None
        family = compact_ws(ev.get("equipment_family"))
        for row in tracking_meta(ev).get("procurement_refs") or []:
            if not isinstance(row, dict):
                continue
            ref = compact_ws(row.get("order_ref"))
            evidence = compact_ws(row.get("evidence_quote"))
            source = compact_ws(row.get("source_path"))
            supplier = compact_ws(row.get("supplier")) or infer_supplier_from_ref(ref)
            description = compact_ws(row.get("description")) or None
            mpn = compact_ws(row.get("manufacturer_pn")) or None
            qty = row.get("quantity") if isinstance(row.get("quantity"), int) else None

            if ref and evidence_contains_identifier(evidence, ref):
                final_ref = ref
                final_supplier = supplier or infer_supplier_from_ref(ref)
                mode = "literal"
            else:
                recovered_visible = extract_visible_order_from_evidence(evidence, supplier)
                if not recovered_visible:
                    rejected += 1
                    continue
                final_ref, recovered_supplier = recovered_visible
                final_supplier = recovered_supplier or supplier or infer_supplier_from_ref(final_ref)
                recovered += 1
                mode = "recovered_literal_from_evidence"

            key = (eid, upper_id(final_ref), alnum_norm(evidence), source)
            if not eid or not upper_id(final_ref) or key in seen:
                continue
            seen.add(key)
            out.append({
                "repair_event_id": eid,
                "log_number": log,
                "equipment_family": family,
                "supplier": final_supplier,
                "order_ref": final_ref,
                "order_ref_normalized": upper_id(final_ref),
                "description": description,
                "manufacturer_pn": mpn,
                "quantity": qty,
                "evidence_quote": evidence,
                "source_path": source,
                "grounding_mode": mode,
            })
    return out, rejected, recovered


def strict_rma_rows(events: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    out: List[Dict[str, Any]] = []
    rejected = 0
    seen = set()
    for ev in events:
        eid = compact_ws(ev.get("repair_event_id"))
        log = compact_ws(ev.get("log_number")) or None
        family = compact_ws(ev.get("equipment_family"))
        for row in tracking_meta(ev).get("rma_numbers") or []:
            if not isinstance(row, dict):
                continue
            value = compact_ws(row.get("value"))
            evidence = compact_ws(row.get("evidence_quote"))
            source = compact_ws(row.get("source_path"))
            if not value or not evidence_contains_identifier(evidence, value):
                rejected += 1
                continue
            norm = normalize_rma_ref(value)
            display_value = re.sub(r"^RMA\s*#?\s*[:\-]?\s*", "", value, flags=re.I).strip() or value
            key = (eid, norm, source)
            if not norm or key in seen:
                continue
            seen.add(key)
            out.append({
                "repair_event_id": eid,
                "log_number": log,
                "equipment_family": family,
                "rma_number": display_value,
                "rma_normalized": norm,
                "evidence_quote": evidence,
                "source_path": source,
            })
    return out, rejected


def known_procurement_only_replacement(row: Dict[str, Any]) -> bool:
    pn = compact_ws(row.get("manufacturer_part_number") or row.get("part_number"))
    pnu = upper_id(pn)
    text = compact_ws(row.get("text"))
    evidence = compact_ws(row.get("evidence_quote"))
    order = compact_ws(row.get("distributor_order_ref"))
    classification = compact_ws(row.get("pn_classification")).casefold()

    if classification == "procurement_reference_reclassified" and not pn:
        return True
    if pnu.startswith(KNOWN_PROCUREMENT_PREFIXES):
        return True
    if order:
        ou = upper_id(order)
        if pnu and pnu in {ou, re.sub(r"^(DGK|MSR|NWK|DSK)", "", ou)}:
            return True

    # Critical DRL rule: MSR 56889 means Mouser order reference, not Mfr PN 56889.
    # Same logic applies to DGK/NWK/DSK lines.
    if pnu:
        for blob in (text, evidence):
            m = re.search(r"\b(DGK|MSR|NWK|DSK)\s*[-:#]?\s*([A-Z0-9][A-Z0-9._/-]{2,})\b", blob, flags=re.I)
            if not m:
                continue
            token = upper_id(m.group(2))
            combined = upper_id(m.group(1) + m.group(2))
            if pnu in {token, combined}:
                # If the row is only the procurement notation, it is not a replacement part.
                t = re.sub(r"\b(DGK|MSR|NWK|DSK)\s*[-:#]?\s*[A-Z0-9][A-Z0-9._/-]{2,}\b", "", text, flags=re.I)
                t = re.sub(r"(?i)\b(parts?|ordered?|order|from|qty|quantity|replaced|used)\b", "", t)
                if not re.search(r"[A-Za-z]{3,}", t):
                    return True
                # Even with extra words, a numeric PN copied solely from MSR/DGK token is unsafe.
                if pnu.isdigit():
                    return True
    return False


def load_source_events(paths: Dict[str, Path]) -> List[Dict[str, Any]]:
    return read_jsonl(paths["events"])


def load_source_parts(paths: Dict[str, Path]) -> List[Dict[str, Any]]:
    return read_jsonl(paths["parts"])


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        PRAGMA temp_store=MEMORY;

        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE sources(name TEXT PRIMARY KEY, path TEXT NOT NULL, mtime_ns INTEGER NOT NULL, row_count INTEGER NOT NULL);

        CREATE TABLE files(
          id INTEGER PRIMARY KEY,
          source_file_id INTEGER,
          relative_path TEXT NOT NULL,
          filename TEXT NOT NULL,
          parent_path TEXT,
          extension TEXT,
          size INTEGER,
          mtime_ns INTEGER,
          detected_log TEXT,
          file_kind TEXT,
          absolute_path TEXT NOT NULL
        );
        CREATE INDEX idx_files_log ON files(detected_log);
        CREATE INDEX idx_files_filename ON files(filename COLLATE NOCASE);

        CREATE TABLE repair_events(
          repair_event_id TEXT PRIMARY KEY,
          log_number TEXT,
          equipment_family TEXT,
          top_folders_json TEXT,
          source_paths_json TEXT,
          reported_problem_text TEXT,
          repair_history_text TEXT,
          test_outcome_text TEXT,
          all_fact_text TEXT
        );
        CREATE INDEX idx_events_log ON repair_events(log_number);
        CREATE INDEX idx_events_family ON repair_events(equipment_family COLLATE NOCASE);

        CREATE TABLE rma_refs(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          repair_event_id TEXT NOT NULL,
          rma_number TEXT NOT NULL,
          rma_normalized TEXT NOT NULL,
          evidence_quote TEXT,
          source_path TEXT
        );
        CREATE INDEX idx_rma_norm ON rma_refs(rma_normalized);

        CREATE TABLE procurement_refs(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          repair_event_id TEXT NOT NULL,
          supplier TEXT,
          order_ref TEXT NOT NULL,
          order_ref_normalized TEXT NOT NULL,
          description TEXT,
          manufacturer_pn TEXT,
          quantity INTEGER,
          evidence_quote TEXT,
          source_path TEXT,
          grounding_mode TEXT NOT NULL
        );
        CREATE INDEX idx_order_norm ON procurement_refs(order_ref_normalized);

        CREATE TABLE replacement_mentions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          repair_event_id TEXT NOT NULL,
          log_number TEXT,
          equipment_family TEXT,
          manufacturer_pn TEXT,
          pn_normalized TEXT,
          quantity INTEGER,
          text TEXT,
          evidence_quote TEXT,
          procurement_only_excluded INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX idx_repl_pn ON replacement_mentions(pn_normalized);
        CREATE INDEX idx_repl_family ON replacement_mentions(equipment_family COLLATE NOCASE);

        CREATE TABLE product_families(
          equipment_family TEXT PRIMARY KEY,
          repair_event_count INTEGER NOT NULL,
          events_with_parts INTEGER NOT NULL,
          indexed_component_count INTEGER NOT NULL,
          top_parts_json TEXT NOT NULL,
          knowledge_scope TEXT NOT NULL
        );

        CREATE TABLE product_parts(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          equipment_family TEXT NOT NULL,
          component_key TEXT NOT NULL,
          display_name TEXT NOT NULL,
          manufacturer_pn TEXT,
          description TEXT,
          repair_event_count INTEGER NOT NULL,
          recorded_pieces INTEGER NOT NULL,
          qty_unstated_mentions INTEGER NOT NULL,
          variants_json TEXT NOT NULL,
          event_ids_json TEXT NOT NULL,
          knowledge_scope TEXT NOT NULL,
          UNIQUE(equipment_family, component_key)
        );
        CREATE INDEX idx_product_parts_pn ON product_parts(manufacturer_pn COLLATE NOCASE);
        CREATE INDEX idx_product_parts_family ON product_parts(equipment_family COLLATE NOCASE);

        CREATE TABLE search_items(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          item_type TEXT NOT NULL,
          item_key TEXT NOT NULL UNIQUE,
          primary_value TEXT,
          title TEXT NOT NULL,
          subtitle TEXT,
          equipment_family TEXT,
          repair_event_id TEXT,
          log_number TEXT,
          source_path TEXT,
          search_text TEXT NOT NULL,
          rank_hint REAL NOT NULL DEFAULT 0,
          payload_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX idx_search_type ON search_items(item_type);
        CREATE INDEX idx_search_primary ON search_items(primary_value COLLATE NOCASE);

        CREATE VIRTUAL TABLE search_fts USING fts5(
          search_text,
          content='search_items',
          content_rowid='id',
          tokenize='trigram'
        );
        """
    )


def add_search_item(
    conn: sqlite3.Connection,
    *,
    item_type: str,
    item_key: str,
    primary_value: Optional[str],
    title: str,
    subtitle: Optional[str],
    equipment_family: Optional[str],
    repair_event_id: Optional[str],
    log_number: Optional[str],
    source_path: Optional[str],
    search_text: str,
    rank_hint: float = 0.0,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO search_items(
             item_type,item_key,primary_value,title,subtitle,equipment_family,
             repair_event_id,log_number,source_path,search_text,rank_hint,payload_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            item_type, item_key, primary_value, title, subtitle, equipment_family,
            repair_event_id, log_number, source_path, norm_text(search_text), float(rank_hint),
            json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")),
        ),
    )


def import_files(conn: sqlite3.Connection, file_db: Path, counts: BuildCounts) -> str:
    src = connect_ro(file_db)
    try:
        root = file_index_root(src)
        cols = {r[1] for r in src.execute("PRAGMA table_info(files)")}
        id_expr = "id" if "id" in cols else "rowid"
        rows = src.execute(
            f"SELECT {id_expr} AS source_id,relative_path,filename,parent_path,extension,size,mtime_ns,detected_log,file_kind FROM files"
        )
        batch = []
        search_batch = []
        for r in rows:
            rel = str(r["relative_path"])
            full = str(Path(root) / Path(rel))
            batch.append((r["source_id"], rel, r["filename"], r["parent_path"], r["extension"], r["size"], r["mtime_ns"], r["detected_log"], r["file_kind"], full))
            payload = {
                "relative_path": rel, "filename": r["filename"], "extension": r["extension"],
                "size": r["size"], "file_kind": r["file_kind"], "drl_log": r["detected_log"],
            }
            search_batch.append((
                "file", f"file:{r['source_id']}", r["filename"], r["filename"], r["parent_path"], None, None,
                r["detected_log"], full, norm_text(f"{rel} {r['detected_log'] or ''} file path filename folder"), 0.0,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            ))
            if len(batch) >= 2000:
                conn.executemany("INSERT INTO files(source_file_id,relative_path,filename,parent_path,extension,size,mtime_ns,detected_log,file_kind,absolute_path) VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
                conn.executemany("INSERT INTO search_items(item_type,item_key,primary_value,title,subtitle,equipment_family,repair_event_id,log_number,source_path,search_text,rank_hint,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", search_batch)
                counts.files += len(batch); counts.search_items += len(search_batch)
                batch.clear(); search_batch.clear()
        if batch:
            conn.executemany("INSERT INTO files(source_file_id,relative_path,filename,parent_path,extension,size,mtime_ns,detected_log,file_kind,absolute_path) VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
            conn.executemany("INSERT INTO search_items(item_type,item_key,primary_value,title,subtitle,equipment_family,repair_event_id,log_number,source_path,search_text,rank_hint,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", search_batch)
            counts.files += len(batch); counts.search_items += len(search_batch)
        return root
    finally:
        src.close()


def import_events(conn: sqlite3.Connection, events: Sequence[Dict[str, Any]], counts: BuildCounts) -> None:
    for ev in events:
        eid = compact_ws(ev.get("repair_event_id"))
        if not eid:
            continue
        log = compact_ws(ev.get("log_number")) or None
        family = compact_ws(ev.get("equipment_family")) or "[unknown equipment]"
        folders = [compact_ws(x) for x in (ev.get("top_folders") or []) if compact_ws(x)]
        paths = event_source_paths(ev)
        reported = fact_text(ev, "basic_reported_problem")
        history = fact_text(ev, "repair_history_notes")
        testing = fact_text(ev, "explicit_test_outcome")
        part_fact = fact_text(ev, "parts_replaced")
        all_fact = " | ".join(x for x in (reported, history, part_fact, testing) if x)
        conn.execute(
            "INSERT OR REPLACE INTO repair_events VALUES(?,?,?,?,?,?,?,?,?)",
            (eid, log, family, json.dumps(folders, ensure_ascii=False), json.dumps(paths, ensure_ascii=False), reported, history, testing, all_fact),
        )
        # Tracking identifiers are indexed only through the strict literal-grounding
        # path in import_tracking(). Do not leak unvalidated RMA/order values into
        # the generic event search text.
        source = paths[0] if paths else None
        add_search_item(
            conn,
            item_type="event", item_key=f"event:{eid}", primary_value=log or eid,
            title=f"Repair event {log or eid}", subtitle=family, equipment_family=family,
            repair_event_id=eid, log_number=log, source_path=source,
            search_text=f"{eid} {log or ''} {family} {' '.join(folders)} {' '.join(paths)} {all_fact} repair event history traveler line card",
            rank_hint=1.0,
            payload={"reported_problem": reported, "repair_history": history, "test_outcome": testing, "source_paths": paths},
        )
        counts.events += 1; counts.search_items += 1


def import_tracking(conn: sqlite3.Connection, rmas: Sequence[Dict[str, Any]], orders: Sequence[Dict[str, Any]], counts: BuildCounts) -> None:
    for r in rmas:
        conn.execute(
            "INSERT INTO rma_refs(repair_event_id,rma_number,rma_normalized,evidence_quote,source_path) VALUES(?,?,?,?,?)",
            (r["repair_event_id"], r["rma_number"], r["rma_normalized"], r.get("evidence_quote"), r.get("source_path")),
        )
        add_search_item(
            conn, item_type="rma", item_key=f"rma:{r['repair_event_id']}:{r['rma_normalized']}:{sha1_short(r.get('source_path') or '')}",
            primary_value=r["rma_number"], title=f"RMA {r['rma_number']}", subtitle=r.get("equipment_family"),
            equipment_family=r.get("equipment_family"), repair_event_id=r["repair_event_id"], log_number=r.get("log_number"),
            source_path=r.get("source_path"),
            search_text=f"RMA {r['rma_number']} {r['rma_normalized']} {r.get('log_number') or ''} {r.get('equipment_family') or ''} {r.get('evidence_quote') or ''} {r.get('source_path') or ''}",
            rank_hint=5.0, payload=r,
        )
        counts.rmas += 1; counts.search_items += 1

    for o in orders:
        conn.execute(
            "INSERT INTO procurement_refs(repair_event_id,supplier,order_ref,order_ref_normalized,description,manufacturer_pn,quantity,evidence_quote,source_path,grounding_mode) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (o["repair_event_id"], o.get("supplier"), o["order_ref"], o["order_ref_normalized"], o.get("description"), o.get("manufacturer_pn"), o.get("quantity"), o.get("evidence_quote"), o.get("source_path"), o["grounding_mode"]),
        )
        add_search_item(
            conn, item_type="order", item_key=f"order:{o['repair_event_id']}:{o['order_ref_normalized']}:{sha1_short(o.get('source_path') or '')}",
            primary_value=o["order_ref"], title=f"Order ref {o['order_ref']}", subtitle=o.get("supplier") or "supplier not stated",
            equipment_family=o.get("equipment_family"), repair_event_id=o["repair_event_id"], log_number=o.get("log_number"), source_path=o.get("source_path"),
            search_text=f"{o['order_ref']} {o['order_ref_normalized']} {o.get('supplier') or ''} {o.get('description') or ''} {o.get('manufacturer_pn') or ''} {o.get('log_number') or ''} {o.get('equipment_family') or ''} {o.get('evidence_quote') or ''} order procurement supplier Digi-Key Mouser DGK MSR NWK DSK",
            rank_hint=5.0, payload=o,
        )
        counts.orders += 1; counts.search_items += 1


def replacement_rows_with_event(parts: Sequence[Dict[str, Any]], event_map: Dict[str, Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    for row in parts:
        r = dict(row)
        eid = compact_ws(r.get("repair_event_id"))
        ev = event_map.get(eid, {})
        r["repair_event_id"] = eid
        r["log_number"] = compact_ws(r.get("log_number") or ev.get("log_number")) or None
        r["equipment_family"] = compact_ws(r.get("equipment_family") or ev.get("equipment_family")) or "[unknown equipment]"
        r["manufacturer_part_number"] = compact_ws(r.get("manufacturer_part_number") or r.get("part_number")) or None
        yield r


def component_group_key(row: Dict[str, Any]) -> Tuple[str, str]:
    pn = compact_ws(row.get("manufacturer_part_number"))
    if pn:
        return "pn", upper_id(pn)
    text = compact_ws(row.get("text"))
    key = re.sub(r"[^a-z0-9]+", "", text.casefold())[:96]
    return "text", key or sha1_short(text)


def import_replacements_and_products(conn: sqlite3.Connection, events: Sequence[Dict[str, Any]], parts: Sequence[Dict[str, Any]], counts: BuildCounts) -> None:
    event_map = {compact_ws(e.get("repair_event_id")): e for e in events}
    product_agg: Dict[Tuple[str, str], Dict[str, Any]] = {}
    product_event_ids: Dict[str, set] = collections.defaultdict(set)
    product_events_with_parts: Dict[str, set] = collections.defaultdict(set)

    for ev in events:
        family = compact_ws(ev.get("equipment_family")) or "[unknown equipment]"
        eid = compact_ws(ev.get("repair_event_id"))
        if eid:
            product_event_ids[family].add(eid)

    for idx, row in enumerate(replacement_rows_with_event(parts, event_map), 1):
        eid = row["repair_event_id"]
        family = row["equipment_family"]
        log = row.get("log_number")
        pn = row.get("manufacturer_part_number")
        qty = row.get("quantity") if isinstance(row.get("quantity"), int) else None
        text = compact_ws(row.get("text"))
        evidence = compact_ws(row.get("evidence_quote"))
        procurement_only = known_procurement_only_replacement(row)
        if procurement_only:
            counts.procurement_only_replacements_excluded += 1

        conn.execute(
            "INSERT INTO replacement_mentions(repair_event_id,log_number,equipment_family,manufacturer_pn,pn_normalized,quantity,text,evidence_quote,procurement_only_excluded) VALUES(?,?,?,?,?,?,?,?,?)",
            (eid, log, family, pn, upper_id(pn) if pn else None, qty, text, evidence, 1 if procurement_only else 0),
        )
        counts.replacements += 1

        if not procurement_only:
            add_search_item(
                conn, item_type="replacement", item_key=f"replacement:{idx}:{eid}", primary_value=pn or text,
                title=pn or (text or "Replacement item"), subtitle=f"{family} | log {log or '-'}",
                equipment_family=family, repair_event_id=eid, log_number=log, source_path=None,
                search_text=f"{pn or ''} {text} {evidence} {family} {log or ''} replacement replaced part component",
                rank_hint=1.0, payload={"manufacturer_pn": pn, "quantity": qty, "text": text, "evidence_quote": evidence},
            )
            counts.search_items += 1
            product_events_with_parts[family].add(eid)
            kind, key = component_group_key(row)
            gkey = (family, f"{kind}:{key}")
            g = product_agg.setdefault(gkey, {
                "family": family, "kind": kind, "key": key, "events": set(), "pieces": 0,
                "unstated": 0, "pn_variants": collections.Counter(), "text_variants": collections.Counter(),
            })
            if eid:
                g["events"].add(eid)
            if qty is None:
                g["unstated"] += 1
            else:
                g["pieces"] += qty
            if pn:
                g["pn_variants"][pn] += 1
            if text:
                g["text_variants"][text] += 1

    by_family_parts: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for (family, component_key), g in product_agg.items():
        pn = g["pn_variants"].most_common(1)[0][0] if g["pn_variants"] else None
        description = g["text_variants"].most_common(1)[0][0] if g["text_variants"] else None
        display = pn or description or component_key
        variants = list(dict.fromkeys([x for x, _ in g["pn_variants"].most_common()] + [x for x, _ in g["text_variants"].most_common(5)]))
        evs = sorted(g["events"])
        conn.execute(
            "INSERT INTO product_parts(equipment_family,component_key,display_name,manufacturer_pn,description,repair_event_count,recorded_pieces,qty_unstated_mentions,variants_json,event_ids_json,knowledge_scope) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (family, component_key, display, pn, description, len(evs), int(g["pieces"]), int(g["unstated"]), json.dumps(variants, ensure_ascii=False), json.dumps(evs), KNOWLEDGE_SCOPE),
        )
        part_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        rec = {"id": part_id, "display": display, "pn": pn, "description": description, "repairs": len(evs), "pieces": int(g["pieces"]), "unstated": int(g["unstated"]), "variants": variants}
        by_family_parts[family].append(rec)
        add_search_item(
            conn, item_type="product_part", item_key=f"product_part:{part_id}", primary_value=pn or display,
            title=display, subtitle=f"{family} | repairs={len(evs)} | pieces={int(g['pieces'])}", equipment_family=family,
            repair_event_id=None, log_number=None, source_path=None,
            search_text=f"{family} {display} {pn or ''} {description or ''} {' '.join(variants)} part parts component components replacement replacements stock stocking usage",
            rank_hint=math.log2(1 + len(evs)) * 4.0,
            payload={**rec, "equipment_family": family, "knowledge_scope": KNOWLEDGE_SCOPE},
        )
        counts.product_parts += 1; counts.search_items += 1

    families = sorted(set(product_event_ids) | set(by_family_parts), key=str.casefold)
    for family in families:
        parts_sorted = sorted(by_family_parts.get(family, []), key=lambda x: (-x["repairs"], -x["pieces"], x["display"].casefold()))
        top = parts_sorted[:15]
        conn.execute(
            "INSERT INTO product_families(equipment_family,repair_event_count,events_with_parts,indexed_component_count,top_parts_json,knowledge_scope) VALUES(?,?,?,?,?,?)",
            (family, len(product_event_ids.get(family, set())), len(product_events_with_parts.get(family, set())), len(parts_sorted), json.dumps(top, ensure_ascii=False), KNOWLEDGE_SCOPE),
        )
        add_search_item(
            conn, item_type="product", item_key=f"product:{sha1_short(family,20)}", primary_value=family,
            title=family, subtitle=f"repair events={len(product_event_ids.get(family,set()))} | indexed parts={len(parts_sorted)}",
            equipment_family=family, repair_event_id=None, log_number=None, source_path=None,
            search_text=f"{family} product equipment model repair history parts components {' '.join(str(p['display']) for p in top)}",
            rank_hint=math.log2(1 + len(product_event_ids.get(family, set()))) * 3.0,
            payload={"equipment_family": family, "repair_event_count": len(product_event_ids.get(family,set())), "events_with_parts": len(product_events_with_parts.get(family,set())), "indexed_component_count": len(parts_sorted), "top_parts": top, "knowledge_scope": KNOWLEDGE_SCOPE},
        )
        counts.products += 1; counts.search_items += 1


def finalize_fts(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO search_fts(search_fts) VALUES('rebuild')")
    conn.execute("INSERT INTO search_fts(search_fts) VALUES('optimize')")


def write_meta(conn: sqlite3.Connection, *, file_db: Path, tracking_root: Path, benchmark_root: Path, paths: Dict[str, Path], counts: BuildCounts, share_root: str, elapsed: float) -> None:
    meta = {
        "schema_version": str(SCHEMA_VERSION),
        "software_version": VERSION,
        "built_at": utc_now(),
        "knowledge_scope": KNOWLEDGE_SCOPE,
        "file_coverage": "full_persistent_drl_file_index",
        "knowledge_coverage": "frozen_10pct_benchmark_only_until_more_ingestion_is_available",
        "share_root": share_root,
        "80_20_rule": "fixed_default",
        "llm_calls": "0",
        "nas_rescan": "0",
        "build_seconds": f"{elapsed:.3f}",
        "file_index_source": str(file_db),
        "tracking_root": str(tracking_root),
        "benchmark_root": str(benchmark_root),
        "source_file_index_mtime_ns": str(source_mtime_ns(file_db)),
        "source_events_mtime_ns": str(source_mtime_ns(paths['events'])),
        "source_parts_mtime_ns": str(source_mtime_ns(paths['parts'])),
        "source_tracking_db_mtime_ns": str(source_mtime_ns(paths['tracking_db'])),
    }
    for k, v in counts.__dict__.items():
        meta[f"count_{k}"] = str(v)
    conn.executemany("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", list(meta.items()))
    source_rows = [
        ("file_index", str(file_db), source_mtime_ns(file_db), counts.files),
        ("events", str(paths["events"]), source_mtime_ns(paths["events"]), counts.events),
        ("replacement_mentions", str(paths["parts"]), source_mtime_ns(paths["parts"]), counts.replacements),
        ("tracking_lookup", str(paths["tracking_db"]), source_mtime_ns(paths["tracking_db"]), counts.rmas + counts.orders),
    ]
    conn.executemany("INSERT OR REPLACE INTO sources(name,path,mtime_ns,row_count) VALUES(?,?,?,?)", source_rows)


def build_db(args: argparse.Namespace) -> BuildCounts:
    file_db = Path(args.file_index)
    tracking_root = Path(args.tracking_root)
    benchmark_root = Path(args.benchmark_root)
    target = Path(args.db)
    paths = enriched_paths(tracking_root, benchmark_root)

    if not file_db.exists():
        raise FileNotFoundError(f"DRL file index not found: {file_db}")
    if not paths["events"].exists():
        raise FileNotFoundError(f"Repair-event source not found: {paths['events']}")
    if not paths["parts"].exists():
        raise FileNotFoundError(f"Replacement source not found: {paths['parts']}")

    events = load_source_events(paths)
    parts = load_source_parts(paths)
    rmas, rma_rejected = strict_rma_rows(events)
    orders, order_rejected, order_recovered = strict_procurement_rows(events)

    ensure_parent(target)
    temp = Path(str(target) + ".building")
    for p in (temp, Path(str(temp) + "-wal"), Path(str(temp) + "-shm")):
        if p.exists():
            p.unlink()

    counts = BuildCounts(rmas_rejected=rma_rejected, orders_rejected=order_rejected, orders_recovered_from_evidence=order_recovered)
    t0 = time.perf_counter()
    conn = sqlite3.connect(str(temp), timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        create_schema(conn)
        share_root = import_files(conn, file_db, counts)
        import_events(conn, events, counts)
        import_tracking(conn, rmas, orders, counts)
        import_replacements_and_products(conn, events, parts, counts)
        finalize_fts(conn)
        elapsed = time.perf_counter() - t0
        write_meta(conn, file_db=file_db, tracking_root=tracking_root, benchmark_root=benchmark_root, paths=paths, counts=counts, share_root=share_root, elapsed=elapsed)
        conn.commit()
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.commit()
    finally:
        conn.close()

    # Atomic replacement: an interrupted build never destroys the last good index.
    os.replace(temp, target)
    return counts


def load_meta(conn: sqlite3.Connection) -> Dict[str, str]:
    try:
        return {str(r[0]): str(r[1]) for r in conn.execute("SELECT key,value FROM meta")}
    except Exception:
        return {}


def db_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    tables = ["files", "repair_events", "rma_refs", "procurement_refs", "replacement_mentions", "product_families", "product_parts", "search_items"]
    out = {}
    for t in tables:
        try:
            out[t] = int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
        except Exception:
            out[t] = 0
    return out


def knowledge_stale(meta: Dict[str, str], args: argparse.Namespace) -> List[str]:
    paths = enriched_paths(Path(args.tracking_root), Path(args.benchmark_root))
    current = {
        "file index": source_mtime_ns(Path(args.file_index)),
        "events": source_mtime_ns(paths["events"]),
        "replacement mentions": source_mtime_ns(paths["parts"]),
        "tracking DB": source_mtime_ns(paths["tracking_db"]),
    }
    stored = {
        "file index": int(meta.get("source_file_index_mtime_ns", "0") or 0),
        "events": int(meta.get("source_events_mtime_ns", "0") or 0),
        "replacement mentions": int(meta.get("source_parts_mtime_ns", "0") or 0),
        "tracking DB": int(meta.get("source_tracking_db_mtime_ns", "0") or 0),
    }
    return [name for name, mt in current.items() if mt and mt != stored.get(name, 0)]


def fts_query(user_query: str) -> Optional[str]:
    chunks = re.findall(r"[A-Za-z0-9]+", user_query.casefold())
    chunks = [c for c in chunks if len(c) >= 3]
    if not chunks:
        return None
    # Trigram tokenizer gives substring behavior for each chunk; AND preserves
    # Everything-style multiple-term semantics.
    return " AND ".join(f'"{c}"' for c in chunks)


def custom_rank(row: sqlite3.Row, query: str, fts_rank: float) -> float:
    q = norm_text(query)
    qa = alnum_norm(query)
    primary = norm_text(row["primary_value"] or "")
    pa = alnum_norm(row["primary_value"] or "")
    title = norm_text(row["title"] or "")
    family = norm_text(row["equipment_family"] or "")
    log = alnum_norm(row["log_number"] or "")
    source = norm_text(row["source_path"] or "")
    score = ITEM_TYPE_BONUS.get(row["item_type"], 0.0) + float(row["rank_hint"] or 0.0)

    if qa and pa == qa:
        score += 1000.0
    elif q and primary == q:
        score += 980.0
    elif qa and log == qa:
        score += 950.0
    elif qa and pa.startswith(qa):
        score += 800.0
    elif qa and qa in pa:
        score += 700.0
    if q and title.startswith(q):
        score += 500.0
    elif q and q in title:
        score += 420.0
    if q and family.startswith(q):
        score += 390.0
    elif q and q in family:
        score += 330.0
    if q and q in source:
        score += 100.0
    # bm25 is negative for good FTS5 matches. Small contribution only.
    score += max(0.0, min(20.0, -float(fts_rank) * 2.0))
    return score


def search_db(conn: sqlite3.Connection, query: str, *, candidate_limit: int = 800, result_limit: int = 80) -> List[Dict[str, Any]]:
    query = compact_ws(query)
    if not query:
        return []
    fq = fts_query(query)
    candidates: List[Tuple[int, float]] = []
    if fq:
        try:
            candidates = [(int(r[0]), float(r[1])) for r in conn.execute(
                "SELECT rowid,bm25(search_fts) FROM search_fts WHERE search_fts MATCH ? ORDER BY bm25(search_fts) LIMIT ?",
                (fq, int(candidate_limit)),
            )]
        except sqlite3.OperationalError:
            candidates = []
    if not candidates:
        # Short/odd queries fallback. Local DB only; never touches NAS.
        qn = norm_text(query)
        candidates = [(int(r[0]), 0.0) for r in conn.execute(
            "SELECT id FROM search_items WHERE instr(search_text, ?) > 0 LIMIT ?",
            (qn, int(candidate_limit)),
        )]
    if not candidates:
        return []

    ids = [x[0] for x in candidates]
    rank_map = dict(candidates)
    rows: List[sqlite3.Row] = []
    for start in range(0, len(ids), 500):
        chunk = ids[start:start+500]
        qs = ",".join("?" * len(chunk))
        rows.extend(conn.execute(f"SELECT * FROM search_items WHERE id IN ({qs})", chunk).fetchall())
    out = []
    for row in rows:
        score = custom_rank(row, query, rank_map.get(int(row["id"]), 0.0))
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            payload = {}
        out.append({k: row[k] for k in row.keys()} | {"score": score, "payload": payload})
    out.sort(key=lambda r: (-r["score"], r["item_type"], norm_text(r["title"])))
    return out[:result_limit]


def render_result_line(r: Dict[str, Any]) -> List[str]:
    t = r["item_type"]
    p = r.get("payload") or {}
    out: List[str] = []
    if t == "rma":
        out.append(f"RMA {r['primary_value']} | log={r.get('log_number') or '-'} | {r.get('equipment_family') or '-'}")
        if r.get("source_path"):
            out.append(f"  source: {r['source_path']}")
    elif t == "order":
        out.append(f"{r['primary_value']} | supplier={r.get('subtitle') or '-'} | log={r.get('log_number') or '-'} | {r.get('equipment_family') or '-'}")
        if p.get("description") or p.get("manufacturer_pn"):
            out.append(f"  description: {p.get('description') or '-'} | manufacturer PN: {p.get('manufacturer_pn') or '-'} | qty={display_quantity(p.get('quantity'))}")
        if p.get("evidence_quote"):
            out.append(f"  evidence: {p['evidence_quote']}")
    elif t == "product":
        out.append(f"{r['title']} | repair events indexed={p.get('repair_event_count',0)} | events with parts={p.get('events_with_parts',0)}")
        top = p.get("top_parts") or []
        if top:
            bits = [f"{x.get('display')} ({x.get('repairs')} repairs)" for x in top[:8]]
            out.append("  top indexed parts: " + "; ".join(bits))
    elif t == "product_part":
        out.append(f"{r['title']} | {r.get('equipment_family') or '-'} | repairs={p.get('repairs',0)} | pieces={p.get('pieces',0)} | qty-unstated={p.get('unstated',0)}")
        variants = p.get("variants") or []
        if len(variants) > 1:
            out.append("  observed variants: " + ", ".join(str(x) for x in variants[:8]))
    elif t == "event":
        out.append(f"log={r.get('log_number') or r.get('primary_value') or '-'} | {r.get('equipment_family') or '-'}")
        if p.get("reported_problem"):
            out.append("  problem: " + compact_ws(p["reported_problem"])[:260])
        if p.get("repair_history"):
            out.append("  history: " + compact_ws(p["repair_history"])[:260])
        paths = p.get("source_paths") or []
        if paths:
            out.append("  source: " + str(paths[0]))
    elif t == "replacement":
        out.append(f"{r['title']} | log={r.get('log_number') or '-'} | {r.get('equipment_family') or '-'} | qty={display_quantity(p.get('quantity'))}")
        if p.get("text") and p.get("text") != r["title"]:
            out.append("  replacement: " + compact_ws(p.get("text"))[:220])
    elif t == "file":
        out.append(str(r.get("source_path") or r["title"]))
        if r.get("log_number"):
            out[-1] += f" | log={r['log_number']}"
    else:
        out.append(r["title"])
    return out


def render_search(conn: sqlite3.Connection, query: str, args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    results = search_db(conn, query, candidate_limit=args.candidate_limit, result_limit=max(args.top * 8, 80))
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    meta = load_meta(conn)

    if args.json:
        print(json.dumps({
            "version": VERSION,
            "query": query,
            "elapsed_ms": round(elapsed_ms, 3),
            "file_coverage": meta.get("file_coverage"),
            "knowledge_coverage": meta.get("knowledge_coverage"),
            "results": results[: args.top * 5],
        }, indent=2, ensure_ascii=False))
        return 0

    print(f'# NOVA-DRL search | query="{query}" | {elapsed_ms:.1f} ms')
    print("Coverage: files=full DRL index | repair/parts/RMA/orders=current frozen 10% knowledge corpus")
    if not results:
        print("No indexed matches.")
        return 0

    for heading, types in GROUP_ORDER:
        subset = [r for r in results if r["item_type"] in types][: args.top]
        if not subset:
            continue
        print(f"\n{heading}")
        print("-" * len(heading))
        for r in subset:
            for line in render_result_line(r):
                print(line)
    return 0


def command_status(args: argparse.Namespace) -> int:
    file_db = Path(args.file_index)
    tracking_root = Path(args.tracking_root)
    benchmark_root = Path(args.benchmark_root)
    paths = enriched_paths(tracking_root, benchmark_root)
    db = Path(args.db)
    print(f"# Nova DRL Unified Knowledge Index Status v{VERSION}")
    print(f"DRL file index:       {'FOUND' if file_db.exists() else 'MISSING'} | {file_db}")
    print(f"Repair-event source:  {'FOUND' if paths['events'].exists() else 'MISSING'} | {paths['events']}")
    print(f"Replacement source:   {'FOUND' if paths['parts'].exists() else 'MISSING'} | {paths['parts']}")
    print(f"Tracking source:      {'FOUND' if paths['tracking_db'].exists() else 'MISSING'} | {paths['tracking_db']}")
    print(f"Unified knowledge DB: {'FOUND' if db.exists() else 'NOT BUILT'} | {db}")
    print("Search engine:        SQLite FTS5 trigram | partial/case-insensitive | local only")
    print("AI/LLM calls:         OFF | retrieval layer only")
    print("NAS search/rescan:    OFF | reads persistent local indexes/corpus")
    print("80/20 rule:           FIXED DEFAULT")
    if not db.exists():
        return 0
    conn = connect_ro(db)
    try:
        meta = load_meta(conn)
        counts = db_counts(conn)
        stale = knowledge_stale(meta, args)
        print(f"Knowledge scope:      {meta.get('knowledge_scope','?')}")
        print(f"Indexed files:        {counts['files']:,}")
        print(f"Repair events:        {counts['repair_events']:,}")
        print(f"RMA refs:             {counts['rma_refs']:,}")
        print(f"Procurement refs:     {counts['procurement_refs']:,}")
        print(f"Replacement mentions: {counts['replacement_mentions']:,}")
        print(f"Product families:     {counts['product_families']:,}")
        print(f"Product-part rows:    {counts['product_parts']:,}")
        print(f"Search items:         {counts['search_items']:,}")
        print(f"Built at:             {meta.get('built_at','?')}")
        print(f"Source freshness:     {'STALE -> refresh recommended: ' + ', '.join(stale) if stale else 'CURRENT'}")
    finally:
        conn.close()
    return 0


def source_plan_counts(args: argparse.Namespace) -> Dict[str, Any]:
    file_db = Path(args.file_index)
    tracking_root = Path(args.tracking_root)
    benchmark_root = Path(args.benchmark_root)
    paths = enriched_paths(tracking_root, benchmark_root)
    if not file_db.exists():
        raise FileNotFoundError(file_db)
    events = load_source_events(paths)
    parts = load_source_parts(paths)
    rmas, rrej = strict_rma_rows(events)
    orders, orej, orecovered = strict_procurement_rows(events)
    src = connect_ro(file_db)
    try:
        nfiles = int(src.execute("SELECT COUNT(*) FROM files").fetchone()[0])
    finally:
        src.close()
    safe_parts = [p for p in parts if not known_procurement_only_replacement(p)]
    families = {compact_ws(e.get("equipment_family")) for e in events if compact_ws(e.get("equipment_family"))}
    approx_component_keys = {(compact_ws(p.get("equipment_family")), component_group_key({**p, "manufacturer_part_number": p.get("manufacturer_part_number") or p.get("part_number")})[1]) for p in safe_parts}
    return {
        "files": nfiles, "events": len(events), "parts": len(parts), "safe_parts": len(safe_parts),
        "rmas": len(rmas), "rma_rejected": rrej, "orders": len(orders), "order_rejected": orej,
        "orders_recovered": orecovered, "families": len(families), "product_parts_est": len(approx_component_keys),
        "search_items_est": nfiles + len(events) + len(rmas) + len(orders) + len(safe_parts) + len(families) + len(approx_component_keys),
        "paths": paths,
    }


def command_plan(args: argparse.Namespace) -> int:
    p = source_plan_counts(args)
    print(f"# Nova DRL Unified Knowledge Index v{VERSION} — PLAN ONLY")
    print(f"Full DRL file records:          {p['files']:,}")
    print(f"Repair events available:        {p['events']:,}")
    print(f"Replacement mentions available: {p['parts']:,}")
    print(f"Replacement rows usable as parts:{p['safe_parts']:,}")
    print(f"Strict RMA refs:                {p['rmas']:,} | rejected unsupported={p['rma_rejected']:,}")
    print(f"Strict procurement refs:        {p['orders']:,} | rejected unsupported={p['order_rejected']:,} | recovered literally from evidence={p['orders_recovered']:,}")
    print(f"Equipment/product families:     {p['families']:,}")
    print(f"Estimated product-part rows:    {p['product_parts_est']:,}")
    print(f"Estimated unified search items: {p['search_items_est']:,}")
    print("Search implementation:          SQLite FTS5 trigram + identifier-aware ranking")
    print("Partial search:                 YES | e.g. 1526990 finds BRD-1526990")
    print("Unified identifiers:            filename/path, model, serial text, RMA, DRL log, manufacturer PN, DGK/MSR/NWK/DSK refs, repair text")
    print("Product parts in index:         YES | grouped by equipment family from ingested knowledge")
    print("AI/LLM calls:                   0")
    print("NAS discovery/rescan:           0")
    print("Current knowledge coverage:     frozen 10% corpus; file-path coverage remains full DRL index")
    print("80/20 rule:                     FIXED DEFAULT")
    return 0


def command_build(args: argparse.Namespace) -> int:
    action = "REFRESH" if Path(args.db).exists() else "BUILD"
    t0 = time.perf_counter()
    try:
        counts = build_db(args)
    except KeyboardInterrupt:
        print("\nInterrupted. Previous good knowledge DB remains untouched.")
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    elapsed = time.perf_counter() - t0
    print(f"# Nova DRL Unified Knowledge Index v{VERSION} — {action} COMPLETE")
    print(f"Unified DB:                  {args.db}")
    print(f"Files indexed:               {counts.files:,}")
    print(f"Repair events indexed:       {counts.events:,}")
    print(f"Strict RMA refs indexed:     {counts.rmas:,} | rejected={counts.rmas_rejected:,}")
    print(f"Strict order refs indexed:   {counts.orders:,} | rejected={counts.orders_rejected:,} | recovered={counts.orders_recovered_from_evidence:,}")
    print(f"Replacement mentions stored: {counts.replacements:,}")
    print(f"Procurement-only part rows excluded from product-part knowledge: {counts.procurement_only_replacements_excluded:,}")
    print(f"Product families indexed:    {counts.products:,}")
    print(f"Product-part rows indexed:   {counts.product_parts:,}")
    print(f"Unified search items:        {counts.search_items:,}")
    print(f"Elapsed:                     {elapsed:.1f}s")
    print("AI/LLM calls:                0")
    print("NAS rescan:                  0")
    print("Current knowledge scope:     frozen 10% repair corpus + full DRL file metadata")
    return 0


def command_self_check(args: argparse.Namespace) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: unified knowledge DB not built: {db}", file=sys.stderr)
        return 2
    conn = connect_ro(db)
    try:
        queries = ["RCL1A", "GB8", "DGK", "MSR", "Line Card"]
        # Add real identifier examples if available.
        r = conn.execute("SELECT rma_number FROM rma_refs LIMIT 1").fetchone()
        if r: queries.append(str(r[0]))
        p = conn.execute("SELECT manufacturer_pn FROM product_parts WHERE manufacturer_pn IS NOT NULL LIMIT 1").fetchone()
        if p: queries.append(str(p[0]))
        print(f"# Nova DRL Unified Knowledge Index Self-Check v{VERSION}")
        ok = True
        for q in queries:
            t0 = time.perf_counter(); rows = search_db(conn, q, result_limit=20); ms = (time.perf_counter()-t0)*1000
            print(f"{q!r:30s} -> {len(rows):2d} results in {ms:7.2f} ms")
            if ms > args.self_check_warn_ms:
                ok = False
        print("Result:", "PASS" if ok else f"PASS WITH SPEED WARNING > {args.self_check_warn_ms:.0f} ms")
        return 0
    finally:
        conn.close()


def command_search(args: argparse.Namespace, query: str) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: unified knowledge DB not built: {db}", file=sys.stderr)
        print(f"Build it with: python3 tools/nova_drl_unified_knowledge_index_v1_4_8.py --build")
        return 2
    conn = connect_ro(db)
    try:
        return render_search(conn, query, args)
    finally:
        conn.close()


def command_prompt(args: argparse.Namespace) -> int:
    db = Path(args.db)
    if not db.exists():
        print(f"Unified knowledge DB not built: {db}")
        print("Run the v1.4.8 build first.")
        return 2
    conn = connect_ro(db)
    print(f"Nova DRL Unified Knowledge Search v{VERSION}")
    print("Type any full/partial PN, model, serial, RMA, DRL log, Digi-Key/Mouser order ref, or other identifying text.")
    print("Search is local/indexed; no AI call is used for simple lookups. Commands: :help  :status  :quit")
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
                print("Enter anything you know. Examples: BRD-1526990 | 1526990 | S07211 | 53434 | DGK52102 | IXFX24N100 | RCL1A")
                print("v1.4.8 is the instant retrieval layer. Interpretive troubleshooting questions are handled by the AI reasoning layer above this index.")
                continue
            if q == ":status":
                meta = load_meta(conn); c = db_counts(conn)
                print(f"files={c['files']:,} events={c['repair_events']:,} products={c['product_families']:,} product_parts={c['product_parts']:,} RMAs={c['rma_refs']:,} orders={c['procurement_refs']:,}")
                print(f"knowledge scope={meta.get('knowledge_scope','?')}")
                continue
            render_search(conn, q, args)
    finally:
        conn.close()
    return 0


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=f"Nova DRL Unified Knowledge Index v{VERSION}")
    p.add_argument("--file-index", default=DEFAULT_FILE_INDEX)
    p.add_argument("--tracking-root", default=DEFAULT_TRACKING_ROOT)
    p.add_argument("--benchmark-root", default=DEFAULT_BENCHMARK_ROOT)
    p.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--prompt", action="store_true")
    mode.add_argument("--self-check", action="store_true")
    mode.add_argument("--search")
    p.add_argument("--top", type=int, default=8, help="Maximum results per rendered group")
    p.add_argument("--candidate-limit", type=int, default=800)
    p.add_argument("--json", action="store_true")
    p.add_argument("--self-check-warn-ms", type=float, default=250.0)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
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
    # Engineer-friendly default: no flags opens the unified prompt.
    return command_prompt(args)


if __name__ == "__main__":
    raise SystemExit(main())
