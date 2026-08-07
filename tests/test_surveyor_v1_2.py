#!/usr/bin/env python3
import importlib.util, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("s",str(ROOT/"ingest"/"nova_surveyor_v1_2.py"))
s=importlib.util.module_from_spec(spec); spec.loader.exec_module(s)
refs={"oems":["GENMARK"],"technicians":["ERICH"],"sites":{"MTV":"Micron Technology Virginia"}}
d=s.decode_log("230809002"); assert d["repair_date"]=="2023-08-09" and d["daily_sequence"]=="002"
with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp)/"RBT - GB8-MT GENMARK SN 80050477 UTI MTV ERICH"; p.mkdir()
    for n in ["120920001 Line Card Original.jpg","130402001 Line Card Warranty.JPG","130402001 Robot Test Report.PDF",
              "191029005 Line Card Original.jpg","191029005 Receiving Pic (1).JPG","191029005 Return Shipment Packaging (1).JPG"]:
        (p/n).write_bytes(b"x")
    (p/"Copy of Floppy").mkdir()
    r=s.serial_history(p,refs,False)
    assert r["summary"]["repair_event_count"]==3
    assert r["summary"]["warranty_event_count"]==1
    assert r["summary"]["first_repair_date"]=="2012-09-20"
    assert r["summary"]["most_recent_repair_date"]=="2019-10-29"
    assert any(x["role"]=="unit_configuration_archive" for x in r["unit_level_evidence"])
print("PASS: Nova Surveyor v1.2 tests")
