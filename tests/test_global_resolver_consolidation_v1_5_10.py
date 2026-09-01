#!/usr/bin/env python3
import importlib.util, inspect, json, sqlite3, sys, tempfile
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'tools' / 'nova_drl_unified_knowledge_engine_v1_5_3.py'
UI = ROOT / 'tools' / 'nova_drl_unified_knowledge_index_v1_5_10.py'

def load(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec); sys.modules[name]=mod; spec.loader.exec_module(mod); return mod
eng=load('v153_engine_for_v1510_global',ENGINE)
ui=load('v1510_global',UI)

resolver_source = '\n'.join([
    inspect.getsource(ui.resolve_base_product),
    inspect.getsource(ui._model_variant_of),
    inspect.getsource(ui._variant_related),
    inspect.getsource(ui._display_reference_pn),
    inspect.getsource(ui.aggregate_product_parts),
])
for forbidden in ('PRE-200','MR-J2S-40A','HCPL-7800','FDH038AN08A1','ISL6551IR','STTH1506TPI'):
    assert forbidden not in resolver_source, f'product-specific resolver patch leaked into production: {forbidden}'

def write_jsonl(path, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8') as f:
        for r in rows: f.write(json.dumps(r)+'\n')

def ev(eid,fam,problem='fault'):
    log=eid.replace('log_','')
    return {'repair_event_id':eid,'log_number':log,'equipment_family':fam,'top_folders':[fam],
            'primary_source_paths':[f'/x/{log}.jpg'],'supporting_source_paths':[],
            'facts':{'basic_reported_problem':[{'text':problem}], 'parts_replaced':[], 'repair_history_notes':[], 'explicit_test_outcome':[]}}

with tempfile.TemporaryDirectory() as td:
    t=Path(td); fi=t/'file.sqlite'; full=t/'full'; db=t/'knowledge.sqlite'
    con=sqlite3.connect(fi); con.executescript("""
      CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
      INSERT INTO meta VALUES('share_root','/mnt/drl');
      CREATE TABLE files(id INTEGER PRIMARY KEY,relative_path TEXT,filename TEXT,parent_path TEXT,extension TEXT,size INTEGER,mtime_ns INTEGER,detected_log TEXT,file_kind TEXT);
    """); con.commit(); con.close()

    events=[]; parts=[]; seq=[1]
    def add_family(fam,n):
        ids=[]
        for _ in range(n):
            eid=f"log_26{seq[0]:07d}"; seq[0]+=1; events.append(ev(eid,fam)); ids.append(eid)
        return ids

    pre_ids=[]
    pre_ids += add_family('PREALIGNER - PRE-200-B BROOKS',28)
    pre_ids += add_family('PREALIGNER - PRE-200 BROOKS',22)
    pre_ids += add_family('PREALIGNER - PRE-200B-CE BROOKS',9)
    pre_ids += add_family('PREALIGNER - PRE-200-CE BROOKS',3)

    abc_ids=[]
    abc_ids += add_family('BRD - ABC-100 OEM',7)
    abc_ids += add_family('BRD - ABC-100-B OEM',4)
    abc_ids += add_family('BRD - ABC-100B-CE OEM',3)
    abc_ids += add_family('BRD - ABC-100-CE OEM',2)
    add_family('BRD - ABC-1000 OEM',5)

    zx_ids=[]
    zx_ids += add_family('CNTL - ZX500 OEM',6)
    zx_ids += add_family('CNTL - ZX500-S7 OEM',4)
    zx_ids += add_family('CNTL - ZX500S-ALT OEM',2)

    kit_ids=add_family('DRV - QX-900 OEM',16)
    def part(eid,pn,text=None):
        fam=next(e['equipment_family'] for e in events if e['repair_event_id']==eid)
        parts.append({'repair_event_id':eid,'log_number':eid.replace('log_',''),'equipment_family':fam,
                      'part_number':pn,'quantity':1,'text':text or f'Replaced {pn}','evidence_quote':text or f'Replaced {pn}'})
    for eid in kit_ids[:8]: part(eid,'7300')
    for eid in kit_ids[8:11]: part(eid,'ABC7300')
    for eid in kit_ids[11:13]: part(eid,'7300A')
    for eid in kit_ids[:4]: part(eid,'6622')
    for eid in kit_ids[4:10]: part(eid,'ZX6622IR')
    for eid in kit_ids[10:13]: part(eid,'ZX 6622IR')
    for eid in kit_ids[13:15]: part(eid,'ZY6622IR')
    for eid in kit_ids[:7]: part(eid,'45AB07C1')
    for eid in kit_ids[7:12]: part(eid,'FDX045AB07C1')
    for eid in kit_ids[12:14]: part(eid,'O45AB07C1')

    write_jsonl(full/'repair_events_v1_5_2.jsonl',events)
    write_jsonl(full/'replacement_mentions_v1_5_2.jsonl',parts)
    for name in ('rma_refs','customer_po_refs','procurement_refs'): write_jsonl(full/f'{name}_v1_5_2.jsonl',[])
    (full/'drl_full_corpus_summary_v1_5_2.txt').write_text('synthetic')
    args=Namespace(file_index=str(fi),full_root=str(full),db=str(db),top=8,candidate_limit=800,json=False,self_check_warn_ms=250.0)
    eng.build_db(args)
    con=eng.connect_ro(db); ua=Namespace(db=str(db),top=8,candidate_limit=800,reports_dir=str(t/'reports'),report_port=8765,printer=None)

    r=ui.resolve_base_product(con,'PRE-200')
    assert r and r['base_part_number']=='PRE-200', r
    assert len(ui.product_event_rows(con,r['families']))==62, r
    assert set(r['model_variants'])=={'PRE-200','PRE-200-B','PRE-200B-CE','PRE-200-CE'}, r

    r=ui.resolve_base_product(con,'ABC-100-B')
    assert r and r['base_part_number']=='ABC-100', r
    assert len(ui.product_event_rows(con,r['families']))==16, r
    assert 'ABC-1000' not in r['model_variants'], r

    r=ui.resolve_base_product(con,'ZX500S-ALT')
    assert r and r['base_part_number']=='ZX500', r
    assert len(ui.product_event_rows(con,r['families']))==12, r

    _, groups, _=ui.search_report(con,'QX-900',ua)
    got={x['payload']['pn']:x['payload']['repairs'] for x in dict(groups)['PARTS REPLACED']}
    assert got.get('7300')==13, got
    assert got.get('ZX6622IR')==15, got
    assert got.get('FDX045AB07C1')==14, got
    assert '6622' not in got and '45AB07C1' not in got, got
    con.close()

print('PASS: Nova DRL v1.5.10 global resolver consolidation tests')
