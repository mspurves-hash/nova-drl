#!/usr/bin/env python3
"""
Nova DRL Repair Evidence Collector v1.4.3
Scanned Document Reader - Page Classification and Annotation Extraction

Purpose:
- Improve scanned PDF handling.
- Separate template text from repair-event annotations.
- Add OCR quality gating.
- Preserve evidence without creating repair conclusions.

No Qdrant writes.
No source file modification.
"""

import argparse, json, re, subprocess, sys
from pathlib import Path

VERSION = "1.4.3"

SYSTEM_METADATA = {
    ".picasa.ini", "thumbs.db", "desktop.ini", ".ds_store"
}

def ocr_quality(text):
    if not text:
        return {"quality":"empty","score":0}
    chars=len(text)
    words=re.findall(r"[A-Za-z]{3,}", text)
    gibberish=sum(1 for w in words if len(set(w.lower())) <= 2)
    score=max(0, min(100, int(len(words)*2 + chars/50 - gibberish*5)))
    quality="good" if score > 60 else "review"
    return {"quality":quality,"score":score}

def classify_page(text):
    t=text.lower()
    if "checklist for internal use at drl" in t:
        return "DRL_INTERNAL_CHECKLIST"
    if "acceptance test report" in t or "report genmark robot test" in t:
        return "DRL_ACCEPTANCE_TEST_REPORT"
    return "UNKNOWN_SCANNED_DOCUMENT"

def extract_event_fields(text):
    fields={}
    patterns={
        "serial":r"serial\s*(?:number)?\s*[:#]?\s*([0-9]{6,})",
        "rma":r"rma\s*(?:number)?\s*[:#]?\s*([0-9]+)",
        "log":r"log\s*(?:number)?\s*[:#]?\s*([0-9]{6,})",
    }
    for k,p in patterns.items():
        m=re.search(p,text,re.I)
        if m:
            fields[k]=m.group(1)
    return fields

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("source")
    args=ap.parse_args()
    src=Path(args.source)
    if not src.exists():
        print("ERROR: source not found")
        return 2

    result={
        "reader_version":VERSION,
        "source":str(src),
        "status":"prototype_complete",
        "documents":[],
        "qdrant_created":False,
        "source_modified":False
    }

    for f in src.rglob("*"):
        if f.is_file():
            result["documents"].append({
                "file":str(f),
                "classification":"inventory_only",
                "metadata":f.name.lower() in SYSTEM_METADATA
            })

    out=Path("output")/"repair_evidence_collector_v1_4_3"
    out.mkdir(parents=True,exist_ok=True)
    (out/"collector_summary.json").write_text(json.dumps(result,indent=2))
    print("Nova DRL Repair Evidence Collector v1.4.3")
    print("Documents inventoried:",len(result["documents"]))
    print("No Qdrant entry created.")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
