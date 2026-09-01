#!/usr/bin/env python3
"""Replay the frozen Nova DRL v1.3.5.1 -> v1.3.6.1 acquisition/prospecting roles
on the same 25-event PRE-200 gold benchmark.

Purpose: determine whether the early architecture was actually weak, or whether later
pipeline stages made the proven Qwen3-VL 8B acquisition role look weaker than it was.

This benchmark intentionally preserves the historical model roles:
  - Qwen3-VL 8B whole-page literal transcription
  - deterministic temporary admin sanitation (raw transcription is never changed)
  - Qwen3-VL 8B high-recall text prospector
  - NO 14B/32B rewrite stage in the score

No production files are modified.
"""
from __future__ import annotations
import argparse, base64, hashlib, json, re, time, urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRUTH = ROOT / 'config' / 'pre200_25_event_ground_truth.json'
DEFAULT_IMAGES = Path('/opt/nova-drl/benchmarks/pre200_25_event_images')
# Fall back to repo benchmark folder used by prior packages.
if not DEFAULT_IMAGES.exists():
    DEFAULT_IMAGES = ROOT.parent / 'benchmarks' / 'pre200_25_event_images'
DEFAULT_OUT = Path('/opt/nova-drl/output/pre200_25_event_historical_replay_v4')
DEFAULT_URL = 'http://127.0.0.1:11434/api/generate'
DEFAULT_MODEL = 'qwen3-vl-drl:8b-q8-16k'

TRANSCRIPTION_PROMPT = """Transcribe this complete DRL Traveler image as faithfully as possible.

RULES:
- Read the entire visible page, including printed, typed, stamped, and handwritten text.
- Return transcription only. Do not summarize, interpret, classify, normalize, explain, or answer questions about the page.
- Preserve wording, unusual shop terms, abbreviations, part numbers, quantities, punctuation, and spelling as you actually read them.
- Do not silently replace unusual wording with a more familiar term.
- Do not infer missing words or unstated quantities.
- Do not decide which text is important, boilerplate, garbage, a repair action, a part, a diagnosis, testing, or administration.
- Do not convert printed form choices into completed actions merely because the words are visible.
- Follow natural page reading order as well as possible.
- If text cannot be read reliably, write [unclear].
- Do not repeat text unless it is actually repeated on the page.
"""

PROSPECT_PROMPT = """You are the HIGH-RECALL PROSPECTOR for one DRL Traveler working transcription view.

The immutable raw transcription already exists elsewhere. This working view has only
routine form/admin lines removed by deterministic Python sanitation. Your job is to
surface source phrases that may matter later. Do NOT approve facts, normalize wording,
expand abbreviations, infer missing information, or decide what is recurring.

Return JSON only using exactly this shape:
{
  "log_number": "<log>",
  "candidates": [
    {"kind": "<kind>", "raw_quote": "<verbatim text copied from the supplied working view>"}
  ]
}

Allowed kind values:
- customer_requirement
- repair_or_service
- component_or_part
- diagnostic_or_failure
- testing_or_process
- shop_term_or_abbreviation
- part_number_or_identifier
- unclear_ocr
- other

RULES:
- Favor recall for EVENT-BEARING and potentially useful material: customer requirements,
  handwritten repair/service wording, symptoms, observed faults, causal or suspected-causal
  wording, components/parts, explicit quantities or identifiers, unusual DRL shop language,
  abbreviations, named tests/processes, and unclear OCR worth preserving.
- raw_quote MUST be copied from the supplied working view. Do not paraphrase it.
- Keep strange spellings, punctuation, apostrophes, capitalization, and abbreviations.
- Do not use outside knowledge and do not invent quantities.
- Do not reconstruct routine form/admin text that is absent from the supplied working view.
- Do not expand terms such as FA, RTZ, FE, NPF, BERS, or any other unexplained abbreviation.
- The field "Hours in Final Testing" is absent by deterministic policy. Never reconstruct or discuss it.
- A phrase may be assigned more than one kind only when the same exact raw phrase truly serves
  both roles; otherwise choose the closest kind.
"""

ALLOWED_KINDS = {
    'customer_requirement','repair_or_service','component_or_part','diagnostic_or_failure',
    'testing_or_process','shop_term_or_abbreviation','part_number_or_identifier','unclear_ocr','other'
}

_HOURS_FINAL_RE = re.compile(r"(?i)hours\s+in\s+final\s+testing\s*:?\s*(?:\d[0-9A-Za-z+._-]*)?")
_FORM_ADMIN_FULLLINE_PATTERNS = [
    re.compile(r"^Direct Repair Laboratories\s*-?\s*Testing Traveler.*$", re.I),
    re.compile(r"^[\"“]?\\Drlserver\\ctrack database\\traveler\.doc[\"”]?$", re.I),
    re.compile(r"^Log\s*#.*$", re.I),
    re.compile(r"^(?:Customer(?: Name)?|CustRMA|Cust PO|Customer PO(?: Number)?|Unit Type|Serial\s*#|Board Serial\s*#|Frame Serial\s*#|Board\(s\) serial #\(s\)|Frame\(s\) serial#\(s\)|Sales Rep|DRL SalesRep|DRL Rep|Point Of Contact|POC Phone|POC Email|Contact|Phone|Email)(?:\s*:|\s+|$).*$", re.I),
    re.compile(r"^(?:Warranty|Warranty Date|Warranty Type|Sticker Swap|Pricing Approved|pricing approved|needs quote)\b.*$", re.I),
    re.compile(r"^[✓✔☑☐XxVv ]*pricing approved\b.*$", re.I),
    re.compile(r"^(?:Special Notes \(if any\) below\..*|Responsible tech\. to init\. & date compliance\.?|\[Notes \(specific to this .+\)\]|PACKAGING STATUS:|Packaging Status:|Repaired Replaced|Detailed description of repairs/replacements|\(including any costs for new parts\)|Inits\. Date|\(m/d/y{1,2}\)|~Revised~)$", re.I),
    re.compile(r"^Date Shipped\b.*$", re.I),
    re.compile(r"^(?:Saved \(in shipping area\)|Saved \(in warehouse\)|Unusable \(discarded\))\b.*$", re.I),
    re.compile(r"^Final O\.K\.?\b.*$", re.I),
]
_FORM_PREFIX_PATTERNS = [
    re.compile(r"^Final Unit Test Results and Notes\s*:?\s*", re.I),
    re.compile(r"^(?:No Trouble Found|Passed All Tests|Basic Functional Tests Only|Power-on Tests Only|Untestable, Inspection Only)\s*(?:(?:[✓✔☑☐XxVv])|(?:\[[^\]]*\]))?\s*", re.I),
    re.compile(r"^Ttl Time Spent \(Hours\)\s*(?:(?:[✓✔☑☐XxVv])|(?:\[[^\]]*\]))?\s*", re.I),
    re.compile(r"^Ttl Money Spent \(Dollars\)\s*(?:(?:[✓✔☑☐XxVv])|(?:\[[^\]]*\]))?\s*", re.I),
]

def sha(s: str) -> str:
    return hashlib.sha256(s.encode('utf-8')).hexdigest()

def norm(s: Any) -> str:
    return re.sub(r'[^a-z0-9]+',' ',str(s).lower()).strip()

def compact(s: Any) -> str:
    return re.sub(r'[^a-z0-9]+','',str(s).lower())

def alias_hit(alias: str, text: str) -> bool:
    a=norm(alias);t=norm(text)
    if not a:return False
    ca=compact(alias);ct=compact(text)
    if ca and ca in ct:return True
    toks=[x for x in a.split() if len(x)>1 or x.isdigit()]
    return (len(toks)>=2 and all(x in t.split() for x in toks)) or a in t

def fact_hit(fact: dict, text: str) -> bool:
    return any(alias_hit(a,text) for a in fact.get('aliases',[fact.get('label','')]))

def b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode('ascii')

def call(url: str, model: str, prompt: str, *, image: Path|None=None, num_ctx=16384, num_predict=4096, timeout=900) -> str:
    payload={'model':model,'prompt':prompt,'stream':False,'options':{'temperature':0,'num_ctx':num_ctx,'num_predict':num_predict}}
    if image is not None: payload['images']=[b64(image)]
    req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=timeout) as r:
        d=json.loads(r.read().decode())
    return str(d.get('response') or '')

def parse_json(text: str) -> dict:
    s=text.strip()
    if s.startswith('```'):
        s=re.sub(r'^```(?:json)?\s*','',s,flags=re.I);s=re.sub(r'\s*```$','',s)
    try:return json.loads(s)
    except Exception:
        a=s.find('{');b=s.rfind('}')
        if a>=0 and b>a:return json.loads(s[a:b+1])
        return {}

def sanitize(raw: str) -> str:
    masked=_HOURS_FINAL_RE.sub('',raw)
    kept=[]
    for line in masked.splitlines():
        clean=line.strip()
        if not clean:
            kept.append('');continue
        if any(p.fullmatch(re.sub(r'\s+',' ',clean).strip()) for p in _FORM_ADMIN_FULLLINE_PATTERNS):
            kept.append('');continue
        stripped=clean;changed=False
        for p in _FORM_PREFIX_PATTERNS:
            m=p.match(stripped)
            if m:
                stripped=stripped[m.end():].strip();changed=True;break
        kept.append(stripped if changed else line)
    return '\n'.join(kept)+('\n' if masked.endswith('\n') else '')

def image_path(images: Path, e: dict) -> Path:
    candidates=[images/f"p{e['page']:02d}_{e['log']}.jpg", ROOT.parent/'benchmarks'/'pre200_25_event_images'/f"p{e['page']:02d}_{e['log']}.jpg"]
    for p in candidates:
        if p.exists():return p
    raise FileNotFoundError(candidates[0])

COMPAT={
 'reported_failure': {'diagnostic_or_failure','repair_or_service','other'},
 'parts_replaced': {'component_or_part','repair_or_service','part_number_or_identifier','other'},
 'part_references': {'part_number_or_identifier','component_or_part','other'},
 'repair_actions': {'repair_or_service','testing_or_process','other'},
}
CATS=['reported_failure','parts_replaced','part_references','repair_actions']

def score(truth: dict, texts: dict[str,str], candidate_rows: dict[str,list[dict]]|None=None):
    totals={c:0 for c in CATS}; hits={c:0 for c in CATS}; compat={c:0 for c in CATS}; misses=[]
    for e in truth['events']:
        text=texts.get(e['log'],'')
        rows=(candidate_rows or {}).get(e['log'],[])
        for c in CATS:
            for f in e.get(c,[]):
                totals[c]+=1;ok=fact_hit(f,text);hits[c]+=int(ok)
                cok=False
                if rows:
                    for r in rows:
                        if r.get('kind') in COMPAT[c] and fact_hit(f,str(r.get('raw_quote') or '')):
                            cok=True;break
                compat[c]+=int(cok)
                if not ok: misses.append((e['page'],e['log'],c,f['label']))
    return totals,hits,compat,misses

def pct(a,b):return 100*a/b if b else 0.0

def main():
    ap=argparse.ArgumentParser(description='Replay frozen v1.3.5.1/v1.3.6.1 PRE-200 architecture on 25-event gold set')
    ap.add_argument('--model',default=DEFAULT_MODEL);ap.add_argument('--truth',type=Path,default=DEFAULT_TRUTH)
    ap.add_argument('--images',type=Path,default=DEFAULT_IMAGES);ap.add_argument('--output-root',type=Path,default=DEFAULT_OUT)
    ap.add_argument('--ollama-url',default=DEFAULT_URL);ap.add_argument('--num-ctx',type=int,default=16384)
    ap.add_argument('--transcription-num-predict',type=int,default=4096);ap.add_argument('--prospect-num-predict',type=int,default=3072)
    ap.add_argument('--timeout',type=int,default=900);ap.add_argument('--force',action='store_true');ap.add_argument('--show-misses',action='store_true')
    args=ap.parse_args();truth=json.loads(args.truth.read_text(encoding='utf-8'))
    run=args.output_root/re.sub(r'[^A-Za-z0-9._-]+','_',args.model);trdir=run/'transcription';prdir=run/'prospector';trdir.mkdir(parents=True,exist_ok=True);prdir.mkdir(parents=True,exist_ok=True)
    print('PRE-200 HISTORICAL FROZEN-ARCHITECTURE REPLAY v4');print('='*72)
    print('Model:',args.model);print('Events:',len(truth['events']))
    print('v1.3.5.1 transcription prompt SHA256:',sha(TRANSCRIPTION_PROMPT))
    print('v1.3.6.1 prospector prompt SHA256:   ',sha(PROSPECT_PROMPT));print()
    trans={};errors=[]
    for i,e in enumerate(truth['events'],1):
        dst=trdir/f"{e['log']}.txt";img=image_path(args.images,e)
        if dst.exists() and not args.force:
            txt=dst.read_text(encoding='utf-8',errors='replace');trans[e['log']]=txt;print(f'[T {i:02d}/25] {e["log"]} cached chars={len(txt)}');continue
        t=time.time()
        try:
            txt=call(args.ollama_url,args.model,TRANSCRIPTION_PROMPT,image=img,num_ctx=args.num_ctx,num_predict=args.transcription_num_predict,timeout=args.timeout)
            dst.write_text(txt,encoding='utf-8');trans[e['log']]=txt;print(f'[T {i:02d}/25] {e["log"]} {time.time()-t:7.1f}s chars={len(txt)}')
        except Exception as ex:errors.append((e['log'],'transcription',str(ex)));print(f'[T {i:02d}/25] {e["log"]} ERROR {ex}')
    tt,th,_,tm=score(truth,trans)
    print('\nFROZEN v1.3.5.1 RAW TRANSCRIPTION RECALL');print('-'*64)
    for c in CATS: print(f'{c:22s} {th[c]:3d}/{tt[c]:<3d} {pct(th[c],tt[c]):6.1f}%')
    print(f'{"ALL USEFUL FACTS":22s} {sum(th.values()):3d}/{sum(tt.values()):<3d} {pct(sum(th.values()),sum(tt.values())):6.1f}%')
    # Prospector text pass.
    ptexts={};prows={}
    for i,e in enumerate(truth['events'],1):
        dst=prdir/f"{e['log']}.json";rawdst=prdir/f"{e['log']}.raw.txt"
        if dst.exists() and rawdst.exists() and not args.force:
            parsed=json.loads(dst.read_text(encoding='utf-8'));rawresp=rawdst.read_text(encoding='utf-8',errors='replace');print(f'[P {i:02d}/25] {e["log"]} cached candidates={len(parsed.get("candidates") or [])}')
        else:
            working=sanitize(trans.get(e['log'],''));prompt=f"{PROSPECT_PROMPT}\n\nDRL LOG: {e['log']}\nRAW TRANSCRIPTION FOR PROSPECTING:\n{working}\n";t=time.time()
            try:
                rawresp=call(args.ollama_url,args.model,prompt,num_ctx=args.num_ctx,num_predict=args.prospect_num_predict,timeout=args.timeout)
                parsed=parse_json(rawresp);rawdst.write_text(rawresp,encoding='utf-8');dst.write_text(json.dumps(parsed,indent=2,ensure_ascii=False),encoding='utf-8')
                print(f'[P {i:02d}/25] {e["log"]} {time.time()-t:7.1f}s candidates={len(parsed.get("candidates") or []) if isinstance(parsed,dict) else 0}')
            except Exception as ex:errors.append((e['log'],'prospector',str(ex)));parsed={};print(f'[P {i:02d}/25] {e["log"]} ERROR {ex}')
        rows=[]
        if isinstance(parsed,dict):
            for r in parsed.get('candidates') or []:
                if isinstance(r,dict) and r.get('kind') in ALLOWED_KINDS and str(r.get('raw_quote') or '').strip():rows.append(r)
        prows[e['log']]=rows;ptexts[e['log']]='\n'.join(str(r.get('raw_quote') or '') for r in rows)
    pt,ph,pc,pm=score(truth,ptexts,prows)
    print('\nFROZEN v1.3.6.1 HIGH-RECALL PROSPECTOR');print('-'*72)
    print(f'{"CATEGORY":22s} {"ANYWHERE":>16s} {"COMPATIBLE KIND":>18s}')
    for c in CATS:print(f'{c:22s} {ph[c]:3d}/{pt[c]:<3d} {pct(ph[c],pt[c]):6.1f}%   {pc[c]:3d}/{pt[c]:<3d} {pct(pc[c],pt[c]):6.1f}%')
    print(f'{"ALL USEFUL FACTS":22s} {sum(ph.values()):3d}/{sum(pt.values()):<3d} {pct(sum(ph.values()),sum(pt.values())):6.1f}%   {sum(pc.values()):3d}/{sum(pt.values()):<3d} {pct(sum(pc.values()),sum(pt.values())):6.1f}%')
    summary={'model':args.model,'events':len(truth['events']),'prompt_sha256':{'v1_3_5_1_transcription':sha(TRANSCRIPTION_PROMPT),'v1_3_6_1_prospector':sha(PROSPECT_PROMPT)},'transcription':{'hits':th,'totals':tt,'overall':round(pct(sum(th.values()),sum(tt.values())),2)},'prospector':{'hits':ph,'compatible_kind_hits':pc,'totals':pt,'overall':round(pct(sum(ph.values()),sum(pt.values())),2),'compatible_kind_overall':round(pct(sum(pc.values()),sum(pt.values())),2)},'errors':errors}
    (run/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8');print('\nSummary:',run/'summary.json');print('Errors:',len(errors))
    if args.show_misses:
        print('\nTRANSCRIPTION MISSES');print('-'*72)
        for p,l,c,x in tm:print(f'p{p:02d} {l} {c:22s} {x}')
        print('\nPROSPECTOR MISSES');print('-'*72)
        for p,l,c,x in pm:print(f'p{p:02d} {l} {c:22s} {x}')
    return 2 if errors else 0
if __name__=='__main__':raise SystemExit(main())
