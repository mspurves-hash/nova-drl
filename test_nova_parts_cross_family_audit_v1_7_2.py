#!/usr/bin/env python3
import json, subprocess, sys, tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
TARGET=HERE/'nova_parts_cross_family_audit_v1_7_2.py'

def ev(eid,fam): return {'repair_event_id':eid,'equipment_family':fam}
def rep(eid,pn=None,desc='part',qty=1): return {'repair_event_id':eid,'part_number':pn,'description':desc,'raw_quote':desc,'quantity':qty}

def main():
  with tempfile.TemporaryDirectory() as td:
    td=Path(td); ep=td/'e.jsonl'; rp=td/'r.jsonl'; out=td/'o'
    events=[ev('a1','A'),ev('a2','A'),ev('a3','A'),ev('a4','A'),ev('b1','B'),ev('b2','B'),ev('b3','B')]
    rows=[
      rep('a1','ABC-1234','ABC-1234'), rep('a2','ABC-1234','ABC-1234'),
      rep('a3','ABC1234','ABC1234'),       # format duplicate, safe
      rep('a4','ABC-2234','ABC-2234'),    # numeric conflict, must not pair
      rep('b1','FDH038AN08A1','FDH038AN08A1'), rep('b2','FDH038AN08A1','FDH038AN08A1'),
      rep('b3','38AN08A1','38AN08A1'),     # containment; numeric signature equal after int norm
    ]
    ep.write_text(''.join(json.dumps(x)+'\n' for x in events)); rp.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    run=subprocess.run([sys.executable,str(TARGET),'--events',str(ep),'--replacements',str(rp),'--output-root',str(out),'--family','A','--family','B'],capture_output=True,text=True)
    assert run.returncode==0,run.stdout+'\n'+run.stderr
    pairs=[json.loads(x) for x in (out/'safe_alias_pair_proposals_v1_7_2.jsonl').read_text().splitlines() if x.strip()]
    labels={(p['left_label'],p['right_label']) for p in pairs} | {(p['right_label'],p['left_label']) for p in pairs}
    assert ('ABC-1234','ABC1234') in labels
    assert ('ABC-1234','ABC-2234') not in labels
    assert ('FDH038AN08A1','38AN08A1') in labels
    metrics=[json.loads(x) for x in (out/'family_candidate_metrics_v1_7_2.jsonl').read_text().splitlines() if x.strip()]
    assert len(metrics)==2
    ma=next(x for x in metrics if x['family']=='A'); assert ma['replacement_repair_events']==4 and ma['recurring_2plus_candidates']==1
    # plan-only no writes
    po=td/'plan'; run2=subprocess.run([sys.executable,str(TARGET),'--events',str(ep),'--replacements',str(rp),'--output-root',str(po),'--family','A','--plan-only'],capture_output=True,text=True)
    assert run2.returncode==0 and not po.exists() and 'PLAN ONLY' in run2.stdout
  print('PASS: Nova DRL Cross-Family Parts Generalization Audit v1.7.2 tests')
if __name__=='__main__': main()
