#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import tempfile
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "ingest" / "nova_traveler_reader_v1_3_4_4_4.py"
spec = importlib.util.spec_from_file_location("reader", SCRIPT)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def assert_true(value, msg):
    if not value:
        raise AssertionError(msg)


def test_legacy_layout_detected():
    layout = {
        "table_left": 18,
        "repaired_replaced_divider": 218,
        "description_left": 2215,
        "table_right": 2465,
    }
    assert_true(reader.legacy_clip_suspected(layout), "150622005 legacy role-shift should be detected")
    assert_true(reader.robust_expand_pixels(layout) > 200, "expansion should be derived from narrow-column width")


def test_good_layout_not_recovered():
    # Known frozen 130813004 geometry from validation transcript.
    layout = {
        "table_left": 0,
        "repaired_replaced_divider": 250,
        "description_left": 354,
        "table_right": 2603,
    }
    assert_true(not reader.legacy_clip_suspected(layout), "known-good frozen layout must not trigger legacy recovery")


def test_content_does_not_require_mark():
    # Synthetic blank description cell vs handwriting-like strokes.
    blank = Image.new("L", (700, 120), 255)
    written = blank.copy()
    d = ImageDraw.Draw(written)
    d.line((80, 60, 250, 35), fill=20, width=7)
    d.line((240, 35, 430, 70), fill=20, width=7)
    d.line((430, 70, 590, 45), fill=20, width=7)
    d.ellipse((310, 35, 345, 75), outline=20, width=5)
    b = reader.meaningful_ink_features(blank)
    w = reader.meaningful_ink_features(written)
    assert_true(not b["meaningful"], "blank row must remain blank")
    assert_true(w["meaningful"], "meaningful description content must be retained without any disposition mark")


def test_profile_policy():
    import json
    profile = json.loads((HERE.parent / "config" / "traveler_relevance_profile_v1_3_4_4_4.json").read_text())
    keys = set(profile["knowledge_sections"].keys())
    assert_true(keys == {"identity_header", "special_notes", "repairs_replacements"}, "only human-highlighted Traveler sections should be knowledge/review sections")
    policy = profile["knowledge_sections"]["repairs_replacements"]["policy"]
    assert_true("not admission gates" in policy, "marks must be attributes, not gates")
    assert_true(profile["qdrant_write_enabled"] is False, "Qdrant must remain disabled")
    assert_true(profile["automatic_fact_acceptance"] is False, "automatic fact acceptance must remain disabled")


def main():
    test_legacy_layout_detected()
    test_good_layout_not_recovered()
    test_content_does_not_require_mark()
    test_profile_policy()
    print("PASS: Nova Traveler Reader v1.3.4.4.4 relevance hardening tests")


if __name__ == "__main__":
    main()
