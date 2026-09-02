#!/usr/bin/env python3
"""Blind RCL1A cross-family benchmark for the proven-baseline additive passes.

This does NOT alter production data and does NOT feed benchmark answers to the model.
It runs only the two candidate additive vision passes that improved PRE-200:
  1) high-recall technical evidence
  2) PN/reference-focused extraction

The established RCL1A 156-unique-repair counts are used only AFTER extraction for scoring.
The frozen v1.3.5.1/v1.3.6.1 baseline is not replaced by this test; this test asks whether
these additive passes generalize to a very different, parts-heavy product family.
"""
from __future__ import annotations
import argparse, base64, json, re, shutil, subprocess, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BENCH = ROOT/'config'/'rcl1a_benchmark_counts.json'
DEFAULT_PDF = Path('/mnt/drl/Input/RCL1A-1D-W3 All Line Cards.pdf')
DEFAULT_OUT = Path('/opt/nova-drl/output/rcl1a_global_additive_benchmark_v6')
DEFAULT_MODEL = 'qwen3-vl-drl:8b-q8-16k'
DEFAULT_URL = 'http://127.0.0.1:11434/api/generate'

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
- Preserve action + component + axis together when applicable.
- Preserve exact or likely part/reference strings as written. If one character is uncertain, keep the
  useful string and mark only that uncertainty; do not drop the whole PN.
- Replacement means the card explicitly indicates replaced/installed/swapped/rebuilt/used. Do not turn
  cleaning, adjusting, inspecting, testing, or lubricating into a replacement.
- Capture handwritten notes in BOTH the complaint/special-notes area and repair/replacement area.
- Ground everything in the image. Do not invent missing technical details.
- Do not waste output on blank form labels or administrative boilerplate.
'''

PN_PROMPT = r'''Read this ONE DRL Traveler / Line Card as a PART / REFERENCE NUMBER HUNTER.

Do not summarize the repair. Your only job is to preserve visible component/manufacturer/reference
identifiers that may be useful for parts intelligence.

Look carefully in ALL handwritten and typed technical areas, especially replacement notes, ordered-parts
areas, margins, and text next to motors, encoders, ICs, boards, belts, sensors, CCDs, LEDs, solenoids,
and other components.

Return plain text, one candidate per line, in this form when possible:
PART/REFERENCE: <string> | CONTEXT: <nearby component/action text>

Rules:
- Preserve alphanumeric strings and punctuation as written. If one character is uncertain, retain the
  rest and mark only that character with ? rather than dropping the candidate.
- Do not turn DRL log numbers, RMA, Customer PO, dates, phone numbers, prices, DGK/MSR/NWK/DSK order refs,
  or serial numbers into manufacturer PNs.
- Do not invent a PN from the component name.
- If no plausible manufacturer/component/reference string is visible, return: NONE VISIBLE.
'''

HEADINGS = [
    'REPORTED FAILURE / CUSTOMER COMPLAINT:',
    'EXPLICIT PARTS / COMPONENTS REPLACED, INSTALLED, SWAPPED, REBUILT OR USED:',
    'PART / REFERENCE NUMBERS:',
    'OTHER TECHNICAL REPAIR / SERVICE ACTIONS:',
    'EXPLICIT TEST / OUTCOME:',
    'TRACKING / ORDER METADATA:',
]

REPL_HEADING = HEADINGS[1]
PN_HEADING = HEADINGS[2]
VERBS = re.compile(r'(?i)\b(replac(?:e|ed|ing)|install(?:ed)?|swap(?:ped)?|changed?|rebuilt|used|new|pulled\s+from|donor)\b')

def compact(s:str)->str:
    return re.sub(r'[^a-z0-9]+','',s.lower())

def alias_hit(alias:str,text:str)->bool:
    a=compact(alias);t=compact(text)
    if not a:return False
    if a in t:return True
    toks=[x for x in re.split(r'[^a-z0-9]+',alias.lower()) if len(x)>=2]
    nt=set(re.split(r'[^a-z0-9]+',text.lower()))
    return len(toks)>=2 and all(x in nt for x in toks)

def any_alias(spec:dict,text:str)->bool:
    return any(alias_hit(a,text) for a in spec['aliases'])

def image_b64(p:Path)->str:
    return base64.b64encode(p.read_bytes()).decode('ascii')

def call(url,model,prompt,image,num_ctx,num_predict,timeout):
    payload={'model':model,'prompt':prompt,'stream':False,'images':[image_b64(image)],'options':{'temperature':0,'num_ctx':num_ctx,'num_predict':num_predict}}
    req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=timeout) as r:return json.loads(r.read().decode())

def find_pdf(p:Path)->Path:
    if p.exists():return p
    roots=[Path('/mnt/drl'),Path('/opt/nova-drl')]
    candidates=[]
    for root in roots:
        if root.exists():
            candidates.extend(root.rglob('*RCL1A*All*Line*Cards*.pdf'))
            candidates.extend(root.rglob('*RCL1A*all*line*cards*.pdf'))
    uniq=[]
    for c in candidates:
        if c not in uniq:uniq.append(c)
    if len(uniq)==1:return uniq[0]
    if uniq:
        raise SystemExit('Source PDF not found at default. Candidates:\n'+'\n'.join(str(x) for x in uniq[:20])+'\nUse --pdf <path>.')
    raise SystemExit(f'Source PDF not found: {p}. Use --pdf <path>.')

def render(pdf:Path, images:Path, dpi:int):
    images.mkdir(parents=True,exist_ok=True)
    marker=images/f'.rendered_{dpi}dpi'
    if marker.exists() and len(list(images.glob('page-*.jpg')))>=160:return
    if shutil.which('pdftoppm'):
        subprocess.run(['pdftoppm','-jpeg','-r',str(dpi),str(pdf),str(images/'page')],check=True)
        # poppler names page-001.jpg etc; normalize to page-001.jpg naturally.
    elif shutil.which('mutool'):
        subprocess.run(['mutool','draw','-r',str(dpi),'-o',str(images/'page-%03d.jpg'),str(pdf)],check=True)
    else:
        raise SystemExit('Need pdftoppm or mutool to render the benchmark PDF.')
    marker.write_text(str(pdf)+'\n')

def page_image(images:Path,page:int)->Path:
    candidates=[images/f'page-{page:03d}.jpg',images/f'page-{page}.jpg']
    for p in candidates:
        if p.exists():return p
    # pdftoppm sometimes uses -001; glob by numeric suffix.
    gl=list(images.glob(f'page-*{page:03d}.jpg'))
    if gl:return gl[0]
    raise FileNotFoundError(f'No image for page {page} in {images}')

def sections(text:str)->dict[str,str]:
    out={h:'' for h in HEADINGS};current=None
    for raw in text.splitlines():
        line=raw.strip()
        matched=None
        for h in HEADINGS:
            if line.upper().startswith(h.upper()):matched=h;break
        if matched:
            current=matched
            rest=line[len(matched):].strip()
            if rest:out[current]+=rest+'\n'
        elif current:
            out[current]+=raw+'\n'
    return out

def transcription_replacement_lines(text:str)->str:
    lines=text.splitlines();keep=[]
    for i,line in enumerate(lines):
        if VERBS.search(line):
            keep.append(line)
            if i+1<len(lines) and len(lines[i+1].strip())<120:keep.append(lines[i+1])
    return '\n'.join(keep)

def relerr(found:int,expected:int)->float:
    return 100.0*(found-expected)/expected if expected else 0.0

def main():
    ap=argparse.ArgumentParser(description='RCL1A blind global additive benchmark v6')
    ap.add_argument('--pdf',type=Path,default=DEFAULT_PDF);ap.add_argument('--benchmark',type=Path,default=DEFAULT_BENCH)
    ap.add_argument('--model',default=DEFAULT_MODEL);ap.add_argument('--ollama-url',default=DEFAULT_URL)
    ap.add_argument('--output-root',type=Path,default=DEFAULT_OUT);ap.add_argument('--dpi',type=int,default=200)
    ap.add_argument('--num-ctx',type=int,default=16384);ap.add_argument('--num-predict',type=int,default=4096);ap.add_argument('--timeout',type=int,default=900)
    ap.add_argument('--max-pages',type=int,default=0,help='0=all 156 unique benchmark pages; use 5 for smoke test')
    ap.add_argument('--force',action='store_true');ap.add_argument('--show-page-hits',action='store_true')
    args=ap.parse_args();bench=json.loads(args.benchmark.read_text());pdf=find_pdf(args.pdf)
    out=args.output_root/re.sub(r'[^A-Za-z0-9._-]+','_',args.model);imgs=out/'images';hr=out/'high_recall';pn=out/'pn_focus'
    hr.mkdir(parents=True,exist_ok=True);pn.mkdir(parents=True,exist_ok=True);render(pdf,imgs,args.dpi)
    excluded=set(bench['duplicate_pages_excluded']);pages=[p for p in range(1,bench['source_pages']+1) if p not in excluded]
    if args.max_pages:pages=pages[:args.max_pages]
    print('RCL1A GLOBAL ADDITIVE BENCHMARK v6');print('='*78)
    print('Source PDF:',pdf);print('Model:',args.model);print('Unique benchmark pages selected:',len(pages));print('Benchmark answers are NOT included in either model prompt.');print()
    outputs={};errors=[]
    for i,page in enumerate(pages,1):
        img=page_image(imgs,page);hp=hr/f'p{page:03d}.txt';pp=pn/f'p{page:03d}.txt'
        if hp.exists() and not args.force:ht=hp.read_text(encoding='utf-8',errors='replace');hs='cached'
        else:
            t=time.time()
            try:d=call(args.ollama_url,args.model,HIGH_RECALL_PROMPT,img,args.num_ctx,args.num_predict,args.timeout);ht=str(d.get('response') or '');hp.write_text(ht,encoding='utf-8');hs=f'{time.time()-t:.1f}s'
            except Exception as ex:errors.append((page,'high-recall',str(ex)));ht='';hs='ERROR'
        if pp.exists() and not args.force:pt=pp.read_text(encoding='utf-8',errors='replace');ps='cached'
        else:
            t=time.time()
            try:d=call(args.ollama_url,args.model,PN_PROMPT,img,args.num_ctx,2048,args.timeout);pt=str(d.get('response') or '');pp.write_text(pt,encoding='utf-8');ps=f'{time.time()-t:.1f}s'
            except Exception as ex:errors.append((page,'pn-focus',str(ex)));pt='';ps='ERROR'
        sec=sections(ht);rep=sec.get(REPL_HEADING,'');refs=sec.get(PN_HEADING,'')+'\n'+pt
        outputs[page]={'high_recall':ht,'pn_focus':pt,'replacement':rep,'refs':refs,'raw':ht+'\n'+pt}
        print(f'[{i:03d}/{len(pages)}] page {page:03d} high={hs:>7} pn={ps:>7} chars={len(ht)+len(pt)}')
    print('\nRCL1A BENCHMARK — REPAIR-EVENT COUNTS');print('-'*100)
    print(f'{"REFERENCE / COMPONENT":44s} {"EXPECTED":>8s} {"RAW FOUND":>10s} {"REPL-LINK":>10s} {"REPL ERR":>10s}')
    rows=[]
    for spec in bench['parts']:
        raw_pages=[];rep_pages=[]
        for page,d in outputs.items():
            if any_alias(spec,d['raw']):raw_pages.append(page)
            # Strongest score: alias inside explicit replacement section.
            linked=any_alias(spec,d['replacement'])
            # Additive linking: exact/reference alias plus a replacement section on same page.
            # This is intentionally conservative: requires an actual replacement section.
            if not linked and d['replacement'].strip() and any_alias(spec,d['refs']):
                # for exact-ish identifiers, only link when replacement section carries a compatible lexical core
                ref=spec['reference'].lower();r=d['replacement'].lower()
                cores=[]
                if 'fuse' in ref:cores=['fuse']
                elif 'board' in ref:cores=['board']
                elif any(x in ref for x in ['mosfet','ixfx','fdh','irf']):cores=['mosfet','transistor']
                elif any(x in ref for x in ['mc340','isl','ucc','lm','moc']):cores=['ic','chip','control','driver','op amp','op-amp']
                elif 'fb040' in ref:cores=['bridge','rectifier']
                if cores and any(c in r for c in cores):linked=True
            if linked:rep_pages.append(page)
        exp=spec['expected_repairs'];raw=len(raw_pages);rep=len(rep_pages)
        rows.append({'reference':spec['reference'],'expected':exp,'raw_found':raw,'replacement_linked':rep,'replacement_error_pct':round(relerr(rep,exp),1),'raw_pages':raw_pages,'replacement_pages':rep_pages})
        print(f'{spec["reference"][:44]:44s} {exp:8d} {raw:10d} {rep:10d} {relerr(rep,exp):9.1f}%')
        if args.show_page_hits:print('   replacement pages:',','.join(map(str,rep_pages)))
    # rank agreement on the benchmark set
    exp_rank=[r['reference'] for r in sorted(rows,key=lambda x:(-x['expected'],x['reference']))]
    got_rank=[r['reference'] for r in sorted(rows,key=lambda x:(-x['replacement_linked'],x['reference']))]
    positions={name:i for i,name in enumerate(exp_rank)}
    rank_abs=sum(abs(i-positions[name]) for i,name in enumerate(got_rank))/len(rows) if rows else 0
    top6=set(exp_rank[:6]);got6=set(got_rank[:6]);top6_overlap=len(top6&got6)
    print('\nRANK CHECK');print('-'*60);print(f'Top-6 overlap: {top6_overlap}/6');print(f'Mean absolute rank displacement across benchmark parts: {rank_abs:.2f}')
    summary={'model':args.model,'source_pdf':str(pdf),'pages_tested':len(pages),'errors':errors,'rows':rows,'top6_overlap':top6_overlap,'mean_absolute_rank_displacement':round(rank_abs,3)}
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print('Errors:',len(errors));print('Summary:',out/'summary.json')
    if args.max_pages:print('\nNOTE: --max-pages used; expected counts are full-corpus counts, so do not interpret absolute count error until full 156-page run.')
    return 2 if errors else 0
if __name__=='__main__':raise SystemExit(main())
