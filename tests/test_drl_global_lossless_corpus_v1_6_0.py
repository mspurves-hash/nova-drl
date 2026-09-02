#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INGESTER = ROOT / "analysis" / "nova_drl_global_lossless_corpus_ingester_v1_6_0.py"
PARSER = ROOT / "tools" / "drl_lossless_evidence_parser_v1_6_0.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def get_triple(src: str, name: str) -> str:
    m = re.search(rf"{re.escape(name)}\s*=\s*(?:r)?\"\"\"(.*?)\"\"\"", src, re.S)
    assert m, f"missing prompt {name}"
    return m.group(1)


def main() -> int:
    assert INGESTER.exists(), INGESTER
    assert PARSER.exists(), PARSER
    isrc = INGESTER.read_text(encoding="utf-8")
    psrc = PARSER.read_text(encoding="utf-8")

    # Historical proven baseline prompts are regression-frozen by exact SHA256.
    tr = get_triple(isrc, "TRANSCRIPTION_PROMPT")
    pr = get_triple(isrc, "PROSPECT_PROMPT")
    assert hashlib.sha256(tr.encode()).hexdigest() == "590415fdf89e713b85784bddf021652e19d08830545071f9378d79e9bc9dc954"
    assert hashlib.sha256(pr.encode()).hexdigest() == "a0488846ab067b976643186a81f2af42c6a98234c03f96d5b1e2bf7bf0b4ad83"

    # Proven model/role is fixed globally; no downstream model rewrite gate is present.
    assert 'DEFAULT_MODEL = "qwen3-vl-drl:8b-q8-16k"' in isrc
    assert "base.call_json(" not in isrc
    assert "reason_model" not in isrc
    assert '"later_model_rewrite": False' in isrc
    assert '"raw_evidence_deletion": False' in isrc
    assert "HARD GOVERNING RULE" in isrc

    # No product-specific or benchmark-answer rules may enter the production pipeline.
    forbidden = [
        "PRE-200", "RCL1A", "MR-J2S", "GB8",
        "IXFX24N100Q3", "FDH038AN08A1", "STTH1506TPI", "ISL6551IR",
        "HEDS-5540", "SN74LS14N", "AM26LS31CN", "26LS31PC",
    ]
    # Comments/docstrings that describe policy are still production source, so forbid everywhere.
    for token in forbidden:
        assert token.casefold() not in (isrc + "\n" + psrc).casefold(), f"product-specific literal leaked into global pipeline: {token}"

    parser = load(PARSER, "v160_parser")

    high = """REPORTED FAILURE / CUSTOMER COMPLAINT:\n- axis will not home\nEXPLICIT PARTS / COMPONENTS REPLACED, INSTALLED, SWAPPED, REBUILT OR USED:\n- replaced X-axis motor\nOTHER TECHNICAL REPAIR / SERVICE ACTIONS:\n- cleaned and regreased lead screw\nEXPLICIT TEST / OUTCOME:\n- cycle tested overnight\n"""
    sec = parser.parse_high_recall_sections(high)
    assert sec["reported_failure"] == ["axis will not home"]
    assert sec["parts_replaced"] == ["replaced X-axis motor"]
    assert sec["repair_actions"] == ["cleaned and regreased lead screw"]
    assert "cleaned" not in " ".join(sec["parts_replaced"]).lower()

    pn = parser.parse_pn_focus("PART/REFERENCE: ABC-1234-Z | CONTEXT: encoder\nPART/REFERENCE: DGK 55516 | CONTEXT: order\n")
    assert pn[0]["reference"] == "ABC-1234-Z" and pn[0]["eligible_component_reference"] is True
    assert pn[1]["eligible_component_reference"] is False and pn[1]["exclusion_reason"] == "procurement_order_ref"

    track = parser.tracking_from_texts([
        ("transcription", "RMA Number: 12345\nCustomer PO Number: 4500123456\nOrdered: MSR 56889"),
    ])
    assert track["rma_numbers"][0]["normalized"] == "12345"
    assert track["customer_po_numbers"][0]["normalized"] == "4500123456"
    assert track["procurement_refs"][0]["normalized"] == "MSR56889"
    assert track["procurement_refs"][0]["supplier"] == "Mouser"

    # Prospector quote must remain verbatim-bound; hallucinated rows can be preserved but flagged.
    pro = parser.parse_prospector('{"candidates":[{"kind":"repair_or_service","raw_quote":"Replaced motor"},{"kind":"component_or_part","raw_quote":"Invented part"}]}', "Replaced motor\n")
    assert pro[0]["quote_bound_exact"] is True
    assert pro[1]["quote_bound_exact"] is False

    # Derived event facts honor direct section role; PN focus does not become replacement evidence.
    src = {"repair_event_id":"log_260101001","source_record_id":"src_demo","source_path":"/demo/card.jpg","source_relative_path":"demo/card.jpg","source_image_sha256":"abc","equipment_family":"DEMO-UNIT","line_card_sequence":None,"selection_reason":"normal_traveler_primary"}
    ledger = parser.evidence_rows_for_record(src, high_recall_text=high, prospector_text='{"candidates":[]}', prospector_working_view='', pn_focus_text='PART/REFERENCE: ABC-1234-Z | CONTEXT: encoder')
    facts = parser.derive_event_facts(ledger)
    assert len(facts["parts_replaced"]) == 1
    assert len(facts["part_references"]) == 1
    assert "ABC-1234-Z" not in " ".join(x["text"] for x in facts["parts_replaced"])

    print("PASS: Nova DRL v1.6.0 global lossless corpus invariants — proven 8B roles frozen; evidence additive; no product-specific rules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
