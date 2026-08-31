#!/usr/bin/env python3
"""
Nova DRL Full-Corpus Unified Knowledge Engine v1.5.3

Retrieval/index builder over:
- full persistent DRL file metadata index, and
- completed v1.5.2 full repair corpus.

No LLM, vision, or NAS rescan is used. Builds a local SQLite FTS5 trigram
knowledge index for instant partial searches.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

VERSION = "1.5.3"
DEFAULT_FILE_INDEX = "/opt/nova-drl/index/drl_file_index.sqlite"
DEFAULT_FULL_ROOT = "/opt/nova-drl/output/drl_full_corpus_v1_5_2"
DEFAULT_KNOWLEDGE_DB = "/opt/nova-drl/index/drl_knowledge_index.sqlite"
KNOWLEDGE_SCOPE = "full_corpus_v1_5_2"
BASE_SCRIPT = Path(__file__).with_name("nova_drl_unified_knowledge_index_v1_4_8.py")


def _load_old():
    spec = importlib.util.spec_from_file_location("nova_drl_v148_storage", BASE_SCRIPT)
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load {BASE_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.ITEM_TYPE_BONUS["customer_po"] = 148.0
    return mod


old = _load_old()
connect_ro = old.connect_ro
search_db = old.search_db
compact_ws = old.compact_ws
alnum_norm = old.alnum_norm
upper_id = old.upper_id
source_mtime_ns = old.source_mtime_ns


@dataclass
class BuildCounts:
    files: int = 0
    events: int = 0
    rmas: int = 0
    rmas_rejected: int = 0
    customer_pos: int = 0
    customer_pos_rejected: int = 0
    orders: int = 0
    orders_rejected: int = 0
    orders_recovered_from_evidence: int = 0
    replacements: int = 0
    procurement_only_replacements_excluded: int = 0
    products: int = 0
    product_parts: int = 0
    search_items: int = 0


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    return old.read_jsonl(path)


def source_paths(full_root: Path) -> Dict[str, Path]:
    return {
        "events": full_root / "repair_events_v1_5_2.jsonl",
        "parts": full_root / "replacement_mentions_v1_5_2.jsonl",
        "rmas": full_root / "rma_refs_v1_5_2.jsonl",
        "customer_pos": full_root / "customer_po_refs_v1_5_2.jsonl",
        "orders": full_root / "procurement_refs_v1_5_2.jsonl",
        "summary": full_root / "drl_full_corpus_summary_v1_5_2.txt",
    }


def evidence_contains(evidence: Any, value: Any) -> bool:
    e = alnum_norm(evidence)
    v = alnum_norm(value)
    return bool(v and v in e)


def strict_rmas(rows: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    out, rejected, seen = [], 0, set()
    for row in rows:
        eid = compact_ws(row.get("repair_event_id"))
        value = compact_ws(row.get("rma_number"))
        evidence = compact_ws(row.get("evidence_quote"))
        if not eid or not value or not evidence_contains(evidence, value):
            rejected += 1
            continue
        norm = compact_ws(row.get("rma_normalized")) or old.normalize_rma_ref(value)
        key = (eid, upper_id(norm), compact_ws(row.get("source_path")))
        if key in seen:
            continue
        seen.add(key)
        out.append({**row, "rma_normalized": upper_id(norm)})
    return out, rejected


def strict_customer_pos(rows: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    out, rejected, seen = [], 0, set()
    for row in rows:
        eid = compact_ws(row.get("repair_event_id"))
        value = compact_ws(row.get("customer_po"))
        evidence = compact_ws(row.get("evidence_quote"))
        if not eid or not value or not evidence_contains(evidence, value):
            rejected += 1
            continue
        norm = compact_ws(row.get("customer_po_normalized")) or upper_id(value)
        key = (eid, upper_id(norm), alnum_norm(evidence))
        if key in seen:
            continue
        seen.add(key)
        out.append({**row, "customer_po_normalized": upper_id(norm)})
    return out, rejected


def strict_orders(rows: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int, int]:
    out, rejected, recovered, seen = [], 0, 0, set()
    for row in rows:
        eid = compact_ws(row.get("repair_event_id"))
        ref = compact_ws(row.get("order_ref"))
        evidence = compact_ws(row.get("evidence_quote"))
        supplier = compact_ws(row.get("supplier")) or old.infer_supplier_from_ref(ref)
        mode = "literal"
        if not eid:
            rejected += 1
            continue
        if not ref or not evidence_contains(evidence, ref):
            visible = old.extract_visible_order_from_evidence(evidence, supplier)
            if not visible:
                rejected += 1
                continue
            ref, vis_supplier = visible
            supplier = vis_supplier or supplier or old.infer_supplier_from_ref(ref)
            recovered += 1
            mode = "recovered_literal_from_evidence"
        norm = compact_ws(row.get("order_ref_normalized")) if evidence_contains(evidence, row.get("order_ref")) else ""
        norm = upper_id(norm or ref)
        key = (eid, norm, alnum_norm(evidence), compact_ws(row.get("source_path")))
        if key in seen:
            continue
        seen.add(key)
        out.append({**row, "order_ref": ref, "order_ref_normalized": norm, "supplier": supplier, "grounding_mode": mode})
    return out, rejected, recovered


def create_schema(conn: sqlite3.Connection) -> None:
    old.create_schema(conn)
    conn.executescript(
        """
        CREATE TABLE customer_po_refs(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          repair_event_id TEXT NOT NULL,
          customer_po TEXT NOT NULL,
          customer_po_normalized TEXT NOT NULL,
          evidence_quote TEXT,
          source_path TEXT
        );
        CREATE INDEX idx_customer_po_norm ON customer_po_refs(customer_po_normalized);
        """
    )


def import_tracking(conn: sqlite3.Connection, rmas, customer_pos, orders, counts: BuildCounts) -> None:
    for r in rmas:
        conn.execute(
            "INSERT INTO rma_refs(repair_event_id,rma_number,rma_normalized,evidence_quote,source_path) VALUES(?,?,?,?,?)",
            (r["repair_event_id"], r["rma_number"], r["rma_normalized"], r.get("evidence_quote"), r.get("source_path")),
        )
        old.add_search_item(
            conn, item_type="rma", item_key=f"rma:{r['repair_event_id']}:{r['rma_normalized']}:{old.sha1_short(r.get('source_path') or '')}",
            primary_value=r["rma_number"], title=f"RMA {r['rma_number']}", subtitle=r.get("equipment_family"),
            equipment_family=r.get("equipment_family"), repair_event_id=r["repair_event_id"], log_number=r.get("log_number"),
            source_path=r.get("source_path"),
            search_text=f"RMA {r['rma_number']} {r['rma_normalized']} {r.get('log_number') or ''} {r.get('equipment_family') or ''} {r.get('evidence_quote') or ''} {r.get('source_path') or ''}",
            rank_hint=5.0, payload=r,
        )
        counts.rmas += 1; counts.search_items += 1

    for p in customer_pos:
        conn.execute(
            "INSERT INTO customer_po_refs(repair_event_id,customer_po,customer_po_normalized,evidence_quote,source_path) VALUES(?,?,?,?,?)",
            (p["repair_event_id"], p["customer_po"], p["customer_po_normalized"], p.get("evidence_quote"), p.get("source_path")),
        )
        old.add_search_item(
            conn, item_type="customer_po", item_key=f"customer_po:{p['repair_event_id']}:{p['customer_po_normalized']}",
            primary_value=p["customer_po"], title=f"Customer PO {p['customer_po']}", subtitle=p.get("equipment_family"),
            equipment_family=p.get("equipment_family"), repair_event_id=p["repair_event_id"], log_number=p.get("log_number"),
            source_path=p.get("source_path"),
            search_text=f"Customer PO {p['customer_po']} {p['customer_po_normalized']} {p.get('log_number') or ''} {p.get('equipment_family') or ''} {p.get('evidence_quote') or ''}",
            rank_hint=5.0, payload=p,
        )
        counts.customer_pos += 1; counts.search_items += 1

    for o in orders:
        conn.execute(
            "INSERT INTO procurement_refs(repair_event_id,supplier,order_ref,order_ref_normalized,description,manufacturer_pn,quantity,evidence_quote,source_path,grounding_mode) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (o["repair_event_id"], o.get("supplier"), o["order_ref"], o["order_ref_normalized"], o.get("description"), o.get("manufacturer_pn"), o.get("quantity"), o.get("evidence_quote"), o.get("source_path"), o.get("grounding_mode") or "literal"),
        )
        old.add_search_item(
            conn, item_type="order", item_key=f"order:{o['repair_event_id']}:{o['order_ref_normalized']}:{old.sha1_short(o.get('source_path') or '')}",
            primary_value=o["order_ref"], title=f"Order ref {o['order_ref']}", subtitle=o.get("supplier") or "supplier not stated",
            equipment_family=o.get("equipment_family"), repair_event_id=o["repair_event_id"], log_number=o.get("log_number"), source_path=o.get("source_path"),
            search_text=f"{o['order_ref']} {o['order_ref_normalized']} {o.get('supplier') or ''} {o.get('description') or ''} {o.get('manufacturer_pn') or ''} {o.get('log_number') or ''} {o.get('equipment_family') or ''} {o.get('evidence_quote') or ''} order procurement supplier Digi-Key Mouser DGK MSR NWK DSK",
            rank_hint=5.0, payload=o,
        )
        counts.orders += 1; counts.search_items += 1


def db_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    tables = ["files", "repair_events", "rma_refs", "customer_po_refs", "procurement_refs", "replacement_mentions", "product_families", "product_parts", "search_items"]
    out = {}
    for t in tables:
        try:
            out[t] = int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
        except Exception:
            out[t] = 0
    return out


def write_meta(conn, *, file_db: Path, full_root: Path, paths: Dict[str, Path], counts: BuildCounts, share_root: str, elapsed: float) -> None:
    meta = {
        "schema_version": "2",
        "software_version": VERSION,
        "built_at": old.utc_now(),
        "knowledge_scope": KNOWLEDGE_SCOPE,
        "file_coverage": "full_persistent_drl_file_index",
        "knowledge_coverage": "full_v1_5_2_repair_corpus",
        "share_root": share_root,
        "80_20_rule": "fixed_default",
        "llm_calls": "0",
        "nas_rescan": "0",
        "build_seconds": f"{elapsed:.3f}",
        "file_index_source": str(file_db),
        "full_corpus_root": str(full_root),
        "source_file_index_mtime_ns": str(source_mtime_ns(file_db)),
    }
    for name, p in paths.items():
        meta[f"source_{name}_mtime_ns"] = str(source_mtime_ns(p))
    for k, v in counts.__dict__.items():
        meta[f"count_{k}"] = str(v)
    conn.executemany("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", list(meta.items()))
    source_rows = [("file_index", str(file_db), source_mtime_ns(file_db), counts.files)]
    source_rows += [(name, str(p), source_mtime_ns(p), 0) for name, p in paths.items() if name != "summary"]
    conn.executemany("INSERT OR REPLACE INTO sources(name,path,mtime_ns,row_count) VALUES(?,?,?,?)", source_rows)


def build_db(args: argparse.Namespace) -> BuildCounts:
    file_db = Path(args.file_index)
    full_root = Path(args.full_root)
    target = Path(args.db)
    paths = source_paths(full_root)
    if not file_db.exists():
        raise FileNotFoundError(f"DRL file index not found: {file_db}")
    for key in ("events", "parts", "rmas", "customer_pos", "orders"):
        if not paths[key].exists():
            raise FileNotFoundError(f"Full-corpus source missing: {paths[key]}")

    events = read_jsonl(paths["events"])
    parts = read_jsonl(paths["parts"])
    rmas, rrej = strict_rmas(read_jsonl(paths["rmas"]))
    pos, prej = strict_customer_pos(read_jsonl(paths["customer_pos"]))
    orders, orej, orecovered = strict_orders(read_jsonl(paths["orders"]))

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(str(target) + ".building")
    for p in (temp, Path(str(temp)+"-wal"), Path(str(temp)+"-shm")):
        if p.exists(): p.unlink()
    counts = BuildCounts(rmas_rejected=rrej, customer_pos_rejected=prej, orders_rejected=orej, orders_recovered_from_evidence=orecovered)
    t0 = time.perf_counter()
    conn = sqlite3.connect(str(temp), timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        create_schema(conn)
        share_root = old.import_files(conn, file_db, counts)
        old.import_events(conn, events, counts)
        import_tracking(conn, rmas, pos, orders, counts)
        old.import_replacements_and_products(conn, events, parts, counts)
        old.finalize_fts(conn)
        elapsed = time.perf_counter() - t0
        write_meta(conn, file_db=file_db, full_root=full_root, paths=paths, counts=counts, share_root=share_root, elapsed=elapsed)
        conn.commit()
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.commit()
    finally:
        conn.close()
    os.replace(temp, target)
    return counts


def load_meta(conn: sqlite3.Connection) -> Dict[str, str]:
    return old.load_meta(conn)


def knowledge_stale(meta: Dict[str, str], args: argparse.Namespace) -> List[str]:
    paths = source_paths(Path(args.full_root))
    checks = {"file index": (Path(args.file_index), "source_file_index_mtime_ns")}
    checks.update({name: (p, f"source_{name}_mtime_ns") for name, p in paths.items() if name != "summary"})
    stale = []
    for label, (p, key) in checks.items():
        cur = source_mtime_ns(p)
        oldv = int(meta.get(key, "0") or 0)
        if cur and cur != oldv:
            stale.append(label)
    return stale


def source_plan_counts(args: argparse.Namespace) -> Dict[str, Any]:
    file_db = Path(args.file_index)
    paths = source_paths(Path(args.full_root))
    if not file_db.exists(): raise FileNotFoundError(file_db)
    for k in ("events", "parts", "rmas", "customer_pos", "orders"):
        if not paths[k].exists(): raise FileNotFoundError(paths[k])
    events = read_jsonl(paths["events"])
    parts = read_jsonl(paths["parts"])
    rmas, rrej = strict_rmas(read_jsonl(paths["rmas"]))
    pos, prej = strict_customer_pos(read_jsonl(paths["customer_pos"]))
    orders, orej, orecovered = strict_orders(read_jsonl(paths["orders"]))
    src = connect_ro(file_db)
    try: nfiles = int(src.execute("SELECT COUNT(*) FROM files").fetchone()[0])
    finally: src.close()
    safe_parts = [p for p in parts if not old.known_procurement_only_replacement(p)]
    families = {compact_ws(e.get("equipment_family")) for e in events if compact_ws(e.get("equipment_family"))}
    component_keys = {(compact_ws(p.get("equipment_family")), old.component_group_key({**p, "manufacturer_part_number": p.get("part_number")})[1]) for p in safe_parts}
    return {
        "files": nfiles, "events": len(events), "parts": len(parts), "safe_parts": len(safe_parts),
        "rmas": len(rmas), "rma_rejected": rrej, "customer_pos": len(pos), "customer_po_rejected": prej,
        "orders": len(orders), "order_rejected": orej, "orders_recovered": orecovered,
        "families": len(families), "product_parts_est": len(component_keys),
        "search_items_est": nfiles + len(events)+len(rmas)+len(pos)+len(orders)+len(safe_parts)+len(families)+len(component_keys),
    }


def command_status(args: argparse.Namespace) -> int:
    file_db = Path(args.file_index); full_root = Path(args.full_root); db = Path(args.db); paths = source_paths(full_root)
    print(f"# Nova DRL Full-Corpus Unified Knowledge Index Status v{VERSION}")
    print(f"DRL file index:       {'FOUND' if file_db.exists() else 'MISSING'} | {file_db}")
    print(f"Full corpus root:     {'FOUND' if full_root.exists() else 'MISSING'} | {full_root}")
    for label, key in (("Repair events", "events"), ("Replacement source", "parts"), ("RMA source", "rmas"), ("Customer PO source", "customer_pos"), ("Procurement source", "orders")):
        print(f"{label+':':21s} {'FOUND' if paths[key].exists() else 'MISSING'} | {paths[key]}")
    print(f"Unified knowledge DB: {'FOUND' if db.exists() else 'NOT BUILT'} | {db}")
    print("Search engine:        SQLite FTS5 trigram | partial/case-insensitive | local only")
    print("AI/LLM calls:         OFF | retrieval layer only")
    print("NAS search/rescan:    OFF | reads persistent local indexes/corpus")
    print("80/20 rule:           FIXED DEFAULT")
    if db.exists():
        conn = connect_ro(db)
        try:
            meta = load_meta(conn); c = db_counts(conn); stale = knowledge_stale(meta, args)
            print(f"Knowledge scope:      {meta.get('knowledge_scope','?')}")
            print(f"Indexed files:        {c['files']:,}")
            print(f"Repair events:        {c['repair_events']:,}")
            print(f"RMA refs:             {c['rma_refs']:,}")
            print(f"Customer PO refs:     {c['customer_po_refs']:,}")
            print(f"Procurement refs:     {c['procurement_refs']:,}")
            print(f"Replacement mentions: {c['replacement_mentions']:,}")
            print(f"Product families:     {c['product_families']:,}")
            print(f"Product-part rows:    {c['product_parts']:,}")
            print(f"Search items:         {c['search_items']:,}")
            print(f"Built at:             {meta.get('built_at','?')}")
            print(f"Source freshness:     {'STALE -> refresh recommended: ' + ', '.join(stale) if stale else 'CURRENT'}")
        finally: conn.close()
    return 0


def command_plan(args: argparse.Namespace) -> int:
    p = source_plan_counts(args)
    print(f"# Nova DRL Full-Corpus Unified Knowledge Index v{VERSION} — PLAN ONLY")
    print(f"Full DRL file records:           {p['files']:,}")
    print(f"Full repair events available:    {p['events']:,}")
    print(f"Replacement mentions available:  {p['parts']:,}")
    print(f"Replacement rows usable as parts:{p['safe_parts']:,}")
    print(f"Strict RMA refs:                 {p['rmas']:,} | rejected unsupported={p['rma_rejected']:,}")
    print(f"Strict Customer PO refs:         {p['customer_pos']:,} | rejected unsupported={p['customer_po_rejected']:,}")
    print(f"Strict procurement refs:         {p['orders']:,} | rejected unsupported={p['order_rejected']:,} | recovered literally={p['orders_recovered']:,}")
    print(f"Equipment/product families:      {p['families']:,}")
    print(f"Estimated product-part rows:     {p['product_parts_est']:,}")
    print(f"Estimated unified search items:  {p['search_items_est']:,}")
    print("Knowledge coverage after build:  FULL v1.5.2 repair corpus + full DRL file index")
    print("Partial search:                  YES")
    print("Product parts in index:          YES | grouped by equipment family")
    print("AI/LLM calls:                    0")
    print("NAS discovery/rescan:            0")
    print("80/20 rule:                      FIXED DEFAULT")
    return 0


def command_build(args: argparse.Namespace) -> int:
    action = "REFRESH" if Path(args.db).exists() else "BUILD"
    t0 = time.perf_counter()
    try: counts = build_db(args)
    except KeyboardInterrupt:
        print("\nInterrupted. Previous good knowledge DB remains untouched."); return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr); return 2
    elapsed = time.perf_counter()-t0
    print(f"# Nova DRL Full-Corpus Unified Knowledge Index v{VERSION} — {action} COMPLETE")
    print(f"Unified DB:                  {args.db}")
    print(f"Files indexed:               {counts.files:,}")
    print(f"Repair events indexed:       {counts.events:,}")
    print(f"Strict RMA refs indexed:     {counts.rmas:,} | rejected={counts.rmas_rejected:,}")
    print(f"Strict Customer PO refs:     {counts.customer_pos:,} | rejected={counts.customer_pos_rejected:,}")
    print(f"Strict order refs indexed:   {counts.orders:,} | rejected={counts.orders_rejected:,} | recovered={counts.orders_recovered_from_evidence:,}")
    print(f"Replacement mentions stored: {counts.replacements:,}")
    print(f"Procurement-only part rows excluded: {counts.procurement_only_replacements_excluded:,}")
    print(f"Product families indexed:    {counts.products:,}")
    print(f"Product-part rows indexed:   {counts.product_parts:,}")
    print(f"Unified search items:        {counts.search_items:,}")
    print(f"Elapsed:                     {elapsed:.1f}s")
    print("Knowledge scope:             FULL v1.5.2 repair corpus + full DRL file metadata")
    print("AI/LLM calls:                0")
    print("NAS rescan:                  0")
    return 0


def command_self_check(args: argparse.Namespace) -> int:
    db = Path(args.db)
    if not db.exists(): print(f"ERROR: unified knowledge DB not built: {db}", file=sys.stderr); return 2
    conn = connect_ro(db)
    try:
        queries = ["RCL1A", "GB8", "DGK", "MSR", "Line Card"]
        for sql in ("SELECT rma_number FROM rma_refs LIMIT 1", "SELECT customer_po FROM customer_po_refs LIMIT 1", "SELECT manufacturer_pn FROM product_parts WHERE manufacturer_pn IS NOT NULL LIMIT 1"):
            r = conn.execute(sql).fetchone()
            if r and r[0]: queries.append(str(r[0]))
        print(f"# Nova DRL Full-Corpus Unified Knowledge Index Self-Check v{VERSION}")
        ok = True
        for q in queries:
            t0 = time.perf_counter(); rows = search_db(conn, q, result_limit=20); ms=(time.perf_counter()-t0)*1000
            print(f"{q!r:30s} -> {len(rows):2d} results in {ms:7.2f} ms")
            if ms > args.self_check_warn_ms: ok = False
        print("Result:", "PASS" if ok else f"PASS WITH SPEED WARNING > {args.self_check_warn_ms:.0f} ms")
        return 0
    finally: conn.close()


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=f"Nova DRL Full-Corpus Unified Knowledge Engine v{VERSION}")
    p.add_argument("--file-index", default=DEFAULT_FILE_INDEX)
    p.add_argument("--full-root", default=DEFAULT_FULL_ROOT)
    p.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--plan-only", action="store_true")
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--self-check", action="store_true")
    mode.add_argument("--search")
    p.add_argument("--top", type=int, default=8)
    p.add_argument("--candidate-limit", type=int, default=800)
    p.add_argument("--json", action="store_true")
    p.add_argument("--self-check-warn-ms", type=float, default=250.0)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = make_parser().parse_args(argv)
    if args.status: return command_status(args)
    if args.plan_only: return command_plan(args)
    if args.build or args.refresh: return command_build(args)
    if args.self_check: return command_self_check(args)
    if args.search is not None:
        conn = connect_ro(Path(args.db))
        try:
            rows = search_db(conn, args.search, candidate_limit=args.candidate_limit, result_limit=max(args.top*8,80))
            print(json.dumps(rows[:args.top*5], indent=2, ensure_ascii=False) if args.json else f"matches={len(rows)}")
        finally: conn.close()
        return 0
    return command_status(args)


if __name__ == "__main__":
    raise SystemExit(main())
