#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "ingest" / "nova_traveler_reader_v1_3_4_4_6.py"
spec = importlib.util.spec_from_file_location("reader", SCRIPT)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def assert_true(value, msg):
    if not value:
        raise AssertionError(msg)


def synthetic_segmented_traveler_table():
    """Printed table whose horizontal rules are segmented by vertical grid lines."""
    im = Image.new("RGB", (2300, 1700), "white")
    d = ImageDraw.Draw(im)
    xs = [260, 395, 530, 1660, 1800, 1980]
    ys = [330, 500, 640, 780, 920, 1060, 1200, 1340, 1480]

    # Full semantic vertical rules.
    for x in xs:
        d.line((x, ys[0], x, ys[-1]), fill="black", width=4)

    # Draw each horizontal as separate per-column segments with tiny endpoint
    # variation. v1.3.4.4.5's identical-endpoint family is intentionally not a
    # requirement; projection coverage should recover these as one row rule.
    for j, y in enumerate(ys):
        for i in range(len(xs)-1):
            left = xs[i] + (2 if (i+j) % 2 else 0)
            right = xs[i+1] - (3 if (i+j) % 3 == 0 else 1)
            d.line((left, y, right, y), fill="black", width=4)

    # Unrelated horizontal lines in nearby form sections.
    d.line((100, 180, 2100, 180), fill="black", width=3)
    d.line((80, 1600, 2200, 1600), fill="black", width=3)

    # Meaningful row 2 description WITHOUT a disposition mark.
    d.line((700, 705, 920, 682), fill="black", width=9)
    d.line((910, 682, 1190, 720), fill="black", width=9)

    # Meaningful row 3 plus Replaced X.
    d.line((720, 835, 1010, 855), fill="black", width=9)
    d.line((425, 815, 500, 875), fill="black", width=10)
    d.line((500, 815, 425, 875), fill="black", width=10)

    # Meaningful row 4 plus Repaired X.
    d.line((735, 970, 1110, 990), fill="black", width=9)
    d.line((290, 955, 365, 1015), fill="black", width=10)
    d.line((365, 955, 290, 1015), fill="black", width=10)
    return im, xs, ys


def test_outline_recovers_grid_network_not_matching_endpoints():
    im, xs, ys = synthetic_segmented_traveler_table()
    # Deliberately clips the repaired column on the left and date/right border.
    seed = [500, 430, 1840, 1320]
    out = reader.find_repairs_table_outline(im, seed)
    assert_true(out["status"] == "ok", f"grid-network outline should resolve, got {out}")
    assert_true(abs(out["printed_left"] - xs[0]) <= 10, f"left border mismatch: {out}")
    assert_true(abs(out["printed_right"] - xs[-1]) <= 10, f"right border mismatch: {out}")
    assert_true(abs(out["printed_top"] - ys[0]) <= 10, f"top border mismatch: {out}")
    assert_true(abs(out["printed_bottom"] - ys[-1]) <= 10, f"bottom border mismatch: {out}")
    assert_true(out["final_crop_uses_handwriting_extent"] is False, "handwriting must not define boundary")
    assert_true(out["final_crop_uses_fixed_expansion"] is False, "fixed expansion must not define boundary")
    assert_true("projection" in out["boundary_basis"], "boundary should use horizontal projection coverage")


def test_semantic_columns_and_rows_from_recovered_box():
    im, xs, ys = synthetic_segmented_traveler_table()
    seed = [500, 430, 1840, 1320]
    out = reader.find_repairs_table_outline(im, seed)
    table = im.crop(tuple(out["outline_box"]))
    cols = reader.resolve_semantic_columns(table)
    assert_true(cols["status"] == "ok", f"semantic columns should resolve: {cols}")
    grid = reader.resolve_body_rows(table)
    assert_true(grid["status"] == "ok", f"row grid should resolve: {grid}")
    assert_true(grid["physical_row_count"] == len(ys)-2, f"expected {len(ys)-2} body rows, got {grid}")


def test_meaningful_content_survives_without_mark():
    im, _xs, _ys = synthetic_segmented_traveler_table()
    seed = [500, 430, 1840, 1320]
    out = reader.find_repairs_table_outline(im, seed)
    table = im.crop(tuple(out["outline_box"]))
    cols = reader.resolve_semantic_columns(table)
    grid = reader.resolve_body_rows(table)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        rows = reader.detect_content_rows(table, grid["body_boundaries"], cols["semantic_columns"], Path(td))
    meaningful = [r for r in rows if r["meaningful_description_content"]]
    assert_true(len(meaningful) >= 3, f"meaningful rows should be retained: {meaningful}")
    assert_true(any(not r["repaired_mark_present_provisional"] and not r["replaced_mark_present_provisional"] for r in meaningful), "unmarked meaningful content must survive")
    assert_true(any(r["repaired_mark_present_provisional"] for r in meaningful), "repaired mark should be captured as attribute")
    assert_true(any(r["replaced_mark_present_provisional"] for r in meaningful), "replaced mark should be captured as attribute")


def test_profile_policy():
    profile = json.loads((HERE.parent / "config" / "traveler_relevance_profile_v1_3_4_4_6.json").read_text())
    assert_true(profile["profile_version"] == "1.3.4.4.6", "profile version mismatch")
    keys = set(profile["knowledge_sections"].keys())
    assert_true(keys == {"identity_header", "special_notes", "repairs_replacements"}, "only highlighted Traveler sections are knowledge/review")
    rr = profile["knowledge_sections"]["repairs_replacements"]
    assert_true("grid network" in rr["boundary_policy"], "printed grid network must define repair evidence region")
    assert_true("not admission gates" in rr["content_policy"], "marks must remain attributes, not gates")
    assert_true(profile["qdrant_write_enabled"] is False, "Qdrant must remain disabled")
    assert_true(profile["automatic_fact_acceptance"] is False, "automatic fact acceptance must remain disabled")


def main():
    test_outline_recovers_grid_network_not_matching_endpoints()
    test_semantic_columns_and_rows_from_recovered_box()
    test_meaningful_content_survives_without_mark()
    test_profile_policy()
    print("PASS: Nova Traveler Reader v1.3.4.4.6 printed-grid-network relevance hardening tests")


if __name__ == "__main__":
    main()
