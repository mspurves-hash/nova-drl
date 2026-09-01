#!/usr/bin/env python3
"""PRE-200 cached pipeline comparison v3.

No model calls. Uses the already-cached production + high-recall 8B vision outputs,
the v1.5.2 structured corpus, and (when present) the cached 14B reason-pass outputs.
It also scores a deterministic/lossless heading parser that never rewrites or drops
vision evidence. This tests whether the second LLM is even necessary for classification.
"""
from __future__ import annotations
import argparse, json, re, sys
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_TRUTH=ROOT/'benchmarks'/'pre200_25_event_ground_truth.json'
DEFAULT_VISION_ROOT=Path('/opt/nova-drl/output/pre200_25_event_model_benchmark')
DEFAULT_REASON_ROOT=Path('/opt/nova-drl/output/pre200_25_event_reason_benchmark')
DEFAULT_CORPUS=Path('/opt/nova-drl/output/drl_full_corpus_v1_5_2/repair_events_v1_5_2.jsonl')
DEFAULT_MODEL='qwen3-vl-drl:8b-q8-16k'
DEFAULT_REASON='qwen25-drl:14b-q6-16k'
CATS=('reported_failure','parts_replaced','part_references','repair_actions')


def safe_name(s):
    v=re.sub(r'[^A-Za-z0-9._-]+','_',s).strip('._'); return v or 'model'
def norm(s): return re.sub(r'[^a-z0-9]+',' ',str(s).lower()).strip()
def compact(s): return re.sub(r'[^a-z0-9]+','',str(s).lower())
def alias_hit(alias,text):
    a=norm(alias); t=norm(text)
    if not a:return False
    if compact(alias) and compact(alias) in compact(text):return True
    toks=[x for x in a.split() if len(x)>1 or x.isdigit()]
    if len(toks)>=2 and all(tok in t.split() for tok in toks):return True
    return a in t
def fact_hit(fact,text): return any(alias_hit(a,text) for a in fact.get('aliases',[fact.get('label','')]))
def flatten(obj,path=''):
    out=[]
    if isinstance(obj,dict):
        for k,v in obj.items():out.extend(flatten(v,f'{path}.{k}' if path else str(k)))
    elif isinstance(obj,list):
        for i,v in enumerate(obj):out.extend(flatten(v,f'{path}[{i}]'))
    elif obj is not None:out.append((path,str(obj)))
    return out

def heading_category(line):
    """Map a vision heading to one of the benchmark categories, or a non-scored bucket."""
    s=norm(line.rstrip(':').strip('*# '))
    # Order matters: explicit part/reference heading before generic parts heading.
    if ('part' in s and ('reference' in s or 'number' in s)):
        return 'part_references'
    if ('reported' in s and ('failure' in s or 'problem' in s or 'complaint' in s)) or s.startswith('basic reported problem'):
        return 'reported_failure'
    if 'part' in s and any(w in s for w in ('replaced','replacement','assemblies','component','used','installed','swapped','rebuilt')):
        return 'parts_replaced'
    if ('repair' in s and ('history' in s or 'action' in s or 'service' in s or 'technical' in s)) or s.startswith('other technical'):
        return 'repair_actions'
    if 'test' in s or 'outcome' in s:
        return 'test_outcome'
    if 'tracking' in s or 'order metadata' in s:
        return 'tracking'
    return None

def parse_sections(text):
    """Lossless section parser: classify by heading, preserve original lines verbatim."""
    buckets=defaultdict(list); current='unassigned'
    for raw in str(text).splitlines():
        line=raw.strip()
        if not line:continue
        # headings are usually colon terminated; also accept markdown heading lines
        hc=heading_category(line) if (line.endswith(':') or line.startswith('#') or (line.upper()==line and len(line)<120)) else None
        if hc:
            current=hc; continue
        # Strip only bullet marker for matching; do not rewrite evidence itself.
        item=re.sub(r'^\s*[-*•]+\s*','',line)
        buckets[current].append(item)
    return buckets

def section_text(buckets,cat):
    vals=list(buckets.get(cat,[]))
    # A PN can legitimately be written in the replacement bullet itself.
    if cat=='part_references': vals += list(buckets.get('parts_replaced',[]))
    return ' | '.join(vals)

def score_texts(truth,texts,sectioned=False):
    totals=defaultdict(lambda:{'n':0,'any':0,'field':0})
    for ev in truth['events']:
        txt=texts.get(ev['log'],'')
        buckets=parse_sections(txt) if sectioned else None
        for cat in CATS:
            ftxt=section_text(buckets,cat) if sectioned else ''
            for fact in ev.get(cat,[]):
                totals[cat]['n']+=1
                a=fact_hit(fact,txt); f=fact_hit(fact,ftxt) if sectioned else False
                totals[cat]['any']+=int(a); totals[cat]['field']+=int(f)
    return totals

def score_records(truth,records):
    needles={
      'reported_failure':('basic_reported_problem','reported_problem','reported_failure','customer_complaint'),
      'parts_replaced':('parts_replaced','part_replaced','replacement_part','replacement_parts'),
      'part_references':('parts_replaced','part_number','part_numbers','manufacturer_pn','reference_pn'),
      'repair_actions':('repair_history','repair_history_notes','repair_action','repair_actions'),
    }
    totals=defaultdict(lambda:{'n':0,'any':0,'field':0})
    for ev in truth['events']:
        rec=records.get(ev['log']); flat=flatten(rec) if rec else []
        alltext=' | '.join(v for _,v in flat)
        for cat in CATS:
            ctext=' | '.join(v for p,v in flat if any(n in p.lower() for n in needles[cat]))
            for fact in ev.get(cat,[]):
                totals[cat]['n']+=1
                a=fact_hit(fact,alltext) if rec else False; f=fact_hit(fact,ctext) if rec else False
                totals[cat]['any']+=int(a); totals[cat]['field']+=int(f)
    return totals

def load_raw(root,model,prompt,truth):
    d=root/safe_name(model)/prompt/'raw'; out={}
    for ev in truth['events']:
        p=d/f"{ev['log']}.txt"
        if p.exists():out[ev['log']]=p.read_text(encoding='utf-8',errors='replace')
    return out,d

def event_has_log(record,log):
    for _,v in flatten(record):
        if log==re.sub(r'\D','',v) or log in v:return True
    return False

def load_corpus(path,truth):
    wanted={e['log'] for e in truth['events']}; found={}
    if not path.exists():return found
    with path.open(encoding='utf-8') as f:
        for ln in f:
            if not ln.strip():continue
            try:r=json.loads(ln)
            except Exception:continue
            blob=json.dumps(r,ensure_ascii=False)
            for log in list(wanted):
                if log in blob and event_has_log(r,log):found[log]=r; wanted.remove(log)
            if not wanted:break
    return found

def load_reason(root,vision_model,vision_prompt,reason_model,truth):
    d=root/f'{safe_name(vision_model)}__{vision_prompt}__to__{safe_name(reason_model)}'/'structured'; out={}
    for ev in truth['events']:
        p=d/f"{ev['log']}.json"
        if p.exists():
            try:out[ev['log']]=json.loads(p.read_text(encoding='utf-8'))
            except Exception:pass
    return out,d

def summary(totals):
    n=sum(d['n'] for d in totals.values()); a=sum(d['any'] for d in totals.values()); f=sum(d['field'] for d in totals.values())
    return n,a,f,(100*a/n if n else 0),(100*f/n if n else 0)
def row(label,totals,has_field=True):
    n,a,f,pa,pf=summary(totals)
    fs=f'{f:3d}/{n:<3d} {pf:5.1f}%' if has_field else '      n/a'
    print(f'{label:36s} {a:3d}/{n:<3d} {pa:5.1f}%   {fs}')

def main():
    ap=argparse.ArgumentParser(description='Compare cached PRE-200 ingestion stages without new model calls')
    ap.add_argument('--truth',type=Path,default=DEFAULT_TRUTH)
    ap.add_argument('--vision-root',type=Path,default=DEFAULT_VISION_ROOT)
    ap.add_argument('--reason-root',type=Path,default=DEFAULT_REASON_ROOT)
    ap.add_argument('--corpus',type=Path,default=DEFAULT_CORPUS)
    ap.add_argument('--vision-model',default=DEFAULT_MODEL)
    ap.add_argument('--reason-model',default=DEFAULT_REASON)
    ap.add_argument('--show-category-detail',action='store_true')
    args=ap.parse_args()
    if not args.truth.exists():print('ERROR truth missing:',args.truth,file=sys.stderr);return 2
    truth=json.loads(args.truth.read_text(encoding='utf-8'))
    prod,pdir=load_raw(args.vision_root,args.vision_model,'production',truth)
    high,hdir=load_raw(args.vision_root,args.vision_model,'high-recall',truth)
    if len(prod)!=len(truth['events']) or len(high)!=len(truth['events']):
        print('ERROR: expected both cached 25-event vision runs.')
        print('production:',len(prod),'from',pdir);print('high-recall:',len(high),'from',hdir);return 2
    union={e['log']:prod.get(e['log'],'')+'\n\n'+high.get(e['log'],'') for e in truth['events']}
    corpus=load_corpus(args.corpus,truth)
    reason,rdir=load_reason(args.reason_root,args.vision_model,'high-recall',args.reason_model,truth)

    metrics=[]
    if corpus:metrics.append(('Existing v1.5.2 structured',score_records(truth,corpus),True))
    metrics.append(('8B production raw',score_texts(truth,prod,False),False))
    metrics.append(('8B production direct sections',score_texts(truth,prod,True),True))
    metrics.append(('8B high-recall raw',score_texts(truth,high,False),False))
    metrics.append(('8B high-recall direct sections',score_texts(truth,high,True),True))
    metrics.append(('8B production + high-recall union',score_texts(truth,union,False),False))
    metrics.append(('8B union direct sections',score_texts(truth,union,True),True))
    if reason:metrics.append(('14B reason on high-recall',score_records(truth,reason),True))

    print('PRE-200 CACHED PIPELINE COMPARISON v3')
    print('='*76)
    print('No model calls. Lossless/direct = heading parser only; evidence is never rewritten.')
    print()
    print(f"{'PIPELINE STAGE':36s} {'ANYWHERE':>15s}   {'RIGHT FIELD':>15s}")
    print('-'*76)
    for label,t,field in metrics:row(label,t,field)

    if args.show_category_detail:
        for label,t,field in metrics:
            print('\n'+label);print('-'*60)
            print(f"{'CATEGORY':22s} {'ANYWHERE':>15s} {'RIGHT FIELD':>15s}")
            for cat in CATS:
                d=t[cat];n=d['n'];a=d['any'];f=d['field']
                rf=f'{f}/{n} {100*f/n:5.1f}%' if field else 'n/a'
                print(f'{cat:22s} {a:3d}/{n:<3d} {100*a/n:5.1f}% {rf:>15s}')
    print('\nInterpretation:')
    print('  If direct sections retain nearly all raw recall, the 14B classifier is unnecessary for initial fielding.')
    print('  If the prompt union raises ANYWHERE materially, multi-prompt acquisition is complementary.')
    print('  Exact PN/reference recall remains an acquisition problem if part_references stays low before classification.')
    return 0

if __name__=='__main__':raise SystemExit(main())
