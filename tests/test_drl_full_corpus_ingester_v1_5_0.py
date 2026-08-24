#!/usr/bin/env python3
from __future__ import annotations
import importlib.util
import sqlite3
import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
SCRIPT = ROOT / "analysis" / "nova_drl_full_corpus_ingester_v1_5_0.py"
spec = importlib.util.spec_from_file_location("v150", SCRIPT)
m = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(m)


def build_db(db: Path, share: Path):
    con = sqlite3.connect(db)
    con.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    CREATE TABLE files(relative_path TEXT,filename TEXT,parent_path TEXT,extension TEXT,size INTEGER,mtime_ns INTEGER,detected_log TEXT,file_kind TEXT);
    """)
    con.execute("INSERT INTO meta VALUES('share_root',?)", (str(share),))
    con.execute("INSERT INTO meta VALUES('software_version','1.4.2')")
    base = "000 folder for tech scans"
    rows = [
        ("BRD - TEST-100 ASYST SN A1 ROGER", "240101001 Line Card Original (1).jpg", "240101001"),
        ("BRD - TEST-100 ASYST SN A1 ROGER", "240101001 Line Card Original (2).jpg", "240101001"),
        ("PS - TEST-200 ACME SN B2 TECH", "240102001 Line Card Original.jpg", "240102001"),
        ("PS - TEST-300 ACME SN C3 TECH", "repair photo.jpg", None),
    ]
    for folder, name, log in rows:
        d = share / base / folder
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_bytes(b"fake")
        rel = p.relative_to(share).as_posix()
        con.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?)", (rel, name, str(Path(rel).parent), p.suffix.lower(), 4, 1, log, "image"))
    d = share / base / "PS - TEST-200 ACME SN B2 TECH" / ".picasaoriginals"
    d.mkdir(parents=True)
    p = d / "240102001 Line Card Original.jpg"
    p.write_bytes(b"fake")
    rel = p.relative_to(share).as_posix()
    con.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?)", (rel, p.name, str(Path(rel).parent), ".jpg", 4, 1, "240102001", "image"))
    con.commit()
    con.close()


def main():
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        share = t / "share"
        share.mkdir()
        db = t / "idx.sqlite"
        build_db(db, share)

        class A:
            pass

        args = A()
        args.index_db = str(db)
        args.share_root = str(share)
        args.tech_base = "000 folder for tech scans"
        args.manifest = str(t / "manifest.json")
        args.refresh_manifest = False
        args.limit_folders = None
        args.limit_sampled_folders = None
        args.typed_pair_engineer = "ROGER"

        _, _, manifest, selection, events = m.prepare(args, persist_manifest=True)
        assert manifest["folder_count"] == 3
        assert selection["folder_exception_count"] == 1
        assert selection["excluded_counts"].get("picasa_backup") == 1
        assert len(events) == 2
        roger = [e for e in events if e.get("typed_pair_optimization_applied")][0]
        assert len(roger["primary_documents"]) == 1
        assert roger["primary_documents"][0]["line_card_sequence"] == 2
        assert len(roger["supporting_documents"]) == 1

        parsed = {
            "technical_evidence": "PARTS / ASSEMBLIES REPLACED OR USED: Replaced IC PN HCPL-2400 qty 1",
            "rma_numbers": [{"value": "53434", "evidence_quote": "RMA # 53434"}],
            "customer_po_numbers": [{"value": "8200632948", "evidence_quote": "Cust PO: 8200632948"}],
            "procurement_refs": [
                {"order_ref": "MSR 56889", "supplier": None, "description": None, "manufacturer_pn": None, "quantity": 1, "evidence_quote": "Parts Order MSR 56889"},
                {"order_ref": "DGK52102", "supplier": "Digi-Key", "description": None, "manufacturer_pn": None, "quantity": None, "evidence_quote": "Parts ordered DigiKey 55516"},
                {"order_ref": "8200632948", "supplier": None, "description": None, "manufacturer_pn": None, "quantity": None, "evidence_quote": "Cust PO: 8200632948"},
            ],
        }
        validated = m.parse_unified_vision(parsed)
        assert validated["tracking"]["rma_numbers"][0]["normalized"] == "53434"
        assert validated["tracking"]["customer_po_numbers"][0]["normalized"] == "8200632948"
        refs = [x["normalized"] for x in validated["tracking"]["procurement_refs"]]
        assert "MSR56889" in refs
        assert "DGK52102" not in refs
        assert "55516" in refs
        assert "8200632948" not in refs
        msr = validated["tracking"]["procurement_refs"][refs.index("MSR56889")]
        assert msr["supplier"] == "Mouser"

        assert m.procurement_only_part({"text": "MSR 56889", "part_number": "56889", "evidence_quote": "Parts Order MSR 56889"}) is True
        assert m.procurement_only_part({"text": "Replaced optocoupler", "part_number": "HCPL-2400", "evidence_quote": "Replaced HCPL-2400"}) is False

    print("PASS: Nova DRL Full Repair-History Corpus Ingester v1.5.0 tests")


if __name__ == "__main__":
    main()
