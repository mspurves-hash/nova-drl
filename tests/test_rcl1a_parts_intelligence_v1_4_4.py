#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "analysis" / "nova_rcl1a_parts_intelligence_v1_4_4.py"
spec = importlib.util.spec_from_file_location("v144", SCRIPT)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def mention(mid, event, pn, desc, qty, quote):
    return {
        "mention_id": mid,
        "repair_event_id": event,
        "source_record_ids": ["src_" + event],
        "raw_quote": quote,
        "part_number": pn,
        "description": desc,
        "quantity": qty,
        "quantity_text": str(qty) if qty is not None else None,
        "action": "replaced",
        "uncertain": False,
    }


mentions = [
    mention("m1", "log_230101001", "FDH038AN08A1", "power MOSFET", 8, "replaced 8 FDH038AN08A1"),
    mention("m2", "log_230101002", "038AN08A1", "power MOSFET", 6, "replaced 6 038AN08A1"),
    mention("m3", "log_230101001", "IXFX24N/100Q3", "power MOSFET", 5, "replaced 5 IXFX24N/100Q3"),
    mention("m4", "log_230101003", "IXFX24N100", "power MOSFET", 4, "replaced 4 IXFX24N100"),
    mention("m5", "log_230101001", "KLK 15A 600V", "15 amp 600V fuse", 2, "replaced 2 KLK 15A 600V fuses"),
    mention("m6", "log_230101002", "0KLK015.T", "15 amp 600V fuse", 1, "replaced 1 0KLK015.T fuse"),
    mention("m7", "log_230101004", "KCK 15 Amp 600V", "15 amp 600V fuse", 1, "used KCK 15 Amp 600V fuse"),
    mention("m8", "log_230101005", "15 Amp 250V", "15 amp 250V standard fuse", 1, "replaced 15 Amp 250V standard fuse"),
    mention("m9", "log_230101006", None, "SMART board", None, "replaced SMART board"),
]

family_map = {
    "descriptors": [
        {"descriptor_id": "d1", "mention_ids": ["m1", "m2", "m3", "m4"]},
        {"descriptor_id": "d2", "mention_ids": ["m5", "m6"]},
        {"descriptor_id": "d3", "mention_ids": ["m7"]},
        {"descriptor_id": "d4", "mention_ids": ["m8"]},
        {"descriptor_id": "d5", "mention_ids": ["m9"]},
    ],
    "families": [
        {"part_family_id": "pf_mos", "label": "MOSFET Family", "member_descriptor_ids": ["d1"]},
        {"part_family_id": "pf_fuse600a", "label": "Fuses with 15 Amp and 600V rating", "member_descriptor_ids": ["d2"]},
        {"part_family_id": "pf_fuse600b", "label": "KCK 15Amp 600V", "member_descriptor_ids": ["d3"]},
        {"part_family_id": "pf_fuse250", "label": "15Amp 250V Standard", "member_descriptor_ids": ["d4"]},
        {"part_family_id": "pf_board", "label": "SMART Board Family", "member_descriptor_ids": ["d5"]},
    ],
}

hint_id, hint_label = mod.family_hint_maps(family_map)
obs = mod.build_pn_observations(mentions, hint_id, hint_label)
assert len(obs) == 8, len(obs)
by_pn = {x["observed_pn"]: x for x in obs}
assert by_pn["FDH038AN08A1"]["repair_event_count"] == 1
assert "MOSFET Family" in by_pn["IXFX24N/100Q3"]["provisional_family_labels"]

by_id = {x["observed_pn_id"]: x for x in obs}
id_of = {x["observed_pn"]: x["observed_pn_id"] for x in obs}

# PN grouping keeps the two actual MOSFET PNs separate while consolidating their OCR forms.
parsed_pn = {
    "clusters": [
        {"likely_pn": "FDH038AN08A1", "confidence": "high", "member_observed_pn_ids": [id_of["FDH038AN08A1"], id_of["038AN08A1"]]},
        {"likely_pn": "IXFX24N100Q3", "confidence": "high", "member_observed_pn_ids": [id_of["IXFX24N/100Q3"], id_of["IXFX24N100"]]},
        {"likely_pn": "KLK-15", "confidence": "medium", "member_observed_pn_ids": [id_of["KLK 15A 600V"], id_of["0KLK015.T"]]},
        {"likely_pn": "KCK 15 Amp 600V", "confidence": "medium", "member_observed_pn_ids": [id_of["KCK 15 Amp 600V"]]},
        {"likely_pn": "15 Amp 250V", "confidence": "high", "member_observed_pn_ids": [id_of["15 Amp 250V"]]},
    ]
}
pn_groups = mod.validate_pn_clusters(parsed_pn, list(by_id), by_id)
pn_groups = mod.consolidate_same_likely_pn(pn_groups)
labels = {x["likely_pn"] for x in pn_groups}
assert "FDH038AN08A1" in labels
assert "IXFX24N100Q3" in labels
assert "KCK 15 Amp 600V" in labels
assert "15 Amp 250V" in labels

# An ungrounded model invention is rejected in favor of observed evidence.
wrong = mod.validate_pn_clusters({"clusters": [{"likely_pn": "TOTALLYWRONG123", "confidence": "high", "member_observed_pn_ids": [id_of["FDH038AN08A1"]]}]}, [id_of["FDH038AN08A1"]], by_id)
assert wrong[0]["likely_pn"] == "FDH038AN08A1"

pn_map = {"observations": obs, "groups": pn_groups}
signals = mod.build_signal_rows(mentions, pn_map, family_map)
sig_by_label = {x["label"]: x for x in signals}
assert "FDH038AN08A1" in sig_by_label
assert "IXFX24N100Q3" in sig_by_label
assert "SMART board" in sig_by_label

# Build a functional family view: the two exact MOSFET PNs may share one MOSFET family.
mos_sigs = [sig_by_label["FDH038AN08A1"]["signal_id"], sig_by_label["IXFX24N100Q3"]["signal_id"]]
f600_sigs = [sig_by_label["KLK-15"]["signal_id"], sig_by_label["KCK 15 Amp 600V"]["signal_id"]]
f250_sig = sig_by_label["15 Amp 250V"]["signal_id"]
board_sig = sig_by_label["SMART board"]["signal_id"]

parsed_fam = {
    "families": [
        {"label": "MOSFET Family", "functional_class": "semiconductor", "member_signal_ids": mos_sigs},
        {"label": "15A / 600V Fuse Family", "functional_class": "fuse_protection", "member_signal_ids": f600_sigs},
        {"label": "15A / 250V Fuse Family", "functional_class": "fuse_protection", "member_signal_ids": [f250_sig]},
        {"label": "SMART Board Family", "functional_class": "board_assembly", "member_signal_ids": [board_sig]},
    ]
}
by_sig = {x["signal_id"]: x for x in signals}
temp = mod.validate_stage1_families(parsed_fam, list(by_sig), by_sig, 1)
# Treat stage-1 as final via merge-singleton behavior.
merge_parsed = {"families": [
    {"label": t["label"], "functional_class": t["functional_class"], "member_temp_family_ids": [t["temp_family_id"]]}
    for t in temp
]}
final_fams = mod.validate_merge(merge_parsed, temp)
family_result = {"signals": signals, "families": final_fams}

pn_usage = mod.aggregate_pn_usage(mentions, pn_map)
pn_stats = {x["likely_pn"]: x for x in pn_usage}
assert pn_stats["FDH038AN08A1"]["repairs_containing_pn"] == 2
assert pn_stats["FDH038AN08A1"]["recorded_pieces"] == 14
assert pn_stats["IXFX24N100Q3"]["repairs_containing_pn"] == 2
assert pn_stats["IXFX24N100Q3"]["recorded_pieces"] == 9

family_rows, _ = mod.aggregate_functional_families(mentions, family_result)
fam_stats = {x["label"]: x for x in family_rows}
# m1 and m3 share log_230101001, so MOSFET family counts three distinct repair events, not four mentions.
assert fam_stats["MOSFET Family"]["repairs_containing_family"] == 3
assert fam_stats["MOSFET Family"]["recorded_pieces"] == 23
assert fam_stats["15A / 600V Fuse Family"]["repairs_containing_family"] == 3
assert fam_stats["15A / 600V Fuse Family"]["recorded_pieces"] == 4
assert fam_stats["15A / 250V Fuse Family"]["repairs_containing_family"] == 1
assert fam_stats["SMART Board Family"]["quantity_unstated_mentions"] == 1

# Rating conflicts should discourage candidate merging.
a = by_pn["KCK 15 Amp 600V"]
b = by_pn["15 Amp 250V"]
assert mod.pn_candidate_similarity(a, b) < mod.pn_candidate_similarity(by_pn["FDH038AN08A1"], by_pn["038AN08A1"])

print("PASS: Nova DRL RCL1A 80/20 Parts Intelligence v1.4.4 tests")
