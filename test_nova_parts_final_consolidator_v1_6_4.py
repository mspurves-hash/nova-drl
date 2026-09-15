#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_parts_final_consolidator_v1_6_4.py"
RULES = HERE / "rcl1a_final_consolidation_rules_v1_6_4.jsonl"

spec = importlib.util.spec_from_file_location("consolidator", TARGET)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

FAMILY = "PS - RCL1A-1D-W3 RACAL"

def candidate(cid, label, events, pieces, kind="explicit_part_number", examples=None, desc=None):
    return {
        "candidate_id": cid,
        "display_label": label,
        "candidate_kind": kind,
        "part_number_variants": [label] if kind == "explicit_part_number" else [],
        "description_variants": desc or ([label] if kind == "description_only" else []),
        "evidence_examples": examples or [label],
        "repair_event_ids": list(events),
        "repair_event_count": len(set(events)),
        "mention_count": len(events),
        "recorded_pieces": pieces,
    }

def recurring(cid, label, source_ids, events, mentions, pieces, kind="explicit_part_number"):
    return {
        "canonical_id": cid,
        "family": FAMILY,
        "canonical_label": label,
        "canonical_kind": kind,
        "repair_event_count": len(set(events)),
        "repair_event_ids": list(events),
        "mention_count": mentions,
        "recorded_pieces": pieces,
        "source_candidate_ids": source_ids,
        "absorbed_alias_labels": [],
    }

def main():
    rules = m.load_rules(RULES, FAMILY)

    # Hard fuse disambiguation.
    p = candidate("p1", "15Amp 250V Pigtail", ["e1","e2"], 2, examples=["1 x 15Amp 250V pigtail fuse"])
    k = candidate("k1", "KLK 15A 250V FUSE", ["e3","e4"], 2, examples=["2 KCK 15A 600V fuses"])
    p_rule, p_conf = m.choose_rule(m.matching_rules(rules, p["display_label"], m.candidate_text(p, p["display_label"])))
    k_rule, k_conf = m.choose_rule(m.matching_rules(rules, k["display_label"], m.candidate_text(k, k["display_label"])))
    assert not p_conf and p_rule["target_label"] == "15A 250V Pigtail Fuse"
    assert not k_conf and k_rule["target_label"] == "0KLK015.T"

    # Exact cleanup families.
    checks = {
        "038AN08A1 mosfet": "FDH038AN08A1",
        "DXFX24N100Q3": "IXFX24N100Q3",
        "ISL6551R": "ISL6551IR",
        "IRP9952PBF": "IRF9952PBF",
    }
    for observed, expected in checks.items():
        c = candidate("x_"+observed, observed, ["a","b"], 2, examples=[observed])
        ms = m.matching_rules(rules, observed, m.candidate_text(c, observed))
        rule, conflict = m.choose_rule(ms)
        assert not conflict, (observed, conflict)
        assert rule and rule["target_label"] == expected, (observed, rule)

    # Full miniature run: 8 upstream recurring rows should collapse to 4 final
    # identities (FDH, Q3, ISL, and two fuse families = actually 5), while
    # screened Pigtail Fuse evidence is safely recovered.
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        resolver = td/"resolver"
        review = td/"review"
        out = td/"out"
        resolver.mkdir()
        review.mkdir()

        candidates = [
            candidate("fdh1", "FDH038AN08A1", ["f1","f2"], 2, examples=["FDH038AN08A1 MOSFET"]),
            candidate("fdh2", "038AN08A1 mosfet", ["f3","f4"], 5, examples=["038AN08A1 mosfet N-ch, Fairchild"]),
            candidate("q31", "IXFX24N100Q3", ["q1","q2"], 4),
            candidate("q32", "DXFX24N100Q3", ["q3","q4"], 1, examples=["DXFX24N100Q3 - MOSFET"]),
            candidate("isl1", "ISL6551IR", ["i1","i2"], 2),
            candidate("isl2", "ISL6551R", ["i3","i4"], 2),
            candidate("klk1", "KLK 15A 250V FUSE", ["k1","k2"], 2, examples=["2 KCK 15A 600V fuses"]),
            candidate("pig1", "15Amp 250V Pigtail", ["p1","p2"], 2, examples=["1 x 15Amp 250V pigtail fuse"]),
            # Not upstream recurring / human selected; recover by explicit domain rule.
            candidate("pig_extra", "Pigtail Fuse", ["p3","p4","p5"], 3, kind="description_only",
                      examples=["1 15 Amp 250V pigtail Fuse"]),
        ]
        by = {c["candidate_id"]: c for c in candidates}

        rec = [
            recurring("c1","FDH038AN08A1",["fdh1"],["f1","f2"],2,2),
            recurring("c2","038AN08A1 mosfet",["fdh2"],["f3","f4"],2,5,"description_only"),
            recurring("c3","IXFX24N100Q3",["q31"],["q1","q2"],2,4),
            recurring("c4","DXFX24N100Q3",["q32"],["q3","q4"],2,1),
            recurring("c5","ISL6551IR",["isl1"],["i1","i2"],2,2),
            recurring("c6","ISL6551R",["isl2"],["i3","i4"],2,2),
            recurring("c7","KLK 15A 250V FUSE",["klk1"],["k1","k2"],2,2),
            recurring("c8","15Amp 250V Pigtail",["pig1"],["p1","p2"],2,2),
        ]
        # canonical_parts only needs to exist for input contract.
        (resolver/"canonical_parts_v1_6_3_1.jsonl").write_text(
            "".join(json.dumps(r)+"\n" for r in rec), encoding="utf-8"
        )
        (resolver/"recurring_parts_80_20_v1_6_3_1.jsonl").write_text(
            "".join(json.dumps(r)+"\n" for r in rec), encoding="utf-8"
        )
        (review/"review_candidates.jsonl").write_text(
            "".join(json.dumps(c)+"\n" for c in candidates), encoding="utf-8"
        )

        run = subprocess.run(
            [
                sys.executable, str(TARGET),
                "--resolver-root", str(resolver),
                "--review-root", str(review),
                "--rules", str(RULES),
                "--output-root", str(out),
                "--family", FAMILY,
            ],
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, run.stdout + "\n" + run.stderr

        final = [
            json.loads(x)
            for x in (out/"final_parts_recurring_80_20_v1_6_4.jsonl").read_text().splitlines()
            if x.strip()
        ]
        labels = {r["canonical_label"]: r for r in final}
        assert set(labels) == {
            "FDH038AN08A1",
            "IXFX24N100Q3",
            "ISL6551IR",
            "0KLK015.T",
            "15A 250V Pigtail Fuse",
        }, labels.keys()
        assert labels["15A 250V Pigtail Fuse"]["repair_event_count"] == 5
        assert "pig_extra" in labels["15A 250V Pigtail Fuse"]["recovered_screened_candidate_ids"]
        assert labels["0KLK015.T"]["repair_event_count"] == 2
        assert labels["FDH038AN08A1"]["repair_event_count"] == 4

    print("PASS: Nova DRL Final Parts Consolidator v1.6.4 tests")

if __name__ == "__main__":
    main()
