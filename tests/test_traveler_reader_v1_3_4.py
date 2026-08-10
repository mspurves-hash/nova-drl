#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'reader', str(ROOT/'ingest'/'nova_traveler_reader_v1_3_4.py')
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

parsed = reader.parse_row_response('Machined Comm\'s | EF | 9/25/23')
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

# Synthetic repair table with four right-column handwriting anchors plus form lines.
image = Image.new('L', (1000, 700), 255)
draw = ImageDraw.Draw(image)
draw.line((0, 180, 999, 180), fill=80, width=2)
draw.line((810, 0, 810, 699), fill=80, width=2)
draw.line((900, 0, 900, 699), fill=80, width=2)
for center in (260, 365, 490, 610):
    draw.rectangle((835, center-8, 850, center+8), fill=20)
    draw.rectangle((930, center-7, 955, center+7), fill=20)

detection = reader.detect_repair_anchors(
    image, x_start=0.82, x_end=0.995, y_start=0.25,
    expected_entries=4
)
centers = [item['y'] for item in detection['anchors']]
assert len(centers) == 4, centers
assert all(abs(a-b) < 20 for a,b in zip(centers, (260,365,490,610))), centers
bands = reader.build_entry_bands(centers, image.height)
assert len(bands) == 4

blank = Image.new('L', (500, 100), 255)
assert reader.ink_density(blank) < 0.001

print('PASS: Nova Traveler Reader v1.3.4 tests')
