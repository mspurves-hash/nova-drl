#!/usr/bin/env python3
from pathlib import Path
import importlib.util
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ingest" / "nova_gb8_large_scale_acquisition_v1_0.py"
spec = importlib.util.spec_from_file_location("gb8_launcher", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "RBT - GB8-MT GENMARK SN 1").mkdir()
    (root / "RBT - GB8S GENMARK SN 2").mkdir()
    (root / "RBT - GB4S GENMARK SN 3").mkdir()
    (root / "OTHER - GB8").mkdir()
    selected = mod.select_unit_folders(root, "RBT - GB8")
    names = [x.name for x in selected]
    assert names == ["RBT - GB8-MT GENMARK SN 1", "RBT - GB8S GENMARK SN 2"], names

assert mod.DEFAULT_FOLDER_PREFIX == "RBT - GB8"
assert "whole_traveler_corpus_v1_3_5_1" in str(mod.DEFAULT_OUTPUT_ROOT)
print("PASS: Nova DRL GB8 Large-Scale Acquisition Launcher v1.0 tests")
