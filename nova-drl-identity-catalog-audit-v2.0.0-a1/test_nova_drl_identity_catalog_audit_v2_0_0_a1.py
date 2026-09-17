#!/usr/bin/env python3

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import nova_drl_identity_catalog_audit_v2_0_0_a1 as audit


def write_jsonl(path: Path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class IdentityAuditTests(unittest.TestCase):
    def make_fixture(self, root: Path):
        db = root / "knowledge.sqlite"
        events = root / "repair_events_lossless_v1_6_0.jsonl"
        launcher = root / "nova-drl"

        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE product_families("
            "base_part_number TEXT, equipment_family TEXT, "
            "repair_event_count INTEGER)"
        )
        conn.execute("CREATE TABLE repair_events(repair_event_id TEXT)")
        conn.execute("CREATE TABLE product_parts(x TEXT)")
        rows = [
            ("MR-J2S-40A", "SVO DRV - MR-J2S-40A MITSUBISHI", 227),
            ("GENMARK", "RBT - GB7 GENMARK", 80),
            ("M", "ENG SERVICES - M P586 VITRIUM TECH", 1179),
            ("AB-12-3", "BRD - AB-12-3 OEM1", 8),
            ("AB1-23", "BRD - AB1-23 OEM2", 7),
            ("ELA-B014CFT-03", "SVO DRV - ELA-B014CFT-03 ACME", 8),
            ("ELA-B014CFT-03", "SVO DRV - ELA-B014CFT-03 ACME CORP", 2),
            ("VHP", "RBT - VHP BROOKS", 11),
            ("ORPHAN-77", "BRD - SOMETHING-ELSE OEM3", 50),
        ]
        conn.executemany("INSERT INTO product_families VALUES(?,?,?)", rows)
        conn.executemany(
            "INSERT INTO repair_events VALUES(?)", [(f"e{i}",) for i in range(5)]
        )
        conn.executemany("INSERT INTO product_parts VALUES(?)", [("x",), ("y",)])
        conn.commit()
        conn.close()

        event_rows = []
        families = [
            ("SVO DRV - MR-J2S-40A MITSUBISHI", 5),
            ("RBT - GB7 GENMARK", 4),
            ("RBT - GB8 GENMARK", 3),
            ("RBT - GB4S GENMARK", 3),
            ("ENG SERVICES - M P586 VITRIUM TECH", 6),
            ("BRD - AB-12-3 OEM1", 2),
            ("BRD - AB1-23 OEM2", 2),
            ("SVO DRV - ELA-B014CFT-03 ACME", 3),
            ("SVO DRV - ELA-B014CFT-03 ACME CORP", 1),
            ("RBT - VHP BROOKS", 2),
            ("BRD - BM23995 EXEC CAR ASYST", 9),
            ("CNTL - ESC-200 BROOKS", 4),
            ("RBT - XU-RCM7231 YASKAWA", 7),
        ]
        n = 0
        for family, count in families:
            for _ in range(count):
                n += 1
                event_rows.append(
                    {
                        "repair_event_id": f"log_{n}",
                        "equipment_family": family,
                        "facts": {},
                    }
                )
        write_jsonl(events, event_rows)

        launcher.write_text(
            "#!/usr/bin/env bash\n"
            "exec python3 /opt/nova-drl/nova_drl_hard_part_first_unique_prefix_v1_5_18.py \"$@\"\n",
            encoding="utf-8",
        )
        return db, events, launcher

    def test_read_only_audit_detects_known_hazards(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db, events, launcher = self.make_fixture(root)

            conn = sqlite3.connect(db)
            before = conn.execute(
                "SELECT COUNT(*) FROM product_families"
            ).fetchone()[0]
            conn.close()

            report = audit.build_audit(
                db,
                events,
                [launcher],
                ["MR-J2S-40A", "BM23995", "XU-RCM7231", "ESC-200", "VHP"],
                oem_min_families=3,
            )

            self.assertEqual(report["status"], "audit_complete_no_approvals")
            self.assertEqual(report["safety"]["database_writes"], 0)
            self.assertEqual(report["safety"]["approved_identities_created"], 0)

            identities = report["identity_audit"]
            by_key = {
                row["identity_key"]: row for row in identities["all_candidates"]
            }

            self.assertIn("identity_too_short", by_key["M"]["critical_flags"])
            self.assertIn(
                "trailing_oem_like_identity",
                by_key["GENMARK"]["critical_flags"],
            )
            self.assertIn(
                "normalization_collision",
                by_key["AB123"]["critical_flags"],
            )
            self.assertEqual(
                by_key["AB123"]["raw_part_numbers"],
                ["AB-12-3", "AB1-23"],
            )
            self.assertIn(
                "multi_family_mapping",
                by_key["ELAB014CFT03"]["review_flags"],
            )
            self.assertIn(
                "not_observed_as_exact_family_token_v1_6_0",
                by_key["ORPHAN77"]["critical_flags"],
            )
            self.assertIn(
                "mapped_family_does_not_contain_exact_token",
                by_key["ORPHAN77"]["critical_flags"],
            )
            self.assertEqual(
                by_key["MRJ2S40A"]["disposition"],
                "clean_candidate_not_approved",
            )
            self.assertIn(
                "short_alphabetic_identity",
                by_key["VHP"]["review_flags"],
            )

            benchmark = {
                row["part_number"]: row for row in identities["benchmarks"]
            }
            self.assertEqual(
                benchmark["BM23995"]["status"],
                "observed_in_v1_6_0_but_missing_legacy_identity",
            )
            self.assertEqual(
                benchmark["MR-J2S-40A"]["status"],
                "clean_candidate_not_approved",
            )

            launcher_row = report["launchers"]["paths"][0]
            self.assertTrue(
                any("1.5.18" in version for version in launcher_row["version_mentions"])
            )

            conn = sqlite3.connect(db)
            after = conn.execute(
                "SELECT COUNT(*) FROM product_families"
            ).fetchone()[0]
            conn.close()
            self.assertEqual(before, after)

    def test_schema_without_explicit_pn_column_blocks_identity_audit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "knowledge.sqlite"
            events = root / "events.jsonl"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE product_families(equipment_family TEXT)")
            conn.commit()
            conn.close()
            write_jsonl(
                events,
                [
                    {
                        "repair_event_id": "log_1",
                        "equipment_family": "SVO DRV - MR-J2S-40A MITSUBISHI",
                        "facts": {},
                    }
                ],
            )

            report = audit.build_audit(db, events, [], ["MR-J2S-40A"], 3)
            self.assertEqual(
                report["sqlite"]["status"],
                "no_explicit_part_number_column",
            )
            self.assertEqual(report["status"], "blocked_identity_source_schema")
            self.assertEqual(
                report["identity_audit"]["legacy_identity_groups"], 0
            )

    def test_missing_lossless_input_blocks_but_never_guesses(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db, _, launcher = self.make_fixture(root)
            missing = root / "missing.jsonl"
            report = audit.build_audit(
                db, missing, [launcher], ["MR-J2S-40A"], 3
            )
            self.assertEqual(report["status"], "blocked_lossless_family_source")
            self.assertEqual(
                report["identity_audit"]["policy"]["candidate_auto_approval"],
                False,
            )

    def test_rendered_summary_states_safety_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db, events, launcher = self.make_fixture(root)
            report = audit.build_audit(
                db, events, [launcher], ["MR-J2S-40A"], 3
            )
            text = audit.render_summary(report, 10)
            self.assertIn("READ-ONLY AUDIT", text)
            self.assertIn("ZERO IDENTITIES APPROVED", text)
            self.assertIn("NEVER AUTO-MERGE", text)
            self.assertIn("Production launcher changed: NO", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
