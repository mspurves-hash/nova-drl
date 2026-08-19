#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analysis" / "nova_drl_10pct_corpus_ingester_v1_4_6.py"
spec = importlib.util.spec_from_file_location("v146", SCRIPT)
m = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(m)


def build_db(db: Path, share: Path):
    conn = sqlite3.connect(db)
    conn.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE files(
      relative_path TEXT, filename TEXT, parent_path TEXT, extension TEXT,
      size INTEGER, mtime_ns INTEGER, detected_log TEXT, file_kind TEXT
    );
    """)
    conn.execute("INSERT INTO meta VALUES('share_root',?)", (str(share),))
    conn.execute("INSERT INTO meta VALUES('software_version','1.4.2')")
    base = "000 folder for tech scans"
    # 100 top-level repair folders => exact 10% sample is 10 folders.
    for i in range(100):
        folder = f"RBT - MODEL-{i:03d} MAKER SN S{i:05d} ROGER" if i % 3 == 0 else f"PS - MODEL-{i:03d} MAKER SN S{i:05d} TECH"
        d = share / base / folder
        d.mkdir(parents=True, exist_ok=True)
        log = f"24{(i%12)+1:02d}{(i%27)+1:02d}{(i%900)+1:03d}"
        # Ensure valid dates, but DETECTED log is supplied by index in this synthetic DB.
        if i == 0:
            # Roger pair plus picasa backup.
            files = [
                (f"{log} Line Card Original (1).jpg", log),
                (f"{log} Line Card Original (2).jpg", log),
            ]
            for name, lg in files:
                p = d / name; p.write_bytes(b"fake")
                rel = p.relative_to(share).as_posix()
                conn.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?)", (rel,name,str(Path(rel).parent),".jpg",4,1,lg,"image"))
            pd = d / ".picasaoriginals"; pd.mkdir()
            p = pd / f"{log} Line Card Original.jpg"; p.write_bytes(b"fake")
            rel = p.relative_to(share).as_posix()
            conn.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?)", (rel,p.name,str(Path(rel).parent),".jpg",4,1,log,"image"))
        elif i == 1:
            # Folder deliberately lacks a Line Card; only photo.
            p = d / "repair photo.jpg"; p.write_bytes(b"fake")
            rel = p.relative_to(share).as_posix()
            conn.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?)", (rel,p.name,str(Path(rel).parent),".jpg",4,1,None,"image"))
        else:
            p = d / f"{log} Line Card Original.jpg"; p.write_bytes(b"fake")
            rel = p.relative_to(share).as_posix()
            conn.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?)", (rel,p.name,str(Path(rel).parent),".jpg",4,1,log,"image"))
    conn.commit(); conn.close()


def main():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td); share = t / "share"; share.mkdir(); db = t / "index.sqlite"
        build_db(db, share)
        by_folder, meta = m.load_tech_scan_rows(db, "000 folder for tech scans")
        assert len(by_folder) == 100, len(by_folder)

        s1 = m.deterministic_sample(sorted(by_folder), 10.0, "seed")
        s2 = m.deterministic_sample(sorted(by_folder), 10.0, "seed")
        assert s1 == s2
        assert len(s1) == 10
        assert len({x['folder'] for x in s1}) == 10

        # Force a manifest containing folders 0,1 plus eight others so source rules are deterministic.
        names = sorted(by_folder, key=str.casefold)
        f0 = next(x for x in names if "MODEL-000" in x)
        f1 = next(x for x in names if "MODEL-001" in x)
        chosen = [f0, f1] + [x for x in names if x not in {f0,f1}][:8]
        manifest = {
            "version": m.VERSION,
            "all_top_level_folder_count": 100,
            "sample_percent": 10.0,
            "sample_seed": "test",
            "sample_folder_count": 10,
            "sampled_folders": [{"sample_rank":i+1,"folder":f,"sample_hash":m.sample_hash(f,"test")} for i,f in enumerate(chosen)],
        }
        class A: pass
        args=A(); args.share_root=str(share); args.limit_sampled_folders=None; args.typed_pair_engineer="ROGER"
        sel = m.select_sample_sources(args, by_folder, manifest)
        assert sel["sample_folder_count_effective"] == 10
        assert sel["folder_exception_count"] == 1, sel["folder_exceptions"]
        assert sel["excluded_counts"].get("picasa_backup") == 1
        events = m.build_event_plan(sel["selected_documents"], "ROGER")
        roger = [e for e in events if any("MODEL-000" in f for f in e["top_folders"])][0]
        assert roger["typed_pair_optimization_applied"] is True
        assert len(roger["primary_documents"]) == 1
        assert roger["primary_documents"][0]["line_card_sequence"] == 2
        assert len(roger["supporting_documents"]) == 1
        assert roger["supporting_documents"][0]["line_card_sequence"] == 1
        assert m.equipment_family_from_folder("ALIGNER - EG-300B-009 ASYST SN A123 SEMITORR ROGER") == "ALIGNER - EG-300B-009 ASYST"
        assert m.equipment_family_from_folder("15450 - 10 SN 3011900D") == "15450 - 10"

        evidence = ["PARTS / ASSEMBLIES REPLACED OR USED:\nReplaced belt PN 314-2GT qty 2"]
        parsed = {
            "basic_reported_problem": [],
            "parts_replaced": [{"text":"belt","part_number":"314-2GT","quantity":2,"evidence_quote":"Replaced belt PN 314-2GT qty 2"}],
            "repair_history_notes": [],
            "explicit_test_outcome": [],
        }
        valid = m.validate_event_json(parsed, evidence)
        assert valid["parts_replaced"][0]["part_number"] == "314-2GT"
        assert valid["parts_replaced"][0]["quantity"] == 2

    print("PASS: Nova DRL 10% Benchmark Corpus Ingester v1.4.6 tests")

if __name__ == "__main__":
    main()
