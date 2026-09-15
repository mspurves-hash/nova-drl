#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_parts_ocr_deviant_resolver_v1_6_1.py"

spec = importlib.util.spec_from_file_location("resolver", TARGET)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def row(cid, label, kind="explicit_part_number", events=2, pieces=0):
    return {
        "candidate_id": cid,
        "evidence_hash": "h_" + cid,
        "family": "TEST",
        "candidate_kind": kind,
        "display_label": label,
        "part_number_variants": [label] if kind == "explicit_part_number" else [],
        "description_variants": [label] if kind != "explicit_part_number" else [],
        "repair_event_ids": [f"e{i}" for i in range(events)],
        "repair_event_count": events,
        "mention_count": events,
        "recorded_pieces": pieces,
    }

def main():
    # OCR/format comparisons
    s, _ = m.pn_similarity("ISL6551 IR", "ISL6551IR")
    assert s > 0.99, s

    s, reasons = m.pn_similarity("038AN08A1", "FDH038AN08A1")
    assert s >= 0.92, (s, reasons)

    s, _ = m.description_similarity("transistors", "transistor")
    assert s >= 0.98, s

    # Build a synthetic first human screening.
    selected = [
        row("c1", "ISL6551IR", events=4),
        row("c2", "FDH038AN08A1", events=5),
        row("c3", "Fuse Holder", kind="description_only", events=3),
        row("c4", "ONEOFF", kind="description_only", events=1),
    ]
    screened = [
        row("s1", "ISL6551 IR", events=2),
        row("s2", "038AN08A1", events=3),
        row("s3", "12ABC34", events=2),
        row("s4", "ABC123", events=1),
        row("s5", "transistors", kind="description_only", events=2),
    ]
    canon = [m.canonical_row_from_human(r, "TEST") for r in selected]
    canon = m.merge_duplicate_canonicals(canon)

    aliases, unresolved = m.learn_aliases(screened, canon, 0.92, 0.08)
    amap = {a["observed_label"]: a["canonical_label"] for a in aliases}
    assert amap["ISL6551 IR"] == "ISL6551IR"
    assert amap["038AN08A1"] == "FDH038AN08A1"

    states = {r["candidate_id"]: "human_screened" for r in screened}
    wp, rc, wq, preserved = m.classify_unresolved(
        unresolved, "TEST", states, {}, 2, 3
    )
    qlabels = {x["observed_label"] for x in wq}
    assert "12ABC34" in qlabels
    assert "ABC123" not in qlabels

    recurring = m.aggregate_recurring(canon, aliases, 2)
    labels = {x["canonical_label"]: x for x in recurring}
    assert "ISL6551IR" in labels
    assert "FDH038AN08A1" in labels
    assert labels["FDH038AN08A1"]["repair_event_count"] >= 5
    assert "ONEOFF" not in labels

    # Web result can promote an otherwise unknown recurring PN.
    unknown = row("w1", "XYZ1234", events=3)
    web_result = {
        "status": "valid",
        "canonical_label": "XYZ1234",
        "source": "example distributor",
        "source_url": "https://example.invalid/XYZ1234",
    }
    promoted = m.promote_web_validated(unknown, web_result, "TEST")
    assert promoted["authority"] == "web_validated"
    assert promoted["canonical_label"] == "XYZ1234"

    print("PASS: Nova DRL OCR Deviant Resolver v1.6.1 tests")

if __name__ == "__main__":
    main()
