#!/usr/bin/env python3
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_parts_scale_out_planner_v1_7_1.py"

def ev(eid, fam):
    return {"repair_event_id": eid, "equipment_family": fam}

def rep(eid, pn, qty=1, **kw):
    x = {"repair_event_id": eid, "part_number": pn, "description": "replaced part", "quantity": qty}
    x.update(kw)
    return x

def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        ep = td/'events.jsonl'; rp = td/'repl.jsonl'; out = td/'out'
        events=[]; repl=[]
        # A 30 events, B 18, C 12, D 8, E 6, F 5, G 2
        specs=[('A',30),('B',18),('C',12),('D',8),('E',6),('F',5),('G',2)]
        n=0
        for fam,count in specs:
            for i in range(count):
                eid=f'e{n}'; n+=1; events.append(ev(eid,fam)); repl.append(rep(eid,f'{fam}-PN'))
        # explicit ineligible row should be excluded
        events.append(ev('bad','A')); repl.append(rep('bad','A-BAD', product_part_eligible=False))
        ep.write_text(''.join(json.dumps(x)+'\n' for x in events))
        rp.write_text(''.join(json.dumps(x)+'\n' for x in repl))
        run=subprocess.run([sys.executable,str(TARGET),'--events',str(ep),'--replacements',str(rp),'--output-root',str(out),'--benchmark-family','A','--focus-min-events','10','--focus-max-families','40'],capture_output=True,text=True)
        assert run.returncode==0,run.stdout+'\n'+run.stderr
        ranked=[json.loads(x) for x in (out/'clean_family_volume_v1_7_1.jsonl').read_text().splitlines() if x.strip()]
        focus=[json.loads(x) for x in (out/'operational_focus_v1_7_1.jsonl').read_text().splitlines() if x.strip()]
        sample=[json.loads(x) for x in (out/'generalization_sample_v1_7_1.jsonl').read_text().splitlines() if x.strip()]
        manifest=json.loads((out/'parts_scale_out_manifest_v1_7_1.json').read_text())
        assert [x['equipment_family'] for x in ranked]==['A','B','C','D','E','F','G']
        assert ranked[0]['replacement_repair_events']==30
        assert [x['equipment_family'] for x in focus]==['A','B','C']
        assert all(x['equipment_family']!='A' for x in sample)
        assert all(x['replacement_repair_events']>=5 for x in sample)
        assert manifest['counts']['usable_replacement_rows']==81
        assert manifest['counts']['exclusions']['product_part_eligible=false']==1
        assert manifest['policy']['literal_80pct_is_mandate'] is False
        # plan-only writes nothing
        po=td/'plan'
        run2=subprocess.run([sys.executable,str(TARGET),'--events',str(ep),'--replacements',str(rp),'--output-root',str(po),'--plan-only'],capture_output=True,text=True)
        assert run2.returncode==0 and not po.exists() and 'PLAN ONLY' in run2.stdout
    print('PASS: Nova DRL Clean Parts Scale-Out Planner v1.7.1 tests')

if __name__=='__main__': main()
