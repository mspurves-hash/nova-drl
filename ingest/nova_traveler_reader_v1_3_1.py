#!/usr/bin/env python3
import argparse, json, re, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

VERSION="1.3.1"
TRAVELER_RE=re.compile(r"^(?P<log>\d{9})\s+Line\s+Card\s+(?P<kind>Original|Warranty)\b.*\.(?P<ext>jpg|jpeg|png|pdf|tif|tiff)$",re.I)
IMAGE_EXTS={".jpg",".jpeg",".png",".tif",".tiff"}

REGIONS={
"identity":{"box":(0.05,0.08,0.53,0.43),"psm":[6,11],"description":"Identity / RMA / customer / serial / warranty"},
"packaging_status":{"box":(0.53,0.05,0.96,0.20),"psm":[6,11],"description":"Packaging status"},
"repairs_replacements":{"box":(0.53,0.18,0.96,0.61),"psm":[6,11,12],"description":"Detailed repairs / replacements"},
"special_notes":{"box":(0.05,0.43,0.53,0.83),"psm":[6,11,12],"description":"Special notes"},
"final_test":{"box":(0.53,0.59,0.96,0.91),"psm":[6,11],"description":"Final unit test results"},
"shipping_final_ok":{"box":(0.05,0.82,0.53,0.98),"psm":[6,11],"description":"Shipping / hours / Final O.K."}
}

def now_utc(): return datetime.now(timezone.utc).isoformat()

def require_pillow():
    try:
        from PIL import Image, ImageOps, ImageEnhance, ImageFilter
        return Image,ImageOps,ImageEnhance,ImageFilter
    except Exception:
        raise RuntimeError("Pillow is required. Install with: sudo apt install python3-pil")

def run_command(args,timeout=180):
    try:
        p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=timeout)
        return p.returncode,p.stdout,p.stderr
    except Exception as e:
        return 999,"",str(e)

def find_travelers(root):
    root=Path(root).resolve()
    if not root.exists() or not root.is_dir(): raise ValueError("Serial folder does not exist: {}".format(root))
    out=[]
    for p in sorted(root.rglob("*")):
        if p.is_dir() or p.is_symlink(): continue
        m=TRAVELER_RE.match(p.name)
        if m:
            out.append({"path":p,"relative_path":str(p.relative_to(root)),"log_number":m.group("log"),
                        "traveler_kind":m.group("kind").lower(),"warranty":m.group("kind").lower()=="warranty"})
    return out

def fractional_box_to_pixels(box,w,h):
    l=max(0,min(w,int(round(box[0]*w)))); t=max(0,min(h,int(round(box[1]*h))))
    r=max(l+1,min(w,int(round(box[2]*w)))); b=max(t+1,min(h,int(round(box[3]*h))))
    return l,t,r,b

def preprocess_crop(img,scale=2.0):
    Image,ImageOps,ImageEnhance,ImageFilter=require_pillow()
    crop=ImageOps.autocontrast(img.convert("L"))
    if scale!=1.0:
        crop=crop.resize((int(crop.width*scale),int(crop.height*scale)),Image.Resampling.LANCZOS)
    crop=ImageEnhance.Contrast(crop).enhance(1.35)
    crop=ImageEnhance.Sharpness(crop).enhance(1.25)
    try: crop=crop.filter(ImageFilter.MedianFilter(size=3))
    except Exception: pass
    return crop

def ocr_quality_score(text):
    text=text or ""
    if not text.strip(): return -100000
    alnum=sum(c.isalnum() for c in text)
    words=re.findall(r"[A-Za-z0-9][A-Za-z0-9#./+\-]{1,}",text)
    shorts=sum(1 for w in words if len(w)<=2)
    return alnum+4*len(words)-2*shorts

def tesseract_ocr(path,psm):
    if not shutil.which("tesseract"):
        return {"status":"dependency_missing","psm":psm,"text":"","score":-100000}
    code,out,err=run_command(["tesseract",str(path),"stdout","--psm",str(psm)])
    return {"status":"ok" if code==0 else "error","psm":psm,"text":out if code==0 else "",
            "stderr":err.strip(),"score":ocr_quality_score(out) if code==0 else -100000}

def extract_regions(image_path,per_log):
    Image,_,_,_=require_pillow()
    with Image.open(image_path) as img:
        img.load(); w,h=img.size
        crops=per_log/"crops"; crops.mkdir(parents=True,exist_ok=True)
        results={}
        for name,cfg in REGIONS.items():
            px=fractional_box_to_pixels(cfg["box"],w,h)
            processed=preprocess_crop(img.crop(px),2.0)
            crop_path=crops/(name+".png"); processed.save(crop_path)
            passes=[tesseract_ocr(crop_path,p) for p in cfg["psm"]]
            best=max(passes,key=lambda x:x["score"])
            results[name]={"description":cfg["description"],"pixel_box":px,"crop_path":str(crop_path),
                           "ocr_passes":passes,"selected_psm":best["psm"],"selected_score":best["score"],
                           "selected_text":best["text"],"status":best["status"]}
        return {"image_width":w,"image_height":h,"regions":results}

def render_regions(data):
    out=[]
    for name,r in data["regions"].items():
        out += ["="*72,name.upper(),r["description"],"Selected PSM: {}".format(r["selected_psm"]),"-"*72,
                r["selected_text"].rstrip(),""]
    return "\n".join(out)

def read_serial(serial_folder,output_dir):
    src=Path(serial_folder).resolve(); out=Path(output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    records=[]
    for item in find_travelers(src):
        p=item["path"]; per_log=out/item["log_number"]; per_log.mkdir(parents=True,exist_ok=True)
        if p.suffix.lower() not in IMAGE_EXTS:
            rec={"reader_version":VERSION,"source_path":str(p),"relative_path":item["relative_path"],
                 "log_number":item["log_number"],"traveler_kind":item["traveler_kind"],"warranty":item["warranty"],
                 "status":"unsupported_in_form_mode","regions":{}}
        else:
            ext=extract_regions(p,per_log)
            rec={"reader_version":VERSION,"processed_at_utc":now_utc(),"source_path":str(p),
                 "relative_path":item["relative_path"],"log_number":item["log_number"],
                 "traveler_kind":item["traveler_kind"],"warranty":item["warranty"],"status":"ok",**ext}
            (per_log/"traveler_regions.txt").write_text(render_regions(ext),encoding="utf-8")
        (per_log/"traveler_regions.json").write_text(json.dumps(rec,indent=2,ensure_ascii=False),encoding="utf-8")
        records.append(rec)
    summary={"reader_version":VERSION,"source_serial_folder":str(src),"traveler_count":len(records),
             "successful_form_reads":sum(r["status"]=="ok" for r in records),
             "travelers":[{"log_number":r["log_number"],"traveler_kind":r["traveler_kind"],
                           "warranty":r["warranty"],"status":r["status"],"regions_read":len(r.get("regions",{}))}
                          for r in records]}
    (out/"traveler_reader_v1_3_1_summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    return summary

def main():
    ap=argparse.ArgumentParser(description="Nova DRL Traveler Reader v1.3.1")
    ap.add_argument("serial_folder"); ap.add_argument("--output"); a=ap.parse_args()
    src=Path(a.serial_folder).resolve()
    if not src.exists() or not src.is_dir():
        print("ERROR: Serial folder not found: {}".format(src),file=sys.stderr); return 2
    if not shutil.which("tesseract"):
        print("ERROR: tesseract not found",file=sys.stderr); return 2
    try: require_pillow()
    except Exception as e: print("ERROR: {}".format(e),file=sys.stderr); return 2
    safe=re.sub(r"[^A-Za-z0-9._-]+","_",src.name).strip("_")
    out=Path(a.output).resolve() if a.output else Path.cwd()/"output"/"traveler_reader_v1_3_1"/safe
    s=read_serial(src,out)
    print("\nNova DRL Traveler Reader v{}".format(VERSION))
    print("Travelers found: {}".format(s["traveler_count"]))
    print("Successful DRL form reads: {}".format(s["successful_form_reads"]))
    for r in s["travelers"]:
        print("{} {} status={} regions={}".format(r["log_number"],r["traveler_kind"],r["status"],r["regions_read"]))
    print("\nReports: {}".format(out))
    print("READ-ONLY COMPLETE: No DRL source files were changed.")
    return 0

if __name__=="__main__": raise SystemExit(main())
