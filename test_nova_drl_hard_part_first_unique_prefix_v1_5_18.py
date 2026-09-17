#!/usr/bin/env python3
import importlib.util
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_drl_hard_part_first_unique_prefix_v1_5_18.py"
FAMILY = HERE / "nova_drl_family_first_search_v1_5_13.py"

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

m = load(TARGET, "m")
ff = load(FAMILY, "ff")

def main():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE repair_events(repair_event_id TEXT, equipment_family TEXT)"
    )

    rows = []
    def add(prefix, family, n):
        rows.extend((f"{prefix}{i}", family) for i in range(n))

    add("ela", "SVO DRV - ELA-B014CFT-03 ACME", 8)
    # Same exact PN spelling variant / OEM spelling may safely stay one PN.
    add("ela2", "SVO DRV - ELA-B014CFT-03 ACME CORP", 2)

    add("amb1", "SVO DRV - ELA-B015CFT-03 ACME", 4)
    add("amb2", "SVO DRV - ELA-B015CFT-04 ACME", 3)

    add("g", "CNTL - 9800106841 GENMARK", 2)
    add("e", "CNTL - ESC-200 BROOKS", 3)
    add("j", "SVO DRV - MR-J2S-40A MITSUBISHI", 6)
    add("m", "ENG SERVICES - M P586 VITRIUM TECH", 20)
    conn.executemany("INSERT INTO repair_events VALUES(?,?)", rows)

    resolver = m.make_hard_part_number_first_resolver(ff)

    # Exact remains exact.
    r = resolver(conn, "SVO DRV - ELA-B014CFT-03")
    assert r and r["base_part_number"] == "ELA-B014CFT-03"
    assert r["resolution_source"].startswith("hard_exact")

    # User's edge case: partial uniquely identifies one full PN.
    r = resolver(conn, "SVO DRV - ELA-B014")
    assert r, "ELA-B014 should resolve uniquely"
    assert r["base_part_number"] == "ELA-B014CFT-03", r
    assert len(r["families"]) == 2
    assert r["resolution_source"].startswith("unique_prefix")

    # Numeric prefix works only when unique.
    r = resolver(conn, "CNTL - 980010")
    assert r and r["base_part_number"] == "9800106841"

    # Ambiguous partial must never guess or aggregate.
    assert resolver(conn, "SVO DRV - ELA-B015") is None

    # Broad/OEM/category text never reaches old M-family behavior.
    for q in ["CNTL - Genmark", "GENMARK", "CNTL - ESC", "MITSUBISHI", "M"]:
        assert resolver(conn, q) is None, q

    # Existing exact cases still work.
    assert resolver(conn, "ESC-200")["display_family"] == "CNTL - ESC-200 BROOKS"
    assert resolver(conn, "MR-J2S-40A")["display_family"].startswith("SVO DRV - MR-J2S-40A")

    print("PASS: v1.5.18 exact + unique-prefix Part #-first regression tests")

if __name__ == "__main__":
    main()
