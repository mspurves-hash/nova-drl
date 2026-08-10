#!/usr/bin/env python3
import importlib.util
import json
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('collector', str(ROOT/'ingest'/'nova_repair_evidence_collector_v1_4.py'))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

refs = {'oems':['GENMARK'],'technicians':['ERICH'],'sites':{'MTV':'Micron Technology Virginia'}}

assert c.decode_log_number('230809002')['repair_date'] == '2023-08-09'
assert c.decode_log_number('230809002')['daily_sequence'] == '002'
assert c.classify_assignment(Path('230809002 Receiving Pic (1).JPG'))['scope'] == 'repair_event'
assert c.classify_assignment(Path('Floppy Copy/params.bin'))['scope'] == 'unit_level'

# Minimal DOCX and XLSX generators for stdlib extraction tests.
def make_docx(path, text):
    xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>{}</w:t></w:r></w:p></w:body></w:document>'.format(text)
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('word/document.xml', xml)

def make_xlsx(path, text):
    shared = '<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="1" uniqueCount="1"><si><t>{}</t></si></sst>'.format(text)
    sheet = '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('xl/sharedStrings.xml', shared)
        z.writestr('xl/worksheets/sheet1.xml', sheet)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)/'RBT - GB8-MT GENMARK SN 80010732 UTI MICRON ERICH'
    root.mkdir()
    logs = ['130130006','130402001','130613003','130813004','131017001','150622005','180115003','191029005','200902002','221013005','260619009']
    warranty = set(['130402001','130613003','130813004','131017001'])

    event_files = {
        '130130006':['RBT RPT.pdf'],
        '130402001':['Robot Checklist.PDF','Robot Test Report.PDF'],
        '130613003':['Robot Checklist.PDF','Robot Test Report.PDF'],
        '130813004':['Receiving Pic (1).JPG','Receiving Pic (2).JPG','Robot Test Report.PDF'],
        '131017001':['Receiving Pic (1).JPG','Return Shipment Packaging (1).JPG'],
        '150622005':['Receiving Pic (1).JPG','Receiving Pic (2).JPG','Receiving Pic (3).JPG','RBT RPT.pdf'],
        '180115003':['Receiving Pic (1).JPG','Receiving Pic (2).JPG','Receiving Pic (3).JPG','Receiving Pic (4).JPG','Receiving Pic (5).JPG','Return Shipment Packaging (1).JPG','Return Shipment Packaging (2).JPG'],
        '191029005':['Receiving Pic (1).JPG','Receiving Pic (2).JPG','Receiving Pic (3).JPG','Receiving Pic (4).JPG','Receiving Pic (5).JPG','Return Shipment Packaging (1).JPG','Return Shipment Packaging (2).JPG','Return Shipment Packaging (3).JPG','Internal Checklist Notes.docx'],
        '200902002':['Gold Incoming Failure Analysis Report.xlsx','Receiving Pic (1).JPG'],
        '221013005':['Field Failure Report.pdf','Receiving Pic (1).JPG','Receiving Pic (2).JPG'],
        '260619009':['Internal Checklist Notes.docx','Failure Analysis Report.txt'],
    }

    for log in logs:
        if log != '260619009':
            kind = 'Warranty' if log in warranty else 'Original'
            (root/'{} Line Card {}.jpg'.format(log,kind)).write_bytes(b'image')
        for suffix in event_files[log]:
            path = root/'{} {}'.format(log,suffix)
            if path.suffix.lower() == '.docx':
                make_docx(path, 'Technician checklist notes for '+log)
            elif path.suffix.lower() == '.xlsx':
                make_xlsx(path, 'Failure analysis for '+log)
            else:
                path.write_bytes(b'content')

    # 47 event files + 2 unit-level files = 49 original files.
    for dirname in ['Floppy Copy','Unit Photos','Parameter Archive','Unit Configuration']:
        (root/dirname).mkdir()
    (root/'Floppy Copy'/'params.bin').write_bytes(b'params')
    (root/'Unit Photos'/'unit_note.txt').write_text('Unit-level note', encoding='utf-8')

    derived_root = Path(tmp)/'traveler_reader'
    safe = c.safe_name(root.name)
    d = derived_root/safe/'191029005'
    d.mkdir(parents=True)
    (d/'traveler_regions.txt').write_text('Traveler OCR evidence', encoding='utf-8')

    out = Path(tmp)/'output'
    summary = c.collect_evidence(root,out,refs,traveler_output_root=derived_root,inventory_only=True)

    assert summary['repair_event_count'] == 11
    assert summary['original_source_file_count'] == 49
    assert summary['warranty_event_count'] == 4
    assert summary['missing_traveler_event_count'] == 1
    assert summary['unit_level_item_count'] == 6
    assert summary['unaccounted_original_file_count'] == 0
    assert len([e for e in summary['repair_events'] if e['log_number']=='191029005'][0]['derived_traveler_artifacts']) == 1

    # Text extractors work independently of the full collection run.
    docx_path = root/'191029005 Internal Checklist Notes.docx'
    assert 'Technician checklist notes' in c.extract_text(docx_path)['text']
    xlsx_path = root/'200902002 Gold Incoming Failure Analysis Report.xlsx'
    assert 'Failure analysis' in c.extract_text(xlsx_path)['text']
    txt_path = root/'260619009 Failure Analysis Report.txt'
    assert c.extract_text(txt_path)['status'] == 'ok'

    c.write_outputs(summary,out)
    assert (out/'serial_evidence_summary.json').exists()
    assert (out/'events'/'191029005'/'repair_evidence_bundle.json').exists()
    bundle = json.loads((out/'events'/'191029005'/'repair_evidence_bundle.json').read_text())
    assert bundle['repair_event']['accepted_as_repair_summary'] is False

print('PASS: Nova Repair Evidence Collector v1.4 tests')
