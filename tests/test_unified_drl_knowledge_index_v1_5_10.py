#!/usr/bin/env python3
import importlib.util, json, sqlite3, sys, tempfile
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'tools' / 'nova_drl_unified_knowledge_engine_v1_5_3.py'
UI = ROOT / 'tools' / 'nova_drl_unified_knowledge_index_v1_5_10.py'

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod; spec.loader.exec_module(mod); return mod

eng = load('v153_engine_for_v1510_test', ENGINE)
ui = load('v1510_ui_test', UI)

assert ui.DRL_80_20_HARD_INVARIANT is True
assert ui.ALLOW_EXPERT_KNOWLEDGE_OVERRIDES is False
assert ui.EXPERT_INPUT_ROLE == 'sanity_check_only_unless_explicitly_promoted'
assert ui.PRODUCT_PART_MIN_REPAIRS >= 2
assert not hasattr(ui, 'KIT_CONFIG')
assert not hasattr(ui, 'REFERENCE_PN_EXPERT_MAP')


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        for r in rows: f.write(json.dumps(r) + '\n')

with tempfile.TemporaryDirectory() as td:
    t=Path(td); fi=t/'file.sqlite'; full=t/'full'; db=t/'knowledge.sqlite'
    con=sqlite3.connect(fi)
    con.executescript('''
      CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
      INSERT INTO meta VALUES('share_root','/mnt/drl');
      CREATE TABLE files(id INTEGER PRIMARY KEY,relative_path TEXT,filename TEXT,parent_path TEXT,extension TEXT,size INTEGER,mtime_ns INTEGER,detected_log TEXT,file_kind TEXT);
    ''')
    con.commit(); con.close()

    events=[]; parts=[]
    # Mitsubishi family: stable numeric 7800 must remain the reference because it dominates.
    for i in range(1,13):
        fam='SVO DRV - MR-J2S-40A MITSUBISHI' if i<11 else 'SVO DRV - MR-J2S-40A-S12 MITSUBISHI'
        log=f'260101{i:03d}'; eid=f'log_{log}'
        events.append({'repair_event_id':eid,'log_number':log,'equipment_family':fam,'top_folders':[fam],
                       'primary_source_paths':[f'/x/{log}.jpg'],'supporting_source_paths':[],
                       'facts':{'basic_reported_problem':[{'text':'E9 alarm'}], 'parts_replaced':[], 'repair_history_notes':[], 'explicit_test_outcome':[]}})
    variants={1:'7800',2:'7800',3:'7800',4:'7800',5:'7800',6:'HCPL7800',7:'HCPL7800',8:'7800A',9:'7800A'}
    for i,pn in variants.items():
        log=f'260101{i:03d}'; parts.append({'repair_event_id':f'log_{log}','log_number':log,'equipment_family':events[i-1]['equipment_family'],'part_number':pn,'quantity':1,'text':f'Replaced {pn}','evidence_quote':f'Replaced {pn}'})
    # one text-only explicit replacement variant joins by shape
    parts.append({'repair_event_id':'log_260101010','log_number':'260101010','equipment_family':events[9]['equipment_family'],'part_number':None,'quantity':1,'text':'Changed HPC-7800','evidence_quote':'Changed HPC-7800'})

    # RCL1A-like corpus: complete recurring alphanumeric reference must beat numeric/short OCR forms.
    base_idx=len(events)
    for i in range(1,31):
        log=f'260201{i:03d}'; eid=f'log_{log}'; fam='PS - RCL1A-1D-W3 RACAL'
        events.append({'repair_event_id':eid,'log_number':log,'equipment_family':fam,'top_folders':[fam],
                       'primary_source_paths':[f'/x/{log}.jpg'],'supporting_source_paths':[],
                       'facts':{'basic_reported_problem':[{'text':'No DC output'}], 'parts_replaced':[], 'repair_history_notes':[], 'explicit_test_outcome':[]}})
    def add(prod_i,pn,text=None):
        ev=events[base_idx+prod_i-1]; parts.append({'repair_event_id':ev['repair_event_id'],'log_number':ev['log_number'],'equipment_family':ev['equipment_family'],'part_number':pn,'quantity':1,'text':text or f'Replaced {pn}','evidence_quote':text or f'Replaced {pn}'})
    for i in range(1,7): add(i,'6551')
    for i in range(7,11): add(i,'ISL6551IR')
    for i in range(11,14): add(i,'ISL 6551IR')
    for i in range(14,16): add(i,'ICL6551IR')

    for i in range(1,6): add(i,'1506')
    for i in range(6,9): add(i,'STTH1506TPI')
    for i in range(9,12): add(i,'STTH 1506 TPI')
    for i in range(12,14): add(i,'511-STTH1506')

    for i in range(1,9): add(i,'38AN08A1')
    for i in range(9,13): add(i,'FDH038AN08A1')
    for i in range(13,15): add(i,'O38AN08A1')

    for i in range(1,5): add(i,'IXFX 24N/100')
    for i in range(5,8): add(i,'IXFX24N100Q3')
    for i in range(8,10): add(i,'IXFX24N100')
    for i in range(10,12): add(i,'IXFX2N100Q3')

    # text-only numeric OCR noise must not become a reference PN without raw-PN support.
    for i in range(16,21): add(i,None,'Replaced bad 1002 component')
    # one-off raw PN is retained in corpus but suppressed from normal recurring view.
    add(30,'ODD-X1')

    write_jsonl(full/'repair_events_v1_5_2.jsonl',events)
    write_jsonl(full/'replacement_mentions_v1_5_2.jsonl',parts)
    for name in ('rma_refs','customer_po_refs','procurement_refs'):
        write_jsonl(full/f'{name}_v1_5_2.jsonl',[])
    (full/'drl_full_corpus_summary_v1_5_2.txt').write_text('synthetic')

    args=Namespace(file_index=str(fi),full_root=str(full),db=str(db),top=8,candidate_limit=800,json=False,self_check_warn_ms=250.0)
    eng.build_db(args)
    con=eng.connect_ro(db)
    ui_args=Namespace(db=str(db),top=8,candidate_limit=800,reports_dir=str(t/'reports'),report_port=8765,printer=None)

    _, groups, _=ui.search_report(con,'MR-J2S-40A',ui_args)
    got={r['payload']['pn']:r['payload']['repairs'] for r in dict(groups)['PARTS REPLACED']}
    assert got.get('7800')==10, got
    assert 'HCPL7800' not in got and '7800A' not in got, got

    _, groups, _=ui.search_report(con,'RCL1A',ui_args)
    got={r['payload']['pn']:r['payload']['repairs'] for r in dict(groups)['PARTS REPLACED']}
    assert got.get('ISL6551IR')==15, got
    assert got.get('STTH1506TPI')==13, got
    assert got.get('FDH038AN08A1')==14, got
    assert got.get('IXFX24N100Q3')==11, got
    assert '6551' not in got and '1506' not in got and '1002' not in got and 'ODD-X1' not in got, got
    con.close()

print('PASS: Nova DRL 80/20 Global Resolver Consolidation v1.5.10 tests')
