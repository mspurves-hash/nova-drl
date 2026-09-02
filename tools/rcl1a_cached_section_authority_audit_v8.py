#!/usr/bin/env python3
"""Cached RCL1A v8 section-authority / ambiguity audit.

No model calls. Reads v6 cached outputs. Expected counts are used only for scoring.
Generic changes versus v7:
- Explicit high-recall replacement section is authoritative replacement evidence.
- Ambiguous families are allowed to remain unassigned rather than guessed.
- PN/reference evidence can inherit replacement role only from same-page compatible replacement evidence.
- Broad board/assembly matches are guarded when the actual replacement object is a smaller component.
"""
from __future__ import annotations
import argparse, csv, json, re
from collections import defaultdict
from pathlib import Path

from drl_global_evidence_linker_v8 import (
    Evidence, best_family_match_v8, authoritative_replacement_link,
    extract_evidence, evidence_component_signature, compatible_component_signature,
    pn_likeness
)

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_BENCH=ROOT/'config'/'rcl1a_benchmark_counts.json'
DEFAULT_CACHE=Path('/opt/nova-drl/output/rcl1a_global_additive_benchmark_v6/qwen3-vl-drl_8b-q8-16k')
DEFAULT_OUT=Path('/opt/nova-drl/output/rcl1a_cached_section_authority_audit_v8')


def current_v6_summary(cache:Path):
    p=cache/'summary.json'
    return json.loads(p.read_text()) if p.exists() else None


def page_files(cache:Path,pages:list[int]):
    for page in pages:
        hp=cache/'high_recall'/f'p{page:03d}.txt'; pp=cache/'pn_focus'/f'p{page:03d}.txt'
        if not hp.exists() or not pp.exists():
            raise SystemExit(f'Missing v6 cache for page {page}. Run completed v6 first.')
        yield page,hp.read_text(encoding='utf-8',errors='replace'),pp.read_text(encoding='utf-8',errors='replace')


def family_id(fam): return fam['reference']


def candidate_assignment(families,ev:Evidence):
    texts=[ev.candidate]
    if ev.context and ev.context != ev.candidate:
        texts.append(ev.candidate+' '+ev.context)
    best=None; ambiguity=False
    for txt in texts:
        fam,m,status=best_family_match_v8(families,txt,threshold=0.80)
        if status=='ambiguous-family':
            ambiguity=True
        if fam and m:
            rec=(m.score+0.04*pn_likeness(ev.candidate),fam,m,txt)
            if best is None or rec[0]>best[0]: best=rec
    if not best:
        return {'unassigned':True,'ambiguity':ambiguity,'evidence':ev}
    _,fam,m,txt=best
    linked,reason=authoritative_replacement_link(ev,fam,m)
    return {'unassigned':False,'family':fam,'match':m,'evidence':ev,'match_text':txt,'linked':linked,'link_reason':reason,'fused':False}


def analyze(bench,cache:Path):
    excluded=set(bench['duplicate_pages_excluded']); pages=[p for p in range(1,bench['source_pages']+1) if p not in excluded]
    fams=bench['parts']; assigned=[]; unassigned=[]; bypage=defaultdict(list)
    for page,ht,pt in page_files(cache,pages):
        evs=extract_evidence(ht,pt,page)
        recs=[]
        for ev in evs:
            a=candidate_assignment(fams,ev)
            recs.append(a)
            if a.get('unassigned'): unassigned.append(a)
            else: assigned.append(a)
        bypage[page]=recs

    # Additive same-page evidence fusion. A reference-only PN can inherit replacement status
    # when a same-page explicit replacement-section item names a compatible component class.
    for page,recs in bypage.items():
        replacement_sigs=[]
        for a in recs:
            ev=a['evidence']
            if ev.source=='replacement':
                sig=evidence_component_signature(ev)
                if sig: replacement_sigs.append(sig)
        if not replacement_sigs: continue
        for a in recs:
            if a.get('unassigned') or a.get('linked'): continue
            ev=a['evidence']
            if ev.source not in {'pn','pn_focus'}: continue
            sig=evidence_component_signature(ev)
            if sig and any(compatible_component_signature(sig,rs) for rs in replacement_sigs):
                a['linked']=True; a['fused']=True; a['link_reason']='same-page-component-role-fusion'

    byfam=defaultdict(list)
    for a in assigned: byfam[family_id(a['family'])].append(a)
    rows=[]
    for fam in fams:
        ref=family_id(fam); arr=byfam.get(ref,[])
        raw_pages=sorted({a['evidence'].page for a in arr})
        linked_pages=sorted({a['evidence'].page for a in arr if a['linked']})
        direct_pages=sorted({a['evidence'].page for a in arr if a['linked'] and not a.get('fused')})
        fused_pages=sorted({a['evidence'].page for a in arr if a.get('fused')})
        rows.append({'reference':ref,'expected':fam['expected_repairs'],'raw_pages':raw_pages,'linked_pages':linked_pages,'direct_pages':direct_pages,'fused_pages':fused_pages,'raw_found':len(raw_pages),'replacement_linked':len(linked_pages),'evidence':arr})
    return rows,unassigned


def rank_metrics(rows,key):
    exp_rank=[r['reference'] for r in sorted(rows,key=lambda x:(-x['expected'],x['reference']))]
    got_rank=[r['reference'] for r in sorted(rows,key=lambda x:(-x[key],x['reference']))]
    pos={n:i for i,n in enumerate(exp_rank)}
    mad=sum(abs(i-pos[n]) for i,n in enumerate(got_rank))/len(rows)
    return len(set(exp_rank[:6])&set(got_rank[:6])),mad


def main():
    ap=argparse.ArgumentParser(description='RCL1A cached section-authority audit v8')
    ap.add_argument('--benchmark',type=Path,default=DEFAULT_BENCH); ap.add_argument('--cache',type=Path,default=DEFAULT_CACHE)
    ap.add_argument('--output-root',type=Path,default=DEFAULT_OUT); ap.add_argument('--detail',action='store_true'); ap.add_argument('--show-changes',action='store_true')
    args=ap.parse_args(); bench=json.loads(args.benchmark.read_text()); args.output_root.mkdir(parents=True,exist_ok=True)
    v6=current_v6_summary(args.cache); rows,unassigned=analyze(bench,args.cache); v6map={r['reference']:r for r in (v6 or {}).get('rows',[])}
    print('RCL1A CACHED SECTION-AUTHORITY / AMBIGUITY AUDIT v8'); print('='*110)
    print('No model calls. No evidence deletion. Derived family/link status only; ambiguous evidence is preserved unassigned.\n')
    print(f'{"REFERENCE / COMPONENT":42s} {"EXP":>4s} {"V6":>5s} {"V8 RAW":>7s} {"V8 LINK":>8s} {"DIRECT":>7s} {"FUSED":>6s} {"ERR V8":>8s}')
    print('-'*110)
    for r in rows:
        exp=r['expected']; old=int(v6map.get(r['reference'],{}).get('replacement_linked',0)); new=r['replacement_linked']; e8=100*(new-exp)/exp if exp else 0
        print(f'{r["reference"][:42]:42s} {exp:4d} {old:5d} {r["raw_found"]:7d} {new:8d} {len(r["direct_pages"]):7d} {len(r["fused_pages"]):6d} {e8:7.1f}%')
    old6=(v6 or {}).get('top6_overlap'); oldmad=(v6 or {}).get('mean_absolute_rank_displacement'); n6,nmad=rank_metrics(rows,'replacement_linked')
    print('\nRANK CHECK'); print('-'*76); print(f'v6 Top-6 overlap: {old6}/6   -> v8 candidate: {n6}/6'); print(f'v6 mean abs rank displacement: {oldmad} -> v8 candidate: {nmad:.2f}')
    print(f'Unassigned/ambiguous evidence rows preserved: {len(unassigned)}')

    csvp=args.output_root/'rcl1a_v8_evidence_ledger.csv'
    with csvp.open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f); w.writerow(['reference','page','source','candidate','context','match_reason','score','matched_alias','linked','fused','link_reason'])
        for r in rows:
            for a in r['evidence']:
                ev=a['evidence'];m=a['match'];w.writerow([r['reference'],ev.page,ev.source,ev.candidate,ev.context,m.reason,f'{m.score:.3f}',m.alias,int(a['linked']),int(a.get('fused',False)),a['link_reason']])
    summary={'rows':[{k:v for k,v in r.items() if k!='evidence'} for r in rows],'v8_top6_overlap':n6,'v8_mean_absolute_rank_displacement':round(nmad,3),'unassigned_or_ambiguous_evidence_rows':len(unassigned),'evidence_ledger':str(csvp)}
    (args.output_root/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')

    if args.show_changes and v6:
        print('\nPAGE-LEVEL CHANGES FROM v6'); print('-'*110)
        for r in rows:
            old=set(v6map.get(r['reference'],{}).get('replacement_pages',[])); new=set(r['linked_pages']); added=sorted(new-old); removed=sorted(old-new)
            if added or removed:
                print(f'\n{r["reference"]}')
                print('  added by section-authority / generic normalization:',','.join(map(str,added)) if added else '-')
                print('  reclassified away from this derived family (raw evidence preserved):',','.join(map(str,removed)) if removed else '-')
    if args.detail:
        print('\n80/20 INTERPRETATION'); print('-'*110)
        print('A good candidate should improve the dominant families/rank without forcing generic ambiguous evidence into a specific family.')
        print('Do not chase low-frequency one-offs. If dominant counts/rank do not materially improve, stop this audit layer.')
    print('\nOutputs:'); print(' ',args.output_root/'summary.json'); print(' ',csvp)
    return 0

if __name__=='__main__': raise SystemExit(main())
