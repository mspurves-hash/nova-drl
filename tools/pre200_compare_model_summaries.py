#!/usr/bin/env python3
import argparse,json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('summaries',nargs='+',type=Path); a=ap.parse_args()
    print(f"{'RUN':48s} {'RECALL':>10s}"); print('-'*62)
    for p in a.summaries:
        d=json.loads(p.read_text())
        if 'model' in d: name=f"VISION {d['model']} / {d.get('prompt_mode','')}"; pct=d['all']['recall_pct']
        else: name=f"REASON {d.get('vision_model','')} / {d.get('vision_prompt','')} -> {d.get('reason_model','')}"; pct=d['all']['field_pct']
        print(f'{name[:48]:48s} {pct:9.1f}%')
if __name__=='__main__': main()
