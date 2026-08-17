#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analysis" / "nova_power_supply_corpus_pilot_v1_4_0.py"
spec = importlib.util.spec_from_file_location("pspilot", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def test_blind_runtime_has_no_known_benchmark_part_numbers():
    text = SCRIPT.read_text(encoding="utf-8")
    # These are deliberately checked only in the TEST, not used by the runtime.
    assert "IXFX24N100Q3" not in text
    assert "FDH038AN08A1" not in text


def test_duplicate_similarity():
    a = "Repair notes: replaced 2 ABC123 power devices and fuse. Final test passed."
    b = "Repair notes - replaced 2 ABC123 power devices and fuse Final test passed"
    c = "Completely unrelated customer note about a connector."
    assert mod.jaccard(mod.shingles(a), mod.shingles(b)) > 0.70
    assert mod.jaccard(mod.shingles(a), mod.shingles(c)) < 0.30


def test_quote_binding_and_extraction_validation():
    transcription = "Replaced 3 QX-100 devices. Also cleaned board and soldered trace. Replaced fan."
    parsed = {
        "record_class": "repair_record",
        "replacements": [
            {"raw_quote": "Replaced 3 QX-100 devices.", "part_number": "QX-100", "description": "power device", "quantity": 3, "quantity_text": "3", "action": "replaced", "uncertain": False},
            {"raw_quote": "Replaced fan.", "part_number": None, "description": "fan", "quantity": 1, "quantity_text": None, "action": "replaced", "uncertain": False},
            {"raw_quote": "Invented quote", "part_number": "BAD", "description": "bad", "quantity": 1, "action": "replaced", "uncertain": False}
        ]
    }
    rc, rows, rejected = mod.validate_extraction(parsed, 7, transcription)
    assert rc == "repair_record"
    assert len(rows) == 2
    assert rows[0]["quantity"] == 3
    assert any(x["reason"] == "raw_quote_not_bound" for x in rejected)


def test_vague_quantity_stays_unstated():
    transcription = "Replaced several surface mount components."
    parsed = {"record_class": "repair_record", "replacements": [{
        "raw_quote": "Replaced several surface mount components.",
        "part_number": None,
        "description": "surface mount components",
        "quantity": None,
        "quantity_text": "several",
        "action": "replaced",
        "uncertain": False,
    }]}
    _, rows, _ = mod.validate_extraction(parsed, 1, transcription)
    assert rows[0]["quantity"] is None


def test_frequency_counts_distinct_pages_and_pieces():
    mentions = [
        {"mention_id": "m1", "page_number": 1, "part_number": "QX-100", "description": "device", "quantity": 2, "uncertain": False},
        {"mention_id": "m2", "page_number": 1, "part_number": "QX-100", "description": "device", "quantity": 1, "uncertain": False},
        {"mention_id": "m3", "page_number": 2, "part_number": "QX-100", "description": "device", "quantity": None, "uncertain": False},
    ]
    fmap = {
        "descriptors": [{"descriptor_id": "d1", "mention_ids": ["m1", "m2", "m3"]}],
        "families": [{"part_family_id": "pf1", "label": "QX-100", "member_descriptor_ids": ["d1"], "origin": "python_singleton"}],
    }
    rows = mod.count_frequencies(mentions, fmap)
    assert len(rows) == 1
    assert rows[0]["repairs_containing_part"] == 2
    assert rows[0]["recorded_pieces"] == 3
    assert rows[0]["quantity_unstated_mentions"] == 1


def test_normalization_preserves_unassigned_descriptors():
    descriptors = [
        {"descriptor_id": "d1", "raw_key": "qx100", "part_numbers": ["QX100"], "descriptions": [], "example_quotes": [], "mention_ids": ["m1"]},
        {"descriptor_id": "d2", "raw_key": "qx-100", "part_numbers": ["QX-100"], "descriptions": [], "example_quotes": [], "mention_ids": ["m2"]},
        {"descriptor_id": "d3", "raw_key": "fan", "part_numbers": [], "descriptions": ["fan"], "example_quotes": [], "mention_ids": ["m3"]},
    ]
    parsed = {"clusters": [{"label": "QX100 family", "member_descriptor_ids": ["d1", "d2"]}]}
    fams = mod.validate_normalization(parsed, descriptors)
    assert len(fams) == 2
    assert any(set(f["member_descriptor_ids"]) == {"d1", "d2"} for f in fams)
    assert any(f["member_descriptor_ids"] == ["d3"] for f in fams)



def test_synthetic_pipeline_stages_without_network():
    import argparse
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        imgs = td / "imgs"
        out = td / "out"
        imgs.mkdir()
        for i in range(1, 4):
            (imgs / f"p{i}.jpg").write_bytes((f"fake-image-{i}").encode())
        args = argparse.Namespace(
            output_root=str(out), source_pdf="", source_images_root=str(imgs),
            vision_model="fake-vision", reason_model="fake-reason",
            vision_num_ctx=16384, vision_num_predict=1024, reason_num_ctx=16384,
            extract_num_predict=1024, normalize_num_predict=1024, render_dpi=180,
            duplicate_candidate_threshold=0.72, timeout=10,
            force_acquisition=False, force_dedupe=False, force_extraction=False, force_normalize=False,
            acquire_only=False
        )
        old_call = mod.call_ollama
        old_json = mod.call_json_with_retry
        old_info = mod.model_info
        try:
            def fake_info(name):
                return {"requested_model": name, "available": True, "digest": "fake-digest"}
            def fake_vision(model, prompt, *, image_path, num_ctx, num_predict, timeout):
                if image_path.name == "p1.jpg" or image_path.name == "p2.jpg":
                    return "Repair A. Replaced 2 QX-100 devices."
                return "Repair B. Replaced fan."
            def fake_json(model, prompt, *, num_ctx, num_predict, timeout, retries, cache_dir):
                if prompt.startswith(mod.EXTRACTION_PROMPT):
                    if "QX-100" in prompt:
                        return {"record_class":"repair_record","replacements":[{"raw_quote":"Replaced 2 QX-100 devices.","part_number":"QX-100","description":"power device","quantity":2,"quantity_text":"2","action":"replaced","uncertain":False}]}, []
                    return {"record_class":"repair_record","replacements":[{"raw_quote":"Replaced fan.","part_number":None,"description":"fan","quantity":1,"quantity_text":None,"action":"replaced","uncertain":False}]}, []
                if prompt.startswith(mod.NORMALIZE_PROMPT):
                    return {"clusters": []}, []
                return {"duplicate": False, "confidence":"high", "reason":"different"}, []
            mod.model_info = fake_info
            mod.call_ollama = fake_vision
            mod.call_json_with_retry = fake_json
            pages = mod.discover_image_pages(imgs)
            recs = mod.acquire_pages(args, "images", pages, None)
            dedupe = mod.adjudicate_duplicates(args, recs)
            assert dedupe["source_page_count"] == 3
            assert dedupe["unique_page_count"] == 2
            assert dedupe["duplicate_pages_excluded"] == 1
            ext = mod.extract_replacements(args, recs, dedupe)
            assert len(ext["mentions"]) == 2
            fmap = mod.normalize_part_families(args, ext["mentions"])
            freq = mod.count_frequencies(ext["mentions"], fmap)
            assert len(freq) == 2
            assert sum(x["recorded_pieces"] for x in freq) == 3
        finally:
            mod.call_ollama = old_call
            mod.call_json_with_retry = old_json
            mod.model_info = old_info


def test_no_qdrant_execution_path():
    text = SCRIPT.read_text(encoding="utf-8").lower()
    assert "qdrant" in text  # policy/status wording is allowed
    assert "6333" not in text
    assert "/collections/" not in text


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print("PASS: Nova DRL Power Supply Corpus Pilot v1.4.0 tests")


if __name__ == "__main__":
    main()
