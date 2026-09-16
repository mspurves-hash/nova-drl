#!/usr/bin/env python3
import importlib.util
import re
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_drl_evidence_drilldown_v1_8_1.py"

spec = importlib.util.spec_from_file_location("d", TARGET)
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class Gate:
    @staticmethod
    def component_matches_text(label, text):
        def singular(tok):
            tok = tok.upper()
            if len(tok) > 4 and tok.endswith("IES"):
                return tok[:-3] + "Y"
            if len(tok) > 3 and tok.endswith("ES"):
                return tok[:-2]
            if len(tok) > 3 and tok.endswith("S"):
                return tok[:-1]
            return tok
        lt = [singular(x) for x in d.normalized_words(label)]
        tt = [singular(x) for x in d.normalized_words(text)]
        return bool(lt) and all(x in tt for x in lt)


class Semantic:
    REPLACE_VERB_RE = re.compile(
        r"\b(?:replace|replaced|changing|changed|swap|swapped)\b", re.I
    )
    ACTION_PATTERNS = {
        "REPLACE": r"(?:replace|replaced|changed|swap|swapped)",
        "CLEAN": r"(?:clean|cleaned)",
        "LUBRICATE": r"(?:lubricate|lubricated)",
        "ADJUST": r"(?:adjust|adjusted)",
        "REPAIR": r"(?:repair|repaired)",
        "REBUILD": r"(?:rebuild|rebuilt)",
    }

    @staticmethod
    def _singular(tok):
        tok = tok.upper()
        if len(tok) > 4 and tok.endswith("IES"):
            return tok[:-3] + "Y"
        if len(tok) > 3 and tok.endswith("ES"):
            return tok[:-2]
        if len(tok) > 3 and tok.endswith("S"):
            return tok[:-1]
        return tok

    @staticmethod
    def split_action_label(label):
        p = label.split(None, 1)
        return (p[0], p[1]) if len(p) == 2 else (None, None)

    @staticmethod
    def split_clauses(text):
        return [x.strip() for x in re.split(r"[.;]|\s+\|\s+", text) if x.strip()]

    @staticmethod
    def clause_contains_object(clause, obj):
        clause_tokens = [Semantic._singular(x) for x in d.normalized_words(clause)]
        obj_tokens = [Semantic._singular(x) for x in d.normalized_words(obj)]
        return bool(obj_tokens) and all(x in clause_tokens for x in obj_tokens)

    @staticmethod
    def direct_action_on_object(clause, label):
        verb, obj = Semantic.split_action_label(label)
        if not verb or not obj:
            return False
        pat = Semantic.ACTION_PATTERNS.get(verb)
        return (
            Semantic.clause_contains_object(clause, obj)
            and bool(pat)
            and bool(re.search(pat, clause, re.I))
        )


def main():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE replacement_mentions("
        "repair_event_id TEXT, manufacturer_pn TEXT, quantity INTEGER,"
        "text TEXT, evidence_quote TEXT, procurement_only_excluded INTEGER)"
    )

    conn.execute(
        "INSERT INTO replacement_mentions VALUES(?,?,?,?,?,0)",
        ("e1", None, 1, "Bearing", "Replaced bearing during overhaul"),
    )
    event1 = {
        "repair_event_id": "e1",
        "all_fact_text": "Robot overhaul complete. Travel smooth after repair.",
    }
    ev1 = d.action_evidence(conn, event1, "REPLACE BEARING", Gate, Semantic)
    assert ev1
    assert any("Replaced bearing" in value for _, value in ev1)

    event2 = {
        "repair_event_id": "e2",
        "all_fact_text": "Replaced bearings and belts. Retested robot.",
    }
    ev2 = d.action_evidence(conn, event2, "REPLACE BELT", Gate, Semantic)
    assert ev2
    assert any("Replaced bearings and belts" in value for _, value in ev2)

    event3 = {
        "repair_event_id": "e3",
        "all_fact_text": "Cleaned Z brake and lubricated lead screw. Motion smooth.",
    }
    ev3 = d.action_evidence(conn, event3, "CLEAN Z BRAKE", Gate, Semantic)
    assert ev3
    assert any("Cleaned Z brake" in value for _, value in ev3)

    event4 = {
        "repair_event_id": "e4",
        "all_fact_text": "Bearing noisy during incoming inspection. Unit overhauled.",
    }
    ev4 = d.action_evidence(conn, event4, "REPLACE BEARING", Gate, Semantic)
    assert ev4
    assert ev4[0][0] == "Supporting repair context"

    print("PASS: Nova DRL Evidence Drill-Down v1.8.1 action-evidence tests")


if __name__ == "__main__":
    main()
