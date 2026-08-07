#!/usr/bin/env python3
import importlib.util
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ingest" / "nova_surveyor_v1_1.py"

spec = importlib.util.spec_from_file_location("nova_surveyor_v1_1", str(SCRIPT))
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

assert mod.model_matches("GB8-MT", "GB8")
assert mod.model_matches("GB8", "GB8")
assert not mod.model_matches("GB7", "GB8")
assert mod.extract_log_number("100831011 Line Card Original.jpg") == "100831011"

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp) / "tech scans"
    root.mkdir()

    gb8 = root / folder
    gb8.mkdir()
    (gb8 / "100831011 Line Card Original.jpg").write_bytes(b"traveler")
    (gb8 / "Photo 1.jpg").write_bytes(b"photo")

    gb7 = root / "RBT - GB7 GENMARK SN 12345 CUSTOMER MTV ERICH"
    gb7.mkdir()

    discovery = mod.discover_repair_folders(
        root, refs, equipment_type="RBT", oem="GENMARK", model="GB8"
    )
    assert discovery["summary"]["matching_repair_folders"] == 1
    assert discovery["repair_folders"][0]["model"] == "GB8-MT"

    report = mod.survey_repair_folder(gb8, refs, hash_files=False)
    assert report["summary"]["file_count"] == 2
    assert report["summary"]["primary_traveler_count"] == 1
    assert report["summary"]["primary_log_numbers"] == ["100831011"]
    assert report["hashing_enabled"] is False

print("PASS: Nova Surveyor v1.1 tests")
