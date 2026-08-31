#!/usr/bin/env python3
import contextlib, importlib.util, io, json, sqlite3, sys, tempfile
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'tools' / 'nova_drl_unified_knowledge_engine_v1_5_3.py'
UI = ROOT / 'tools' / 'nova_drl_unified_knowledge_index_v1_5_4.py'

def load(name, path):
    spec=importlib.util.spec_from_file_location(name, path)
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod

eng=load('v153_engine_for_v154_test', ENGINE)
ui=load('v154_ui_test', UI)

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
      '000 folder for tech scans/BRD - 1526990 TURBO DOSE ASYST SN ABC/170412002 Line Card Original.jpg',
      '170412002 Line Card Original.jpg','000 folder for tech scans/BRD - 1526990 TURBO DOSE ASYST SN ABC','.jpg',100,1,'170412002','file'))
    con.commit(); con.close()

    events=[]; parts=[]
    for i in range(1,5):
        log=f'26010100{i}'
        eid=f'log_{log}'
        events.append({'repair_event_id':eid,'log_number':log,'equipment_family':'BRD - 1526990 TURBO DOSE ASYST','top_folders':['BRD - 1526990 TURBO DOSE ASYST SN ABC'],'primary_source_paths':[f'/mnt/drl/x/{log} Line Card.jpg'],'supporting_source_paths':[], 'facts':{'basic_reported_problem':[],'parts_replaced':[],'repair_history_notes':[],'explicit_test_outcome':[]}})
    # PN-A in 4 distinct repairs, PN-B in 2, PN-C in 1.
    for i in range(1,5): parts.append({'repair_event_id':f'log_26010100{i}','log_number':f'26010100{i}','equipment_family':'BRD - 1526990 TURBO DOSE ASYST','part_number':'PN-A100','quantity':1,'text':'PN-A100','evidence_quote':'replaced PN-A100'})
    for i in range(1,3): parts.append({'repair_event_id':f'log_26010100{i}','log_number':f'26010100{i}','equipment_family':'BRD - 1526990 TURBO DOSE ASYST','part_number':'PN-B200','quantity':1,'text':'PN-B200','evidence_quote':'replaced PN-B200'})
    parts.append({'repair_event_id':'log_260101001','log_number':'260101001','equipment_family':'BRD - 1526990 TURBO DOSE ASYST','part_number':'PN-C300','quantity':3,'text':'PN-C300','evidence_quote':'replaced PN-C300'})
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
    results, groups, _=ui.search_report(con,'1526990',args)
    parts_group=dict(groups).get('PARTS REPLACED')
    assert parts_group, 'PARTS REPLACED group missing'
    got=[((r.get('payload') or {}).get('pn'), int((r.get('payload') or {}).get('repairs') or 0)) for r in parts_group[:3]]
    assert got == [('PN-A100',4),('PN-B200',2),('PN-C300',1)], got

    buf=io.StringIO()
    with contextlib.redirect_stdout(buf): ui.render_console(con,'1526990',args,show_pdf_hint=False)
    out=buf.getvalue()
    assert 'PART NUMBER' in out and 'TIMES REPLACED' in out
    assert 'PN-A100' in out and 'PN-B200' in out and 'PN-C300' in out
    assert out.index('PN-A100') < out.index('PN-B200') < out.index('PN-C300')
    # Main list is intentionally simple; no pieces/variants prose in the ranked part section.
    section=out.split('PARTS REPLACED',1)[1].split('REPAIR HISTORY',1)[0]
    assert 'Recorded pieces' not in section and 'Observed variants' not in section

    blocks=ui.pdf_report_blocks('1526990',groups,1.0)
    txt='\n'.join(x[1] for x in blocks)
    assert 'PART NUMBER' in txt and 'TIMES REPLACED' in txt
    assert txt.index('PN-A100') < txt.index('PN-B200') < txt.index('PN-C300')
    con.close()

print('PASS: Nova DRL Vertical Parts Presentation v1.5.4 tests')
