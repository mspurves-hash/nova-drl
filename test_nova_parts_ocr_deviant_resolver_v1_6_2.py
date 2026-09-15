#!/usr/bin/env python3
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_parts_ocr_deviant_resolver_v1_6_2.py"

spec = importlib.util.spec_from_file_location("resolver", TARGET)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def row(cid, label, kind="explicit_part_number", events=2, pieces=0, variants=None):
    return {
        "candidate_id": cid,
        "evidence_hash": "h_" + cid,
        "family": "TEST",
        "candidate_kind": kind,
        "display_label": label,
        "part_number_variants": variants if variants is not None else ([label] if kind == "explicit_part_number" else []),
        "description_variants": [label] if kind != "explicit_part_number" else [],
        "repair_event_ids": [f"{cid}_e{i}" for i in range(events)],
        "repair_event_count": events,
        "mention_count": events,
        "recorded_pieces": pieces,
    }

def main():
    # Existing OCR/format behavior.
    s, _ = m.pn_similarity("ISL6551 IR", "ISL6551IR")
    assert s > 0.99, s

    s, reasons = m.pn_similarity("038AN08A1", "FDH038AN08A1")
    assert s >= 0.92, (s, reasons)

    # Supplier detection: Mouser 511 prefix.
    info = m.detect_supplier_part_number("511-S TTH 1506 TPI", ("511",))
    assert info and info["supplier"] == "Mouser"
    assert info["supplier_part_number"] == "511-STTH1506TPI"
    assert info["manufacturer_body_candidate"] == "STTH1506TPI"
    assert info["body_can_seed_human_canonical"] is True

    # Supplier detection: DigiKey -ND suffix.
    info = m.detect_supplier_part_number("LM5110-2M/NOPB-ND", ("511",))
    assert info and info["supplier"] == "DigiKey"
    assert info["manufacturer_body_candidate"] == "LM5110-2M/NOPB"
    assert info["body_can_seed_human_canonical"] is False

    # Human-approved Mouser PN becomes a manufacturer canonical plus supplier alias.
    mouser = row(
        "m1",
        "511-STTH1506",
        events=3,
        variants=["511-STTH1506", "511-S TTH 1506 TPI", "511-STTH1506TPI"],
    )
    canon, supplier_aliases, unresolved = m.build_human_canonical_vocabulary(
        [mouser], "TEST", ("511",), 0.92, 0.08
    )
    labels = {x["canonical_label"] for x in canon}
    assert "STTH1506TPI" in labels, labels
    assert len(supplier_aliases) == 1
    assert supplier_aliases[0]["supplier"] == "Mouser"
    assert supplier_aliases[0]["canonical_label"] == "STTH1506TPI"
    assert not unresolved

    # DigiKey PN links to a separately known manufacturer canonical.
    manufacturer = row("d0", "LM5110-2M/NOPB", events=2)
    digikey = row("d1", "LM5110-2M/NOPB-ND", events=2)
    canon, supplier_aliases, unresolved = m.build_human_canonical_vocabulary(
        [manufacturer, digikey], "TEST", ("511",), 0.92, 0.08
    )
    labels = {x["canonical_label"] for x in canon}
    assert "LM5110-2M/NOPB" in labels
    dk = [x for x in supplier_aliases if x["supplier"] == "DigiKey"]
    assert len(dk) == 1 and dk[0]["canonical_label"] == "LM5110-2M/NOPB"
    assert not unresolved

    # DigiKey alone does NOT manufacture a canonical just because -ND was stripped.
    canon, supplier_aliases, unresolved = m.build_human_canonical_vocabulary(
        [digikey], "TEST", ("511",), 0.92, 0.08
    )
    assert not canon
    assert not supplier_aliases
    assert len(unresolved) == 1
    assert unresolved[0]["_supplier_info"]["supplier"] == "DigiKey"

    # Supplier wrapper is resolved before OCR learning.
    base = m.canonical_row_from_label(manufacturer, "TEST", "LM5110-2M/NOPB")
    linked, residual = m.resolve_supplier_rows(
        [digikey], "TEST", [base], {"d1": "human_screened"}, ("511",), 0.92, 0.08
    )
    assert len(linked) == 1
    assert not residual
    assert linked[0]["supplier"] == "DigiKey"
    assert linked[0]["canonical_label"] == "LM5110-2M/NOPB"

    # Existing web queue behavior still works.
    unknown = row("w1", "12ABC34", events=2)
    states = {"w1": "human_screened"}
    wp, rc, wq, preserved = m.classify_unresolved(
        [unknown], "TEST", states, {}, 2, 3
    )
    assert {x["observed_label"] for x in wq} == {"12ABC34"}

    # Recurrence aggregation accepts supplier aliases without double counting
    # a supplier-selected row already represented in the canonical seed.
    canon = [m.canonical_row_from_label(mouser, "TEST", "STTH1506TPI", authority="human_selected_supplier_body")]
    sa = m.make_supplier_alias(
        mouser,
        "TEST",
        m.best_supplier_info_for_row(mouser, ("511",)),
        canon[0],
        source_state="human_selected",
        counts_already_in_canonical=True,
    )
    recurring = m.aggregate_recurring(canon, [], [sa], 2)
    assert len(recurring) == 1
    assert recurring[0]["mention_count"] == mouser["mention_count"]

    print("PASS: Nova DRL supplier-aware OCR Deviant Resolver v1.6.2 tests")

if __name__ == "__main__":
    main()
