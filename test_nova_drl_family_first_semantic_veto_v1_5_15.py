#!/usr/bin/env python3
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = HERE / "nova_drl_family_first_semantic_veto_v1_5_15.py"

spec = importlib.util.spec_from_file_location("m", TARGET)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fake(snips):
    class S:
        @staticmethod
        def _technician_repair_snippets(event):
            return snips
    return S()


def veto(snips, action):
    assert m.event_should_be_vetoed(fake(snips), {}, action), (snips, action)

def keep(snips, action):
    assert not m.event_should_be_vetoed(fake(snips), {}, action), (snips, action)


def main():
    # MR-J2S-40A known false positives.
    veto(
        ["Bus voltage is 302V after fix. Parameters restored. Motor tests worked okay. Parameters verified."],
        "REPAIR MOTOR",
    )
    veto(
        ["Billed as repair per MP. Turned motor all side. No screws. All OK; have not changed any parts."],
        "REPLACE MOTOR",
    )
    veto(
        ["Incoming BV 200 works great turning Motor with 220V AC input. Affix Gold F/W installed.",
         "Re-installed their fan"],
        "REPAIR MOTOR",
    )
    veto(
        ["Fixed motor issue (wasn't moving with gold firmware). Replaced batteries per MPR."],
        "REPLACE MOTOR",
    )

    # Legitimate direct/coordinated repair actions should survive.
    keep(["Replaced bearings and belts. Retested robot."], "REPLACE BEARING")
    keep(["Replaced bearings and belts. Retested robot."], "REPLACE BELT")
    keep(["Replaced Z lead screw and aligned axis."], "REPLACE Z LEAD SCREW")
    keep(["Repaired board and replaced resistor."], "REPAIR BOARD")
    keep(["Repaired PCB traces."], "REPAIR PCB")
    keep(["Replaced connector J3."], "REPLACE CONNECTOR")
    keep(["Cleaned Z brake and lubricated lead screw."], "CLEAN Z BRAKE")
    keep(["Lubricated lead screw."], "LUBRICATE LEAD SCREW")
    keep(["Adjusted belt tension."], "ADJUST BELT")
    keep(["Rebuilt bearing assembly."], "REBUILD BEARING")

    # Preserve imperfect-but-not-contradictory legacy evidence.
    keep(["Bearing noisy; unit repaired and tested."], "REPLACE BEARING")
    keep(["Board repaired, all tests pass."], "REPAIR BOARD")

    # Negation still vetoes.
    veto(["No parts replaced. Motor test passed."], "REPLACE MOTOR")

    print("PASS: Nova DRL v1.5.15 conservative semantic-veto tests")


if __name__ == "__main__":
    main()
