#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "nova_drl_file_index_v1_4_2.py"
spec = importlib.util.spec_from_file_location("idx142", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def main() -> None:
    assert mod.VERSION == "1.4.2"
    assert mod.detect_drl_log("130130006 Line Card Original.jpg") == "130130006"
    assert mod.detect_drl_log("serial 80050608") is None
    assert mod.detect_drl_log("991332001 impossible date") is None
    assert mod.tokenize_query('RCL1A "LINE CARD"') == ["rcl1a", "line card"]

    with tempfile.TemporaryDirectory(prefix="nova_drl_index_142_") as td:
        root = Path(td) / "share"
        db = Path(td) / "index.sqlite"

        # Crucial production behavior: RCL1A is in a parent folder while LINE
        # is in the filename. Search must span the full path like Matt's
        # Everything workflow, not basename only.
        a = root / "Power Supplies" / "RCL1A-1D-W3" / "Repairs" / "130130006 Line Card Original.jpg"
        b = root / "Power Supplies" / "RCL1A-1D-W3" / "Repairs" / "130130006 Unit Photo.jpg"
        c = root / "Robots" / "GB8" / "130130006 Line Card Original.jpg"
        d = root / "Power Supplies" / "RCL1A-1D-W3" / "Repairs" / "130813004 Line Card Warranty.JPG"
        write(a, b"aaa")
        write(b, b"bbb")
        write(c, b"ccc")
        write(d, b"dddd")

        conn = mod.connect_db(str(db))
        stats = mod.run_scan(conn, str(root), progress_every=0)
        assert stats.seen == 4, stats
        assert stats.added == 4, stats
        assert stats.errors == 0, stats

        total, rows = mod.search_index(conn, "RCL1A LINE", limit=None)
        assert total == 2, [r["relative_path"] for r in rows]
        names = {r["filename"] for r in rows}
        assert names == {a.name, d.name}, names
        assert all(r["detected_log"] in {"130130006", "130813004"} for r in rows)

        # Case-insensitive and extension filtering.
        total2, _ = mod.search_index(conn, "rcl1a line", extensions=["jpg"], limit=None)
        assert total2 == 2
        total3, _ = mod.search_index(conn, "GB8 LINE", limit=None)
        assert total3 == 1

        # Same DRL log can legitimately exist on multiple indexed files. The
        # index preserves both; downstream analysis decides repair-event grouping.
        total4, rows4 = mod.search_index(conn, "130130006", limit=None)
        assert total4 == 3
        assert sum(1 for r in rows4 if r["detected_log"] == "130130006") == 3

        # Refresh: add one, change one, delete one. Ensure stale deletion occurs
        # only after a clean complete scan.
        time.sleep(0.01)
        write(a, b"aaa-changed")
        e = root / "Power Supplies" / "RCL1A-1D-W3" / "Repairs" / "230101001 Line Card Original.png"
        write(e, b"new")
        b.unlink()

        stats2 = mod.run_scan(conn, str(root), progress_every=0)
        assert stats2.seen == 4, stats2
        assert stats2.added == 1, stats2
        assert stats2.changed >= 1, stats2
        assert stats2.deleted == 1, stats2
        n, _, _ = mod.db_counts(conn)
        assert n == 4, n

        total5, rows5 = mod.search_index(conn, "RCL1A LINE", limit=None)
        assert total5 == 3, [r["relative_path"] for r in rows5]

        # DB root binding protects against accidentally mixing two shares.
        other = Path(td) / "other_share"
        other.mkdir()
        try:
            mod.bind_root(conn, str(other))
        except RuntimeError:
            pass
        else:
            raise AssertionError("root binding mismatch should fail")

        scan = mod.latest_scan(conn)
        assert scan is not None and scan["status"] == "completed"

    print("PASS: Nova DRL File Index v1.4.2 tests")


if __name__ == "__main__":
    main()
