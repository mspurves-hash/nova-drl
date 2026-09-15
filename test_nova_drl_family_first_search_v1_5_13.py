#!/usr/bin/env python3
import importlib.util
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_drl_family_first_search_v1_5_13.py"

spec = importlib.util.spec_from_file_location("ff", TARGET)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def main():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE repair_events("
        "repair_event_id TEXT, equipment_family TEXT)"
    )

    rows = []
    for i in range(5):
        rows.append((f"b{i}", "BRD - BM23995 EXEC CAR ASYST"))
    for i in range(4):
        rows.append((f"x{i}", "RBT - XU-RCM7231 YASKAWA"))
    for i in range(6):
        rows.append((f"j{i}", "SVO DRV - MR-J2S-40A MITSUBISHI"))
    for i in range(20):
        rows.append((f"m{i}", "ENG SERVICES - M P586 VITRIUM TECH"))
    conn.executemany("INSERT INTO repair_events VALUES(?,?)", rows)

    assert m.exact_family_matches(conn, "BM23995") == [
        "BRD - BM23995 EXEC CAR ASYST"
    ]
    assert m.exact_family_matches(conn, "XU-RCM7231") == [
        "RBT - XU-RCM7231 YASKAWA"
    ]
    assert m.exact_family_matches(conn, "MR-J2S-40A") == [
        "SVO DRV - MR-J2S-40A MITSUBISHI"
    ]
    assert m.exact_family_matches(conn, "ASYST") == []

    def bad_original(conn, query):
        return {
            "base_part_number": "M",
            "display_family": "ENG SERVICES - M P586 VITRIUM TECH",
            "families": ["ENG SERVICES - M P586 VITRIUM TECH"],
            "model_variants": [],
        }

    resolver = m.make_family_first_resolver(bad_original)

    r = resolver(conn, "BM23995")
    assert r["families"] == ["BRD - BM23995 EXEC CAR ASYST"]
    assert r["resolution_source"] == "exact_drl_part_number_family_first"

    r = resolver(conn, "XU-RCM7231")
    assert r["families"] == ["RBT - XU-RCM7231 YASKAWA"]

    r = resolver(conn, "MR-J2S-40A")
    assert r["families"] == ["SVO DRV - MR-J2S-40A MITSUBISHI"]

    # Specific missing Part # rejects the bogus M fallback.
    assert resolver(conn, "ZZZ-12345") is None

    # Broad manufacturer/description terms retain original search behavior.
    broad = resolver(conn, "ASYST")
    assert broad["base_part_number"] == "M"

    print("PASS: Nova DRL Family-First Search Wrapper v1.5.13 tests")


if __name__ == "__main__":
    main()
