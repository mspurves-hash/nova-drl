#!/usr/bin/env python3
import argparse,csv,hashlib,json,re
from collections import Counter
from datetime import date
from pathlib import Path

VERSION='1.2.1'
DEFAULT_OEMS=['GENMARK','BROOKS','ASYST','PRI','RORZE','YASKAWA','KAWASAKI','NIKON','TAZMO','HINE']
DEFAULT_TECHNICIANS=['ERICH','MATT']
DEFAULT_SITES={'MTV':'Micron Technology Virginia'}
LOG_RE=re.compile(r'^(?P<log>\d{9})\b',re.I)
TRAVELER_RE=re.compile(r'^(?P<log>\d{9})\s+Line\s+Card\s+(?P<kind>Original|Warranty)\b.*\.(jpg|jpeg|png|pdf|tif|tiff)$',re.I)
IMAGE_EXTS={'.jpg','.jpeg','.png','.tif','.tiff','.bmp','.gif','.webp'}
VIDEO_EXTS={'.mp4','.mov','.avi','.mkv','.wmv','.mpg','.mpeg','.m4v'}
DOC_EXTS={'.pdf','.doc','.docx','.txt','.rtf','.md'}
SHEET_EXTS={'.xls','.xlsx','.xlsm','.csv','.tsv'}
CONFIG_EXTS={'.par','.prm','.cfg','.conf','.ini','.json','.xml','.dat','.bin','.bak'}

def clean(v): return re.sub(r'\s+',' ',str(v)).strip()
def upper(v): return clean(v).upper()

def load_refs(config_dir):
    cfg=Path(config_dir); refs={'oems':DEFAULT_OEMS[:],'technicians':DEFAULT_TECHNICIANS[:],'sites':dict(DEFAULT_SITES)}
    for key,name in [('oems','oems.json'),('technicians','technicians.json'),('sites','site_codes.json')]:
        try:
            p=cfg/name
            if not p.exists(): continue
            d=json.loads(p.read_text(encoding='utf-8'))
            if key=='sites': refs[key]={upper(k):clean(v) for k,v in d['sites'].items()}
            else: refs[key]=[upper(x) for x in d[key]]
        except Exception: pass
    return refs

def parse_folder(name,refs):
    raw=clean(name); out={'original_folder_name':raw,'equipment_type':None,'model':None,'oem':None,'serial_number':None,'customer':None,'site_code':None,'site_name':None,'technician':None,'parse_confidence':'low','parse_notes':[]}
    if ' - ' not in raw: out['parse_notes'].append("Missing expected ' - ' separator."); return out
    typ,rest=raw.split(' - ',1); out['equipment_type']=upper(typ)
    toks=rest.split(); ups=[upper(x) for x in toks]
    oi=next((i for i,x in enumerate(ups) if x in refs['oems']),None)
    if oi is None: out['parse_notes'].append('Known OEM not found.'); return out
    out['model']=' '.join(toks[:oi]); out['oem']=ups[oi]
    si=next((i for i in range(oi+1,len(ups)) if ups[i]=='SN'),None)
    if si is None or si+1>=len(toks): out['parse_notes'].append('SN marker or serial number not found.'); return out
    out['serial_number']=toks[si+1]; tail=toks[si+2:]
    if tail and upper(tail[-1]) in refs['technicians']: out['technician']=upper(tail[-1]); tail=tail[:-1]
    if tail and upper(tail[-1]) in refs['sites']:
        code=upper(tail[-1]); out['site_code']=code; out['site_name']=refs['sites'][code]; tail=tail[:-1]
    if tail: out['customer']=' '.join(tail)
    strong=[out['equipment_type'],out['model'],out['oem'],out['serial_number'],out['customer']]
    out['parse_confidence']='high' if all(strong) and out['site_code'] and out['technician'] else ('medium' if all(strong) else 'low')
    return out

def model_matches(parsed,requested): return True if not requested else bool(parsed and upper(parsed).startswith(upper(requested)))

def discover(root,refs,typ=None,oem=None,model=None,limit=None):
    rows=[]
    for child in sorted(Path(root).resolve().iterdir(),key=lambda p:p.name.lower()):
        if not child.is_dir() or child.is_symlink(): continue
        m=parse_folder(child.name,refs)
        if typ and upper(m['equipment_type'] or '')!=upper(typ): continue
        if oem and upper(m['oem'] or '')!=upper(oem): continue
        if model and not model_matches(m['model'],model): continue
        rows.append({'folder_name':child.name,'full_path':str(child),**m})
        if limit and len(rows)>=limit: break
    return rows

def decode_log(log):
    s=str(log or ''); out={'log_number':s,'valid':False,'repair_date':None,'repair_date_display':None,'daily_sequence':None}
    if not re.fullmatch(r'\d{9}',s): return out
    yy,mm,dd=int(s[:2]),int(s[2:4]),int(s[4:6])
    try: d=date(2000+yy,mm,dd)
    except ValueError: return out
    out.update({'valid':True,'repair_date':d.isoformat(),'repair_date_display':f'{mm}/{dd}/{2000+yy}','daily_sequence':s[6:]}); return out

def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def classify(path):
    n=path.name; low=n.lower(); ext=path.suffix.lower(); m=LOG_RE.match(n); log=m.group('log') if m else None
    t=TRAVELER_RE.match(n)
    if t:
        warranty=t.group('kind').lower()=='warranty'; return log,'traveler',warranty,'confirmed','DRL traveler naming rule'
    if any(x in low for x in ['failure analysis report','failure analysis','field failure report','field failure','incoming failure analysis','gold incoming failure analysis']): return log,'failure_analysis_report',False,'high','Failure-analysis wording'
    if 'robot test report' in low or ('test report' in low and 'robot' in low): return log,'robot_test_report',False,'high','Robot test-report wording'
    if 'robot checklist' in low: return log,'robot_checklist',False,'high','Robot checklist wording'
    if 'internal checklist notes' in low or 'checklist notes' in low: return log,'internal_checklist_notes',False,'high','Checklist-notes wording'
    if 'rbt rpt' in low or 'rbt report' in low or 'robot report' in low: return log,'rbt_report',False,'medium','RBT/robot report wording'
    if any(x in low for x in ['receiving pic','receiving picture','incoming pic','incoming picture','incoming photo','receiving photo']): return log,'receiving_photo',False,'high','Incoming/receiving photo wording'
    if any(x in low for x in ['return shipment packaging','return shipping packaging','return packaging','shipment packaging','shipping packaging']): return log,'return_packaging_photo',False,'high','Return/shipping packaging wording'
    if any(x in low for x in ['floppy','parameter','params','uploadparam','upload param','configuration','config backup']): return log,'configuration_evidence',False,'high','Configuration/floppy/parameter wording'
    if ext in VIDEO_EXTS: return log,'movie',False,'high','Video extension'
    if ext in IMAGE_EXTS: return log,'photo',False,'medium','Image; specific role uncertain'
    if ext in SHEET_EXTS: return log,'structured_document',False,'low','Spreadsheet; role uncertain'
    if ext in DOC_EXTS: return log,'document',False,'low','Document; role uncertain'
    if ext in CONFIG_EXTS: return log,'technical_file',False,'low','Technical/config file; role uncertain'
    return log,'unknown',False,'low','No confirmed rule'

def unit_role(path):
    low=path.name.lower()
    if any(x in low for x in ['floppy','parameter','params','configuration','config']): return 'unit_configuration_archive','high'
    return ('unit_level_folder' if path.is_dir() else 'unit_level_evidence'),'medium'

def serial_history(source,refs,do_hash=False):
    source=Path(source).resolve(); meta=parse_folder(source.name,refs); events={}; unit=[]; files=[]
    for child in sorted(source.iterdir(),key=lambda p:p.name.lower()):
        if child.is_dir() and not child.is_symlink() and not LOG_RE.match(child.name):
            role,conf=unit_role(child); unit.append({'relative_path':str(child.relative_to(source)),'role':role,'classification_confidence':conf,'is_directory':True})
    for p in sorted(source.rglob('*')):
        if p.is_dir() or p.is_symlink(): continue
        log,role,warranty,conf,reason=classify(p)
        rec={'relative_path':str(p.relative_to(source)),'filename':p.name,'role':role,'classification_confidence':conf,'classification_reason':reason,'log_number':log,'warranty':warranty,'sha256':sha256(p) if do_hash else None}
        files.append(rec)
        if not log:
            role2,conf2=unit_role(p); unit.append({'relative_path':rec['relative_path'],'role':role2,'classification_confidence':conf2,'is_directory':False}); continue
        if log not in events: events[log]={**decode_log(log),'warranty':False,'traveler_count':0,'evidence_count':0,'role_counts':Counter(),'evidence':[]}
        e=events[log]; e['evidence'].append(rec); e['evidence_count']+=1; e['role_counts'][role]+=1
        if role=='traveler': e['traveler_count']+=1
        if warranty: e['warranty']=True
    ev=list(events.values())
    for e in ev: e['role_counts']=dict(sorted(e['role_counts'].items())); e['missing_traveler']=(e['traveler_count']==0)
    ev.sort(key=lambda e:(e['repair_date'] or '9999-99-99',e['daily_sequence'] or '999',e['log_number']))
    dates=[e['repair_date'] for e in ev if e['repair_date']]
    return {'surveyor_version':VERSION,'mode':'serial_history','source_folder':str(source),'folder_metadata':meta,'hashing_enabled':do_hash,'repair_events':ev,'unit_level_evidence':unit,'files':files,'summary':{'repair_event_count':len(ev),'file_count':len(files),'warranty_event_count':sum(1 for e in ev if e['warranty']),'missing_traveler_event_count':sum(1 for e in ev if e['missing_traveler']),'unit_level_evidence_count':len(unit),'first_repair_date':min(dates) if dates else None,'most_recent_repair_date':max(dates) if dates else None}}

def safe_name(s): return re.sub(r'[^A-Za-z0-9._-]+','_',s).strip('_') or 'survey'

def write_serial(report,outdir):
    out=Path(outdir); out.mkdir(parents=True,exist_ok=True); (out/'nova_serial_history.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    with (out/'nova_repair_events.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['log_number','repair_date','repair_date_display','daily_sequence','warranty','traveler_count','evidence_count','missing_traveler']; w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(report['repair_events'])
    with (out/'nova_serial_files.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['relative_path','filename','role','classification_confidence','classification_reason','log_number','warranty','sha256']; w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(report['files'])
    m,s=report['folder_metadata'],report['summary']; lines=[f'NOVA DRL SURVEYOR v{VERSION} - SERIAL HISTORY','='*72,f"Model: {m.get('model') or 'Unknown'}",f"Serial: {m.get('serial_number') or 'Unknown'}",f"Customer: {m.get('customer') or 'Unknown'}",'',f"Repair events: {s['repair_event_count']}",f"Evidence files: {s['file_count']}",f"Warranty events: {s['warranty_event_count']}",f"Events missing traveler: {s['missing_traveler_event_count']}",f"Unit-level evidence: {s['unit_level_evidence_count']}",f"First repair: {s['first_repair_date'] or 'Unknown'}",f"Latest repair: {s['most_recent_repair_date'] or 'Unknown'}",'','REPAIR HISTORY']
    for e in report['repair_events']:
        tags=[]
        if e['warranty']: tags.append('WARRANTY')
        if e['missing_traveler']: tags.append('NO TRAVELER FOUND')
        tag=' ['+', '.join(tags)+']' if tags else ''
        lines+=['',f"{e['repair_date_display'] or 'Invalid date'}  {e['log_number']}  Seq {e['daily_sequence'] or '???'}{tag}",f"  Traveler(s): {e['traveler_count']}",f"  Evidence: {e['evidence_count']}"]
        for role,count in sorted(e['role_counts'].items()): lines.append(f'    {role}: {count}')
        for x in e['evidence']: lines.append('    - '+x['relative_path'])
    lines+=['','UNIT-LEVEL EVIDENCE']
    if report['unit_level_evidence']:
        for x in report['unit_level_evidence']: lines.append(f"  - [{x['role']}] {x['relative_path']}")
    else: lines.append('  None')
    (out/'nova_serial_history.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8'); return out

def main():
    ap=argparse.ArgumentParser(description='Nova DRL Surveyor v1.2.1'); ap.add_argument('source'); ap.add_argument('--discover',action='store_true'); ap.add_argument('--type',dest='typ'); ap.add_argument('--oem'); ap.add_argument('--model'); ap.add_argument('--limit',type=int); ap.add_argument('--hash',action='store_true'); ap.add_argument('--output'); ap.add_argument('--config'); a=ap.parse_args()
    cfg=Path(a.config).resolve() if a.config else Path(__file__).resolve().parent.parent/'config'; refs=load_refs(cfg)
    if a.discover:
        rows=discover(a.source,refs,a.typ,a.oem,a.model,a.limit); out=Path(a.output) if a.output else Path.cwd()/'output'/('discovery_'+safe_name(f'{a.typ}_{a.oem}_{a.model}')); out.mkdir(parents=True,exist_ok=True); (out/'nova_domain_discovery.json').write_text(json.dumps(rows,indent=2),encoding='utf-8'); print(f'\nNova DRL Surveyor v{VERSION} - DOMAIN DISCOVERY\nMatches: {len(rows)}'); [print(' ',r['folder_name']) for r in rows[:20]]; print('\nREAD-ONLY COMPLETE: No DRL source files were changed.'); return 0
    report=serial_history(a.source,refs,a.hash); out=Path(a.output) if a.output else Path.cwd()/'output'/safe_name(Path(a.source).name); write_serial(report,out); s=report['summary']; m=report['folder_metadata']; print(f'\nNova DRL Surveyor v{VERSION} - SERIAL HISTORY'); print(f"Model: {m.get('model')}\nSerial: {m.get('serial_number')}\nRepair events: {s['repair_event_count']}\nFiles: {s['file_count']}\nWarranty events: {s['warranty_event_count']}\nEvents missing traveler: {s['missing_traveler_event_count']}\nUnit-level evidence: {s['unit_level_evidence_count']}\nFirst repair: {s['first_repair_date']}\nLatest repair: {s['most_recent_repair_date']}"); print('\nREPAIR EVENTS')
    for e in report['repair_events']:
        tags=[]
        if e['warranty']: tags.append('WARRANTY')
        if e['missing_traveler']: tags.append('NO TRAVELER')
        print(f"  {e['repair_date_display']}  {e['log_number']} seq {e['daily_sequence']} {'/'.join(tags)} | {e['evidence_count']} files")
    print(f'\nReports: {out}\nREAD-ONLY COMPLETE: No DRL source files were changed.'); return 0
if __name__=='__main__': raise SystemExit(main())
