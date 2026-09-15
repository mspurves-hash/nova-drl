#!/usr/bin/env python3
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_drl_family_first_action_guard_v1_5_14.py"

spec = importlib.util.spec_from_file_location("g", TARGET)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def yes(snippet, action):
    assert m.snippet_supports_action(snippet, action), (snippet, action)

def no(snippet, action):
    assert not m.snippet_supports_action(snippet, action), (snippet, action)


def main():
    # The four MR-J2S-40A false positives.
    no("Bus voltage is 302V after fix. Parameters restored. Motor tests worked okay. Parameters verified.", "REPAIR MOTOR")
    no("Billed as repair per MP. - Turned motor all side. - No screws, has one battery. - All OK — no problem, have not changed any parts.", "REPLACE MOTOR")
    no("Incoming BV 200 works great turning Motor with 220V AC input. Affix Gold F/W installed.", "REPAIR MOTOR")
    no("Fixed motor issue (wasn't moving with gold firmware). Replaced batteries per MPR.", "REPLACE MOTOR")
    no("Fixed motor issue (wasn't moving with gold firmware). Replaced batteries per MPR.", "REPAIR MOTOR")

    # Legitimate direct actions.
    yes("Replaced motor.", "REPLACE MOTOR")
    yes("Motor was replaced.", "REPLACE MOTOR")
    yes("Repaired motor and tested unit.", "REPAIR MOTOR")
    yes("Cleaned connector and retested.", "CLEAN CONNECTOR")
    yes("Adjusted belt tension.", "ADJUST BELT")
    yes("Rebuilt bearing assembly.", "REBUILD BEARING")
    yes("Lubricated lead screw.", "LUBRICATE LEAD SCREW")
    yes("Replaced the main board.", "REPLACE BOARD")
    yes("Replaced batteries per MPR.", "REPLACE BATTERIES")

    # Negation wins.
    no("Did not replace motor.", "REPLACE MOTOR")
    no("No parts replaced; motor test passed.", "REPLACE MOTOR")
    no("Motor not replaced.", "REPLACE MOTOR")

    print("PASS: Nova DRL v1.5.14 direct-action semantic guard tests")


if __name__ == "__main__":
    main()
