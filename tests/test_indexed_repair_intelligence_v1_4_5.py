#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
SCRIPT = HERE.parent.parent / "analysis" / "nova_indexed_repair_intelligence_v1_4_5.py"
spec = importlib.util.spec_from_file_location("v145", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)


def add_file(conn, root: Path, rel: str, detected_log=None):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"synthetic")
    name = p.name
    ext = p.suffix.lower()
    parent = str(Path(rel).parent)
    search = rel.casefold()
    conn.execute(
        "INSERT INTO files(relative_path,filename,parent_path,extension,size,mtime_ns,detected_log,file_kind,search_text) VALUES(?,?,?,?,?,?,?,?,?)",
        (rel, name, parent, ext, len(b"synthetic"), 1, detected_log, "file", search),
    )


def build_db(db: Path, share: Path):
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
    conn.execute("CREATE TABLE files(relative_path TEXT,filename TEXT,parent_path TEXT,extension TEXT,size INTEGER,mtime_ns INTEGER,detected_log TEXT,file_kind TEXT,search_text TEXT)")
    conn.execute("INSERT INTO meta(key,value) VALUES('share_root',?)", (str(share),))

    # Roger: (2) is typed primary, (1) supporting.
    base = "000 folder for tech scans/RBT - XU-RCM7231 YASKAWA SN A MICRON ROGER"
    add_file(conn, share, f"{base}/230101001 Line Card Original (1).jpg", "230101001")
    add_file(conn, share, f"{base}/230101001 Line Card Original (2).jpg", "230101001")

    # Roger: (3) remains additional primary evidence.
    add_file(conn, share, f"{base}/230101002 Line Card Original (1).jpg", "230101002")
    add_file(conn, share, f"{base}/230101002 Line Card Original (2).jpg", "230101002")
    add_file(conn, share, f"{base}/230101002 Line Card Original (3).jpg", "230101002")

    # Roger: if no (2), do not skip (1).
    add_file(conn, share, f"{base}/230101003 Line Card Original (1).jpg", "230101003")

    # Non-Roger: numbered cards are all primary; shortcut MUST NOT generalize.
    base2 = "000 folder for tech scans/RBT - XU-RCM7231 YASKAWA SN B MICRON VICTOR"
    add_file(conn, share, f"{base2}/230101004 Line Card Original (1).jpg", "230101004")
    add_file(conn, share, f"{base2}/230101004 Line Card Original (2).jpg", "230101004")

    # Legacy 10-digit token, no valid 9-digit log. Should still become one event.
    base3 = "000 folder for tech scans/RBT - XU-RCM7231 YASKAWA SN C UTI MTV ROGER"
    add_file(conn, share, f"{base3}/2001220014 Line Card Original (1).jpg", None)
    add_file(conn, share, f"{base3}/2001220014 Line Card Original (2).jpg", None)

    # Picasa backup must be excluded.
    add_file(conn, share, f"{base}/.picasaoriginals/230101001 Line Card Original.jpg", "230101001")

    # Non-line file should match broad LINE token but be filtered.
    add_file(conn, share, f"{base}/230101005 UR Email Line 00040.msg", "230101005")
    conn.commit(); conn.close()


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        share = root / "share"
        db = root / "index.sqlite"
        share.mkdir()
        build_db(db, share)

        sel = mod.select_line_cards(db, share, "XU-RCM7231 LINE")
        assert sel["raw_index_matches"] == 12, sel
        assert sel["selected_document_count"] == 10, sel
        assert sel["excluded_counts"].get("picasa_backup") == 1, sel
        assert sel["excluded_counts"].get("filename_not_line_card") == 1, sel

        events = mod.build_event_plan(sel["selected_documents"], "ROGER")
        byid = {e["repair_event_id"]: e for e in events}
        assert len(events) == 5, [e["repair_event_id"] for e in events]

        e = byid["log_230101001"]
        assert e["typed_pair_optimization_applied"] is True
        assert [d["line_card_sequence"] for d in e["primary_documents"]] == [2]
        assert [d["line_card_sequence"] for d in e["supporting_documents"]] == [1]

        e = byid["log_230101002"]
        assert [d["line_card_sequence"] for d in e["primary_documents"]] == [2,3]
        assert [d["line_card_sequence"] for d in e["supporting_documents"]] == [1]

        e = byid["log_230101003"]
        assert e["typed_pair_optimization_applied"] is False
        assert [d["line_card_sequence"] for d in e["primary_documents"]] == [1]

        e = byid["log_230101004"]
        assert e["typed_pair_engineer_match"] is False
        assert e["typed_pair_optimization_applied"] is False
        assert [d["line_card_sequence"] for d in e["primary_documents"]] == [1,2]
        assert e["supporting_documents"] == []

        e = byid["legacy_2001220014"]
        assert e["log_number"] is None
        assert e["legacy_event_token"] == "2001220014"
        assert [d["line_card_sequence"] for d in e["primary_documents"]] == [2]
        assert [d["line_card_sequence"] for d in e["supporting_documents"]] == [1]

        # Candidate creation and clustering fallback helpers stay event-bound.
        sample_events = [{
            "repair_event_id":"log_230101001","log_number":"230101001","legacy_event_token":None,
            "primary_source_paths":[],"supporting_source_paths":[],"typed_pair_optimization_applied":True,
            "facts":{c:[] for c in mod.CATEGORIES}
        }]
        sample_events[0]["facts"]["repair_actions"] = [{"text":"Rebuilt R axis motor","evidence_quote":"Rebuilt R axis motor"}]
        facts = mod.make_fact_candidates(sample_events)
        assert len(facts["repair_actions"]) == 1
        assert facts["repair_actions"][0]["repair_event_id"] == "log_230101001"

    print("PASS: Nova DRL Indexed Repair Event Intelligence v1.4.5 tests")

if __name__ == "__main__":
    main()
