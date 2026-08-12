#!/usr/bin/env python3
import importlib.util
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ingest" / "nova_traveler_reader_v1_3_5_1.py"
spec = importlib.util.spec_from_file_location("collector", SCRIPT)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

assert c.VERSION == "1.3.5.1"
assert c.DEFAULT_MODEL == "qwen3-vl-drl:8b-q8-16k"
assert "Return transcription only" in c.TRANSCRIPTION_PROMPT
assert "Do not summarize, interpret, classify" in c.TRANSCRIPTION_PROMPT
assert "Do not silently replace unusual wording" in c.TRANSCRIPTION_PROMPT
assert "Do not infer missing words or unstated quantities" in c.TRANSCRIPTION_PROMPT

# Audit signal must never alter raw text.
raw = "Turkey fat is cause\nSugar Cube test\nBlue Schmoo's\n"
audit = c.transcription_audit(raw)
assert audit["possible_runaway_repetition"] is False
assert audit["audit_only"] is True

looped = "\n".join(["same header"] * 8)
audit2 = c.transcription_audit(looped)
assert audit2["possible_runaway_repetition"] is True

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    source = td / "source"
    unit = source / "RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH"
    unit.mkdir(parents=True)

    a = unit / "130130006 Line Card Original.jpg"
    b = unit / "130813004 Line Card Warranty.JPG"
    cimg = unit / "150622005 Line Card Original.JPG"
    ignored = unit / "150622005 Receiving Pic.JPG"
    Image.new("RGB", (1200, 900), "white").save(a)
    Image.new("RGB", (1200, 900), "white").save(b)
    # Exact duplicate image bytes for duplicate-hash manifest test.
    cimg.write_bytes(a.read_bytes())
    Image.new("RGB", (800, 600), "white").save(ignored)

    items = c.discover_travelers(source)
    assert [x["log_number"] for x in items] == ["130130006", "130813004", "150622005"]
    assert [x["variant"] for x in items] == ["original", "warranty", "original"]
    assert all("Receiving Pic" not in x["filename"] for x in items)

    out = td / "output"
    args = SimpleNamespace(
        inventory_only=False,
        model="qwen3-vl-drl:8b-q8-16k",
        num_ctx=16384,
        num_predict=8192,
        timeout=60,
        force=False,
    )
    model_info = {"requested_model": args.model, "digest": "testdigest", "available": True}

    def fake_vision(path, model, num_ctx, num_predict, timeout):
        return f"RAW TRANSCRIPTION FOR {path.name}\nTurkey fat is cause\n"

    records = []
    with patch.object(c, "call_ollama_vision", side_effect=fake_vision):
        for item in items:
            rec = c.collect_one(item, source, out, model_info, args)
            records.append(rec)
            assert rec["vision_status"] == "ok"
            assert rec["classification_performed"] is False
            assert rec["accepted_fact_count"] == 0
            assert rec["qdrant_entries_created"] == 0
            raw_path = Path(rec["raw_transcription_path"])
            assert raw_path.exists()
            assert "Turkey fat is cause" in raw_path.read_text(encoding="utf-8")

    manifest = c.write_manifest(out, source, records, model_info, args)
    assert manifest["traveler_count"] == 3
    assert manifest["classification_performed"] is False
    assert manifest["accepted_fact_count"] == 0
    assert manifest["qdrant_entries_created"] == 0
    assert manifest["exact_duplicate_hash_group_count"] == 1

    # Resume: same evidence/model/prompt/settings must reuse without a vision call.
    with patch.object(c, "call_ollama_vision", side_effect=AssertionError("vision should not run")):
        reused = c.collect_one(items[0], source, out, model_info, args)
        assert reused.get("collection_action") == "reused_existing"

    # Inventory-only must make no model call and preserve zero-fact policy.
    args.inventory_only = True
    with patch.object(c, "call_ollama_vision", side_effect=AssertionError("vision should not run")):
        inv = c.collect_one(items[0], source, td / "inventory", model_info, args)
        assert inv["vision_status"] == "not_run_inventory_only"
        assert inv["classification_performed"] is False
        assert inv["accepted_fact_count"] == 0

    assert c.source_is_under_output(source, source / "bad-output") is True
    assert c.source_is_under_output(source, td / "outside-output") is False

print("PASS: Nova Whole Traveler Corpus Collector v1.3.5.1 tests")
