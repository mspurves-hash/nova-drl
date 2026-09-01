#!/usr/bin/env python3
import argparse, json, re, sys
from pathlib import Path
from collections import defaultdict

DEFAULT_CORPUS = Path('/opt/nova-drl/output/drl_full_corpus_v1_5_2/repair_events_v1_5_2.jsonl')
DEFAULT_TRUTH = Path(__file__).resolve().parents[1] / 'benchmarks' / 'pre200_25_event_ground_truth.json'

CATS = {
    'reported_failure': ('basic_reported_problem','reported_problem','reported_failure','customer_complaint'),
    'parts_replaced': ('parts_replaced','part_replaced','replacement_part','replacement_parts'),
    'part_references': ('parts_replaced','part_number','part_numbers','manufacturer_pn','reference_pn'),
    'repair_actions': ('repair_history','repair_history_notes','repair_action','repair_actions'),
}

def norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s).lower()).strip()

def compact(s):
    return re.sub(r'[^a-z0-9]+', '', str(s).lower())

def flatten(obj, path=''):
    out=[]
    if isinstance(obj, dict):
        for k,v in obj.items():
            p=f'{path}.{k}' if path else str(k)
            out.extend(flatten(v,p))
    elif isinstance(obj, list):
        for i,v in enumerate(obj): out.extend(flatten(v,f'{path}[{i}]'))
    elif obj is not None:
        out.append((path,str(obj)))
    return out

def alias_hit(alias, text):
    a=norm(alias); t=norm(text)
    if not a: return False
    if compact(alias) and compact(alias) in compact(text): return True
    toks=[x for x in a.split() if len(x)>1 or x.isdigit()]
    if len(toks)>=2 and all(tok in t.split() for tok in toks): return True
    return a in t

def fact_hit(fact, text):
    return any(alias_hit(a,text) for a in fact.get('aliases',[fact.get('label','')]))

def event_has_log(record, log):
    for p,v in flatten(record):
        if log == re.sub(r'\D','',v):
            return True
        if log in v:
            return True
    return False

def category_text(flat, cat):
    needles=CATS[cat]
    vals=[]
    for p,v in flat:
        pl=p.lower()
        if any(n in pl for n in needles): vals.append(v)
    return ' | '.join(vals)

def load_records(path):
    with path.open(encoding='utf-8') as f:
        for ln in f:
            if ln.strip():
                try: yield json.loads(ln)
                except json.JSONDecodeError: pass

def main():
    ap=argparse.ArgumentParser(description='PRE-200 25-event extraction recall bake-off')
    ap.add_argument('--corpus', type=Path, default=DEFAULT_CORPUS)
    ap.add_argument('--truth', type=Path, default=DEFAULT_TRUTH)
    ap.add_argument('--show-misses', action='store_true')
    args=ap.parse_args()
    truth=json.loads(args.truth.read_text(encoding='utf-8'))
    logs=[e['log'] for e in truth['events']]
    if not args.corpus.exists():
        print(f'ERROR: corpus not found: {args.corpus}', file=sys.stderr); return 2
    found={}
    wanted=set(logs)
    for rec in load_records(args.corpus):
        blob=json.dumps(rec,ensure_ascii=False)
        for log in list(wanted):
            if log in blob and event_has_log(rec,log):
                found[log]=rec; wanted.remove(log)
        if not wanted: break
    print('PRE-200 25-EVENT MULTIMODAL EXTRACTION BAKE-OFF')
    print('='*58)
    print(f'Corpus: {args.corpus}')
    print(f'Expected unique repair events: {len(logs)}')
    print(f'Events found in corpus:        {len(found)}')
    if wanted: print('Missing logs:', ', '.join(sorted(wanted)))
    print()

    totals=defaultdict(lambda:{'n':0,'any':0,'field':0})
    misses=[]
    for ev in truth['events']:
        rec=found.get(ev['log'])
        flat=flatten(rec) if rec else []
        alltext=' | '.join(v for _,v in flat)
        for cat in CATS:
            ctext=category_text(flat,cat)
            for fact in ev.get(cat,[]):
                totals[cat]['n'] += 1
                anywhere=fact_hit(fact,alltext) if rec else False
                infield=fact_hit(fact,ctext) if rec else False
                totals[cat]['any'] += int(anywhere)
                totals[cat]['field'] += int(infield)
                if not infield:
                    misses.append((ev['page'],ev['log'],cat,fact['label'],anywhere))

    print(f"{'CATEGORY':22s} {'SOURCE':>7s} {'ANYWHERE':>12s} {'RIGHT FIELD':>13s}")
    print('-'*58)
    for cat in CATS:
        d=totals[cat]; n=d['n']
        pa=100*d['any']/n if n else 0; pf=100*d['field']/n if n else 0
        print(f"{cat:22s} {n:7d} {d['any']:4d}/{n:<4d} {pa:5.1f}%   {d['field']:4d}/{n:<4d} {pf:5.1f}%")
    n=sum(v['n'] for v in totals.values()); a=sum(v['any'] for v in totals.values()); f=sum(v['field'] for v in totals.values())
    print('-'*58)
    print(f"{'ALL USEFUL FACTS':22s} {n:7d} {a:4d}/{n:<4d} {100*a/n:5.1f}%   {f:4d}/{n:<4d} {100*f/n:5.1f}%")
    print()
    print('Interpretation:')
    print('  ANYWHERE   = the structured event contains evidence of the fact somewhere.')
    print('  RIGHT FIELD = the fact landed in the field Nova needs for direct aggregation.')
    print('  A low ANYWHERE score points strongly to acquisition/vision-model loss.')
    print('  A much higher ANYWHERE than RIGHT FIELD points to schema/classification loss.')
    if args.show_misses:
        print('\nMISSES / MISCLASSIFICATIONS')
        print('-'*58)
        for page,log,cat,label,anywhere in misses:
            state='MISCLASSIFIED (seen elsewhere)' if anywhere else 'MISSING ENTIRELY'
            print(f'p{page:02d} {log} {cat:20s} {state:28s} {label}')
    return 0

if __name__=='__main__': raise SystemExit(main())
