#!/usr/bin/env python3
import importlib.util, inspect, json, sqlite3, sys, tempfile
from argparse import Namespace
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'tools'/'nova_drl_unified_knowledge_engine_v1_5_3.py'
UI=ROOT/'tools'/'nova_drl_unified_knowledge_index_v1_5_11.py'

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod
eng=load('v153_engine_for_v1511_rollup',ENGINE)
ui=load('v1511_rollup',UI)

assert ui.GENERIC_COMPONENT_NAME_RECOVERY is True
assert ui.RECURRING_REPAIR_ACTIONS_80_20 is True
source='\n'.join([
    inspect.getsource(ui._component_reference_signals),
    inspect.getsource(ui._replacement_component_signals),
    inspect.getsource(ui.aggregate_product_parts),
    inspect.getsource(ui.aggregate_repair_actions),
])
for forbidden in ('PRE-200','MR-J2S-40A','RCL1A','HNS5540-AA','SN74LS14N'):
    assert forbidden not in source, f'product-specific recovery leaked into production: {forbidden}'

def write_jsonl(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8') as f:
        for r in rows: f.write(json.dumps(r)+'\n')

with tempfile.TemporaryDirectory() as td:
    t=Path(td); fi=t/'file.sqlite'; full=t/'full'; db=t/'knowledge.sqlite'
    con=sqlite3.connect(fi); con.executescript('''
      CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
      INSERT INTO meta VALUES('share_root','/mnt/drl');
      CREATE TABLE files(id INTEGER PRIMARY KEY,relative_path TEXT,filename TEXT,parent_path TEXT,extension TEXT,size INTEGER,mtime_ns INTEGER,detected_log TEXT,file_kind TEXT);
    '''); con.commit(); con.close()

    families=[('PREALIGNER - PRE-200-B BROOKS',28),('PREALIGNER - PRE-200 BROOKS',22),('PREALIGNER - PRE-200B-CE BROOKS',9),('PREALIGNER - PRE-200-CE BROOKS',3)]
    events=[]; ids=[]; seq=1
    for fam,n in families:
        for _ in range(n):
            log=f'261001{seq:03d}'; eid=f'log_{log}'; seq+=1; ids.append(eid)
            events.append({'repair_event_id':eid,'log_number':log,'equipment_family':fam,'top_folders':[fam],
                           'primary_source_paths':[f'/x/{log}.jpg'],'supporting_source_paths':[],
                           'facts':{'basic_reported_problem':[], 'parts_replaced':[], 'repair_history_notes':[], 'explicit_test_outcome':[]}})
    byid={e['repair_event_id']:e for e in events}
    parts=[]
    def add_part(eid,text,pn=None):
        e=byid[eid]; parts.append({'repair_event_id':eid,'log_number':e['log_number'],'equipment_family':e['equipment_family'],
                                   'part_number':pn,'quantity':1,'text':text,'evidence_quote':text})
    def add_hist(eid,text):
        byid[eid]['facts']['repair_history_notes'].append({'text':text,'evidence_quote':text})

    for eid in ids[:10]: add_part(eid,'Replaced Z motor'); add_hist(eid,'Replaced Z motor')
    for eid in ids[10:18]: add_part(eid,'Replaced Z encoder')
    for eid in ids[18:25]: add_part(eid,'Changed Z belt')
    for eid in ids[25:30]: add_part(eid,'Replaced lead screw')
    # Misclassified explicit replacement history must still reach Parts.
    for eid in ids[30:34]: add_hist(eid,'Replaced R motor')
    for eid in ids[34:37]: add_hist(eid,'Changed ladder belt')
    # PN-backed rows still behave as before.
    for eid in ids[37:40]: add_part(eid,'Replaced SN74LS14N','SN74LS14N')
    for eid in ids[40:42]: add_part(eid,'Replaced HNS5540-AA','HNS5540-AA')
    # Non-replacement work must NOT leak into the Parts table.
    for eid in ids[42:48]: add_hist(eid,'Cleaned lead screw')
    for eid in ids[48:53]: add_hist(eid,'Adjusted pin positions')
    for eid in ids[53:57]: add_hist(eid,'Aligned pins')
    for eid in ids[57:60]: add_hist(eid,'Cleaned CCDs')
    # Reused component in a slash note must not be counted as replaced.
    add_part(ids[60],'Replaced Z motor/reused encoder')
    add_hist(ids[60],'Replaced Z motor/reused encoder')

    write_jsonl(full/'repair_events_v1_5_2.jsonl',events)
    write_jsonl(full/'replacement_mentions_v1_5_2.jsonl',parts)
    for name in ('rma_refs','customer_po_refs','procurement_refs'): write_jsonl(full/f'{name}_v1_5_2.jsonl',[])
    (full/'drl_full_corpus_summary_v1_5_2.txt').write_text('synthetic')

    args=Namespace(file_index=str(fi),full_root=str(full),db=str(db),top=20,candidate_limit=800,json=False,self_check_warn_ms=250.0)
    eng.build_db(args)
    con=eng.connect_ro(db); ua=Namespace(db=str(db),top=20,candidate_limit=800,reports_dir=str(t/'reports'),report_port=8765,printer=None)
    _,groups,_=ui.search_report(con,'PRE-200',ua)
    gd=dict(groups)
    product=gd['EQUIPMENT / PRODUCT'][0]['payload']
    assert product['repair_event_count']==62, product

    got={x['payload']['pn']:x['payload']['repairs'] for x in gd['PARTS REPLACED']}
    assert got.get('Z MOTOR')==11, got
    assert got.get('Z ENCODER')==8, got
    assert got.get('Z BELT')==7, got
    assert got.get('LEAD SCREW')==5, got
    assert got.get('R MOTOR')==4, got
    assert got.get('LADDER BELT')==3, got
    assert got.get('SN74LS14N')==3, got
    assert got.get('HNS5540-AA')==2, got
    assert 'ENCODER' not in got, got  # reused encoder segment is not replacement evidence
    assert got.get('PIN') is None and got.get('CCD') is None, got

    actions={x['payload']['repair_action']:x['payload']['repairs'] for x in gd['RECURRING REPAIR ACTIONS']}
    assert actions.get('REPLACE Z MOTOR')==11, actions
    assert actions.get('REPLACE R MOTOR')==4, actions
    assert actions.get('REPLACE LADDER BELT')==3, actions
    assert actions.get('CLEAN LEAD SCREW')==6, actions
    assert actions.get('ADJUST PIN')==5, actions
    assert actions.get('ALIGN PIN')==4, actions
    assert actions.get('CLEAN CCD')==3, actions
    con.close()

print('PASS: Nova DRL v1.5.11 global component + recurring repair-action evidence rollup')
