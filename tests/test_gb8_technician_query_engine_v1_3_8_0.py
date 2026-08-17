#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analysis" / "nova_gb8_technician_query_engine_v1_3_8_0.py"
spec = importlib.util.spec_from_file_location("qe", SCRIPT)
qe = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(qe)


def group(gid, lane, label, serials, logs, variants, areas=None):
    return {
        "group_id": gid,
        "lane": lane,
        "concept_label": label,
        "concept_key": qe.norm(label),
        "distinct_serial_count": len(serials),
        "distinct_log_count": len(logs),
        "candidate_count": len(variants),
        "serial_numbers": serials,
        "logs": logs,
        "raw_variants": variants,
        "v1_3_7_3_service_areas": areas or [],
    }


def variant(cid, log, serial, text):
    return {"candidate_id": cid, "log_number": log, "serial_number": serial, "raw_source_text": text, "source_sha256": "h"+cid}


tech = [
    group("g_y", "diagnostics", "Y Axis Drift", ["80050608", "80050619"], ["130130006", "150413001"], [
        variant("c1", "130130006", "80050608", "Y Drift problem and intermittent off position"),
        variant("c2", "150413001", "80050619", "Y axis would drift; changed encoder - no help"),
    ], ["Servo / drift / homing"]),
    group("g_v", "diagnostics", "Vacuum Issues", ["80050608", "80070633"], ["130130006", "131101001"], [
        variant("c3", "130130006", "80050608", "Vacuum leak at A1 line"),
        variant("c4", "131101001", "80070633", "Found Vacuum Leaks by Solenoids"),
    ], ["Vacuum system"]),
    group("g_b", "repairs", "A1+A2 Belts", ["80050608", "80070633", "80070635"], ["130130006", "140625005", "150528001"], [
        variant("c5", "130130006", "80050608", "Replaced A1+A2 belts"),
        variant("c6", "140625005", "80070633", "New lower A1+A2 belts"),
        variant("c7", "150528001", "80070635", "Adjusted A1+A2 belt tension"),
    ], ["Belts & tension", "A1/A2 arms & geometry"]),
]
ref = [group("g_fa", "terminology", "FA RPT", ["80050608"], ["130130006"], [variant("c8", "130130006", "80050608", "FA RPT")])]
svc = [
    {"service_area":"Belts & tension","distinct_serial_coverage":3,"distinct_log_coverage":3,"recurring_group_count":1,"top_patterns":[]},
    {"service_area":"Vacuum system","distinct_serial_coverage":2,"distinct_log_coverage":2,"recurring_group_count":1,"top_patterns":[]},
]
stock = [
    {"item":"Belts","distinct_serial_coverage":3,"distinct_log_coverage":3,"recurring_group_count":1,"example_pattern_labels":["A1+A2 Belts"]},
    {"item":"Encoders","distinct_serial_coverage":2,"distinct_log_coverage":2,"recurring_group_count":1,"example_pattern_labels":["Y Axis Drift"]},
]

with tempfile.TemporaryDirectory() as td:
    p=Path(td)
    (p/'technician_patterns_v1_3_7_3.json').write_text(json.dumps({"signal_cleaner_version":"1.3.7.3","source_recurring_group_count":4,"technician_group_count":3,"groups":tech,"accepted_fact_count":0,"qdrant_entries_created":0}),encoding='utf-8')
    (p/'reference_patterns_v1_3_7_3.json').write_text(json.dumps({"signal_cleaner_version":"1.3.7.3","reference_group_count":1,"groups":ref}),encoding='utf-8')
    (p/'service_area_rollup_v1_3_7_3.json').write_text(json.dumps({"signal_cleaner_version":"1.3.7.3","areas":svc}),encoding='utf-8')
    (p/'stocking_attention_v1_3_7_3.json').write_text(json.dumps({"signal_cleaner_version":"1.3.7.3","items":stock}),encoding='utf-8')
    (p/'technician_signal_manifest_v1_3_7_3.json').write_text(json.dumps({"signal_cleaner_version":"1.3.7.3","source_recurring_group_count":4,"technician_group_count":3,"reference_group_count":1,"accepted_fact_count":0,"qdrant_entries_created":0}),encoding='utf-8')

    idx=qe.load_index(p)
    assert idx['counts']=={'source':4,'technician':3,'reference':1}
    aliases=qe.load_aliases(None)

    r=qe.execute_query(idx,"Y axis drifting",aliases,"auto",5,3,False)
    assert r['mode']=='search'
    assert r['results'] and r['results'][0]['group']['group_id']=='g_y', r

    r=qe.execute_query(idx,"vacuum leak",aliases,"auto",5,3,False)
    assert r['results'] and r['results'][0]['group']['group_id']=='g_v', r

    r=qe.execute_query(idx,"what normally gets replaced",aliases,"auto",5,3,False)
    assert r['mode']=='stocking' and r['items'][0]['item']=='Belts'

    r=qe.execute_query(idx,"common service areas",aliases,"auto",5,3,False)
    assert r['mode']=='service-areas' and r['areas'][0]['service_area']=='Belts & tension'

    r=qe.execute_query(idx,"serial 80050608",aliases,"auto",10,3,False)
    assert r['mode']=='serial' and r['match_count']==3
    assert all(all(str(e.get('serial_number'))=='80050608' for e in row['examples']) for row in r['results'])

    r=qe.execute_query(idx,"log 130130006",aliases,"auto",10,3,False)
    assert r['mode']=='log' and r['match_count']==3
    assert all(all(str(e.get('log_number'))=='130130006' for e in row['examples']) for row in r['results'])

    # Reference is opt-in.
    r=qe.execute_query(idx,"FA RPT",aliases,"search",10,3,False)
    assert all(x['group']['group_id']!='g_fa' for x in r['results'])
    r=qe.execute_query(idx,"FA RPT",aliases,"search",10,3,True)
    assert any(x['group']['group_id']=='g_fa' for x in r['results'])

print("PASS: Nova DRL GB8 Technician Query Engine v1.3.8.0 tests")
