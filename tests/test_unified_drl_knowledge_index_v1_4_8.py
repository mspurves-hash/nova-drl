#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "nova_drl_unified_knowledge_index_v1_4_8.py"
spec = importlib.util.spec_from_file_location("v148", SCRIPT)
m = importlib.util.module_from_spec(spec)
assert spec and spec.loader
import sys
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def make_file_index(path: Path, share: Path):
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE files(
      id INTEGER PRIMARY KEY,
      relative_path TEXT NOT NULL,
      filename TEXT NOT NULL,
      parent_path TEXT,
      extension TEXT,
      size INTEGER,
      mtime_ns INTEGER,
      detected_log TEXT,
      file_kind TEXT
    );
    """)
    conn.execute("INSERT INTO meta VALUES('share_root',?)", (str(share),))
    rows = [
        (1, "000 folder for tech scans/BRD - BRD-1526990 TEST SN S07211-1-04/240101001 Line Card Original.jpg", "240101001 Line Card Original.jpg", "000 folder for tech scans/BRD - BRD-1526990 TEST SN S07211-1-04", ".jpg", 100, 1, "240101001", "image"),
        (2, "000 folder for tech scans/BRD - BRD-1526990 TEST SN S07211-1-04/repair photo.jpg", "repair photo.jpg", "000 folder for tech scans/BRD - BRD-1526990 TEST SN S07211-1-04", ".jpg", 100, 1, None, "image"),
        (3, "000 folder for tech scans/PS - RCL1A-1D-W3 RACAL SN 123/240101002 Line Card Original.jpg", "240101002 Line Card Original.jpg", "000 folder for tech scans/PS - RCL1A-1D-W3 RACAL SN 123", ".jpg", 100, 1, "240101002", "image"),
    ]
    conn.executemany("INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?)", rows)
    conn.commit(); conn.close()


def make_sources(tracking: Path, benchmark: Path):
    events = [
        {
            "repair_event_id": "log_240101001",
            "log_number": "240101001",
            "equipment_family": "BRD - BRD-1526990 TEST",
            "top_folders": ["BRD - BRD-1526990 TEST SN S07211-1-04"],
            "primary_source_paths": ["/mnt/drl/000 folder for tech scans/BRD - BRD-1526990 TEST SN S07211-1-04/240101001 Line Card Original.jpg"],
            "supporting_source_paths": [],
            "facts": {
                "basic_reported_problem": [{"text": "Board fails intermittently"}],
                "parts_replaced": [
                    {"text": "Replaced belt", "part_number": "314-2GT", "quantity": 2, "evidence_quote": "Replaced belt 314-2GT qty 2"},
                    {"text": "MSR 56889", "part_number": "56889", "quantity": 1, "evidence_quote": "MSR 56889"},
                ],
                "repair_history_notes": [{"text": "Rebuilt and cleaned board"}],
                "explicit_test_outcome": [{"text": "Passed functional test"}],
            },
            "tracking_metadata_v1_4_7": {
                "rma_numbers": [{"value": "53434", "evidence_quote": "RMA 53434", "source_path": "/mnt/drl/card1.jpg"}],
                "procurement_refs": [
                    {"order_ref": "MSR56889", "supplier": "Mouser", "description": None, "manufacturer_pn": None, "quantity": 1, "evidence_quote": "MSR 56889", "source_path": "/mnt/drl/card1.jpg"},
                    {"order_ref": "DGK52102", "supplier": "Digi-Key", "description": None, "manufacturer_pn": None, "quantity": None, "evidence_quote": "Parts ordered DigiKey 55516", "source_path": "/mnt/drl/card1.jpg"},
                ],
            },
        },
        {
            "repair_event_id": "log_240101002",
            "log_number": "240101002",
            "equipment_family": "PS - RCL1A-1D-W3 RACAL",
            "top_folders": ["PS - RCL1A-1D-W3 RACAL SN 123"],
            "primary_source_paths": ["/mnt/drl/000 folder for tech scans/PS - RCL1A-1D-W3 RACAL SN 123/240101002 Line Card Original.jpg"],
            "supporting_source_paths": [],
            "facts": {
                "basic_reported_problem": [{"text": "No output"}],
                "parts_replaced": [{"text": "Replaced MOSFET", "part_number": "IXFX24N100Q3", "quantity": 4, "evidence_quote": "IXFX24N100Q3 qty 4"}],
                "repair_history_notes": [],
                "explicit_test_outcome": [{"text": "Passed"}],
            },
            "tracking_metadata_v1_4_7": {
                "rma_numbers": [{"value": "60001", "evidence_quote": "RMA 60001", "source_path": "/mnt/drl/card2.jpg"}],
                "procurement_refs": [{"order_ref": "DGK52102", "supplier": "Digi-Key", "description": "MOSFET order", "manufacturer_pn": "IXFX24N100Q3", "quantity": 4, "evidence_quote": "Parts order DGK52102 $37.06", "source_path": "/mnt/drl/card2.jpg"}],
            },
        },
    ]
    parts = []
    for ev in events:
        for p in ev["facts"]["parts_replaced"]:
            parts.append({
                "repair_event_id": ev["repair_event_id"],
                "log_number": ev["log_number"],
                "equipment_family": ev["equipment_family"],
                "manufacturer_part_number": p.get("part_number"),
                "part_number": p.get("part_number"),
                "quantity": p.get("quantity"),
                "text": p.get("text"),
                "evidence_quote": p.get("evidence_quote"),
            })
    write_jsonl(tracking / "repair_events_enriched_v1_4_7.jsonl", events)
    write_jsonl(tracking / "replacement_mentions_enriched_v1_4_7.jsonl", parts)
    # Presence-only tracking DB; the builder uses evidence-grounded enriched JSON.
    sqlite3.connect(tracking / "tracking_lookup_v1_4_7.sqlite").close()
    benchmark.mkdir(parents=True, exist_ok=True)


def args_for(td: Path):
    return argparse.Namespace(
        file_index=str(td / "file.sqlite"),
        tracking_root=str(td / "tracking"),
        benchmark_root=str(td / "benchmark"),
        db=str(td / "knowledge.sqlite"),
        top=8,
        candidate_limit=800,
        json=False,
        self_check_warn_ms=1000.0,
    )


def assert_has(rows, item_type, needle=None):
    hits = [r for r in rows if r["item_type"] == item_type]
    assert hits, (item_type, rows)
    if needle is not None:
        assert any(needle.casefold() in (str(r.get("primary_value") or "") + " " + str(r.get("title") or "")).casefold() for r in hits), hits


def main():
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        share = td / "share"; share.mkdir()
        make_file_index(td / "file.sqlite", share)
        make_sources(td / "tracking", td / "benchmark")
        args = args_for(td)

        counts = m.build_db(args)
        assert counts.files == 3
        assert counts.events == 2
        assert counts.rmas == 2
        assert counts.orders == 3  # MSR56889, recovered visible 55516, literal DGK52102
        assert counts.orders_recovered_from_evidence == 1
        assert counts.procurement_only_replacements_excluded == 1

        con = m.connect_ro(Path(args.db))
        try:
            # Procurement notation must not pollute product-part manufacturer PNs.
            assert con.execute("SELECT COUNT(*) FROM product_parts WHERE manufacturer_pn='56889'").fetchone()[0] == 0
            assert con.execute("SELECT COUNT(*) FROM product_parts WHERE manufacturer_pn='314-2GT'").fetchone()[0] == 1

            # Strict order grounding: bad DGK association is not duplicated; evidence-visible 55516 is recovered.
            refs = [r[0] for r in con.execute("SELECT order_ref FROM procurement_refs ORDER BY order_ref")]
            assert refs.count("DGK52102") == 1, refs
            assert "55516" in refs, refs
            assert "MSR56889" in refs, refs

            rows = m.search_db(con, "BRD-1526990")
            assert_has(rows, "product", "BRD-1526990")
            assert_has(rows, "file")

            rows = m.search_db(con, "1526990")
            assert_has(rows, "product", "1526990")

            rows = m.search_db(con, "S07211")
            assert_has(rows, "file")
            assert_has(rows, "event")

            rows = m.search_db(con, "53434")
            assert_has(rows, "rma", "53434")

            rows = m.search_db(con, "DGK52102")
            assert_has(rows, "order", "DGK52102")
            assert all(r.get("repair_event_id") == "log_240101002" for r in rows if r["item_type"] == "order"), rows
            rows = m.search_db(con, "55516")
            assert_has(rows, "order", "55516")
            assert all(r.get("repair_event_id") == "log_240101001" for r in rows if r["item_type"] == "order"), rows

            rows = m.search_db(con, "MSR 56889")
            assert_has(rows, "order", "MSR56889")
            assert not any(r["item_type"] == "product_part" and "56889" in str(r.get("primary_value")) for r in rows)

            rows = m.search_db(con, "314-2GT")
            assert_has(rows, "product_part", "314-2GT")
            rows2 = m.search_db(con, "314")
            assert_has(rows2, "product_part", "314-2GT")

            rows = m.search_db(con, "RCL1A")
            assert_has(rows, "product", "RCL1A")
        finally:
            con.close()

    print("PASS: Nova DRL Unified Knowledge Index v1.4.8 tests")


if __name__ == "__main__":
    main()
