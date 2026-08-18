#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analysis" / "nova_rcl1a_indexed_focused_recovery_v1_4_3.py"
spec = importlib.util.spec_from_file_location("rclidx", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def make_index(db: Path, rows, share_root: Path):
    conn = sqlite3.connect(str(db))
    conn.executescript("""
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE files (
      id INTEGER PRIMARY KEY,
      relative_path TEXT NOT NULL UNIQUE,
      filename TEXT NOT NULL,
      filename_search TEXT NOT NULL,
      parent_path TEXT NOT NULL,
      parent_search TEXT NOT NULL,
      extension TEXT NOT NULL,
      size INTEGER NOT NULL,
      mtime_ns INTEGER NOT NULL,
      detected_log TEXT,
      search_text TEXT NOT NULL,
      file_kind TEXT NOT NULL,
      first_seen_scan INTEGER NOT NULL,
      last_changed_scan INTEGER NOT NULL,
      updated_at TEXT NOT NULL
    );
    """)
    conn.execute("INSERT INTO meta(key,value) VALUES('schema_version','1')")
    conn.execute("INSERT INTO meta(key,value) VALUES('share_root',?)", (str(share_root),))
    for rel, log, kind in rows:
        p = Path(rel)
        ext = p.suffix.lower()
        parent = str(p.parent)
        conn.execute("""INSERT INTO files(relative_path,filename,filename_search,parent_path,parent_search,extension,size,mtime_ns,detected_log,search_text,file_kind,first_seen_scan,last_changed_scan,updated_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (rel,p.name,p.name.casefold(),parent,parent.casefold(),ext,10,1,log,rel.casefold(),kind,1,1,'now'))
    conn.commit(); conn.close()


def base_args(td: Path, db: Path, share: Path):
    return argparse.Namespace(
        index_query="RCL1A LINE", index_db=str(db), share_root=str(share),
        source_images_root=None, source_pdf=None, image_name_regex=r"line\s*card", output_root=str(td / "out"),
        reuse_v140_root=str(td / "none"), vision_model="fake-vision", reason_model="fake-reason",
        vision_num_ctx=16384, vision_num_predict=1024, reason_num_ctx=16384, extract_num_predict=1024,
        normalize_num_predict=1024, render_dpi=300, use_focus_crop=False, duplicate_text_threshold=0.99,
        duplicate_dhash_threshold=22, normalize_candidate_threshold=0.72, normalize_batch_descriptors=60,
        timeout=10, force_focused_acquisition=False, force_dedupe=False, force_extraction=False,
        force_normalize=False, focused_acquire_only=False,
    )


def test_runtime_is_blind_to_prior_benchmark_answers():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "IXFX24N100Q3" not in text
    assert "FDH038AN08A1" not in text
    assert "156 unique" not in text.lower()


def test_index_selector_matches_everything_style_and_filters_noise():
    with tempfile.TemporaryDirectory() as td0:
        td = Path(td0); share = td / "share"; share.mkdir(); db = td / "idx.sqlite"
        paths = [
            ("000 tech/PS - RCL1A-1D-W3 SN 1/230101001 Line Card Original.jpg", "230101001", "image"),
            ("000 tech/PS - RCL1A-1D-W3 SN 1/.picasaoriginals/230101001 Line Card Original.jpg", "230101001", "image"),
            ("000 tech/PS - RCL1A-1D-W3 SN 2/230101002 Line Card Original.pdf", "230101002", "pdf"),
            ("000 tech/PS - RCL1A-1D-W3 SN 3/230101003 UR Email Notification Line 00040.msg", "230101003", "document"),
            ("Input/RCL1A-1D-W3 All Line Cards.pdf", None, "pdf"),
            ("000 tech/PS - OTHER/230101004 Line Card Original.jpg", "230101004", "image"),
        ]
        for rel, _, _ in paths:
            f = share / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(b"x")
        make_index(db, paths, share)
        args = base_args(td, db, share)
        sel = mod.select_index_line_cards(args)
        assert sel["raw_index_matches"] == 5
        assert sel["selected_document_count"] == 2
        names = {Path(r["relative_path"]).name for r in sel["selected_documents"]}
        assert "230101001 Line Card Original.jpg" in names
        assert "230101002 Line Card Original.pdf" in names
        assert sel["excluded_counts"]["picasa_backup"] == 1
        assert sel["excluded_counts"]["filename_not_line_card"] == 1
        assert sel["excluded_counts"]["combined_all_line_cards"] == 1


def test_indexed_source_records_preserve_real_share_path_and_log():
    with tempfile.TemporaryDirectory() as td0:
        td = Path(td0); share = td / "share"; share.mkdir(); db = td / "idx.sqlite"
        rel = "repairs/PS - RCL1A-1D-W3 SN 1/230101001 Line Card Original.jpg"
        f = share / rel; f.parent.mkdir(parents=True); f.write_bytes(b"image")
        make_index(db, [(rel, "230101001", "image")], share)
        args = base_args(td, db, share)
        kind, srcs, pdfsha = mod.make_source_records(args)
        assert kind == "drl_index" and pdfsha is None and len(srcs) == 1
        assert srcs[0]["source_path"] == str(f)
        assert srcs[0]["log_number"] == "230101001"
        assert srcs[0]["index_query"] == "RCL1A LINE"


def test_quote_binding_and_quantity_policy():
    parsed = {"record_class":"repair_record","replacements":[
        {"raw_quote":"Replaced 3 QX-100 devices","part_number":"QX-100","description":"device","quantity":3,"quantity_text":"3","action":"replaced","uncertain":False},
        {"raw_quote":"invented thing","part_number":"BAD","description":"bad","quantity":1,"action":"replaced","uncertain":False},
    ]}
    rc, rows, rejected = mod.validate_extraction(parsed,"log_230101001",["s1"],["Replaced 3 QX-100 devices"])
    assert rc == "repair_record" and len(rows) == 1 and rows[0]["quantity"] == 3
    assert rejected and rejected[0]["reason"] == "raw_quote_not_bound"


def test_same_log_multiple_sources_one_repair_event():
    records = [
        {"source_record_id":"a","source_index":1,"source_path":"/x/230101001 Line Card Original.jpg","log_number":"230101001"},
        {"source_record_id":"b","source_index":2,"source_path":"/x/230101001 Line Card Warranty.jpg","log_number":"230101001"},
        {"source_record_id":"c","source_index":3,"source_path":"/x/230101002 Line Card Original.jpg","log_number":"230101002"},
    ]
    dedupe={"representative_index_by_record_id":{"a":"a","b":"b","c":"c"}}
    events=mod.build_repair_events(records,dedupe)
    assert len(events)==2


def test_frequency_counts_events_not_images():
    mentions=[
        {"mention_id":"m1","repair_event_id":"log_1","part_number":"QX100","description":"device","quantity":2,"uncertain":False},
        {"mention_id":"m2","repair_event_id":"log_1","part_number":"QX100","description":"device","quantity":1,"uncertain":False},
        {"mention_id":"m3","repair_event_id":"log_2","part_number":"QX100","description":"device","quantity":None,"uncertain":False},
    ]
    fmap={"descriptors":[{"descriptor_id":"d1","mention_ids":["m1","m2","m3"]}],"families":[{"part_family_id":"pf1","label":"QX100","member_descriptor_ids":["d1"],"origin":"test"}]}
    rows=mod.count_frequencies(mentions,fmap)
    assert rows[0]["repairs_containing_part"]==2 and rows[0]["recorded_pieces"]==3


def test_synthetic_index_first_pipeline_without_network():
    with tempfile.TemporaryDirectory() as td0:
        td=Path(td0); share=td/"share"; share.mkdir(); db=td/"idx.sqlite"
        paths=[
            ("PS - RCL1A/unit/230101001 Line Card Original.jpg","230101001","image"),
            ("PS - RCL1A/unit/230101001 Line Card Warranty.jpg","230101001","image"),
            ("PS - RCL1A/unit/230101002 Line Card Original.jpg","230101002","image"),
        ]
        for i,(rel,_,_) in enumerate(paths):
            f=share/rel; f.parent.mkdir(parents=True,exist_ok=True); f.write_bytes(b"same-image" if i<2 else b"other-image")
        make_index(db,paths,share)
        args=base_args(td,db,share)
        old_call, old_json, old_info = mod.call_ollama, mod.call_json_with_retry, mod.model_info
        try:
            mod.model_info=lambda name:{"requested_model":name,"available":True,"digest":"fake-digest"}
            def fake_vision(model,prompt,*,image_paths,num_ctx,num_predict,timeout):
                return "Replaced 2 QX-100 devices" if "230101001" in image_paths[0].name else "Changed fan"
            def fake_json(model,prompt,*,num_ctx,num_predict,timeout,retries,cache_dir):
                if prompt.startswith(mod.EXTRACTION_PROMPT):
                    if "QX-100" in prompt:
                        return {"record_class":"repair_record","replacements":[{"raw_quote":"Replaced 2 QX-100 devices","part_number":"QX-100","description":"power device","quantity":2,"quantity_text":"2","action":"replaced","uncertain":False}]},[]
                    return {"record_class":"repair_record","replacements":[{"raw_quote":"Changed fan","part_number":None,"description":"fan","quantity":1,"quantity_text":None,"action":"replaced","uncertain":False}]},[]
                if prompt.startswith(mod.NORMALIZE_BLOCK_PROMPT): return {"clusters":[]},[]
                return {"duplicate":False,"confidence":"high","reason":"different"},[]
            mod.call_ollama=fake_vision; mod.call_json_with_retry=fake_json
            kind,srcs,pdfsha=mod.make_source_records(args)
            assert kind=="drl_index" and len(srcs)==3 and pdfsha is None
            recs=mod.acquire_focused_evidence(args,srcs,{})
            dedupe=mod.adjudicate_duplicates(args,recs)
            assert dedupe["duplicate_records_excluded"]==1
            ext=mod.extract_replacements(args,recs,dedupe)
            assert len(ext["repair_events"])==2 and len(ext["mentions"])==2
            fmap=mod.normalize_part_families(args,ext["mentions"])
            freq=mod.count_frequencies(ext["mentions"],fmap)
            assert sum(x["recorded_pieces"] for x in freq)==3
        finally:
            mod.call_ollama,mod.call_json_with_retry,mod.model_info=old_call,old_json,old_info


def test_no_qdrant_execution_path():
    text=SCRIPT.read_text(encoding="utf-8").lower()
    assert "qdrant" in text and "6333" not in text and "/collections/" not in text


def main():
    tests=[v for k,v in globals().items() if k.startswith("test_") and callable(v)]
    for t in tests: t()
    print("PASS: Nova DRL RCL1A Indexed Focused Recovery v1.4.3 tests")

if __name__ == "__main__": main()
