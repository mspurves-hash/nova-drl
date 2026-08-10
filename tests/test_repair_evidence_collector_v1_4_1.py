#!/usr/bin/env python3
import csv
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'collector', str(ROOT/'ingest'/'nova_repair_evidence_collector_v1_4_1.py')
)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

refs = {'oems':['GENMARK'],'technicians':['ERICH'],'sites':{'MTV':'Micron Technology Virginia'}}

assert c.decode_log_number('230809002')['repair_date'] == '2023-08-09'
assert c.classify_assignment(Path('131017001 Receiving Pictures/DSCN3013.JPG'))['log_number'] == '131017001'
assert c.is_system_metadata_path(Path('131017001 Receiving Pictures/.picasa.ini'))
assert c.is_system_metadata_path(Path('.picasa.ini'))
assert c.is_system_metadata_path(Path('Thumbs.db'))
assert not c.is_system_metadata_path(Path('131017001 Receiving Pictures/DSCN3013.JPG'))
assert c.classify_role(Path('.picasa.ini'))['role'] == 'system_metadata'

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)/'RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH'
    root.mkdir()

    names = [
        '130130006 Line Card Original.jpg',
        '130402001 Line Card Warranty.JPG',
        '130402001 Test Report.PDF',
        '130613003 Line Card Warranty.JPG',
        '130613003 Robot Test Report.PDF',
        '130813004 Line Card Warranty.JPG',
        '130813004 Robot Checklist.PDF',
        '130813004 Robot Test Report.PDF',
        '131017001 Line Card Warranty.JPG',
        '131017001 Robot Checklist.PDF',
        '131017001 Robot Test Report.PDF',
        '150622005 Gold Incoming Failure Analysis Report Robot.xls',
        '150622005 Internal Checklist Notes.docx',
        '150622005 Line Card Original.JPG',
        '150622005 Receiving Pic.JPG',
        '180115003 Gold Incoming Failure Analysis Report.xls',
        '180115003 Line Card Original.jpg',
        '180115003 Receiving Pic (1).JPG',
        '180115003 Receiving Pic (2).JPG',
        '180115003 Receiving Pic (3).JPG',
        '180115003 Receiving Pic (4).JPG',
        '180115003 Receiving Pic (5).JPG',
        '191029005 Line Card Original.jpg',
        '191029005 Receiving Pic (1).JPG',
        '191029005 Receiving Pic (2).JPG',
        '191029005 Receiving Pic (3).JPG',
        '191029005 Receiving Pic (4).JPG',
        '191029005 Return Shipment Packaging (1).JPG',
        '191029005 Return Shipment Packaging (2).JPG',
        '191029005 Return Shipment Packaging (3).JPG',
        '200902002 Line Card Original.jpg',
        '200902002 Receiving Pic (1).JPG',
        '200902002 Receiving Pic (2).JPG',
        '200902002 Receiving Pic (3).JPG',
        '200902002 Receiving Pic (4).JPG',
        '200902002 Receiving Pic (5).JPG',
        '200902002 Receiving Pic (6).JPG',
        '200902002 Receiving Pic (7).JPG',
        '221013005 Line Card Original.jpg',
        '221013005 Receiving Picd (1).JPG',
        '221013005 Receiving Picd (2).JPG',
        '260619009 RECEIVING PIC (1).jpg',
        '260619009 RECEIVING PIC (2).jpg',
        '.picasa.ini',
    ]
    for name in names:
        p = root/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'content')

    nested = root/'131017001 Receiving Pictures'
    nested.mkdir()
    for name in ['DSCN3013.JPG','DSCN3014.JPG','DSCN3015.JPG','DSCN3016.JPG','.picasa.ini']:
        (nested/name).write_bytes(b'content')

    assert len([p for p in root.rglob('*') if p.is_file()]) == 49

    out = Path(tmp)/'output'
    summary = c.collect_evidence(root, out, refs, inventory_only=True)

    assert summary['repair_event_count'] == 11
    assert summary['original_source_file_count'] == 49
    assert summary['event_assigned_file_count'] == 48
    assert summary['event_evidence_file_count'] == 47
    assert summary['event_system_metadata_file_count'] == 1
    assert summary['unit_level_item_count'] == 0
    assert summary['system_metadata_file_count'] == 2
    assert summary['system_metadata_item_count'] == 2
    assert summary['unit_system_metadata_file_count'] == 1
    assert summary['unresolved_file_count'] == 0
    assert summary['unaccounted_original_file_count'] == 0
    assert summary['warranty_event_count'] == 4
    assert summary['missing_traveler_event_count'] == 1

    event = next(e for e in summary['repair_events'] if e['log_number']=='131017001')
    assert len(event['evidence_files']) == 7
    assert len(event['system_metadata_files']) == 1
    assert event['system_metadata_files'][0]['relative_path'].endswith('.picasa.ini')
    assert 'system_metadata' not in event['evidence_counts_by_role']

    metadata_paths = sorted(x['relative_path'] for x in summary['system_metadata'] if x.get('item_type') != 'directory')
    assert metadata_paths == ['.picasa.ini', '131017001 Receiving Pictures/.picasa.ini']
    assert all(x['role']=='system_metadata' for x in summary['system_metadata'])

    # A non-inventory run still excludes metadata content from extraction.
    out2 = Path(tmp)/'output_full'
    full = c.collect_evidence(root, out2, refs, inventory_only=False)
    assert all(x['extraction']['status']=='excluded_system_metadata' for x in full['system_metadata'])

    c.write_outputs(summary, out)
    assert (out/'serial_evidence_summary.json').exists()
    assert (out/'system_metadata'/'system_metadata_bundle.json').exists()
    assert (out/'events'/'131017001'/'repair_evidence_bundle.json').exists()

    with (out/'evidence_index.csv').open(newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 49
    assert sum(1 for row in rows if row['role']=='system_metadata') == 2

print('PASS: Nova Repair Evidence Collector v1.4.1 tests')
