#!/usr/bin/env python3
import importlib.util,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('s',str(ROOT/'ingest'/'nova_surveyor_v1_2_1.py')); s=importlib.util.module_from_spec(spec); spec.loader.exec_module(s)
refs={'oems':['GENMARK'],'technicians':['ERICH'],'sites':{'MTV':'Micron Technology Virginia'}}
d=s.decode_log('230809002'); assert d['repair_date']=='2023-08-09' and d['daily_sequence']=='002'
with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp)/'RBT - GB8-MT GENMARK SN 80010732 UTI MICRON MTV ERICH'; p.mkdir()
    names=['130130006 Line Card Original.jpg','130402001 Line Card Warranty.JPG','130402001 Robot Test Report.PDF','130613003 Line Card Warranty.JPG','180115003 Line Card Original.jpg','180115003 Receiving Pic (1).JPG','191029005 Line Card Original.jpg','191029005 Return Shipment Packaging (1).JPG','200902002 Gold Incoming Failure Analysis Report.pdf','221013005 Field Failure Report.pdf','260619009 Internal Checklist Notes.docx']
    for n in names: (p/n).write_bytes(b'x')
    (p/'Floppy Copy').mkdir(); (p/'Floppy Copy'/'params.bin').write_bytes(b'x')
    r=s.serial_history(p,refs,False); assert r['summary']['repair_event_count']==8; assert r['summary']['warranty_event_count']==2; assert r['summary']['first_repair_date']=='2013-01-30'; assert r['summary']['most_recent_repair_date']=='2026-06-19'; assert any(x['role']=='unit_configuration_archive' for x in r['unit_level_evidence'])
    ev={e['log_number']:e for e in r['repair_events']}; assert ev['200902002']['role_counts']['failure_analysis_report']==1; assert ev['221013005']['role_counts']['failure_analysis_report']==1; assert ev['180115003']['role_counts']['receiving_photo']==1; assert ev['191029005']['role_counts']['return_packaging_photo']==1; assert ev['260619009']['missing_traveler'] is True
print('PASS: Nova Surveyor v1.2.1 tests')
