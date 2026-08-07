#!/usr/bin/env python3
import importlib.util
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ingest" / "nova_surveyor_v1.py"

spec = importlib.util.spec_from_file_location("nova_surveyor", str(SCRIPT))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

refs = {
    "oems": ["GENMARK"],
    "technicians": ["ERICH"],
    "sites": {"MTV": "Micron Technology Virginia"},
}

folder = "RBT - GB8-MT GENMARK SN 80050608 UTI MICRON MTV ERICH"
parsed = mod.parse_repair_folder_name(folder, refs)

assert parsed["equipment_type"] == "RBT"
assert parsed["model"] == "GB8-MT"
assert parsed["oem"] == "GENMARK"
assert parsed["serial_number"] == "80050608"
assert parsed["customer"] == "UTI MICRON"
assert parsed["site_code"] == "MTV"
assert parsed["site_name"] == "Micron Technology Virginia"
assert parsed["technician"] == "ERICH"
assert parsed["parse_confidence"] == "high"

assert mod.extract_log_number("100831011 Line Card Original.jpg") == "100831011"

with tempfile.TemporaryDirectory() as tmp:
    source = Path(tmp) / folder
    source.mkdir()
    (source / "100831011 Line Card Original.jpg").write_bytes(b"test")
    (source / "Photo 1.jpg").write_bytes(b"photo")

    report = mod.survey_folder(source, refs, hash_files=True)
    assert report["summary"]["file_count"] == 2
    assert report["summary"]["primary_traveler_count"] == 1
    assert report["summary"]["primary_log_numbers"] == ["100831011"]

print("PASS: Nova Surveyor v1 basic tests")
