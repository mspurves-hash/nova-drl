#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analysis" / "nova_drl_full_corpus_ingester_v1_5_2.py"
spec = importlib.util.spec_from_file_location("v152", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

assert m.VERSION == "1.5.2"
assert str(m.DEFAULT_CORPUS_MANIFEST).endswith("drl_full_corpus_v1_5_1/full_corpus_manifest_v1_5_1.json")

# Preserve full-corpus membership policy.
sample = m.base.deterministic_sample(["C", "A", "B"], 100.0, "seed")
assert {x["folder"] for x in sample} == {"A", "B", "C"}


# v1.5.2 MUST accept the already-frozen v1.5.1 full-corpus membership without --force.
with tempfile.TemporaryDirectory() as td_manifest:
    td_manifest = Path(td_manifest)
    manifest_path = td_manifest / "full_corpus_manifest_v1_5_1.json"
    frozen = {
        "version":"1.5.1", "sample_percent":100.0,
        "sample_seed":m.DEFAULT_CORPUS_SEED, "tech_base":"000 folder for tech scans",
        "sample_folder_count":2, "all_top_level_folder_count":2,
        "sampled_folders":[{"sample_rank":1,"folder":"A","sample_hash":"a"},{"sample_rank":2,"folder":"B","sample_hash":"b"}],
    }
    manifest_path.write_text(json.dumps(frozen), encoding="utf-8")
    a = argparse.Namespace(
        sample_manifest=str(manifest_path), force_sample=False, sample_percent=100.0,
        sample_seed=m.DEFAULT_CORPUS_SEED, tech_base="000 folder for tech scans"
    )
    got = m.get_or_create_full_corpus_manifest(a, {"A":[],"B":[]}, {}, persist=False)
    assert got["version"] == "1.5.1"
    assert got["sample_folder_count"] == 2

# Tracking rules remain strict.
evidence = ["RMA#: 53434\nCust PO: 8200632948\nParts order DGK52102 $37.06\nMSR 56889\nReplaced 2 x HCPL-2400 optocouplers."]
parsed = {
    "basic_reported_problem": [],
    "parts_replaced": [
        {"text":"MSR 56889", "part_number":"56889", "quantity":1, "evidence_quote":"MSR 56889"},
        {"text":"HCPL-2400 optocouplers", "part_number":"HCPL-2400", "quantity":2, "evidence_quote":"Replaced 2 x HCPL-2400 optocouplers."},
    ],
    "repair_history_notes": [], "explicit_test_outcome": [],
    "rma_numbers": [{"value":"53434", "evidence_quote":"RMA#: 53434"}],
    "customer_po_numbers": [{"value":"8200632948", "evidence_quote":"Cust PO: 8200632948"}],
    "procurement_refs": [
        {"order_ref":"DGK52102", "supplier":"Digi-Key", "description":None, "manufacturer_pn":None, "quantity":None, "evidence_quote":"Parts order DGK52102 $37.06"},
        {"order_ref":"MSR56889", "supplier":"Mouser", "description":None, "manufacturer_pn":None, "quantity":1, "evidence_quote":"MSR 56889"},
    ],
}
out = m.validate_event_json(parsed, evidence)
assert [x["part_number"] for x in out["parts_replaced"]] == ["HCPL-2400"]
assert {m.normalize_order_ref(x["order_ref"]) for x in out["procurement_refs"]} == {"DGK52102", "MSR56889"}

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    # A JPEG that Pillow can read but which exercises the compatibility re-encode path.
    src = td / "legacy.JPG"
    Image.new("CMYK", (3184, 2443), (0, 10, 20, 0)).save(src, "JPEG")
    normalized = td / "normalized.jpg"
    meta = m.normalize_image_for_vision(src, normalized)
    assert normalized.exists() and normalized.stat().st_size > 0
    with Image.open(normalized) as im:
        assert im.format == "JPEG"
        assert im.mode == "RGB"
        assert im.size == (3184, 2443)
    assert meta["original"]["mode"] == "CMYK"
    assert meta["normalized"]["mode"] == "RGB"

    # Simulate Ollama rejecting the original and accepting the normalized retry.
    output = td / "out"
    args = argparse.Namespace(
        output_root=str(output), vision_model="fake-vl", vision_num_ctx=16384,
        vision_num_predict=512, timeout=30, force_vision=False,
    )
    rec = {
        "source_record_id":"img_deadbeef", "repair_event_id":"log_010101001",
        "equipment_family":"TEST - LEGACY JPEG", "source_path":str(src),
        "source_image":str(src), "source_image_sha256":m.base.sha256_file(src),
    }
    old_info = m.base.model_info
    old_call = m.base.call_ollama
    try:
        m.base.model_info = lambda model: {"available":True, "digest":"fake-digest"}
        calls = []
        def fake_call(model, prompt, *, image_paths, num_ctx, num_predict, timeout):
            calls.append(str(image_paths[0]))
            if len(calls) == 1:
                raise RuntimeError("simulated original-image rejection")
            return "PARTS / ASSEMBLIES REPLACED OR USED:\nHCPL-2400"
        m.base.call_ollama = fake_call
        rows = m.acquire_evidence_resilient(args, [rec])
        assert len(rows) == 1
        assert rows[0]["vision_status"] == "ok"
        assert rows[0]["vision_normalized_retry"] is True
        assert rows[0]["traveler_evidence_chars"] > 0
        assert len(calls) == 2 and "vision_normalized_retry" in calls[1]
        summary = json.loads((output / "vision_exception_summary_v1_5_2.json").read_text())
        assert summary["normalized_retry_success_count"] == 1
        assert summary["vision_exception_count"] == 0
    finally:
        m.base.model_info = old_info
        m.base.call_ollama = old_call

    # Simulate both attempts failing: corpus must continue with an exception record.
    output2 = td / "out2"
    args.output_root = str(output2)
    try:
        m.base.model_info = lambda model: {"available":True, "digest":"fake-digest"}
        m.base.call_ollama = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("simulated total vision failure"))
        rows = m.acquire_evidence_resilient(args, [rec])
        assert rows[0]["vision_status"] == "exception"
        assert rows[0]["traveler_evidence_chars"] == 0
        exceptions = [json.loads(x) for x in (output2 / "vision_exceptions_v1_5_2.jsonl").read_text().splitlines() if x.strip()]
        assert len(exceptions) == 1
        assert exceptions[0]["repair_event_id"] == "log_010101001"
    finally:
        m.base.model_info = old_info
        m.base.call_ollama = old_call

print("PASS: Nova DRL Full Corpus Ingester v1.5.2 tests")
