#!/usr/bin/env python3
import json, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_parts_validated_cache_impact_v1_7_6.py"

def wjsonl(path, rows):
    path.write_text("".join(json.dumps(x)+"\n" for x in rows))

def main():
    with tempfile.TemporaryDirectory() as td:
        td=Path(td)
        g=td/"g.jsonl"; fr=td/"fr.jsonl"; fs=td/"fs.jsonl"; m=td/"m.json"; c=td/"c.jsonl"
        wjsonl(g,[
            {"global_identity_id":"g1","identity_key":"LM324N","repair_event_ids":["e1","e2"],"families":["F1","F2"],"mention_count":3,"recorded_pieces":3},
            {"global_identity_id":"g2","identity_key":"D45H11","repair_event_ids":["e2","e3"],"families":["F1"],"mention_count":2,"recorded_pieces":2},
        ])
        wjsonl(fr,[
            {"candidate_kind":"explicit_part_number","identity_key":"LM324N","family":"F1","repair_event_ids":["e1"]},
            {"candidate_kind":"explicit_part_number","identity_key":"LM324N","family":"F2","repair_event_ids":["e2"]},
            {"candidate_kind":"explicit_part_number","identity_key":"D45H11","family":"F1","repair_event_ids":["e2","e3"]},
        ])
        wjsonl(fs,[
            {"family":"F1","replacement_repair_events":4},
            {"family":"F2","replacement_repair_events":2},
        ])
        m.write_text(json.dumps({"counts":{"replacement_repair_events":10,"recurring_output_repair_events":5}}))
        wjsonl(c,[
            {"global_identity_id":"g1","display_label":"LM324n","canonical_label":"LM324N","validated_manufacturer":"TI","validation_confidence":"high"},
            {"global_identity_id":"g2","display_label":"D45H11","canonical_label":"D45H11","validated_manufacturer":"ST","validation_confidence":"high"},
        ])
        run=subprocess.run([
            sys.executable,str(TARGET),
            "--global-identities",str(g),
            "--family-recurring",str(fr),
            "--family-summary",str(fs),
            "--manifest",str(m),
            "--validated-cache",str(c),
        ],capture_output=True,text=True)
        assert run.returncode==0,run.stdout+"\n"+run.stderr
        assert "Distinct repair events touched:         3" in run.stdout
        assert "Share of all replacement repairs:       30.0%" in run.stdout
        assert "Share of recurring-output repairs:      60.0%" in run.stdout
        assert "Equipment families touched:             2" in run.stdout
    print("PASS: Nova DRL Validated Parts Cache Impact Audit v1.7.6 tests")

if __name__=="__main__":
    main()
