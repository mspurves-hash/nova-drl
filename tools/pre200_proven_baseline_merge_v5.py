#!/usr/bin/env python3
"""PRE-200 v5 cached proven-baseline merge benchmark.

No model calls. Compares the frozen v1.3.5.1/v1.3.6.1 historical 8B baseline
against additive combinations using already-cached high-recall and PN-focused outputs.
Nothing rewrites or deletes baseline evidence.
"""
from __future__ import annotations
import argparse, json, re, sys
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_TRUTH=ROOT/'config'/'pre200_25_event_ground_truth.json'
MODEL='qwen3-vl-drl_8b-q8-16k'
HIST_ROOT=Path('/opt/nova-drl/output/pre200_25_event_historical_replay_v4')/MODEL
VISION_ROOT=Path('/opt/nova-drl/output/pre200_25_event_model_benchmark')/MODEL/'high-recall'/'raw'
PN_ROOT=Path('/opt/nova-drl/output/pre200_25_event_pn_focus_benchmark')/MODEL/'raw'
CATS=('reported_failure','parts_replaced','part_references','repair_actions')
COMPAT={
 'reported_failure': {'diagnostic_or_failure','repair_or_service','other'},
 'parts_replaced': {'component_or_part','repair_or_service','part_number_or_identifier','other'},
 'part_references': {'part_number_or_identifier','component_or_part','other'},
 'repair_actions': {'repair_or_service','testing_or_process','other'},
}

def norm(s):return re.sub(r'[^a-z0-9]+',' ',str(s).lower()).strip()
def compact(s):return re.sub(r'[^a-z0-9]+','',str(s).lower())
def alias_hit(alias,text):
    a=norm(alias);t=norm(text)
    if not a:return False
    ca=compact(alias);ct=compact(text)
    if ca and ca in ct:return True
    toks=[x for x in a.split() if len(x)>1 or x.isdigit()]
    return (len(toks)>=2 and all(x in t.split() for x in toks)) or a in t
def fact_hit(fact,text):return any(alias_hit(a,text) for a in fact.get('aliases',[fact.get('label','')]))

def heading_category(line):
    s=norm(line.rstrip(':').strip('*# '))
    if ('part' in s and ('reference' in s or 'number' in s)):return 'part_references'
    if ('reported' in s and ('failure' in s or 'problem' in s or 'complaint' in s)) or s.startswith('basic reported problem'):return 'reported_failure'
    if 'part' in s and any(w in s for w in ('replaced','replacement','assemblies','component','used','installed','swapped','rebuilt')):return 'parts_replaced'
    if ('repair' in s and ('history' in s or 'action' in s or 'service' in s or 'technical' in s)) or s.startswith('other technical'):return 'repair_actions'
    return None

def parse_sections(text):
    buckets=defaultdict(list);cur='unassigned'
    for raw in str(text).splitlines():
        line=raw.strip()
        if not line:continue
        hc=heading_category(line) if (line.endswith(':') or line.startswith('#') or (line.upper()==line and len(line)<120)) else None
        if hc:cur=hc;continue
        buckets[cur].append(re.sub(r'^\s*[-*•]+\s*','',line))
    return buckets

def load_texts(root,truth,suffix='.txt'):
    out={}
    for e in truth['events']:
        p=root/f"{e['log']}{suffix}"
        if p.exists():out[e['log']]=p.read_text(encoding='utf-8',errors='replace')
    return out

def load_prospector(root,truth):
    out={}
    d=root/'prospector'
    for e in truth['events']:
        p=d/f"{e['log']}.json"
        if not p.exists():continue
        try:obj=json.loads(p.read_text(encoding='utf-8'))
        except Exception:continue
        rows=[]
        for r in obj.get('candidates') or []:
            if isinstance(r,dict) and str(r.get('raw_quote') or '').strip():rows.append(r)
        out[e['log']]=rows
    return out

def score(truth, raw_texts=None, direct_texts=None, prospector=None, pn_texts=None, merge_all=False):
    totals=defaultdict(lambda:{'n':0,'any':0,'field':0})
    for e in truth['events']:
        log=e['log']; raw=(raw_texts or {}).get(log,''); direct=(direct_texts or {}).get(log,''); pn=(pn_texts or {}).get(log,'')
        rows=(prospector or {}).get(log,[])
        direct_b=parse_sections(direct) if direct else {}
        row_all=' | '.join(str(r.get('raw_quote') or '') for r in rows)
        any_text=' | '.join(x for x in (raw,direct,row_all,pn) if x)
        for cat in CATS:
            direct_field=' | '.join(direct_b.get(cat,[])) if direct_b else ''
            if cat=='part_references' and direct_b:direct_field+=' | '+' | '.join(direct_b.get('parts_replaced',[]))
            row_field=' | '.join(str(r.get('raw_quote') or '') for r in rows if r.get('kind') in COMPAT[cat])
            pn_field=pn if cat=='part_references' else ''
            field_text=' | '.join(x for x in (row_field,direct_field,pn_field) if x)
            for f in e.get(cat,[]):
                totals[cat]['n']+=1
                totals[cat]['any']+=int(fact_hit(f,any_text))
                totals[cat]['field']+=int(fact_hit(f,field_text))
    return totals

def sums(t):
    n=sum(x['n'] for x in t.values());a=sum(x['any'] for x in t.values());f=sum(x['field'] for x in t.values())
    return n,a,f,100*a/n if n else 0,100*f/n if n else 0

def printrow(label,t):
    n,a,f,pa,pf=sums(t);print(f'{label:44s} {a:3d}/{n:<3d} {pa:5.1f}%   {f:3d}/{n:<3d} {pf:5.1f}%')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--truth',type=Path,default=DEFAULT_TRUTH);ap.add_argument('--detail',action='store_true');args=ap.parse_args()
    truth=json.loads(args.truth.read_text(encoding='utf-8'))
    hist_raw=load_texts(HIST_ROOT/'transcription',truth)
    hist_pro=load_prospector(HIST_ROOT,truth)
    high=load_texts(VISION_ROOT,truth)
    pn=load_texts(PN_ROOT,truth)
    expected=len(truth['events'])
    missing=[]
    for name,obj in [('historical transcription',hist_raw),('historical prospector',hist_pro),('high-recall raw',high),('PN-focus raw',pn)]:
        if len(obj)!=expected:missing.append((name,len(obj)))
    if missing:
        print('ERROR: cached benchmark outputs missing:',file=sys.stderr)
        for n,c in missing:print(f'  {n}: {c}/{expected}',file=sys.stderr)
        return 2

    stages=[
      ('Frozen v1.3.5.1 raw + v1.3.6.1 prospector', score(truth,raw_texts=hist_raw,prospector=hist_pro)),
      ('Frozen baseline + PN-focused additive pass', score(truth,raw_texts=hist_raw,prospector=hist_pro,pn_texts=pn)),
      ('High-recall direct + PN-focused pass', score(truth,direct_texts=high,pn_texts=pn)),
      ('Frozen baseline + high-recall + PN additive', score(truth,raw_texts=hist_raw,direct_texts=high,prospector=hist_pro,pn_texts=pn)),
    ]
    print('PRE-200 PROVEN-BASELINE ADDITIVE MERGE BENCHMARK v5')
    print('='*86)
    print('No model calls. No evidence rewriting. Every candidate path is additive only.')
    print(f"{'PIPELINE':44s} {'ANYWHERE':>15s}   {'FIELD-COMPATIBLE':>17s}")
    print('-'*86)
    for label,t in stages:printrow(label,t)
    if args.detail:
        for label,t in stages:
            print('\n'+label);print('-'*76)
            for c in CATS:
                d=t[c];n=d['n'];a=d['any'];f=d['field']
                print(f'{c:22s} any {a:3d}/{n:<3d} {100*a/n:5.1f}%   field {f:3d}/{n:<3d} {100*f/n:5.1f}%')
    print('\nDecision rule:')
    print('  Keep the frozen historical baseline unless an additive candidate materially improves it.')
    print('  A new pass may add evidence; it may never delete or replace frozen baseline evidence.')
    print('  PN-focus must still pass a separate precision audit before global deployment.')
    return 0
if __name__=='__main__':raise SystemExit(main())
