#!/usr/bin/env python3
"""PRE-200 25-event local vision-model recall benchmark.

Runs one local Ollama vision model directly against the exact 25 benchmark page images,
then scores RAW vision evidence against the fixed 140-fact gold set. This intentionally
isolates visual acquisition/reading recall from the downstream reason/classification pass.
"""
from __future__ import annotations
import argparse, base64, hashlib, json, re, sys, time, urllib.error, urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRUTH = ROOT / 'benchmarks' / 'pre200_25_event_ground_truth.json'
DEFAULT_IMAGES = ROOT / 'benchmarks' / 'pre200_25_event_images'
DEFAULT_OUT = Path('/opt/nova-drl/output/pre200_25_event_model_benchmark')
DEFAULT_URL = 'http://127.0.0.1:11434/api/generate'
DEFAULT_MODEL = 'qwen3-vl-drl:8b-q8-16k'

PRODUCTION_PROMPT = r'''Read this DRL Traveler / Line Card using the standing FIXED 80/20 rule.

This source is primarily REPAIR-HISTORY, PARTS-USAGE, and TRACKING evidence. Do NOT
attempt perfect OCR and do NOT transcribe the whole form. Capture the useful information
a veteran DRL technician would care about.

Return concise plain text only, using whichever headings actually have information:
TRACKING / ORDER METADATA:
BASIC REPORTED PROBLEM:
PARTS / ASSEMBLIES REPLACED OR USED:
OTHER REPAIR-HISTORY NOTES:
EXPLICIT TEST / OUTCOME NOTE:

Rules:
- Ground everything in what is visible on the source.
- Capture RMA when clearly labeled RMA/RMA#.
- Capture Customer PO separately when clearly labeled Cust PO / Customer PO / PO.
- Capture procurement/order references from the ordered-parts area. Historical DRL
  examples include DGK... (Digi-Key), MSR... (Mouser), NWK..., DSK....
- Procurement/order references are NOT manufacturer PNs.
- If a procurement line visibly includes a true manufacturer PN, preserve that PN too.
- Prioritize exact part numbers, assembly names, quantities, axis/component names,
  and replacement/rebuild wording when reasonably legible.
- Preserve a likely PN as written; do not spend effort guessing one uncertain character.
- Do not infer troubleshooting procedures or test methods that are not written.
- If the card contains almost no technical information, say so briefly rather than inventing content.
'''

HIGH_RECALL_PROMPT = r'''Read this ONE DRL Traveler / Line Card as a high-recall evidence collector.

GOAL: preserve essentially every visible TECHNICAL fact that could later help a DRL technician.
Do not summarize several handwritten lines into one generic sentence. Do not decide that a
cleaning, adjustment, alignment, calibration, parameter setting, vacuum check, component swap,
replacement, rebuild, or test is unimportant. Capture each useful fact separately.

Return concise plain text with these headings when evidence exists:
REPORTED FAILURE / CUSTOMER COMPLAINT:
EXPLICIT PARTS / COMPONENTS REPLACED, INSTALLED, SWAPPED, REBUILT OR USED:
PART / REFERENCE NUMBERS:
OTHER TECHNICAL REPAIR / SERVICE ACTIONS:
EXPLICIT TEST / OUTCOME:
TRACKING / ORDER METADATA:

Rules:
- RECALL FIRST. Preserve the visible fact even if you are unsure which later database field it belongs in.
- One bullet per distinct technical fact whenever practical.
- Preserve action + component + axis together, e.g. "replaced Z motor", not merely "motor".
- Preserve exact or likely part/reference strings as written. If one character is uncertain, keep the
  useful string and mark only that uncertainty; do not drop the whole PN.
- Replacement means the card explicitly indicates replaced/installed/swapped/rebuilt/used. Do not turn
  cleaning, adjusting, inspecting, testing, or lubricating into a replacement.
- Capture handwritten notes in BOTH the complaint/special-notes area and repair/replacement area.
- Capture recurring service details such as cleaning/regreasing lead screws, setting pins/heights,
  CCD/LED work, vacuum/solenoid work, alignment, firmware/parameter settings, and cycle testing when visible.
- Ground everything in the image. Do not invent missing technical details.
- Do not waste output on blank form labels or administrative boilerplate.
'''

PROMPTS = {'production': PRODUCTION_PROMPT, 'high-recall': HIGH_RECALL_PROMPT}
CATS = ('reported_failure','parts_replaced','part_references','repair_actions')

def norm(s): return re.sub(r'[^a-z0-9]+', ' ', str(s).lower()).strip()
def compact(s): return re.sub(r'[^a-z0-9]+', '', str(s).lower())

def alias_hit(alias, text):
    a=norm(alias); t=norm(text)
    if not a: return False
    ca=compact(alias); ct=compact(text)
    if ca and ca in ct: return True
    toks=[x for x in a.split() if len(x)>1 or x.isdigit()]
    if len(toks)>=2 and all(tok in t.split() for tok in toks): return True
    return a in t

def fact_hit(fact, text):
    return any(alias_hit(a,text) for a in fact.get('aliases',[fact.get('label','')]))

def safe_name(s):
    v=re.sub(r'[^A-Za-z0-9._-]+','_',s).strip('._')
    return v or 'model'

def image_b64(path): return base64.b64encode(path.read_bytes()).decode('ascii')

def call_ollama(url, model, prompt, image, num_ctx, num_predict, timeout):
    payload={
        'model':model, 'prompt':prompt, 'stream':False,
        'images':[image_b64(image)],
        'options':{'temperature':0,'num_ctx':num_ctx,'num_predict':num_predict},
    }
    req=urllib.request.Request(url,data=json.dumps(payload).encode('utf-8'),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))

def main():
    ap=argparse.ArgumentParser(description='PRE-200 25-event Ollama vision-only recall benchmark')
    ap.add_argument('--model',default=DEFAULT_MODEL)
    ap.add_argument('--prompt',choices=sorted(PROMPTS),default='high-recall')
    ap.add_argument('--truth',type=Path,default=DEFAULT_TRUTH)
    ap.add_argument('--images',type=Path,default=DEFAULT_IMAGES)
    ap.add_argument('--output-root',type=Path,default=DEFAULT_OUT)
    ap.add_argument('--ollama-url',default=DEFAULT_URL)
    ap.add_argument('--num-ctx',type=int,default=8192)
    ap.add_argument('--num-predict',type=int,default=4096)
    ap.add_argument('--timeout',type=int,default=900)
    ap.add_argument('--max-events',type=int,default=0,help='0 = all 25; use 2-3 for smoke test')
    ap.add_argument('--force',action='store_true')
    ap.add_argument('--show-misses',action='store_true')
    args=ap.parse_args()

    truth=json.loads(args.truth.read_text(encoding='utf-8'))
    events=truth['events'][:args.max_events or None]
    prompt=PROMPTS[args.prompt]
    run_dir=args.output_root / safe_name(args.model) / args.prompt
    raw_dir=run_dir/'raw'; meta_dir=run_dir/'meta'
    raw_dir.mkdir(parents=True,exist_ok=True); meta_dir.mkdir(parents=True,exist_ok=True)

    print('PRE-200 25-EVENT LOCAL VISION-MODEL BENCHMARK')
    print('='*64)
    print('Model:       ',args.model)
    print('Prompt mode: ',args.prompt)
    print('Events:      ',len(events))
    print('Ollama:      ',args.ollama_url)
    print('Output:      ',run_dir)
    print()

    errors=[]; elapsed_total=0.0; outputs={}
    for i,e in enumerate(events,1):
        log=e['log']; page=e['page']
        image=args.images/f'p{page:02d}_{log}.jpg'
        raw_path=raw_dir/f'{log}.txt'; meta_path=meta_dir/f'{log}.json'
        if not image.exists():
            errors.append((log,f'image missing: {image}')); print(f'[{i:02d}/{len(events)}] {log} ERROR image missing'); continue
        if raw_path.exists() and meta_path.exists() and not args.force:
            txt=raw_path.read_text(encoding='utf-8',errors='replace'); outputs[log]=txt
            try: m=json.loads(meta_path.read_text(encoding='utf-8')); sec=float(m.get('elapsed_seconds') or 0)
            except Exception: sec=0
            print(f'[{i:02d}/{len(events)}] {log} cached chars={len(txt)}')
            continue
        t0=time.time()
        try:
            data=call_ollama(args.ollama_url,args.model,prompt,image,args.num_ctx,args.num_predict,args.timeout)
            sec=time.time()-t0; elapsed_total+=sec
            txt=str(data.get('response') or '')
            outputs[log]=txt; raw_path.write_text(txt,encoding='utf-8')
            meta={
                'log':log,'page':page,'model':args.model,'prompt_mode':args.prompt,
                'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                'elapsed_seconds':round(sec,3),'response_chars':len(txt),
                'total_duration_ns':data.get('total_duration'),'load_duration_ns':data.get('load_duration'),
                'prompt_eval_count':data.get('prompt_eval_count'),'eval_count':data.get('eval_count'),
                'done_reason':data.get('done_reason'),
            }
            meta_path.write_text(json.dumps(meta,indent=2),encoding='utf-8')
            print(f'[{i:02d}/{len(events)}] {log} {sec:7.1f}s chars={len(txt)}')
        except Exception as exc:
            sec=time.time()-t0; errors.append((log,str(exc)))
            meta_path.write_text(json.dumps({'log':log,'page':page,'model':args.model,'prompt_mode':args.prompt,'elapsed_seconds':round(sec,3),'error':str(exc)},indent=2),encoding='utf-8')
            print(f'[{i:02d}/{len(events)}] {log} ERROR after {sec:.1f}s: {exc}')

    totals=defaultdict(lambda:{'n':0,'hit':0}); misses=[]
    for e in events:
        txt=outputs.get(e['log'],'')
        for cat in CATS:
            for fact in e.get(cat,[]):
                totals[cat]['n']+=1
                hit=fact_hit(fact,txt)
                totals[cat]['hit']+=int(hit)
                if not hit: misses.append((e['page'],e['log'],cat,fact['label']))

    print('\nVISION-ONLY RAW EVIDENCE RECALL')
    print('-'*54)
    print(f"{'CATEGORY':22s} {'SOURCE':>7s} {'RECOVERED':>16s}")
    print('-'*54)
    for cat in CATS:
        d=totals[cat]; n=d['n']; h=d['hit']; p=100*h/n if n else 0
        print(f'{cat:22s} {n:7d} {h:4d}/{n:<4d} {p:6.1f}%')
    n=sum(d['n'] for d in totals.values()); h=sum(d['hit'] for d in totals.values()); p=100*h/n if n else 0
    print('-'*54); print(f"{'ALL USEFUL FACTS':22s} {n:7d} {h:4d}/{n:<4d} {p:6.1f}%")
    print(f'Errors: {len(errors)}')

    summary={
        'benchmark':'PRE-200 25-event local vision-model recall','model':args.model,'prompt_mode':args.prompt,
        'events_requested':len(events),'events_with_output':len(outputs),'errors':[{'log':a,'error':b} for a,b in errors],
        'categories':{cat:{**totals[cat],'recall_pct':round(100*totals[cat]['hit']/totals[cat]['n'],2) if totals[cat]['n'] else 0} for cat in CATS},
        'all':{'n':n,'hit':h,'recall_pct':round(p,2)},
        'run_dir':str(run_dir),
    }
    (run_dir/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print('Summary:',run_dir/'summary.json')
    if args.show_misses:
        print('\nMISSED GOLD FACTS')
        print('-'*64)
        for page,log,cat,label in misses: print(f'p{page:02d} {log} {cat:20s} {label}')
    if errors:
        print('\nERRORS')
        for log,err in errors: print(log,err)
        return 2
    return 0

if __name__=='__main__': raise SystemExit(main())
