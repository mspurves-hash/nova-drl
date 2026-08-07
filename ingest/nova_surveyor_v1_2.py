#!/usr/bin/env python3
import argparse, csv, hashlib, json, re, sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

VERSION = "1.2.0"
DEFAULT_OEMS = ["GENMARK","BROOKS","ASYST","PRI","RORZE","YASKAWA","KAWASAKI","NIKON","TAZMO","HINE"]
DEFAULT_TECHNICIANS = ["ERICH","MATT"]
DEFAULT_SITES = {"MTV":"Micron Technology Virginia"}

LOG_RE = re.compile(r"^(?P<log>\d{9})\b")
TRAVELER_ORIGINAL = re.compile(r"^(?P<log>\d{9})\s+Line\s+Card\s+Original.*\.(jpg|jpeg|png|pdf|tif|tiff)$", re.I)
TRAVELER_WARRANTY = re.compile(r"^(?P<log>\d{9})\s+Line\s+Card\s+Warranty.*\.(jpg|jpeg|png|pdf|tif|tiff)$", re.I)

def clean(v): return re.sub(r"\s+"," ",str(v)).strip()
def upper(v): return clean(v).upper()

def load_refs(config_dir):
    refs={"oems":DEFAULT_OEMS[:],"technicians":DEFAULT_TECHNICIANS[:],"sites":dict(DEFAULT_SITES)}
    cfg=Path(config_dir)
    try:
        p=cfg/"oems.json"
        if p.exists(): refs["oems"]=[upper(x) for x in json.loads(p.read_text())["oems"]]
    except Exception: pass
    try:
        p=cfg/"technicians.json"
        if p.exists(): refs["technicians"]=[upper(x) for x in json.loads(p.read_text())["technicians"]]
    except Exception: pass
    try:
        p=cfg/"site_codes.json"
        if p.exists(): refs["sites"]={upper(k):v for k,v in json.loads(p.read_text())["sites"].items()}
    except Exception: pass
    return refs

def parse_folder(name, refs):
    raw=clean(name)
    out={"original_folder_name":raw,"equipment_type":None,"model":None,"oem":None,"serial_number":None,
         "customer":None,"site_code":None,"site_name":None,"technician":None,"parse_confidence":"low","parse_notes":[]}
    if " - " not in raw:
        out["parse_notes"].append("Missing ' - ' separator"); return out
    typ,rest=raw.split(" - ",1); out["equipment_type"]=upper(typ)
    toks=rest.split(); ups=[upper(x) for x in toks]
    oi=next((i for i,x in enumerate(ups) if x in refs["oems"]),None)
    if oi is None: out["parse_notes"].append("OEM not found"); return out
    out["model"]=" ".join(toks[:oi]); out["oem"]=ups[oi]
    si=next((i for i in range(oi+1,len(ups)) if ups[i]=="SN"),None)
    if si is None or si+1>=len(toks): out["parse_notes"].append("SN not found"); return out
    out["serial_number"]=toks[si+1]; tail=toks[si+2:]
    if tail and upper(tail[-1]) in refs["technicians"]:
        out["technician"]=upper(tail[-1]); tail=tail[:-1]
    if tail and upper(tail[-1]) in refs["sites"]:
        code=upper(tail[-1]); out["site_code"]=code; out["site_name"]=refs["sites"][code]; tail=tail[:-1]
    if tail: out["customer"]=" ".join(tail)
    strong=[out["equipment_type"],out["model"],out["oem"],out["serial_number"],out["customer"]]
    out["parse_confidence"]="high" if all(strong) and out["site_code"] and out["technician"] else ("medium" if all(strong) else "low")
    return out

def model_matches(parsed, requested):
    if not requested: return True
    if not parsed: return False
    p,r=upper(parsed),upper(requested)
    return p==r or p.startswith(r)

def discover(root,refs,typ=None,oem=None,model=None,limit=None):
    root=Path(root).resolve(); rows=[]
    for child in sorted(root.iterdir(),key=lambda p:p.name.lower()):
        if not child.is_dir() or child.is_symlink(): continue
        m=parse_folder(child.name,refs)
        if typ and upper(m["equipment_type"] or "")!=upper(typ): continue
        if oem and upper(m["oem"] or "")!=upper(oem): continue
        if model and not model_matches(m["model"],model): continue
        rows.append({"folder_name":child.name,"full_path":str(child),**m})
        if limit and len(rows)>=limit: break
    return rows

def decode_log(log):
    s=str(log or "")
    out={"log_number":s,"valid":False,"repair_date":None,"repair_date_display":None,"daily_sequence":None}
    if not re.fullmatch(r"\d{9}",s): return out
    yy,mm,dd=int(s[:2]),int(s[2:4]),int(s[4:6])
    try: d=date(2000+yy,mm,dd)
    except ValueError: return out
    out.update({"valid":True,"repair_date":d.isoformat(),"repair_date_display":f"{mm}/{dd}/{2000+yy}","daily_sequence":s[6:]})
    return out

def sha256(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()

def classify(path):
    n=path.name; low=n.lower(); log=(LOG_RE.match(n).group("log") if LOG_RE.match(n) else None)
    if TRAVELER_ORIGINAL.match(n): return log,"traveler",False,"confirmed"
    if TRAVELER_WARRANTY.match(n): return log,"traveler",True,"confirmed"
    rules=[
        ("receiving pic","receiving_photo"),
        ("receiving picture","receiving_photo"),
        ("return shipment packaging","return_packaging_photo"),
        ("robot checklist","robot_checklist"),
        ("robot test report","robot_test_report"),
        ("internal checklist notes","internal_checklist_notes"),
        ("failure analysis report","failure_analysis_report"),
        ("rbt rpt","rbt_report"),
    ]
    for phrase,role in rules:
        if phrase in low: return log,role,False,"high"
    if path.suffix.lower() in {".jpg",".jpeg",".png",".tif",".tiff"}: return log,"photo",False,"medium"
    if path.suffix.lower() in {".pdf",".doc",".docx",".xls",".xlsx",".xlsm",".txt",".csv"}: return log,"document",False,"low"
    return log,"unknown",False,"low"

def serial_history(source,refs,do_hash=False):
    source=Path(source).resolve(); meta=parse_folder(source.name,refs); events={}; unit=[]; all_files=[]
    for child in sorted(source.iterdir(),key=lambda p:p.name.lower()):
        if child.is_dir() and not child.is_symlink() and not LOG_RE.match(child.name):
            role="unit_configuration_archive" if "floppy" in child.name.lower() else "unit_level_folder"
            unit.append({"relative_path":str(child.relative_to(source)),"role":role,"is_directory":True})
    for p in sorted(source.rglob("*")):
        if p.is_dir() or p.is_symlink(): continue
        log,role,warranty,conf=classify(p)
        rec={"relative_path":str(p.relative_to(source)),"filename":p.name,"role":role,
             "classification_confidence":conf,"log_number":log,"warranty":warranty,
             "sha256":sha256(p) if do_hash else None}
        all_files.append(rec)
        if not log:
            unit.append({"relative_path":rec["relative_path"],"role":"unit_level_evidence","is_directory":False}); continue
        if log not in events:
            events[log]={**decode_log(log),"warranty":False,"traveler_count":0,"evidence_count":0,"role_counts":Counter(),"evidence":[]}
        e=events[log]; e["evidence"].append(rec); e["evidence_count"]+=1; e["role_counts"][role]+=1
        if role=="traveler": e["traveler_count"]+=1
        if warranty: e["warranty"]=True
    ev=list(events.values())
    for e in ev: e["role_counts"]=dict(e["role_counts"])
    ev.sort(key=lambda e:(e["repair_date"] or "9999-99-99",e["daily_sequence"] or "999",e["log_number"]))
    dates=[e["repair_date"] for e in ev if e["repair_date"]]
    return {"surveyor_version":VERSION,"mode":"serial_history","source_folder":str(source),"folder_metadata":meta,
            "hashing_enabled":do_hash,"repair_events":ev,"unit_level_evidence":unit,"files":all_files,
            "summary":{"repair_event_count":len(ev),"file_count":len(all_files),
                       "warranty_event_count":sum(1 for e in ev if e["warranty"]),
                       "unit_level_evidence_count":len(unit),
                       "first_repair_date":min(dates) if dates else None,
                       "most_recent_repair_date":max(dates) if dates else None}}

def safe_name(s): return re.sub(r"[^A-Za-z0-9._-]+","_",s).strip("_") or "survey"

def write_serial(report,outdir):
    out=Path(outdir); out.mkdir(parents=True,exist_ok=True)
    (out/"nova_serial_history.json").write_text(json.dumps(report,indent=2))
    with (out/"nova_repair_events.csv").open("w",newline="",encoding="utf-8") as f:
        fields=["log_number","repair_date","repair_date_display","daily_sequence","warranty","traveler_count","evidence_count"]
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(report["repair_events"])
    with (out/"nova_serial_files.csv").open("w",newline="",encoding="utf-8") as f:
        fields=["relative_path","filename","role","classification_confidence","log_number","warranty","sha256"]
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(report["files"])
    m,s=report["folder_metadata"],report["summary"]
    lines=[f"NOVA DRL SURVEYOR v{VERSION} - SERIAL HISTORY","="*70,
           f"Model: {m.get('model') or 'Unknown'}",f"Serial: {m.get('serial_number') or 'Unknown'}",
           f"Customer: {m.get('customer') or 'Unknown'}",f"Repair events: {s['repair_event_count']}",
           f"First repair: {s['first_repair_date']}",f"Latest repair: {s['most_recent_repair_date']}",
           f"Warranty events: {s['warranty_event_count']}","", "REPAIR HISTORY"]
    for e in report["repair_events"]:
        tag=" [WARRANTY]" if e["warranty"] else ""
        lines.append(f"\n{e['repair_date_display']}  {e['log_number']}  Seq {e['daily_sequence']}{tag}")
        for role,count in sorted(e["role_counts"].items()): lines.append(f"  {role}: {count}")
        for x in e["evidence"]: lines.append(f"  - {x['relative_path']}")
    lines+=["","UNIT-LEVEL EVIDENCE"]
    for x in report["unit_level_evidence"]: lines.append(f"  - [{x['role']}] {x['relative_path']}")
    (out/"nova_serial_history.txt").write_text("\n".join(lines)+"\n")
    return out

def main():
    ap=argparse.ArgumentParser(description="Nova DRL Surveyor v1.2")
    ap.add_argument("source"); ap.add_argument("--discover",action="store_true")
    ap.add_argument("--type",dest="typ"); ap.add_argument("--oem"); ap.add_argument("--model"); ap.add_argument("--limit",type=int)
    ap.add_argument("--hash",action="store_true"); ap.add_argument("--output"); ap.add_argument("--config")
    a=ap.parse_args()
    cfg=Path(a.config).resolve() if a.config else Path(__file__).resolve().parent.parent/"config"; refs=load_refs(cfg)
    if a.discover:
        rows=discover(a.source,refs,a.typ,a.oem,a.model,a.limit)
        out=Path(a.output) if a.output else Path.cwd()/"output"/("discovery_"+safe_name(f"{a.typ}_{a.oem}_{a.model}"))
        out.mkdir(parents=True,exist_ok=True)
        (out/"nova_domain_discovery.json").write_text(json.dumps(rows,indent=2))
        print(f"\nNova DRL Surveyor v{VERSION} - DOMAIN DISCOVERY\nMatches: {len(rows)}")
        for r in rows[:20]: print(" ",r["folder_name"])
        if len(rows)>20: print(f"  ... {len(rows)-20} more in report")
        print("\nREAD-ONLY COMPLETE: No DRL source files were changed."); return 0
    report=serial_history(a.source,refs,a.hash)
    out=Path(a.output) if a.output else Path.cwd()/"output"/safe_name(Path(a.source).name)
    write_serial(report,out)
    s=report["summary"]; m=report["folder_metadata"]
    print(f"\nNova DRL Surveyor v{VERSION} - SERIAL HISTORY")
    print(f"Model: {m.get('model')}\nSerial: {m.get('serial_number')}\nRepair events: {s['repair_event_count']}")
    print(f"Files: {s['file_count']}\nWarranty events: {s['warranty_event_count']}")
    print(f"First repair: {s['first_repair_date']}\nLatest repair: {s['most_recent_repair_date']}")
    print("\nREPAIR EVENTS")
    for e in report["repair_events"]:
        tag=" WARRANTY" if e["warranty"] else ""
        print(f"  {e['repair_date_display']}  {e['log_number']} seq {e['daily_sequence']}{tag} | {e['evidence_count']} files")
    print(f"\nReports: {out}\nREAD-ONLY COMPLETE: No DRL source files were changed."); return 0

if __name__=="__main__": raise SystemExit(main())
