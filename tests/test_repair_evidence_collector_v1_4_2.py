#!/usr/bin/env python3
import importlib.util
import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'collector', str(ROOT/'ingest'/'nova_repair_evidence_collector_v1_4_2.py')
)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

refs = {'oems':['GENMARK'],'technicians':['ERICH'],'sites':{'MTV':'Micron Technology Virginia'}}

assert c.VERSION == '1.4.2'
assert c.decode_log_number('230809002')['repair_date'] == '2023-08-09'
assert c.classify_assignment(Path('131017001 Receiving Pictures/DSCN3013.JPG'))['log_number'] == '131017001'
assert c.classify_role(Path('.picasa.ini'))['role'] == 'system_metadata'

sem = c.document_semantics_for_role('robot_checklist')
assert sem['profile'] == 'template_plus_event_annotations'
assert sem['event_annotations_require_review'] is True
assert any('not proof' in x for x in sem['guardrails'])
assert c.ocr_quality_score('Robot Checklist Initial checkout packaging bracket inspection') > c.ocr_quality_score('| | --- ;;')

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)/'RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH'
    root.mkdir()
    (root/'230809002 Line Card Original.jpg').write_bytes(b'image placeholder')
    (root/'.picasa.ini').write_text('metadata', encoding='utf-8')

    # Create a clean, image-only checklist PDF for an OCR integration test.
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as exc:
        raise AssertionError('Pillow required for v1.4.2 test: {}'.format(exc))

    img = Image.new('RGB', (1700, 2200), 'white')
    draw = ImageDraw.Draw(img)
    font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    font = ImageFont.truetype(font_path, 42) if Path(font_path).exists() else ImageFont.load_default()
    bold_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
    bold = ImageFont.truetype(bold_path, 52) if Path(bold_path).exists() else font
    y = 120
    draw.text((120,y),'RBT-GB8-MT (GENMARK)',fill='black',font=bold); y += 100
    draw.text((120,y),'Checklist for internal use at DRL',fill='black',font=bold); y += 120
    for line in [
        'Initial checkout',
        'Check packaging condition and shipping bracket.',
        'Check for general damage and missing hardware.',
        'Record technician initials and handwritten notes.',
        'Printed instructions are procedure knowledge only.',
    ]:
        draw.text((120,y),line,fill='black',font=font); y += 82
    pdf_path = root/'230809002 Robot Checklist.PDF'
    img.save(pdf_path, 'PDF', resolution=300.0)

    out = Path(tmp)/'output'
    full = c.collect_evidence(
        root, out, refs,
        inventory_only=False,
        extract_logs=['230809002'],
        enable_scanned_pdf_ocr=True,
        pdf_dpi=300,
        max_pdf_pages=10,
    )

    assert full['repair_event_count'] == 1
    assert full['original_source_file_count'] == 3
    assert full['unaccounted_original_file_count'] == 0
    assert full['system_metadata_item_count'] == 1
    assert full['scanned_pdf_ocr_document_count'] == 1
    assert full['scanned_pdf_ocr_page_count'] == 1
    assert full['scanned_pdf_ocr_review_document_count'] == 1

    event = full['repair_events'][0]
    checklist = next(x for x in event['evidence_files'] if x['role'] == 'robot_checklist')
    assert checklist['extraction']['status'] == 'ok'
    assert checklist['extraction']['method'] == 'scanned_pdf_ocr'
    assert checklist['extraction']['pages_processed'] == 1
    assert checklist['extraction']['ocr_review_required'] is True
    assert checklist['document_semantics']['profile'] == 'template_plus_event_annotations'
    assert Path(checklist['extraction']['manifest_path']).exists()
    assert Path(checklist['extraction']['artifact_dir']).exists()
    assert Path(checklist['extraction']['text_path']).exists()
    text = Path(checklist['extraction']['text_path']).read_text(encoding='utf-8').lower()
    assert 'checklist' in text
    assert 'packaging' in text

    # A different log remains inventoried but extraction can be deliberately deferred.
    (root/'230810001 Robot Test Report.PDF').write_bytes(pdf_path.read_bytes())
    out2 = Path(tmp)/'filtered'
    filtered = c.collect_evidence(root, out2, refs, inventory_only=False, extract_logs=['230809002'])
    other = next(e for e in filtered['repair_events'] if e['log_number']=='230810001')
    other_report = next(x for x in other['evidence_files'] if x['role']=='robot_test_report')
    assert other_report['extraction']['status'] == 'deferred_by_log_filter'

    c.write_outputs(full, out)
    assert (out/'events'/'230809002'/'repair_evidence_bundle.txt').exists()
    assert (out/'evidence_index.csv').exists()

print('PASS: Nova Repair Evidence Collector v1.4.2 tests')
