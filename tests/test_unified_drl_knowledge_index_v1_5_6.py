#!/usr/bin/env python3
import contextlib, importlib.util, io, json, sqlite3, sys, tempfile
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'tools' / 'nova_drl_unified_knowledge_engine_v1_5_3.py'
UI = ROOT / 'tools' / 'nova_drl_unified_knowledge_index_v1_5_6.py'

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod; spec.loader.exec_module(mod); return mod

eng = load('v153_engine_for_v156_test', ENGINE)
ui = load('v156_ui_test', UI)

def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')

with tempfile.TemporaryDirectory() as td:
    t = Path(td); fi = t/'file.sqlite'; full = t/'full'; db = t/'knowledge.sqlite'
    con = sqlite3.connect(fi)
    con.executescript('''
      CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
      INSERT INTO meta VALUES('share_root','/mnt/drl');
      CREATE TABLE files(id INTEGER PRIMARY KEY,relative_path TEXT,filename TEXT,parent_path TEXT,extension TEXT,size INTEGER,mtime_ns INTEGER,detected_log TEXT,file_kind TEXT);
    ''')
    con.execute('INSERT INTO files VALUES(1,?,?,?,?,?,?,?,?)',(
      '000 folder for tech scans/SVO DRV - MR-J2S-40A MITSUBISHI SN ABC/260101001 Line Card Original.jpg',
      '260101001 Line Card Original.jpg','000 folder for tech scans/SVO DRV - MR-J2S-40A MITSUBISHI SN ABC','.jpg',100,1,'260101001','file'))
    con.commit(); con.close()

    families = [
        'SVO DRV - MR-J2S-40A MITSUBISHI',
        'SVO DRV - MR-J2S-40A MITSUBISHI',
        'SVO DRV - MR-J2S-40A-S12 MITSUBISHI',
        'SVO DRV - MR-J2S-40A MITSHUBISHI',
    ]
    events=[]; parts=[]
    for i in range(1, 13):
        family = families[i % len(families)]
        log=f'260101{i:03d}'; eid=f'log_{log}'
        failure = 'Alarm Code E9 / dead' if i <= 7 else ('Low bus voltage' if i <= 10 else 'Encoder data loss')
        # Mix actual technician work with database/admin/test notes. Notes: FA - is customer failure.
        work = 'Replaced HCPL-7800 and capacitors' if i <= 8 else ('Changed bus capacitors' if i <= 10 else 'Replaced cooling fan')
        history = ' | '.join([
            f'Notes: FA - {failure}',
            work,
            'If present, remove batteries and return without batteries installed per MP',
            'Customer requires FA RPT on all BDs and P/Ss (sent electronically)',
            'Passed relay test.',
            'Warranty Type: Standard',
        ])
        events.append({
            'repair_event_id':eid,'log_number':log,'equipment_family':family,
            'top_folders':[family+' SN ABC'],'primary_source_paths':[f'/mnt/drl/x/{log} Line Card.jpg'],
            'supporting_source_paths':[],
            'facts':{
                'basic_reported_problem':[{'text':failure}],
                'parts_replaced':[],
                'repair_history_notes':[{'text':history}],
                'explicit_test_outcome':[{'text':'Passed relay test.'}],
            }
        })

    # 7800 family: variant spellings across 10 distinct events; event 1 contains two spellings but counts once.
    variants = {
        1:['7800','HCPL7800'], 2:['7800'], 3:['HCPL7800'], 4:['7800A'], 5:['HCPL7800A'],
        6:['630-HCPL-7800A-300E'], 7:['HPC-7800'], 8:['HCL7800'], 9:['7860'], 10:['2800'],
    }
    for i, pns in variants.items():
        log=f'260101{i:03d}'
        for pn in pns:
            parts.append({'repair_event_id':f'log_{log}','log_number':log,'equipment_family':events[i-1]['equipment_family'],
                          'part_number':pn,'quantity':1,'text':f'Replaced {pn}','evidence_quote':f'Replaced {pn}'})

    # Capacitor value variants collapse by value, including value stored in text rather than PN.
    for i in range(1, 9):
        log=f'260101{i:03d}'
        pn = '33uF' if i <= 4 else ('33 µF' if i <= 7 else None)
        parts.append({'repair_event_id':f'log_{log}','log_number':log,'equipment_family':events[i-1]['equipment_family'],
                      'part_number':pn,'quantity':1,'text':'Replaced 33 uF capacitor','evidence_quote':'Replaced 33 uF capacitor'})
    for i in range(1, 7):
        log=f'260101{i:03d}'
        pn = '47uF' if i <= 3 else '47 µF'
        parts.append({'repair_event_id':f'log_{log}','log_number':log,'equipment_family':events[i-1]['equipment_family'],
                      'part_number':pn,'quantity':1,'text':'Replaced 47uF capacitor','evidence_quote':'Replaced 47uF capacitor'})
    # A true unrelated part stays separate.
    for i in (2,11):
        log=f'260101{i:03d}'
        parts.append({'repair_event_id':f'log_{log}','log_number':log,'equipment_family':events[i-1]['equipment_family'],
                      'part_number':'26C31','quantity':1,'text':'Replaced 26C31','evidence_quote':'Replaced 26C31'})

    write_jsonl(full/'repair_events_v1_5_2.jsonl', events)
    write_jsonl(full/'replacement_mentions_v1_5_2.jsonl', parts)
    write_jsonl(full/'rma_refs_v1_5_2.jsonl', [])
    write_jsonl(full/'customer_po_refs_v1_5_2.jsonl', [])
    write_jsonl(full/'procurement_refs_v1_5_2.jsonl', [])
    (full/'drl_full_corpus_summary_v1_5_2.txt').write_text('synthetic')

    build_args=Namespace(file_index=str(fi),full_root=str(full),db=str(db),top=8,candidate_limit=800,json=False,self_check_warn_ms=250.0)
    eng.build_db(build_args)
    con=eng.connect_ro(db)
    args=Namespace(db=str(db),top=8,candidate_limit=800,reports_dir=str(t/'reports'),report_port=8765,printer=None)

    _, groups, _ = ui.search_report(con,'MR-J2S-40A',args)
    gd=dict(groups)
    product=gd['EQUIPMENT / PRODUCT'][0]['payload']
    assert product['base_part_number']=='MR-J2S-40A', product
    assert product['repair_event_count']==12, product

    got=[(r['payload']['pn'],r['payload']['repairs']) for r in gd['PARTS REPLACED']]
    assert got[0] == ('7800',10), got
    assert ('33uF',8) in got, got
    assert ('47uF',6) in got, got
    assert ('26C31',2) in got, got
    assert not any(pn in {'HCPL7800','7800A','HCPL7800A','HPC-7800','7860','2800'} for pn,_ in got), got

    failures=[(r['payload']['failure'],r['payload']['repairs']) for r in gd['REPORTED FAILURE']]
    assert failures[0][1] >= 7, failures
    assert any('E9' in x[0] for x in failures), failures

    histories=[(r['payload']['history'],r['payload']['repairs']) for r in gd['REPAIR HISTORY']]
    assert histories and histories[0][1] >= 8, histories
    flat='\n'.join(x[0] for x in histories)
    assert 'remove batteries' not in flat.lower(), flat
    assert 'fa rpt' not in flat.lower(), flat
    assert 'warranty type' not in flat.lower(), flat
    assert 'relay test' not in flat.lower(), flat
    assert 'Notes: FA' not in flat, flat

    buf=io.StringIO()
    with contextlib.redirect_stdout(buf): ui.render_console(con,'MR-J2S-40A',args,show_pdf_hint=False)
    out=buf.getvalue()
    assert 'Base part number: MR-J2S-40A' in out
    assert '7800' in out and '10' in out
    assert 'TOP REPORTED FAILURES' in out
    assert 'TOP TECHNICIAN REPAIR HISTORY' in out
    assert 'Customer requires FA RPT' not in out

    blocks=ui.pdf_report_blocks('MR-J2S-40A',groups,1.0)
    txt='\n'.join(x[1] for x in blocks)
    assert 'TOP REPORTED FAILURES' in txt
    assert 'TOP TECHNICIAN REPAIR HISTORY' in txt
    assert '7800' in txt
    assert 'remove batteries' not in txt.lower()
    con.close()

print('PASS: Nova DRL Component Core Resolver + Clean Failure/Repair View v1.5.6 tests')
