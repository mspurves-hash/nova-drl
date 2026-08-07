#!/usr/bin/env python3
import importlib.util, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "reader", str(ROOT/"ingest"/"nova_traveler_reader_v1_3.py")
)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)

with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp)/"RBT - GB8-MT GENMARK SN 80050477 UTI MTV ERICH"
    p.mkdir()
    (p/"120920001 Line Card Original.jpg").write_bytes(b"not-a-real-jpeg")
    (p/"130402001 Line Card Warranty.JPG").write_bytes(b"not-a-real-jpeg")
    (p/"191029005 Receiving Pic (1).JPG").write_bytes(b"not-a-real-jpeg")

    found = reader.find_travelers(p)
    assert len(found) == 2

    by_log = {x["log_number"]: x for x in found}
    assert by_log["120920001"]["traveler_kind"] == "original"
    assert by_log["120920001"]["warranty"] is False
    assert by_log["130402001"]["traveler_kind"] == "warranty"
    assert by_log["130402001"]["warranty"] is True

    blank = reader.blank_structured()
    assert set(blank) == set(reader.STRUCTURED_FIELDS)
    assert all(v is None for v in blank.values())

print("PASS: Nova Traveler Reader v1.3 tests")
