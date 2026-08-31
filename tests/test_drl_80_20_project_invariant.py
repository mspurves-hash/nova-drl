#!/usr/bin/env python3
"""DRL Nova hard project invariant regression gate.

This test is intentionally simple and high-signal.  It must remain in future
DRL Nova releases.  The normal product knowledge path is corpus-derived only.
Matt's expert input is a sanity/exception layer unless he explicitly requests a
specific rule be promoted into the system.
"""
from pathlib import Path
import importlib.util, sys

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "tools" / "nova_drl_unified_knowledge_index_v1_5_7.py"

spec = importlib.util.spec_from_file_location("nova_drl_policy_gate_v157", UI)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

assert mod.DRL_80_20_HARD_INVARIANT is True
assert mod.ALLOW_EXPERT_KNOWLEDGE_OVERRIDES is False
assert mod.EXPERT_INPUT_ROLE == "sanity_check_only_unless_explicitly_promoted"
assert mod.PRODUCT_PART_MIN_REPAIRS >= 2
assert not hasattr(mod, "KIT_CONFIG")
assert not hasattr(mod, "load_standard_kit_rules")
assert not hasattr(mod, "qualifying_repair_event_ids")

source = UI.read_text(encoding="utf-8").casefold()
for forbidden in (
    "human_confirmed_standard_kit",
    "drl_standard_repair_kits",
    "expert-confirmed standard repair-kit",
):
    assert forbidden not in source, f"forbidden expert-override path found: {forbidden}"

print("PASS: DRL Nova hard 80/20 project invariant — expert overrides disabled")
