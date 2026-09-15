#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_parts_ocr_deviant_resolver_v1_6_3_1.py"

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


    # Authoritative canonical refinement updates canonical + both alias layers.
    short = m.canonical_row_from_label(
        row("st0", "STTH1506", events=3), "TEST", "STTH1506"
    )
    ocr_alias = {
        "family": "TEST",
        "observed_kind": "explicit_part_number",
        "observed_label": "S T T H 1506 T P I",
        "canonical_id": short["canonical_id"],
        "canonical_label": "STTH1506",
        "repair_event_ids": ["e1"],
        "mention_count": 1,
        "recorded_pieces": 1,
    }
    supplier_alias = {
        "family": "TEST",
        "supplier": "Mouser",
        "supplier_part_number": "511-STTH1506TPI",
        "canonical_id": short["canonical_id"],
        "canonical_label": "STTH1506",
        "repair_event_ids": ["e2"],
        "mention_count": 1,
        "recorded_pieces": 1,
    }
    ovs = [{
        "family": "TEST",
        "canonical_kind": "explicit_part_number",
        "from_canonical": "STTH1506",
        "to_canonical": "STTH1506TPI",
        "authority": "manufacturer_datasheet",
        "source": "STMicroelectronics",
        "source_url": "https://www.st.com/",
        "reason": "official PN",
    }]
    c2, a2, s2, changed = m.apply_canonical_overrides(
        [short], [ocr_alias], [supplier_alias], ovs
    )
    assert c2[0]["canonical_label"] == "STTH1506TPI"
    assert a2[0]["canonical_label"] == "STTH1506TPI"
    assert s2[0]["canonical_label"] == "STTH1506TPI"
    assert len(changed) == 1

    # Regression: exercise the FULL write path so plan-only cannot hide
    # reporting/output-filename bugs.
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        review = td / "review"
        output = td / "output"
        review.mkdir()

        c_keep = row("full_keep", "UCC3818D", events=3, pieces=3)
        c_drop = row("full_drop", "UCC3818", events=2, pieces=2)

        (review / "review_candidates.jsonl").write_text(
            json.dumps(c_keep) + "\n" + json.dumps(c_drop) + "\n",
            encoding="utf-8",
        )
        (review / "selected_parts.jsonl").write_text(
            json.dumps(c_keep) + "\n", encoding="utf-8"
        )
        (review / "suppressed_parts.jsonl").write_text(
            json.dumps(c_drop) + "\n", encoding="utf-8"
        )
        (review / "human_review_decisions.jsonl").write_text("", encoding="utf-8")

        run = subprocess.run(
            [
                sys.executable,
                str(TARGET),
                "--candidate-root", str(review),
                "--training-review-root", str(review),
                "--output-root", str(output),
                "--family", "TEST",
            ],
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, run.stderr + "\\n" + run.stdout
        expected = [
            "canonical_parts_v1_6_3_1.jsonl",
            "canonical_refinements_v1_6_3_1.jsonl",
            "supplier_part_aliases_v1_6_3_1.jsonl",
            "part_aliases_v1_6_3_1.jsonl",
            "web_validation_queue_v1_6_3_1.jsonl",
            "preserved_unpromoted_v1_6_3_1.jsonl",
            "recurring_parts_80_20_v1_6_3_1.jsonl",
            "ocr_deviant_learning_v1_6_3_1.json",
            "parts_resolver_manifest_v1_6_3_1.json",
            "parts_resolver_summary_v1_6_3_1.txt",
        ]
        missing = [name for name in expected if not (output / name).exists()]
        assert not missing, f"Missing full-run outputs: {missing}"
        assert "Outputs:" in run.stdout

    print("PASS: Nova DRL supplier-aware + authoritative canonical Resolver v1.6.3.1 tests + full-write regression")

if __name__ == "__main__":
    main()
