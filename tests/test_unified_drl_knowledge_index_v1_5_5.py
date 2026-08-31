#!/usr/bin/env python3
import contextlib, importlib.util, io, json, sqlite3, sys, tempfile
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'tools' / 'nova_drl_unified_knowledge_engine_v1_5_3.py'
UI = ROOT / 'tools' / 'nova_drl_unified_knowledge_index_v1_5_5.py'

def load(name, path):
    spec=importlib.util.spec_from_file_location(name, path)
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod

eng=load('v153_engine_for_v155_test', ENGINE)
ui=load('v155_ui_test', UI)

def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        for r in rows: f.write(json.dumps(r)+'\n')

with tempfile.TemporaryDirectory() as td:
    t=Path(td); fi=t/'file.sqlite'; full=t/'full'; db=t/'knowledge.sqlite'
    con=sqlite3.connect(fi)
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
        'SVO DRV - MR-J2S-40A MITSUBISHI',
        'SVO DRV - MR-J2S-40A MITSUBISHI',
        'SVO DRV - MR-J2S-40A MITSUBISHI',
        'SVO DRV - MR-J2S-40A-S12 MITSUBISHI',
        'SVO DRV - MR-J2S-40A-S12 MITSUBISHI',
        'SVO DRV - MR-J2S-40A MITSHUBISHI',
    ]
    histories = [
        'Replaced bus capacitors | Final O.K. 1/1/26',
        'Replaced bus capacitors',
        'Changed bus capacitors',
        'Replaced cooling fan',
        'Replaced cooling fan',
        'Replaced bus capacitor',
        'Adjusted bus voltage',
        'Adjusted bus voltage',
    ]
    events=[]; parts=[]
    for i,(family,history) in enumerate(zip(families,histories),1):
        log=f'260101{i:03d}'; eid=f'log_{log}'
        events.append({
            'repair_event_id':eid,'log_number':log,'equipment_family':family,
            'top_folders':[family+' SN ABC'],'primary_source_paths':[f'/mnt/drl/x/{log} Line Card.jpg'],
            'supporting_source_paths':[],
            'facts':{
                'basic_reported_problem':[{'text':'Servo driver fault'}],
                'parts_replaced':[],
                'repair_history_notes':[{'text':history}],
                'explicit_test_outcome':[{'text':'Passed All Tests'}],
            }
        })
    # PN-A across 6 distinct repairs, including suffix and spelling variants.
    for i in (1,2,3,4,6,8):
        log=f'260101{i:03d}'
        parts.append({'repair_event_id':f'log_{log}','log_number':log,'equipment_family':families[i-1],
                      'part_number':'PN-A100','quantity':1,'text':'PN-A100','evidence_quote':'replaced PN-A100'})
    # PN-B across three repairs.
    for i in (1,5,7):
        log=f'260101{i:03d}'
        parts.append({'repair_event_id':f'log_{log}','log_number':log,'equipment_family':families[i-1],
                      'part_number':'PN-B200','quantity':1,'text':'PN-B200','evidence_quote':'replaced PN-B200'})
    # One singleton PN should still remain in the complete parts list.
    parts.append({'repair_event_id':'log_260101002','log_number':'260101002','equipment_family':families[1],
                  'part_number':'PN-C300','quantity':1,'text':'PN-C300','evidence_quote':'replaced PN-C300'})

    write_jsonl(full/'repair_events_v1_5_2.jsonl',events)
    write_jsonl(full/'replacement_mentions_v1_5_2.jsonl',parts)
    write_jsonl(full/'rma_refs_v1_5_2.jsonl',[])
    write_jsonl(full/'customer_po_refs_v1_5_2.jsonl',[])
    write_jsonl(full/'procurement_refs_v1_5_2.jsonl',[])
    (full/'drl_full_corpus_summary_v1_5_2.txt').write_text('synthetic')

    build_args=Namespace(file_index=str(fi),full_root=str(full),db=str(db),top=8,candidate_limit=800,json=False,self_check_warn_ms=250.0)
    eng.build_db(build_args)
    con=eng.connect_ro(db)
    args=Namespace(db=str(db),top=8,candidate_limit=800,reports_dir=str(t/'reports'),report_port=8765,printer=None)

    # Base product search aggregates root + suffix + OEM-spelling variants.
    _, groups, _=ui.search_report(con,'MR-J2S-40A',args)
    gd=dict(groups)
    product=gd['EQUIPMENT / PRODUCT'][0]['payload']
    assert product['base_part_number']=='MR-J2S-40A', product
    assert product['repair_event_count']==8, product
    assert product['variant_count']==2, product

    part_rows=gd['PARTS REPLACED']
    got=[(r['payload']['pn'],r['payload']['repairs']) for r in part_rows]
    assert got[:3]==[('PN-A100',6),('PN-B200',3),('PN-C300',1)], got

    hist=gd['REPAIR HISTORY']
    hgot=[(r['payload']['history'],r['payload']['repairs']) for r in hist]
    assert len(hist) <= 10
    assert hgot[0][1] == 4, hgot  # replace/change bus capacitor cluster
    assert sorted(x[1] for x in hgot[1:]) == [2,2], hgot
    assert all(x[1] >= 2 for x in hgot), hgot
    assert not any('Final O.K.' in x[0] for x in hgot)

    # Searching a suffix resolves back to the higher-volume base product.
    _, sg, _=ui.search_report(con,'MR-J2S-40A-S12',args)
    sp=dict(sg)['EQUIPMENT / PRODUCT'][0]['payload']
    assert sp['base_part_number']=='MR-J2S-40A', sp
    assert sp['repair_event_count']==8, sp

    buf=io.StringIO()
    with contextlib.redirect_stdout(buf): ui.render_console(con,'MR-J2S-40A',args,show_pdf_hint=False)
    out=buf.getvalue()
    assert 'Base part number: MR-J2S-40A' in out
    assert 'Indexed repair events: 8' in out
    assert 'PART NUMBER' in out and 'TIMES REPLACED' in out
    assert 'TOP REPEATED REPAIR HISTORY' in out and 'TIMES SEEN' in out
    assert out.index('PN-A100') < out.index('PN-B200') < out.index('PN-C300')

    blocks=ui.pdf_report_blocks('MR-J2S-40A',groups,1.0)
    txt='\n'.join(x[1] for x in blocks)
    assert 'PART NUMBER' in txt and 'TIMES REPLACED' in txt
    assert 'TOP REPEATED REPAIR HISTORY' in txt and 'TIMES SEEN' in txt
    assert txt.index('PN-A100') < txt.index('PN-B200') < txt.index('PN-C300')
    con.close()

print('PASS: Nova DRL Base-PN Product Resolver + Complete Product View v1.5.5 tests')
