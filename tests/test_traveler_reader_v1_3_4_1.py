#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'reader',
    str(ROOT/'ingest'/'nova_traveler_reader_v1_3_4_1.py'),
)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)

log = reader.decode_log_number('230809002')
assert log['valid'] is True
assert log['log_date'] == '2023-08-09'
assert log['daily_sequence'] == '002'

assert reader.parse_date_field(
    '9/25/23', '230809002'
)['status'] == 'plausible'
assert reader.parse_date_field(
    '9/25/76', '230809002'
)['status'] == 'implausible'
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

# Synthetic repair table. The first entry anchor is deliberately above 30%
# of page height; v1.3.4 missed this class of top entry.
image = Image.new('L', (1000, 700), 255)
draw = ImageDraw.Draw(image)

# Header/body line and repair-entry bottom grid lines.
for y in (100, 230, 350, 480, 610):
    draw.line((0, y, 999, y), fill=80, width=2)

# Description/initials/date vertical rules.
draw.line((790, 0, 790, 699), fill=80, width=2)
draw.line((895, 0, 895, 699), fill=80, width=2)

# Four handwriting anchors; first is at y=180 (<30% of 700).
for center in (180, 300, 430, 560):
    draw.rectangle((820, center-8, 845, center+8), fill=20)
    draw.rectangle((920, center-7, 965, center+7), fill=20)

geometry = reader.detect_table_geometry(image)
assert 95 <= geometry['table_body_top'] <= 110
assert geometry['description_right'] == 790
assert abs(geometry['initials_right'] - 895) <= 2

detection = reader.detect_repair_anchors(
    image,
    expected_entries=4,
    geometry=geometry,
)
centers = [item['y'] for item in detection['anchors']]
assert len(centers) == 4, centers
assert all(
    abs(actual - expected) < 20
    for actual, expected in zip(centers, (180, 300, 430, 560))
), centers

bands = reader.build_entry_bands(
    centers,
    image.height,
    geometry=geometry,
)
assert len(bands) == 4
assert bands[0][0] <= 105
assert bands[0][1] <= 235
assert bands[-1][1] <= 615

# Full date column must extend to the complete parent-image right edge.
with tempfile.TemporaryDirectory() as tmp:
    crops = reader.create_entry_crops(
        image,
        bands,
        Path(tmp),
        geometry=geometry,
    )
    assert len(crops) == 4
    assert crops[0]['pixel_boxes']['date'][2] == image.width
    assert crops[0]['pixel_boxes']['full_row'][2] == image.width



# Rebuild the repairs crop from the original traveler through the full right
# edge so handwritten dates are not clipped by the v1.3.1 96% boundary.
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
    assert info['expanded_fractional_box'][2] == 0.998
    assert expanded.width > int((0.96 - 0.53) * 1200 * 2)

# If the expected count is not met, full processing must stop before vision.
with tempfile.TemporaryDirectory() as tmp:
    log_dir = Path(tmp)/'230809002'
    log_dir.mkdir()
    mismatch_image = Image.new('L', (1000, 700), 255)
    mismatch_draw = ImageDraw.Draw(mismatch_image)
    for y in (100, 260, 430, 610):
        mismatch_draw.line((0, y, 999, y), fill=80, width=2)
    mismatch_draw.line((790, 0, 790, 699), fill=80, width=2)
    mismatch_draw.line((895, 0, 895, 699), fill=80, width=2)
    for center in (190, 360, 540):
        mismatch_draw.rectangle((820, center-8, 845, center+8), fill=20)
        mismatch_draw.rectangle((920, center-7, 965, center+7), fill=20)
    crop_path = log_dir/'repairs.png'
    mismatch_image.save(crop_path)
    (log_dir/'traveler_regions.json').write_text(json.dumps({
        'source_path': '/nonexistent/original.jpg',
        'relative_path': '230809002 Line Card Original.jpg',
        'traveler_kind': 'original',
        'warranty': False,
        'regions': {
            'repairs_replacements': {
                'crop_path': str(crop_path)
            }
        }
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
    assert record['status'] == 'review_required_anchor_count_mismatch'
    assert record['vision_processing_stopped'] is True
    assert record['entries'] == []
    assert (
        log_dir/'vision_extraction_v1_3_4_1'/'anchor_detection_debug.png'
    ).exists()

blank = Image.new('L', (500, 100), 255)
assert reader.ink_density(blank) < 0.001

print('PASS: Nova Traveler Reader v1.3.4.1 tests')
