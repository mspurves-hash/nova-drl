#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "nova_drl_unified_knowledge_index_v1_4_9.py"
spec = importlib.util.spec_from_file_location("v149", SCRIPT)
m = importlib.util.module_from_spec(spec)
assert spec and spec.loader
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
        (2, "000 folder for tech scans/BRD - BRD-1526990 TEST SN S07211-1-04/.picasa.ini", ".picasa.ini", "000 folder for tech scans/BRD - BRD-1526990 TEST SN S07211-1-04", ".ini", 10, 1, None, "other"),
        (3, "000 folder for tech scans/BRD - BRD-1526990 TEST SN S07211-1-04/.picasaoriginals/repair photo.jpg", "repair photo.jpg", "000 folder for tech scans/BRD - BRD-1526990 TEST SN S07211-1-04/.picasaoriginals", ".jpg", 100, 1, None, "image"),
        (4, "000 folder for tech scans/PS - RCL1A-1D-W3 RACAL SN 123/240101002 Line Card Original.jpg", "240101002 Line Card Original.jpg", "000 folder for tech scans/PS - RCL1A-1D-W3 RACAL SN 123", ".jpg", 100, 1, "240101002", "image"),
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
                    {"order_ref": "8200632948", "supplier": None, "description": None, "manufacturer_pn": None, "quantity": None, "evidence_quote": "Cust PO: 8200632948", "source_path": "/mnt/drl/card1.jpg"},
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
    sqlite3.connect(tracking / "tracking_lookup_v1_4_7.sqlite").close()
    benchmark.mkdir(parents=True, exist_ok=True)


def args_for(td: Path):
    return argparse.Namespace(
        file_index=str(td / "file.sqlite"),
        tracking_root=str(td / "tracking"),
        benchmark_root=str(td / "benchmark"),
        db=str(td / "knowledge.sqlite"),
        reports_dir=str(td / "reports"),
        report_port=18765,
        top=8,
        candidate_limit=800,
        json=False,
        self_check_warn_ms=1000.0,
    )


def has_type(rows, kind, needle=None):
    hits = [r for r in rows if m.display_type(r) == kind]
    assert hits, (kind, rows)
    if needle:
        assert any(needle.casefold() in (str(r.get("primary_value") or "") + " " + str(r.get("title") or "")).casefold() for r in hits), hits


def main():
    with tempfile.TemporaryDirectory() as raw:
        td = Path(raw)
        share = td / "share"; share.mkdir()
        make_file_index(td / "file.sqlite", share)
        make_sources(td / "tracking", td / "benchmark")
        args = args_for(td)

        # Build via proven storage engine.
        m.base.VERSION = m.VERSION
        base_args = argparse.Namespace(
            file_index=args.file_index, tracking_root=args.tracking_root, benchmark_root=args.benchmark_root,
            db=args.db, top=args.top, candidate_limit=args.candidate_limit, json=False,
            self_check_warn_ms=args.self_check_warn_ms,
        )
        counts = m.base.build_db(base_args)
        assert counts.files == 4

        con = m.base.connect_ro(Path(args.db))
        try:
            rows = m.search_clean(con, "1526990")
            has_type(rows, "product", "1526990")
            # Picasa legacy metadata/backups never appear in normal engineer results.
            assert not any("picasa" in str(r.get("source_path") or "").casefold() for r in rows if r.get("item_type") == "file"), rows

            rows = m.search_clean(con, "8200632948")
            has_type(rows, "customer_po", "8200632948")
            assert not any(m.display_type(r) == "order" for r in rows if str(r.get("primary_value")) == "8200632948")

            rows = m.search_clean(con, "MSR 56889")
            has_type(rows, "order", "MSR56889")
            assert not any(r.get("item_type") == "product_part" and "56889" in str(r.get("primary_value")) for r in rows)

            # Strict identifier query should not pull the unrelated event.
            rows = m.search_clean(con, "DGK52102")
            has_type(rows, "order", "DGK52102")
            assert all(
                r.get("repair_event_id") in {None, "log_240101002"} or r.get("item_type") == "file"
                for r in rows
            ), rows

            rows = m.search_clean(con, "53434")
            has_type(rows, "rma", "53434")
            assert any(r.get("item_type") == "event" and r.get("repair_event_id") == "log_240101001" for r in rows)

            # PDF report is dependency-free and structurally valid.
            _, groups, elapsed = m.search_report(con, "1526990", args)
            pdf = Path(args.reports_dir) / "test_report.pdf"
            m.write_pdf(pdf, "1526990", groups, elapsed)
            raw_pdf = pdf.read_bytes()
            assert raw_pdf.startswith(b"%PDF-1.4")
            assert b"NOVA DRL Search Report" in raw_pdf
            assert b".picasa.ini" not in raw_pdf
            assert len(raw_pdf) > 1000
        finally:
            con.close()

    print("PASS: Nova DRL Unified Knowledge Search + Print v1.4.9 tests")


if __name__ == "__main__":
    main()
