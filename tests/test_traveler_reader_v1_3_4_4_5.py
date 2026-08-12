#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "ingest" / "nova_traveler_reader_v1_3_4_4_5.py"
spec = importlib.util.spec_from_file_location("reader", SCRIPT)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def assert_true(value, msg):
    if not value:
        raise AssertionError(msg)


def synthetic_traveler_table():
    im = Image.new("RGB", (2200, 1600), "white")
    d = ImageDraw.Draw(im)
    xs = [300, 430, 560, 1600, 1730, 1900]
    ys = [350, 520, 650, 780, 910, 1040, 1170, 1300]
    for y in ys:
        d.line((xs[0], y, xs[-1], y), fill="black", width=3)
    for x in xs:
        d.line((x, ys[0], x, ys[-1]), fill="black", width=3)
    # Row 2 meaningful description with no disposition mark.
    d.line((700, 700, 900, 680), fill="black", width=8)
    d.line((890, 680, 1150, 720), fill="black", width=8)
    # Row 3 meaningful description plus a Replaced mark.
    d.line((720, 830, 1000, 850), fill="black", width=8)
    d.line((455, 820, 520, 875), fill="black", width=9)
    d.line((520, 820, 455, 875), fill="black", width=9)
    return im, xs, ys


def test_outline_uses_printed_box_not_seed_edges():
    im, xs, ys = synthetic_traveler_table()
    # Seed deliberately clips both disposition columns and the date/right side.
    seed = [540, 390, 1740, 1210]
    out = reader.find_repairs_table_outline(im, seed)
    assert_true(out["status"] == "ok", f"outline should resolve, got {out}")
    box = out["outline_box"]
    assert_true(box[0] < seed[0] and box[2] > seed[2], "outline must recover both left and right beyond clipped seed")
    assert_true(abs(out["printed_left"] - xs[0]) <= 8, "left border should come from printed outline")
    assert_true(abs(out["printed_right"] - xs[-1]) <= 8, "right border should come from printed outline")
    assert_true(out["final_crop_uses_handwriting_extent"] is False, "handwriting must not set crop boundary")
    assert_true(out["final_crop_uses_fixed_expansion"] is False, "fixed expansion must not set final boundary")


def test_columns_and_rows_from_printed_grid():
    im, xs, ys = synthetic_traveler_table()
    seed = [540, 390, 1740, 1210]
    out = reader.find_repairs_table_outline(im, seed)
    table = im.crop(tuple(out["outline_box"]))
    cols = reader.resolve_semantic_columns(table)
    assert_true(cols["status"] == "ok", f"semantic columns should resolve: {cols}")
    grid = reader.resolve_body_rows(table)
    assert_true(grid["status"] == "ok", f"row grid should resolve: {grid}")
    assert_true(grid["physical_row_count"] == len(ys) - 2, "header interval must not become repair row")


def test_meaningful_content_does_not_require_mark():
    im, _xs, _ys = synthetic_traveler_table()
    seed = [540, 390, 1740, 1210]
    out = reader.find_repairs_table_outline(im, seed)
    table = im.crop(tuple(out["outline_box"]))
    cols = reader.resolve_semantic_columns(table)
    grid = reader.resolve_body_rows(table)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rows = reader.detect_content_rows(table, grid["body_boundaries"], cols["semantic_columns"], Path(td))
    meaningful = [r for r in rows if r["meaningful_description_content"]]
    assert_true(len(meaningful) >= 2, "synthetic meaningful rows should be retained")
    assert_true(any(not r["repaired_mark_present_provisional"] and not r["replaced_mark_present_provisional"] for r in meaningful), "unmarked meaningful content must survive")


def test_profile_policy():
    profile = json.loads((HERE.parent / "config" / "traveler_relevance_profile_v1_3_4_4_5.json").read_text())
    keys = set(profile["knowledge_sections"].keys())
    assert_true(keys == {"identity_header", "special_notes", "repairs_replacements"}, "only highlighted Traveler sections are knowledge/review")
    rr = profile["knowledge_sections"]["repairs_replacements"]
    assert_true("printed outer table outline" in rr["boundary_policy"], "printed box must define repair evidence region")
    assert_true("not admission gates" in rr["content_policy"], "marks must remain attributes, not gates")
    assert_true(profile["qdrant_write_enabled"] is False, "Qdrant must remain disabled")
    assert_true(profile["automatic_fact_acceptance"] is False, "automatic fact acceptance must remain disabled")


def main():
    test_outline_uses_printed_box_not_seed_edges()
    test_columns_and_rows_from_printed_grid()
    test_meaningful_content_does_not_require_mark()
    test_profile_policy()
    print("PASS: Nova Traveler Reader v1.3.4.4.5 outline-first relevance hardening tests")


if __name__ == "__main__":
    main()
