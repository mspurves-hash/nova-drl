#!/usr/bin/env python3
from pathlib import Path
import importlib.util
import json
import sqlite3
import tempfile
import argparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analysis" / "nova_drl_10pct_tracking_enrichment_v1_4_7.py"
spec = importlib.util.spec_from_file_location("v147", SCRIPT)
m = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(m)


def test_order_ref_rules():
    assert m.is_known_order_ref("DGK52102")
    assert m.is_known_order_ref("MSR-123456")
    assert m.is_known_order_ref("NWK56548")
    assert m.is_known_order_ref("DSK520117")
    assert not m.is_known_order_ref("LM5110")
    assert not m.is_known_order_ref("IXFX24N100Q3")
    assert m.infer_supplier_from_order_ref("DGK52102") == "Digi-Key"
    assert m.infer_supplier_from_order_ref("MSR12345") == "Mouser"
    assert m.infer_supplier_from_order_ref("NWK56548") is None


def test_metadata_validation():
    parsed = {
        "rma_numbers": [
            {"value": "RMA 53434", "evidence_quote": "RMA 53434"},
            {"value": "53434", "evidence_quote": "RMA# 53434"},
        ],
        "procurement_refs": [
            {"order_ref": "DGK52102", "supplier": None, "description": "15A fuse", "manufacturer_pn": "KLK-15", "quantity": 2, "evidence_quote": "DGK52102 KLK-15 Qty 2"},
            {"order_ref": "NWK56548", "supplier": None, "description": None, "manufacturer_pn": None, "quantity": None, "evidence_quote": "NWK56548"},
        ],
    }
    out = m.validate_metadata_json(parsed)
    assert len(out["rma_numbers"]) == 1
    assert out["rma_numbers"][0]["normalized"] == "53434"
    assert out["procurement_refs"][0]["supplier"] == "Digi-Key"
    assert out["procurement_refs"][0]["manufacturer_pn"] == "KLK-15"
    assert out["procurement_refs"][1]["supplier"] is None


def test_event_merge_and_reclassify():
    plan = [{"repair_event_id": "log_230101001"}]
    tracking = [{
        "repair_event_id": "log_230101001",
        "source_path": "/tmp/card.jpg",
        "metadata": {
            "rma_numbers": [{"value": "53434", "normalized": "53434", "evidence_quote": "RMA 53434"}],
            "procurement_refs": [
                {"order_ref": "DGK52102", "normalized": "DGK52102", "supplier": "Digi-Key", "description": "Fuse", "manufacturer_pn": "KLK-15", "quantity": 2, "evidence_quote": "DGK52102 KLK-15"}
            ],
        },
    }]
    merged = m.merge_event_tracking(plan, tracking)
    parts = [{"repair_event_id": "log_230101001", "part_number": "DGK52102", "quantity": 2, "text": "Replaced fuse", "evidence_quote": "DGK52102"}]
    enriched, count = m.enrich_replacement_mentions(parts, merged)
    assert count == 1
    assert enriched[0]["manufacturer_part_number"] == "KLK-15"
    assert enriched[0]["part_number"] == "KLK-15"
    assert enriched[0]["distributor_order_ref"] == "DGK52102"
    assert enriched[0]["pn_classification"] == "procurement_reference_reclassified"



def test_tracking_acquisition_cache():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        img = td / "card.jpg"
        # Minimal valid JPEG via Pillow when available; cache logic only needs readable bytes.
        try:
            from PIL import Image
            Image.new("RGB", (600, 800), "white").save(img, "JPEG")
        except Exception:
            img.write_bytes(b"fake-jpeg-bytes")
        rec = {
            "record_status": "ready",
            "source_record_id": "meta_test0001",
            "repair_event_id": "log_230101001",
            "log_number": "230101001",
            "equipment_family": "TEST",
            "top_folders": ["TEST SN 1"],
            "source_path": str(img),
            "source_relative_path": "TEST/230101001 Line Card.jpg",
            "source_image": str(img),
            "source_image_sha256": m.sha256_file(img),
            "source_pdf_page": None,
        }
        args = argparse.Namespace(
            output_root=str(td / "out"), vision_model="fake-model", force_vision=False,
            no_upper_crop=True, vision_num_ctx=1024, vision_num_predict=256, timeout=5
        )
        old_model_info = m.model_info
        old_call = m.call_vision_json
        try:
            m.model_info = lambda model: {"available": True, "digest": "fake-digest"}
            m.call_vision_json = lambda *a, **k: ({
                "rma_numbers": [{"value": "53434", "evidence_quote": "RMA 53434"}],
                "procurement_refs": [{"order_ref": "DGK52102", "supplier": None, "description": "Fuse", "manufacturer_pn": "KLK-15", "quantity": 2, "evidence_quote": "DGK52102 KLK-15"}]
            }, [{"attempt": 1, "ok": True}], "{}")
            rows1 = m.acquire_tracking_metadata(args, [rec])
            assert rows1[0]["metadata"]["rma_numbers"][0]["normalized"] == "53434"
            assert rows1[0]["metadata"]["procurement_refs"][0]["supplier"] == "Digi-Key"
            # Second call should reuse cache and therefore still work if model call is disabled.
            m.call_vision_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("should not run"))
            rows2 = m.acquire_tracking_metadata(args, [rec])
            assert rows2[0]["metadata"]["procurement_refs"][0]["order_ref"] == "DGK52102"
        finally:
            m.model_info = old_model_info
            m.call_vision_json = old_call


def test_lookup_db():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "lookup.sqlite"
        events = [{
            "repair_event_id": "log_230101001", "log_number": "230101001", "equipment_family": "TEST",
            "top_folders": ["TEST SN 1"], "primary_source_paths": ["/tmp/card.jpg"], "supporting_source_paths": [],
            "tracking_metadata_v1_4_7": {
                "rma_numbers": [{"value": "53434", "evidence_quote": "RMA 53434", "source_path": "/tmp/card.jpg"}],
                "procurement_refs": [{"order_ref": "DGK52102", "supplier": "Digi-Key", "description": "Fuse", "manufacturer_pn": "KLK-15", "quantity": 2, "evidence_quote": "DGK52102", "source_path": "/tmp/card.jpg"}],
            },
        }]
        parts = [{"repair_event_id": "log_230101001", "manufacturer_part_number": "KLK-15", "distributor_order_ref": "DGK52102", "supplier": "Digi-Key", "quantity": 2, "text": "Fuse", "evidence_quote": "DGK52102"}]
        m.build_lookup_db(db, events, parts)
        conn = sqlite3.connect(str(db))
        assert conn.execute("select count(*) from rma_refs where rma_normalized='53434'").fetchone()[0] == 1
        assert conn.execute("select count(*) from procurement_refs where order_ref_normalized='DGK52102'").fetchone()[0] == 1
        conn.close()


def main():
    test_order_ref_rules()
    test_metadata_validation()
    test_event_merge_and_reclassify()
    test_tracking_acquisition_cache()
    test_lookup_db()
    print("PASS: Nova DRL 10% Tracking + Procurement Enrichment v1.4.7 tests")


if __name__ == "__main__":
    main()
