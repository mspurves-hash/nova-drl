#!/usr/bin/env python3
"""Cached RCL1A v7 normalization/replacement-link audit.

No model calls. Reads v6 cached outputs and asks whether generic normalization + relation-aware
replacement linking can explain the v6 count errors. Benchmark expected counts are used only for
final scoring, never to decide a match.
"""
from __future__ import annotations
import argparse, csv, json, re
from collections import defaultdict
from pathlib import Path

from drl_global_evidence_linker_v7 import (
    Evidence, best_family_match, compact, explicit_replacement_object, extract_evidence,
    match_alias, pn_likeness, parse_specs
)

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_BENCH=ROOT/'config'/'rcl1a_benchmark_counts.json'
DEFAULT_CACHE=Path('/opt/nova-drl/output/rcl1a_global_additive_benchmark_v6/qwen3-vl-drl_8b-q8-16k')
DEFAULT_OUT=Path('/opt/nova-drl/output/rcl1a_cached_normalization_link_audit_v7')


def current_v6_summary(cache: Path):
    p=cache/'summary.json'
    return json.loads(p.read_text()) if p.exists() else None


def page_files(cache:Path, pages:list[int]):
    for page in pages:
        hp=cache/'high_recall'/f'p{page:03d}.txt'; pp=cache/'pn_focus'/f'p{page:03d}.txt'
        if not hp.exists() or not pp.exists():
            raise SystemExit(f'Missing v6 cache for page {page}: {hp if not hp.exists() else pp}\nRun the completed v6 benchmark first.')
        yield page,hp.read_text(encoding='utf-8',errors='replace'),pp.read_text(encoding='utf-8',errors='replace')


def family_id(fam): return fam['reference']


def target_core_words(ref:str):
    stop={'the','and','or','family','assembly','component','part','small'}
    return {x for x in re.findall(r'[a-z0-9]+',ref.lower()) if len(x)>=2 and x not in stop}


def candidate_assignment(families, ev:Evidence):
    # Match candidate and candidate+context independently; take stronger one.
    texts=[ev.candidate]
    if ev.context and ev.context != ev.candidate: texts.append(ev.candidate+' '+ev.context)
    best=None
    for txt in texts:
        fam,m=best_family_match(families,txt,threshold=0.80)
        if fam and m:
            rec=(m.score + 0.04*pn_likeness(ev.candidate),fam,m,txt)
            if best is None or rec[0]>best[0]: best=rec
    if not best:return None
    _,fam,m,txt=best
    linked=False; link_reason=''
    if ev.source=='replacement':
        # Explicit replacement section is strong, but relation-aware check prevents "IC on smart board" overcount.
        alias=m.alias
        linked=explicit_replacement_object(ev.context,alias)
        link_reason='replacement-section/object' if linked else 'replacement-section/location-or-ambiguous'
    elif ev.explicit_replacement:
        linked=True; link_reason='pn-context-explicit-replacement'
    return {'family':fam,'match':m,'evidence':ev,'match_text':txt,'linked':linked,'link_reason':link_reason}


def analyze(bench,cache:Path):
    excluded=set(bench['duplicate_pages_excluded']);pages=[p for p in range(1,bench['source_pages']+1) if p not in excluded]
    fams=bench['parts']
    assigned=[]; unassigned=[]
    for page,ht,pt in page_files(cache,pages):
        for ev in extract_evidence(ht,pt,page):
            a=candidate_assignment(fams,ev)
            if a:assigned.append(a)
            else:unassigned.append(ev)
    byfam=defaultdict(list)
    for a in assigned:byfam[family_id(a['family'])].append(a)
    rows=[]
    for fam in fams:
        ref=family_id(fam); arr=byfam.get(ref,[])
        raw_pages=sorted({a['evidence'].page for a in arr})
        linked_pages=sorted({a['evidence'].page for a in arr if a['linked']})
        fuzzy_pages=sorted({a['evidence'].page for a in arr if a['match'].reason in {'fuzzy-pn','descriptor'}})
        exact_pages=sorted({a['evidence'].page for a in arr if a['match'].exactish})
        rows.append({'reference':ref,'expected':fam['expected_repairs'],'raw_pages':raw_pages,'linked_pages':linked_pages,'raw_found':len(raw_pages),'replacement_linked':len(linked_pages),'fuzzy_pages':fuzzy_pages,'exact_pages':exact_pages,'evidence':arr})
    return rows,unassigned


def rank_metrics(rows,key):
    exp_rank=[r['reference'] for r in sorted(rows,key=lambda x:(-x['expected'],x['reference']))]
    got_rank=[r['reference'] for r in sorted(rows,key=lambda x:(-x[key],x['reference']))]
    pos={n:i for i,n in enumerate(exp_rank)}
    mad=sum(abs(i-pos[n]) for i,n in enumerate(got_rank))/len(rows)
    return len(set(exp_rank[:6])&set(got_rank[:6])),mad


def main():
    ap=argparse.ArgumentParser(description='RCL1A cached normalization/link audit v7')
    ap.add_argument('--benchmark',type=Path,default=DEFAULT_BENCH);ap.add_argument('--cache',type=Path,default=DEFAULT_CACHE)
    ap.add_argument('--output-root',type=Path,default=DEFAULT_OUT);ap.add_argument('--detail',action='store_true');ap.add_argument('--show-changes',action='store_true')
    args=ap.parse_args();bench=json.loads(args.benchmark.read_text());args.output_root.mkdir(parents=True,exist_ok=True)
    v6=current_v6_summary(args.cache);rows,unassigned=analyze(bench,args.cache)
    v6map={r['reference']:r for r in (v6 or {}).get('rows',[])}
    print('RCL1A CACHED NORMALIZATION / REPLACEMENT-LINK AUDIT v7');print('='*106)
    print('No model calls. Generic matcher/linker only. Expected counts are used for scoring only, never matching.\n')
    print(f'{"REFERENCE / COMPONENT":42s} {"EXP":>4s} {"V6":>5s} {"V7 RAW":>7s} {"V7 LINK":>8s} {"ERR V6":>8s} {"ERR V7":>8s}')
    print('-'*106)
    for r in rows:
        exp=r['expected'];old=int(v6map.get(r['reference'],{}).get('replacement_linked',0));new=r['replacement_linked'];raw=r['raw_found']
        e6=100*(old-exp)/exp if exp else 0;e7=100*(new-exp)/exp if exp else 0
        print(f'{r["reference"][:42]:42s} {exp:4d} {old:5d} {raw:7d} {new:8d} {e6:7.1f}% {e7:7.1f}%')
    old6=(v6 or {}).get('top6_overlap');oldmad=(v6 or {}).get('mean_absolute_rank_displacement')
    n6,nmad=rank_metrics(rows,'replacement_linked')
    print('\nRANK CHECK');print('-'*72);print(f'v6 Top-6 overlap: {old6}/6   -> v7 candidate: {n6}/6');print(f'v6 mean abs rank displacement: {oldmad} -> v7 candidate: {nmad:.2f}')

    print('\nDISCREPANCY DECOMPOSITION');print('-'*106)
    print('For each family: exact/alias pages, fuzzy/spec pages, linked pages, remaining aggregate gap/excess.')
    for r in rows:
        exp=r['expected'];rem=exp-r['replacement_linked']
        print(f'{r["reference"][:42]:42s} exact={len(r["exact_pages"]):3d} fuzzy={len(r["fuzzy_pages"]):3d} linked={r["replacement_linked"]:3d} remaining={rem:+4d}')

    # Export evidence ledger for human/next benchmark inspection.
    csvp=args.output_root/'rcl1a_v7_evidence_ledger.csv'
    with csvp.open('w',newline='',encoding='utf-8') as f:
        w=csv.writer(f);w.writerow(['reference','page','source','candidate','context','match_reason','score','matched_alias','linked','link_reason'])
        for r in rows:
            for a in r['evidence']:
                ev=a['evidence'];m=a['match'];w.writerow([r['reference'],ev.page,ev.source,ev.candidate,ev.context,m.reason,f'{m.score:.3f}',m.alias,int(a['linked']),a['link_reason']])
    js=[]
    for r in rows:
        js.append({k:v for k,v in r.items() if k!='evidence'})
    summary={'rows':js,'v6_summary':str(args.cache/'summary.json'),'v7_top6_overlap':n6,'v7_mean_absolute_rank_displacement':round(nmad,3),'unassigned_evidence_count':len(unassigned),'evidence_ledger':str(csvp)}
    (args.output_root/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')

    if args.show_changes and v6:
        print('\nPAGE-LEVEL CHANGES FROM v6');print('-'*106)
        for r in rows:
            old=set(v6map.get(r['reference'],{}).get('replacement_pages',[]));new=set(r['linked_pages']);added=sorted(new-old);removed=sorted(old-new)
            if added or removed:
                print(f'\n{r["reference"]}')
                print('  added by generic normalization/linking:', ','.join(map(str,added)) if added else '-')
                print('  removed as weak/location/context links:', ','.join(map(str,removed)) if removed else '-')
    if args.detail:
        print('\nVARIANT / CONTEXT EXAMPLES');print('-'*106)
        for r in rows:
            arr=sorted(r['evidence'],key=lambda a:(a['evidence'].page,-a['match'].score))
            fuzzy=[a for a in arr if a['match'].reason in {'fuzzy-pn','descriptor'}]
            if not fuzzy:continue
            print(f'\n{r["reference"]} — {len(fuzzy)} fuzzy/descriptor evidence rows')
            for a in fuzzy[:12]:
                ev=a['evidence'];m=a['match'];print(f'  p{ev.page:03d} {m.reason:10s} {m.score:.2f} linked={int(a["linked"])} cand={ev.candidate!r} ctx={ev.context[:120]!r}')
    print('\nOutputs:');print(' ',args.output_root/'summary.json');print(' ',csvp)
    print('\nInterpretation guardrail: a remaining aggregate gap is NOT automatically a vision miss; it can still be an unrecognized variant. This audit only proves what the cached text supports.')

if __name__=='__main__':main()
