#!/usr/bin/env python3
import json, subprocess, sys, tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
TARGET=HERE/"nova_parts_tier_a_validation_cache_v1_7_5.py"

def main():
    with tempfile.TemporaryDirectory() as td:
        td=Path(td); w=td/"w.jsonl"; s=td/"s.jsonl"; out=td/"o"
        work=[
            {"priority_tier":"A","display_label":"LM324n","observed_variants":["LM324N","Lm 324n"],"repair_event_count":100,"family_count":20},
            {"priority_tier":"A","display_label":"LM2940CT","observed_variants":["LM2940CT"],"repair_event_count":30,"family_count":2},
            {"priority_tier":"A","display_label":"UNSEEN123","observed_variants":["UNSEEN123"],"repair_event_count":22,"family_count":3},
            {"priority_tier":"B","display_label":"OTHER","observed_variants":["OTHER"],"repair_event_count":50,"family_count":1},
        ]
        seed=[
            {"match_labels":["LM324N"],"canonical_label":"LM324N","validation_status":"validated_authoritative_exact","identity_type":"semiconductor_part","manufacturer":"TI","confidence":"high","source_authority":"primary","source_url":"x","notes":"ok"},
            {"match_labels":["LM2940CT"],"canonical_label":None,"validation_status":"deferred_suffix_sensitive","identity_type":"family_stem","manufacturer":"TI","confidence":"high","source_authority":"primary","source_url":"y","notes":"needs voltage"},
        ]
        w.write_text(''.join(json.dumps(x)+'\n' for x in work))
        s.write_text(''.join(json.dumps(x)+'\n' for x in seed))
        run=subprocess.run([sys.executable,str(TARGET),"--worklist",str(w),"--seed",str(s),"--output-root",str(out)],capture_output=True,text=True)
        assert run.returncode==0,run.stdout+"\n"+run.stderr
        val=[json.loads(x) for x in (out/"validated_global_identity_cache_v1_7_5.jsonl").read_text().splitlines() if x.strip()]
        de=[json.loads(x) for x in (out/"deferred_global_identities_v1_7_5.jsonl").read_text().splitlines() if x.strip()]
        pe=[json.loads(x) for x in (out/"pending_tier_a_identities_v1_7_5.jsonl").read_text().splitlines() if x.strip()]
        assert len(val)==1 and val[0]["canonical_label"]=="LM324N"
        assert len(de)==1 and de[0]["display_label"]=="LM2940CT"
        assert len(pe)==1 and pe[0]["display_label"]=="UNSEEN123"
        assert all(x["display_label"]!="OTHER" for x in val+de+pe)
        plan=td/"plan"
        r2=subprocess.run([sys.executable,str(TARGET),"--worklist",str(w),"--seed",str(s),"--output-root",str(plan),"--plan-only"],capture_output=True,text=True)
        assert r2.returncode==0 and not plan.exists()
    print("PASS: Nova DRL Tier-A Global Validation Cache v1.7.5 tests")
if __name__=="__main__": main()
