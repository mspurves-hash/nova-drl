#!/usr/bin/env python3
import importlib.util, json, sqlite3, tempfile
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'tools' / 'nova_drl_unified_knowledge_engine_v1_5_3.py'
spec = importlib.util.spec_from_file_location('v153_engine_test', ENGINE)
import sys
mod = importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod)


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        for r in rows: f.write(json.dumps(r) + '\n')

with tempfile.TemporaryDirectory() as td:
    t = Path(td); fi=t/'file.sqlite'; full=t/'full'; db=t/'knowledge.sqlite'
    con=sqlite3.connect(fi)
    con.executescript('''
      CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
      INSERT INTO meta VALUES('share_root','/mnt/drl');
      CREATE TABLE files(id INTEGER PRIMARY KEY,relative_path TEXT,filename TEXT,parent_path TEXT,extension TEXT,size INTEGER,mtime_ns INTEGER,detected_log TEXT,file_kind TEXT);
    ''')
    con.executemany('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?)',[
      (1,'000 folder for tech scans/BRD - 1526990 TURBO DOSE ASYST SN ABC/170412002 Line Card Original.jpg','170412002 Line Card Original.jpg','000 folder for tech scans/BRD - 1526990 TURBO DOSE ASYST SN ABC','.jpg',100,1,'170412002','file'),
      (2,'000 folder for tech scans/PS - RCL1A-1D-W3 RACAL SN 123/140424002 Line Card Original.jpg','140424002 Line Card Original.jpg','000 folder for tech scans/PS - RCL1A-1D-W3 RACAL SN 123','.jpg',100,1,'140424002','file'),
    ]); con.commit(); con.close()

    events=[
      {'repair_event_id':'log_170412002','log_number':'170412002','equipment_family':'BRD - 1526990 TURBO DOSE ASYST','top_folders':['BRD - 1526990 TURBO DOSE ASYST SN ABC'],'primary_source_paths':['/mnt/drl/x/170412002 Line Card Original.jpg'],'supporting_source_paths':[], 'facts':{'basic_reported_problem':[{'text':'Board dead no communications'}],'parts_replaced':[{'text':'INA105KP','part_number':'INA105KP','quantity':2}], 'repair_history_notes':[], 'explicit_test_outcome':[]}},
      {'repair_event_id':'log_140424002','log_number':'140424002','equipment_family':'PS - RCL1A-1D-W3 RACAL','top_folders':['PS - RCL1A-1D-W3 RACAL SN 123'],'primary_source_paths':['/mnt/drl/y/140424002 Line Card Original.jpg'],'supporting_source_paths':[], 'facts':{'basic_reported_problem':[{'text':'No DC output'}],'parts_replaced':[{'text':'FDH038AN08A1','part_number':'FDH038AN08A1','quantity':7}], 'repair_history_notes':[], 'explicit_test_outcome':[]}},
    ]
    parts=[
      {'repair_event_id':'log_170412002','log_number':'170412002','equipment_family':'BRD - 1526990 TURBO DOSE ASYST','part_number':'INA105KP','quantity':2,'text':'INA105KP','evidence_quote':'Replaced 2 INA105KP'},
      {'repair_event_id':'log_170412002','log_number':'170412002','equipment_family':'BRD - 1526990 TURBO DOSE ASYST','part_number':'56889','quantity':1,'text':'MSR 56889','evidence_quote':'MSR 56889'},
      {'repair_event_id':'log_140424002','log_number':'140424002','equipment_family':'PS - RCL1A-1D-W3 RACAL','part_number':'FDH038AN08A1','quantity':7,'text':'FDH038AN08A1','evidence_quote':'7 bad FDH038AN08A1 MOSFETs'},
    ]
    rmas=[{'repair_event_id':'log_170412002','log_number':'170412002','equipment_family':'BRD - 1526990 TURBO DOSE ASYST','rma_number':'35356','rma_normalized':'35356','evidence_quote':'RMA 35356','source_path':'/mnt/drl/x'}]
    pos=[{'repair_event_id':'log_170412002','log_number':'170412002','equipment_family':'BRD - 1526990 TURBO DOSE ASYST','customer_po':'8200632948','customer_po_normalized':'8200632948','evidence_quote':'Cust PO: 8200632948'}]
    orders=[{'repair_event_id':'log_170412002','log_number':'170412002','equipment_family':'BRD - 1526990 TURBO DOSE ASYST','supplier':'Mouser','order_ref':'MSR 56889','order_ref_normalized':'MSR56889','description':None,'manufacturer_pn':None,'quantity':1,'evidence_quote':'MSR 56889','source_path':'/mnt/drl/x'}]
    write_jsonl(full/'repair_events_v1_5_2.jsonl',events); write_jsonl(full/'replacement_mentions_v1_5_2.jsonl',parts); write_jsonl(full/'rma_refs_v1_5_2.jsonl',rmas); write_jsonl(full/'customer_po_refs_v1_5_2.jsonl',pos); write_jsonl(full/'procurement_refs_v1_5_2.jsonl',orders)
    (full/'drl_full_corpus_summary_v1_5_2.txt').write_text('synthetic')

    args=Namespace(file_index=str(fi),full_root=str(full),db=str(db),top=8,candidate_limit=800,json=False,self_check_warn_ms=250.0)
    counts=mod.build_db(args)
    assert counts.events==2 and counts.customer_pos==1 and counts.rmas==1 and counts.orders==1
    assert counts.replacements==3 and counts.procurement_only_replacements_excluded==1
    con=mod.connect_ro(db)
    c=mod.db_counts(con)
    assert c['repair_events']==2 and c['customer_po_refs']==1
    assert len(mod.search_db(con,'1526990'))>0
    assert any(r['item_type']=='customer_po' for r in mod.search_db(con,'8200632948'))
    assert any(r['item_type']=='rma' for r in mod.search_db(con,'35356'))
    assert any(r['item_type']=='order' for r in mod.search_db(con,'MSR56889'))
    assert any('FDH038AN08A1' in (r.get('primary_value') or '') for r in mod.search_db(con,'FDH038'))
    bad=con.execute("SELECT COUNT(*) FROM product_parts WHERE manufacturer_pn='56889'").fetchone()[0]
    assert bad==0, 'Mouser order token leaked into manufacturer PN product knowledge'
    con.close()

print('PASS: Nova DRL Full-Corpus Unified Knowledge Index v1.5.3 tests')
