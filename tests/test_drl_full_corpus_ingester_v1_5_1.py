#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analysis" / "nova_drl_full_corpus_ingester_v1_5_1.py"
spec = importlib.util.spec_from_file_location("v151", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

# 100% deterministic corpus membership should keep every folder.
sample = m.base.deterministic_sample(["C", "A", "B"], 100.0, "seed")
assert len(sample) == 3
assert {x["folder"] for x in sample} == {"A", "B", "C"}

# Strict tracking + procurement + parts separation.
evidence = ["""
RMA#: 53434
Cust PO: 8200632948
Parts order DGK52102 $37.06
MSR 56889
Replaced 2 x HCPL-2400 optocouplers.
"""]
parsed = {
    "basic_reported_problem": [],
    "parts_replaced": [
        {"text":"MSR 56889", "part_number":"56889", "quantity":1, "evidence_quote":"MSR 56889"},
        {"text":"HCPL-2400 optocouplers", "part_number":"HCPL-2400", "quantity":2, "evidence_quote":"Replaced 2 x HCPL-2400 optocouplers."},
    ],
    "repair_history_notes": [],
    "explicit_test_outcome": [],
    "rma_numbers": [{"value":"53434", "evidence_quote":"RMA#: 53434"}],
    "customer_po_numbers": [{"value":"8200632948", "evidence_quote":"Cust PO: 8200632948"}],
    "procurement_refs": [
        {"order_ref":"DGK52102", "supplier":"Digi-Key", "description":None, "manufacturer_pn":None, "quantity":None, "evidence_quote":"Parts order DGK52102 $37.06"},
        {"order_ref":"MSR56889", "supplier":"Mouser", "description":None, "manufacturer_pn":None, "quantity":1, "evidence_quote":"MSR 56889"},
    ]
}
out = m.validate_event_json(parsed, evidence)
assert [x["part_number"] for x in out["parts_replaced"]] == ["HCPL-2400"]
orders = {m.normalize_order_ref(x["order_ref"]): x for x in out["procurement_refs"]}
assert "DGK52102" in orders
assert "MSR56889" in orders
assert orders["MSR56889"]["supplier"] == "Mouser"
assert out["rma_numbers"][0]["value"] == "53434"
assert out["customer_po_numbers"][0]["value"] == "8200632948"

# Unsupported cross-event/order hallucination must be rejected/recovered literally.
evidence2 = ["Parts ordered DigiKey 55516"]
parsed2 = {
    "basic_reported_problem": [], "parts_replaced": [], "repair_history_notes": [], "explicit_test_outcome": [],
    "rma_numbers": [], "customer_po_numbers": [],
    "procurement_refs": [{"order_ref":"DGK52102", "supplier":"Digi-Key", "description":None, "manufacturer_pn":None, "quantity":None, "evidence_quote":"Parts ordered DigiKey 55516"}],
}
out2 = m.validate_event_json(parsed2, evidence2)
assert len(out2["procurement_refs"]) == 1
assert out2["procurement_refs"][0]["order_ref"] == "55516"

# Reused v1.4.7 metadata: Customer PO should be moved out of procurement.
old = {
    "rma_numbers": [{"value":"51028", "evidence_quote":"RMA 51028", "source_path":"x"}],
    "procurement_refs": [
        {"order_ref":"8200632948", "supplier":"supplier not stated", "evidence_quote":"Cust PO: 8200632948", "source_path":"x"},
        {"order_ref":"DGK52102", "supplier":"Digi-Key", "evidence_quote":"Parts order DGK52102 $37.06", "source_path":"x"},
        {"order_ref":"DGK52102", "supplier":"Digi-Key", "evidence_quote":"Parts ordered DigiKey 55516", "source_path":"x"},
    ],
}
s = m.strictify_reused_tracking(old)
assert s["rma_numbers"][0]["value"] == "51028"
assert s["customer_po_numbers"][0]["value"] == "8200632948"
refs = [x["order_ref"] for x in s["procurement_refs"]]
assert "DGK52102" in refs
assert "55516" in refs

print("PASS: Nova DRL Full Corpus Ingester v1.5.1 tests")
