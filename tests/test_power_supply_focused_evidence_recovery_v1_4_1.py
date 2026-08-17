#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analysis" / "nova_power_supply_focused_evidence_recovery_v1_4_1.py"
spec = importlib.util.spec_from_file_location("psfocus", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def test_runtime_is_blind_to_prior_benchmark_answers():
    text = SCRIPT.read_text(encoding="utf-8")
    # Deliberately checked in test only. Runtime must not embed known benchmark anchors.
    assert "IXFX24N100Q3" not in text
    assert "FDH038AN08A1" not in text
    assert "156 unique" not in text.lower()


def test_recursive_line_card_discovery_and_log_identity():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "unitA" / "photos").mkdir(parents=True)
        (td / "unitB").mkdir()
        a = td / "unitA" / "230101001 Line Card Original.jpg"
        b = td / "unitA" / "photos" / "230101001 Line Card Warranty.JPG"
        c = td / "unitB" / "not_this_photo.jpg"
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        c.write_bytes(b"c")
        rows = mod.discover_line_card_images(td, r"line\s*card")
        assert len(rows) == 2
        assert mod.extract_log_number(a.name) == "230101001"


def test_quote_binding_across_focused_and_aux_evidence():
    parsed = {"record_class": "repair_record", "replacements": [
        {"raw_quote": "Replaced 3 QX-100 devices", "part_number": "QX-100", "description": "power device", "quantity": 3, "quantity_text": "3", "action": "replaced", "uncertain": False},
        {"raw_quote": "Changed fan", "part_number": None, "description": "fan", "quantity": 1, "quantity_text": None, "action": "replaced", "uncertain": False},
        {"raw_quote": "invented thing", "part_number": "BAD", "description": "bad", "quantity": 1, "action": "replaced", "uncertain": False},
    ]}
    rc, rows, rejected = mod.validate_extraction(parsed, "log_230101001", ["s1"], ["Replaced 3 QX-100 devices", "Changed fan"])
    assert rc == "repair_record"
    assert len(rows) == 2
    assert rows[0]["quantity"] == 3
    assert any(r["reason"] == "raw_quote_not_bound" for r in rejected)


def test_vague_quantity_remains_unstated():
    parsed = {"record_class": "repair_record", "replacements": [{
        "raw_quote": "Replaced several surface mount parts", "part_number": None,
        "description": "surface mount parts", "quantity": None, "quantity_text": "several",
        "action": "replaced", "uncertain": False,
    }]}
    _, rows, _ = mod.validate_extraction(parsed, "record_x", ["s1"], ["Replaced several surface mount parts"])
    assert rows[0]["quantity"] is None


def test_canonical_descriptor_consolidates_punctuation_case_only():
    mentions = [
        {"mention_id":"m1","repair_event_id":"e1","part_number":"QX-100","description":"device","raw_quote":"QX-100"},
        {"mention_id":"m2","repair_event_id":"e2","part_number":"qx 100","description":"device","raw_quote":"qx 100"},
    ]
    ds = mod.build_descriptors(mentions)
    assert len(ds) == 1
    assert set(ds[0]["mention_ids"]) == {"m1","m2"}


def test_fuzzy_candidate_component_links_likely_ocr_variant():
    ds = [
        {"descriptor_id":"d1","canonical_key":"PN:QX100A","part_numbers":["QX100A"],"descriptions":["power transistor"],"example_quotes":[],"mention_ids":[],"event_ids":[]},
        {"descriptor_id":"d2","canonical_key":"PN:QX10OA","part_numbers":["QX10OA"],"descriptions":["power transistor"],"example_quotes":[],"mention_ids":[],"event_ids":[]},
        {"descriptor_id":"d3","canonical_key":"DESC:FAN","part_numbers":[],"descriptions":["fan"],"example_quotes":[],"mention_ids":[],"event_ids":[]},
    ]
    comps = mod.candidate_descriptor_components(ds, 0.72)
    assert any(set(x) == {"d1","d2"} for x in comps)
    assert any(x == ["d3"] for x in comps)


def test_frequency_counts_repair_events_not_images():
    mentions = [
        {"mention_id":"m1","repair_event_id":"log_1","part_number":"QX100","description":"device","quantity":2,"uncertain":False},
        {"mention_id":"m2","repair_event_id":"log_1","part_number":"QX100","description":"device","quantity":1,"uncertain":False},
        {"mention_id":"m3","repair_event_id":"log_2","part_number":"QX100","description":"device","quantity":None,"uncertain":False},
    ]
    fmap = {"descriptors":[{"descriptor_id":"d1","mention_ids":["m1","m2","m3"]}],"families":[{"part_family_id":"pf1","label":"QX100","member_descriptor_ids":["d1"],"origin":"test"}]}
    rows = mod.count_frequencies(mentions, fmap)
    assert rows[0]["repairs_containing_part"] == 2
    assert rows[0]["recorded_pieces"] == 3
    assert rows[0]["quantity_unstated_mentions"] == 1


def test_same_log_multiple_images_becomes_one_repair_event():
    records = [
        {"source_record_id":"a","source_index":1,"source_path":"/x/230101001 Line Card Original.jpg","log_number":"230101001"},
        {"source_record_id":"b","source_index":2,"source_path":"/x/230101001 Line Card Warranty.jpg","log_number":"230101001"},
        {"source_record_id":"c","source_index":3,"source_path":"/x/230101002 Line Card Original.jpg","log_number":"230101002"},
    ]
    dedupe = {"representative_index_by_record_id":{"a":"a","b":"b","c":"c"}}
    events = mod.build_repair_events(records, dedupe)
    assert len(events) == 2
    e = [x for x in events if x["repair_event_id"] == "log_230101001"][0]
    assert set(e["source_record_ids"]) == {"a","b"}



def test_duplicate_scan_member_is_not_reextracted():
    records = [
        {"source_record_id":"a","source_index":1,"source_path":"/x/230101001 Line Card Original.jpg","log_number":"230101001"},
        {"source_record_id":"b","source_index":2,"source_path":"/x/230101001 duplicate.jpg","log_number":"230101001"},
        {"source_record_id":"c","source_index":3,"source_path":"/x/230101001 Line Card Warranty.jpg","log_number":"230101001"},
    ]
    # b is a duplicate scan of a; c is a legitimate second image of same repair event.
    dedupe = {"representative_index_by_record_id":{"a":"a","b":"a","c":"c"}}
    events = mod.build_repair_events(records, dedupe)
    assert len(events) == 1
    assert set(events[0]["source_record_ids"]) == {"a","c"}
    assert "b" not in events[0]["source_record_ids"]

def test_exact_image_duplicates_are_candidate_without_pillow():
    records = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for i, content in enumerate((b"same", b"same", b"different"), 1):
            focus = td / f"f{i}.txt"; focus.write_text("repair text" if i < 3 else "other", encoding="utf-8")
            records.append({
                "source_image_sha256": __import__('hashlib').sha256(content).hexdigest(),
                "image_dhash": None,
                "focused_transcription_path": str(focus),
                "aux_v140_transcription": None,
                "source_path": f"p{i}",
            })
        pairs = mod.propose_duplicate_pairs(records, text_threshold=0.99, dhash_threshold=22)
        assert any(p["record_a_index"] == 0 and p["record_b_index"] == 1 and p["exact_image_sha256"] for p in pairs)


def test_synthetic_image_first_pipeline_without_network():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        root = td / "images"; out = td / "out"; root.mkdir()
        # Two exact duplicate scans, plus one separate repair.
        (root / "230101001 Line Card Original.jpg").write_bytes(b"same-image")
        (root / "230101001 Line Card Warranty.jpg").write_bytes(b"same-image")
        (root / "230101002 Line Card Original.jpg").write_bytes(b"other-image")
        args = argparse.Namespace(
            source_images_root=str(root), source_pdf=None, image_name_regex=r"line\s*card", output_root=str(out),
            reuse_v140_root=str(td / "none"), vision_model="fake-vision", reason_model="fake-reason",
            vision_num_ctx=16384, vision_num_predict=1024, reason_num_ctx=16384, extract_num_predict=1024,
            normalize_num_predict=1024, render_dpi=300, use_focus_crop=False, duplicate_text_threshold=0.99,
            duplicate_dhash_threshold=22, normalize_candidate_threshold=0.72, normalize_batch_descriptors=60,
            timeout=10, force_focused_acquisition=False, force_dedupe=False, force_extraction=False,
            force_normalize=False, focused_acquire_only=False,
        )
        old_call, old_json, old_info = mod.call_ollama, mod.call_json_with_retry, mod.model_info
        try:
            mod.model_info = lambda name: {"requested_model":name,"available":True,"digest":"fake-digest"}
            def fake_vision(model, prompt, *, image_paths, num_ctx, num_predict, timeout):
                name = image_paths[0].name
                if "230101001" in name:
                    return "Replaced 2 QX-100 devices"
                return "Changed fan"
            def fake_json(model, prompt, *, num_ctx, num_predict, timeout, retries, cache_dir):
                if prompt.startswith(mod.EXTRACTION_PROMPT):
                    if "QX-100" in prompt:
                        return {"record_class":"repair_record","replacements":[{"raw_quote":"Replaced 2 QX-100 devices","part_number":"QX-100","description":"power device","quantity":2,"quantity_text":"2","action":"replaced","uncertain":False}]}, []
                    return {"record_class":"repair_record","replacements":[{"raw_quote":"Changed fan","part_number":None,"description":"fan","quantity":1,"quantity_text":None,"action":"replaced","uncertain":False}]}, []
                if prompt.startswith(mod.NORMALIZE_BLOCK_PROMPT):
                    return {"clusters":[]}, []
                return {"duplicate":False,"confidence":"high","reason":"different"}, []
            mod.call_ollama = fake_vision
            mod.call_json_with_retry = fake_json
            kind, srcs, pdfsha = mod.make_source_records(args)
            assert kind == "images" and pdfsha is None and len(srcs) == 3
            recs = mod.acquire_focused_evidence(args, srcs, {})
            dedupe = mod.adjudicate_duplicates(args, recs)
            assert dedupe["duplicate_records_excluded"] == 1
            ext = mod.extract_replacements(args, recs, dedupe)
            assert len(ext["repair_events"]) == 2
            assert len(ext["mentions"]) == 2
            fmap = mod.normalize_part_families(args, ext["mentions"])
            freq = mod.count_frequencies(ext["mentions"], fmap)
            assert len(freq) == 2
            assert sum(x["recorded_pieces"] for x in freq) == 3
        finally:
            mod.call_ollama, mod.call_json_with_retry, mod.model_info = old_call, old_json, old_info


def test_no_qdrant_execution_path():
    text = SCRIPT.read_text(encoding="utf-8").lower()
    assert "qdrant" in text
    assert "6333" not in text
    assert "/collections/" not in text


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print("PASS: Nova DRL Power Supply Focused Evidence Recovery v1.4.1 tests")


if __name__ == "__main__":
    main()
