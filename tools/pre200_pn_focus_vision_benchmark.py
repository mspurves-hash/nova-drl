#!/usr/bin/env python3
"""Focused exact PN/reference-number vision benchmark for the same 25 PRE-200 pages.

This is intentionally a narrow second visual pass: do not summarize repair history; hunt
only visible manufacturer/component/reference strings and their nearby component context.
"""
from __future__ import annotations
import argparse, base64, hashlib, json, re, time, urllib.request
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_TRUTH=ROOT/'benchmarks'/'pre200_25_event_ground_truth.json'
DEFAULT_IMAGES=ROOT/'benchmarks'/'pre200_25_event_images'
DEFAULT_OUT=Path('/opt/nova-drl/output/pre200_25_event_pn_focus_benchmark')
DEFAULT_URL='http://127.0.0.1:11434/api/generate'
DEFAULT_MODEL='qwen3-vl-drl:8b-q8-16k'
PROMPT=r'''Read this ONE DRL Traveler / Line Card as a PART / REFERENCE NUMBER HUNTER.

Do not summarize the repair. Your only job is to preserve visible component/manufacturer/reference
identifiers that may be useful for parts intelligence.

Look carefully in ALL handwritten and typed technical areas, especially replacement notes, ordered-parts
areas, margins, and text next to motors, encoders, ICs, boards, belts, sensors, CCDs, LEDs, solenoids,
and other components.

Return plain text, one candidate per line, in this form when possible:
PART/REFERENCE: <string> | CONTEXT: <nearby component/action text>

Rules:
- Preserve alphanumeric strings and punctuation as written: examples of shapes include HEDS-5540-A01,
  SN74LS14N, 26LS31PC, AM26LS31CN. These are examples of FORMAT only, not answers for this page.
- If one character is uncertain, retain the rest and mark only that character with ? rather than dropping the candidate.
- Do not turn DRL log numbers, RMA, Customer PO, dates, phone numbers, prices, DGK/MSR/NWK/DSK order refs,
  or serial numbers into manufacturer PNs.
- Do not invent a PN from the component name.
- If no plausible manufacturer/component/reference string is visible, return: NONE VISIBLE.
'''

def safe_name(s):
    v=re.sub(r'[^A-Za-z0-9._-]+','_',s).strip('._');return v or 'model'
def norm(s):return re.sub(r'[^a-z0-9]+',' ',str(s).lower()).strip()
def compact(s):return re.sub(r'[^a-z0-9]+','',str(s).lower())
def alias_hit(alias,text):
    a=norm(alias);t=norm(text)
    if not a:return False
    if compact(alias) and compact(alias) in compact(text):return True
    toks=[x for x in a.split() if len(x)>1 or x.isdigit()]
    return len(toks)>=2 and all(x in t.split() for x in toks) or a in t
def fact_hit(fact,text):return any(alias_hit(a,text) for a in fact.get('aliases',[fact.get('label','')]))
def image_b64(p):return base64.b64encode(p.read_bytes()).decode('ascii')
def call(url,model,prompt,image,num_ctx,num_predict,timeout):
    payload={'model':model,'prompt':prompt,'stream':False,'images':[image_b64(image)],'options':{'temperature':0,'num_ctx':num_ctx,'num_predict':num_predict}}
    req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode())

def main():
    ap=argparse.ArgumentParser(description='PRE-200 exact PN/reference-focused local vision benchmark')
    ap.add_argument('--model',default=DEFAULT_MODEL);ap.add_argument('--truth',type=Path,default=DEFAULT_TRUTH)
    ap.add_argument('--images',type=Path,default=DEFAULT_IMAGES);ap.add_argument('--output-root',type=Path,default=DEFAULT_OUT)
    ap.add_argument('--ollama-url',default=DEFAULT_URL);ap.add_argument('--num-ctx',type=int,default=8192)
    ap.add_argument('--num-predict',type=int,default=1536);ap.add_argument('--timeout',type=int,default=900)
    ap.add_argument('--force',action='store_true');ap.add_argument('--show-misses',action='store_true');args=ap.parse_args()
    truth=json.loads(args.truth.read_text(encoding='utf-8'));run=args.output_root/safe_name(args.model);rawdir=run/'raw';rawdir.mkdir(parents=True,exist_ok=True)
    outputs={};errors=[]
    print('PRE-200 PN/REFERENCE-FOCUSED VISION BENCHMARK');print('='*64);print('Model:',args.model);print('Events:',len(truth['events']));print()
    for i,e in enumerate(truth['events'],1):
        log=e['log'];img=args.images/f"p{e['page']:02d}_{log}.jpg";dst=rawdir/f'{log}.txt'
        if dst.exists() and not args.force:
            txt=dst.read_text(encoding='utf-8',errors='replace');outputs[log]=txt;print(f'[{i:02d}/25] {log} cached chars={len(txt)}');continue
        t0=time.time()
        try:
            d=call(args.ollama_url,args.model,PROMPT,img,args.num_ctx,args.num_predict,args.timeout);txt=str(d.get('response') or '')
            outputs[log]=txt;dst.write_text(txt,encoding='utf-8');print(f'[{i:02d}/25] {log} {time.time()-t0:7.1f}s chars={len(txt)}')
        except Exception as exc:errors.append((log,str(exc)));print(f'[{i:02d}/25] {log} ERROR {exc}')
    n=h=0;miss=[]
    for e in truth['events']:
        txt=outputs.get(e['log'],'')
        for fact in e.get('part_references',[]):
            n+=1;ok=fact_hit(fact,txt);h+=int(ok)
            if not ok:miss.append((e['page'],e['log'],fact['label']))
    print('\nPART / REFERENCE NUMBER RECALL');print('-'*48);print(f'Gold references: {n}');print(f'Recovered:       {h}/{n} = {100*h/n if n else 0:.1f}%');print(f'Errors:          {len(errors)}')
    summary={'model':args.model,'gold':n,'recovered':h,'pct':round(100*h/n if n else 0,2),'errors':errors}
    (run/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8');print('Summary:',run/'summary.json')
    if args.show_misses:
        print('\nMISSED PART / REFERENCE FACTS');print('-'*48)
        for page,log,label in miss:print(f'p{page:02d} {log} {label}')
    return 2 if errors else 0
if __name__=='__main__':raise SystemExit(main())
