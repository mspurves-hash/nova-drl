#!/usr/bin/env python3
"""Run the existing Nova text reason/classification stage on cached vision evidence.

This isolates schema/classification loss after a chosen vision-model/prompt run.
"""
from __future__ import annotations
import argparse, json, re, sys, time, urllib.request
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_TRUTH=ROOT/'benchmarks'/'pre200_25_event_ground_truth.json'
DEFAULT_VISION_ROOT=Path('/opt/nova-drl/output/pre200_25_event_model_benchmark')
DEFAULT_OUT=Path('/opt/nova-drl/output/pre200_25_event_reason_benchmark')
DEFAULT_URL='http://127.0.0.1:11434/api/generate'
DEFAULT_REASON='qwen25-drl:14b-q6-16k'
CATS={
 'reported_failure':('basic_reported_problem','reported_problem','reported_failure','customer_complaint'),
 'parts_replaced':('parts_replaced','part_replaced','replacement_part','replacement_parts'),
 'part_references':('parts_replaced','part_number','part_numbers','manufacturer_pn','reference_pn'),
 'repair_actions':('repair_history','repair_history_notes','repair_action','repair_actions'),
}
EVENT_PROMPT=r'''Convert ONE DRL repair event into a concise structured Traveler-history record.
Evidence comes from one or more PRIMARY Line Cards/Travelers for the SAME event.
Return JSON only with this exact top-level shape:
{
  "basic_reported_problem": [{"text":"...","evidence_quote":"..."}],
  "parts_replaced": [{"text":"...","part_number":null,"quantity":null,"evidence_quote":"..."}],
  "repair_history_notes": [{"text":"...","evidence_quote":"..."}],
  "explicit_test_outcome": [{"text":"...","evidence_quote":"..."}],
  "rma_numbers": [{"value":"...","evidence_quote":"..."}],
  "customer_po_numbers": [{"value":"...","evidence_quote":"..."}],
  "procurement_refs": [{"order_ref":"...","supplier":null,"description":null,"manufacturer_pn":null,"quantity":null,"evidence_quote":"..."}]
}

Standing 80/20 rules:
- Travelers are primarily parts/repair-history evidence. Do not manufacture detailed
  diagnostics, procedures, calibration, or testing when absent.
- Capture actual replaced/installed/used/rebuilt components and assemblies.
- part_number is populated only when a manufacturer/component PN/string is actually present in the evidence.
- DGK/MSR/NWK/DSK order references are procurement references, NOT manufacturer PNs.
- RMA, Customer PO, and procurement/order refs are literal tracking fields: the value MUST be visibly supported by its own evidence_quote.
- quantity is an integer only when explicitly stated; otherwise null.
- evidence_quote must be copied from supplied evidence.
- Do not convert administrative/shop routing into technical repair facts.
- If a category has no useful evidence, return an empty list.
'''

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
        for k,v in obj.items(): out.extend(flatten(v,f'{path}.{k}' if path else str(k)))
    elif isinstance(obj,list):
        for i,v in enumerate(obj): out.extend(flatten(v,f'{path}[{i}]'))
    elif obj is not None: out.append((path,str(obj)))
    return out
def category_text(flat,cat):
    return ' | '.join(v for p,v in flat if any(n in p.lower() for n in CATS[cat]))
def parse_json_response(text):
    s=text.strip()
    if s.startswith('```'):
        s=re.sub(r'^```(?:json)?\s*','',s,flags=re.I); s=re.sub(r'\s*```$','',s)
    try:return json.loads(s)
    except json.JSONDecodeError:
        a,b=s.find('{'),s.rfind('}')
        if a>=0 and b>a:return json.loads(s[a:b+1])
        raise
def call(url,model,prompt,num_ctx,num_predict,timeout):
    payload={'model':model,'prompt':prompt,'stream':False,'options':{'temperature':0,'num_ctx':num_ctx,'num_predict':num_predict}}
    req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode())

def main():
    ap=argparse.ArgumentParser(description='Score Nova reason/classification pass using cached PRE-200 vision evidence')
    ap.add_argument('--vision-model',required=True)
    ap.add_argument('--vision-prompt',choices=['production','high-recall'],required=True)
    ap.add_argument('--reason-model',default=DEFAULT_REASON)
    ap.add_argument('--vision-root',type=Path,default=DEFAULT_VISION_ROOT)
    ap.add_argument('--truth',type=Path,default=DEFAULT_TRUTH)
    ap.add_argument('--output-root',type=Path,default=DEFAULT_OUT)
    ap.add_argument('--ollama-url',default=DEFAULT_URL)
    ap.add_argument('--num-ctx',type=int,default=16384)
    ap.add_argument('--num-predict',type=int,default=3072)
    ap.add_argument('--timeout',type=int,default=900)
    ap.add_argument('--force',action='store_true')
    ap.add_argument('--show-misses',action='store_true')
    args=ap.parse_args()
    truth=json.loads(args.truth.read_text(encoding='utf-8'))
    vdir=args.vision_root/safe_name(args.vision_model)/args.vision_prompt/'raw'
    if not vdir.exists(): print('ERROR: vision cache not found:',vdir,file=sys.stderr); return 2
    run_dir=args.output_root/f'{safe_name(args.vision_model)}__{args.vision_prompt}__to__{safe_name(args.reason_model)}'
    outdir=run_dir/'structured'; outdir.mkdir(parents=True,exist_ok=True)
    records={}; errors=[]
    print('PRE-200 REASON/CLASSIFICATION BENCHMARK'); print('='*64)
    print('Vision source:',args.vision_model,'/',args.vision_prompt); print('Reason model:',args.reason_model); print()
    for i,e in enumerate(truth['events'],1):
        log=e['log']; src=vdir/f'{log}.txt'; dst=outdir/f'{log}.json'
        if not src.exists(): errors.append((log,'missing vision evidence')); print(f'[{i:02d}/25] {log} ERROR missing vision evidence'); continue
        if dst.exists() and not args.force:
            try: records[log]=json.loads(dst.read_text(encoding='utf-8')); print(f'[{i:02d}/25] {log} cached'); continue
            except Exception: pass
        evidence=src.read_text(encoding='utf-8',errors='replace')
        payload=EVENT_PROMPT+f'\n\nDRL LOG: {log}\n\nVISION EVIDENCE:\n'+evidence
        t0=time.time(); raw=''
        try:
            data=call(args.ollama_url,args.reason_model,payload,args.num_ctx,args.num_predict,args.timeout); raw=str(data.get('response') or '')
            try: obj=parse_json_response(raw)
            except Exception:
                fix=payload+'\n\nPrevious response was invalid. Return ONLY valid JSON in the exact requested schema.'
                data=call(args.ollama_url,args.reason_model,fix,args.num_ctx,args.num_predict,args.timeout); raw=str(data.get('response') or ''); obj=parse_json_response(raw)
            records[log]=obj; dst.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
            print(f'[{i:02d}/25] {log} {time.time()-t0:7.1f}s')
        except Exception as exc:
            errors.append((log,str(exc))); (outdir/f'{log}.error.txt').write_text(raw+'\n\nERROR: '+str(exc),encoding='utf-8'); print(f'[{i:02d}/25] {log} ERROR {exc}')
    totals=defaultdict(lambda:{'n':0,'any':0,'field':0}); misses=[]
    for e in truth['events']:
        rec=records.get(e['log']); flat=flatten(rec) if rec else []; alltext=' | '.join(v for _,v in flat)
        for cat in CATS:
            ctext=category_text(flat,cat)
            for fact in e.get(cat,[]):
                totals[cat]['n']+=1; anywhere=fact_hit(fact,alltext) if rec else False; field=fact_hit(fact,ctext) if rec else False
                totals[cat]['any']+=int(anywhere); totals[cat]['field']+=int(field)
                if not field: misses.append((e['page'],e['log'],cat,fact['label'],anywhere))
    print('\nSTRUCTURED REASON-PASS RECALL'); print('-'*58)
    print(f"{'CATEGORY':22s} {'SOURCE':>7s} {'ANYWHERE':>12s} {'RIGHT FIELD':>13s}"); print('-'*58)
    for cat in CATS:
        d=totals[cat]; n=d['n']; a=d['any']; f=d['field']; print(f'{cat:22s} {n:7d} {a:4d}/{n:<4d} {100*a/n if n else 0:5.1f}%   {f:4d}/{n:<4d} {100*f/n if n else 0:5.1f}%')
    n=sum(d['n'] for d in totals.values()); a=sum(d['any'] for d in totals.values()); f=sum(d['field'] for d in totals.values())
    print('-'*58); print(f"{'ALL USEFUL FACTS':22s} {n:7d} {a:4d}/{n:<4d} {100*a/n:5.1f}%   {f:4d}/{n:<4d} {100*f/n:5.1f}%")
    summary={'vision_model':args.vision_model,'vision_prompt':args.vision_prompt,'reason_model':args.reason_model,'categories':totals,'all':{'n':n,'any':a,'field':f,'any_pct':round(100*a/n,2),'field_pct':round(100*f/n,2)},'errors':errors}
    (run_dir/'summary.json').write_text(json.dumps(summary,indent=2,default=dict),encoding='utf-8'); print('Summary:',run_dir/'summary.json')
    if args.show_misses:
        print('\nMISSES / MISCLASSIFICATIONS'); print('-'*64)
        for page,log,cat,label,anywhere in misses: print(f'p{page:02d} {log} {cat:20s} {"MISCLASSIFIED" if anywhere else "MISSING" :16s} {label}')
    return 2 if errors else 0
if __name__=='__main__': raise SystemExit(main())
