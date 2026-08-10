#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'reader',
    str(ROOT/'ingest'/'nova_traveler_reader_v1_3_4_2.py'),
)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)

log = reader.decode_log_number('230809002')
assert log['valid'] is True
assert log['log_date'] == '2023-08-09'
assert log['daily_sequence'] == '002'
assert reader.parse_date_field('9/25/23', '230809002')['status'] == 'plausible'
assert reader.parse_date_field('9/25/76', '230809002')['status'] == 'implausible'
assert reader.validate_initials('EF')['status'] == 'valid'

parsed = reader.parse_row_response("Machined Comm's | EF | 9/25/23")
assert parsed['format_compliant'] is True
assert parsed['description'] == "Machined Comm's"

with tempfile.TemporaryDirectory() as tmp:
    glossary_path = Path(tmp)/'glossary.json'
    glossary_path.write_text(json.dumps({
        'entries': [{
            'aliases': ["Comm's"],
            'canonical_term': 'commutators',
            'meaning': 'Motor commutators',
            'category': 'repair_action_term',
            'context': ['motor repair'],
            'user_confirmed': True,
        }]
    }))
    glossary = reader.load_glossary(glossary_path)
    matches = reader.glossary_matches("Machined Comm's", glossary)
    assert matches[0]['canonical_term'] == 'commutators'


def make_table(include_fourth_description=True, touch_date_edge=False):
    image = Image.new('L', (1000, 760), 255)
    draw = ImageDraw.Draw(image)

    # Header line and candidate entry boundaries. Entry 3 crosses y=480 and
    # entry 4 crosses y=650, so v1.3.4.2 must advance to 550 and 710.
    for y in (100, 230, 350, 480, 550, 650, 710):
        draw.line((0, y, 999, y), fill=80, width=2)

    # Description left, description/initials, and initials/date rules.
    for x in (100, 790, 895):
        draw.line((x, 0, x, 759), fill=80, width=2)

    anchors = (180, 300, 430, 600)
    for center in anchors:
        draw.rectangle((820, center-8, 845, center+8), fill=20)
        draw.rectangle((920, center-7, 960, center+7), fill=20)

    # Text-like handwriting strokes. Each row stays below the global
    # horizontal-line threshold, unlike an unrealistic solid full-width bar.
    def scribble(y1, y2):
        draw.rectangle((140, y1, 240, y2), fill=20)
        draw.rectangle((300, y1+2, 400, y2), fill=20)
        draw.rectangle((460, y1+1, 560, y2-1), fill=20)

    # The first two entries end before their first post-anchor form line.
    # Entries three and four continue below the first post-anchor form line.
    scribble(125, 155)
    scribble(185, 215)
    scribble(245, 285)
    scribble(300, 330)
    scribble(365, 405)
    scribble(465, 515)
    if include_fourth_description:
        scribble(570, 610)
        scribble(660, 680)

    if touch_date_edge:
        draw.rectangle((985, 580, 999, 620), fill=20)

    return image, anchors


image, expected_anchor_centers = make_table()
geometry = reader.detect_table_geometry(image)
assert 95 <= geometry['table_body_top'] <= 110, geometry
assert abs(geometry['description_left'] - 100) <= 3, geometry
assert abs(geometry['description_right'] - 790) <= 3, geometry
assert abs(geometry['initials_right'] - 895) <= 3, geometry
assert geometry['date_right'] == image.width

detection = reader.detect_repair_anchors(
    image,
    expected_entries=4,
    geometry=geometry,
)
centers = [item['y'] for item in detection['anchors']]
assert len(centers) == 4, centers
assert all(
    abs(actual - expected) < 20
    for actual, expected in zip(centers, expected_anchor_centers)
), centers

band_result = reader.build_entry_bands(
    centers,
    image.height,
    geometry=geometry,
    image=image,
    return_diagnostics=True,
)
assert len(band_result['bands']) == 4
assert band_result['raw_boundaries'][0] <= 105
assert abs(band_result['raw_boundaries'][1] - 230) <= 4
assert abs(band_result['raw_boundaries'][2] - 350) <= 4
assert abs(band_result['raw_boundaries'][3] - 550) <= 4
assert abs(band_result['raw_boundaries'][4] - 710) <= 4
assert band_result['boundary_checks'][2]['skipped_unsafe_grid_lines'] == [480]
assert band_result['boundary_checks'][3]['skipped_unsafe_grid_lines'] == [650]

coverage = reader.evaluate_row_coverage(band_result, centers, 4)
assert coverage['coverage_ok'] is True, coverage
assert coverage['entries_with_description_ink'] == 4
assert coverage['boundary_crossing_runs'] == []
assert coverage['unsafe_boundaries'] == []
assert coverage['description_coverage_ratio'] >= 0.98

assignments = coverage['description_assignments']
assert assignments[0]['description_ink_start'] <= 130
assert assignments[0]['description_ink_end'] >= 210
assert assignments[2]['description_ink_end'] >= 510
assert assignments[3]['description_ink_end'] >= 675

edge = reader.analyze_date_edge(image, geometry, band_result['bands'])
assert edge['date_edge_clipped_or_touching'] is False, edge

edge_image, edge_anchors = make_table(touch_date_edge=True)
edge_geometry = reader.detect_table_geometry(edge_image)
edge_detection = reader.detect_repair_anchors(
    edge_image, expected_entries=4, geometry=edge_geometry
)
edge_centers = [item['y'] for item in edge_detection['anchors']]
edge_bands = reader.build_entry_bands(
    edge_centers, edge_image.height, geometry=edge_geometry,
    image=edge_image
)
edge_result = reader.analyze_date_edge(edge_image, edge_geometry, edge_bands)
assert edge_result['date_edge_clipped_or_touching'] is True
assert 4 in edge_result['touching_entries']

# Full date crop and row crop must extend to the complete parent-image edge.
with tempfile.TemporaryDirectory() as tmp:
    crops = reader.create_entry_crops(
        image,
        band_result['bands'],
        Path(tmp),
        geometry=geometry,
    )
    assert crops[0]['pixel_boxes']['date'][2] == image.width
    assert crops[0]['pixel_boxes']['full_row'][2] == image.width

# Rebuild through 100% of original width, not the v1.3.1 96% boundary.
with tempfile.TemporaryDirectory() as tmp:
    original = Image.new('RGB', (1200, 1600), 'white')
    source_path = Path(tmp)/'traveler.jpg'
    original.save(source_path)
    output_dir = Path(tmp)/'out'
    output_dir.mkdir()
    expanded, info = reader.load_parent_repairs_image(
        {'source_path': str(source_path)},
        {
            'fractional_box': [0.53, 0.18, 0.96, 0.61],
            'crop_path': '/nonexistent/old_crop.png',
        },
        output_dir,
    )
    assert info['source'] == 'reconstructed_from_original_traveler'
    assert info['expanded_fractional_box'][2] == 1.0
    assert expanded.width >= int((1.0 - 0.53) * 1200 * 2) - 2

# Expected anchors can match while row coverage still fails. Vision must stop.
with tempfile.TemporaryDirectory() as tmp:
    log_dir = Path(tmp)/'230809002'
    log_dir.mkdir()
    incomplete, _ = make_table(include_fourth_description=False)
    crop_path = log_dir/'repairs.png'
    incomplete.save(crop_path)
    (log_dir/'traveler_regions.json').write_text(json.dumps({
        'source_path': '/nonexistent/original.jpg',
        'relative_path': '230809002 Line Card Original.jpg',
        'traveler_kind': 'original',
        'warranty': False,
        'regions': {'repairs_replacements': {'crop_path': str(crop_path)}}
    }))
    record = reader.process_log(
        log_dir,
        'minicpm-v:latest',
        30,
        {'entries': []},
        4,
        0.02,
        True,
    )
    assert record['status'] == 'review_required_row_coverage', record['status']
    assert record['vision_processing_stopped'] is True
    assert record['entries'] == []
    assert (
        log_dir/'vision_extraction_v1_3_4_2'/'anchor_detection_debug.png'
    ).exists()

blank = Image.new('L', (500, 100), 255)
assert reader.ink_density(blank) < 0.001

print('PASS: Nova Traveler Reader v1.3.4.2 tests')
