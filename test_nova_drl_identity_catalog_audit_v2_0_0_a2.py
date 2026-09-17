#!/usr/bin/env python3

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import nova_drl_identity_catalog_audit_v2_0_0_a2 as audit


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
            "equipment_family TEXT, repair_event_count INTEGER, "
            "events_with_parts INTEGER, indexed_component_count INTEGER, "
            "top_parts_json TEXT, knowledge_scope TEXT)"
        )
        conn.execute(
            "CREATE TABLE repair_events("
            "repair_event_id TEXT PRIMARY KEY, equipment_family TEXT)"
        )
        conn.execute(
            "CREATE TABLE product_parts("
            "equipment_family TEXT, component_key TEXT, display_name TEXT, "
            "manufacturer_pn TEXT)"
        )
        conn.execute(
            "INSERT INTO product_families VALUES(?,?,?,?,?,?)",
            ("SVO DRV - MR-J2S-40A MITSUBISHI", 5, 5, 1, "[]", "test"),
        )
        conn.executemany(
            "INSERT INTO repair_events VALUES(?,?)",
            [(f"db_{i}", "SVO DRV - MR-J2S-40A MITSUBISHI") for i in range(5)],
        )
        conn.execute(
            "INSERT INTO product_parts VALUES(?,?,?,?)",
            ("SVO DRV - MR-J2S-40A MITSUBISHI", "1N5408", "1N5408", "1N5408"),
        )
        conn.commit()
        conn.close()

        families = [
            ("SVO DRV - MR-J2S-40A MITSUBISHI", 5),
            ("BRD - AB-12-3 OEM1", 2),
            ("BRD - AB1-23 OEM2", 2),
            ("SVO DRV - ELA-B014CFT-03 ACME", 3),
            ("SVO DRV - ELA-B014CFT-03 ACME CORP", 1),
            ("300MM - TOOL-7 ACME", 6),
            ("BRD - AX-100 OEM3", 3),
            ("RBT - BX-200 OEM3", 2),
            ("PS - CX-300 OEM3", 1),
            ("RBT - VHP BROOKS", 2),
            ("RBT - GB7 GENMARK", 4),
            ("BRD - BM23995 EXEC CAR ASYST", 9),
            ("CNTL - ESC-200 BROOKS", 4),
            ("RBT - XU-RCM7231 YASKAWA", 7),
            ("ENG SERVICES - VITRIUM TECH", 6),
        ]
        event_rows = []
        number = 0
        for family, count in families:
            for _ in range(count):
                number += 1
                event_rows.append(
                    {
                        "repair_event_id": f"lossless_{number}",
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

    def test_raw_tokens_remain_separate_and_collisions_are_quarantined(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db, events, launcher = self.make_fixture(root)

            conn = sqlite3.connect(db)
            before = conn.execute("SELECT COUNT(*) FROM product_families").fetchone()[0]
            conn.close()

            report = audit.build_audit(
                db,
                events,
                [launcher],
                [
                    "MR-J2S-40A",
                    "BM23995",
                    "XU-RCM7231",
                    "ESC-200",
                    "GB7",
                    "VHP",
                    "AB.123",
                    "NOT-THERE-9",
                ],
                oem_min_families=3,
            )

            self.assertEqual(report["status"], "audit_complete_no_approvals")
            self.assertEqual(
                report["sqlite"]["status"],
                "inventory_complete_no_explicit_part_number_column",
            )
            self.assertEqual(report["safety"]["database_identity_rows_used"], 0)
            self.assertEqual(report["safety"]["normalized_identity_merges"], 0)
            self.assertEqual(report["safety"]["approved_identities_created"], 0)

            identities = report["identity_audit"]
            by_key = {
                row["identity_key"]: row for row in identities["all_candidates"]
            }

            self.assertNotIn("1N5408", by_key)
            self.assertIn("AB-12-3", by_key)
            self.assertIn("AB1-23", by_key)
            self.assertNotEqual(
                by_key["AB-12-3"]["identity_key"],
                by_key["AB1-23"]["identity_key"],
            )
            self.assertIn(
                "normalization_collision", by_key["AB-12-3"]["critical_flags"]
            )
            self.assertIn(
                "normalization_collision", by_key["AB1-23"]["critical_flags"]
            )
            self.assertEqual(
                by_key["AB-12-3"]["collision_exact_tokens"],
                ["AB-12-3", "AB1-23"],
            )

            collision = {
                row["comparison_key"]: row
                for row in identities["normalization_collisions"]
            }["AB123"]
            self.assertEqual(collision["exact_tokens"], ["AB-12-3", "AB1-23"])

            self.assertIn(
                "multi_family_mapping",
                by_key["ELA-B014CFT-03"]["review_flags"],
            )
            self.assertIn(
                "category_like_identity", by_key["300MM"]["critical_flags"]
            )
            self.assertIn(
                "trailing_oem_like_identity", by_key["OEM3"]["critical_flags"]
            )
            self.assertIn(
                "short_alphabetic_identity", by_key["VHP"]["review_flags"]
            )
            self.assertEqual(
                by_key["MR-J2S-40A"]["disposition"], "review_candidate"
            )

            benchmarks = {
                row["part_number"]: row for row in identities["benchmarks"]
            }
            self.assertEqual(
                benchmarks["MR-J2S-40A"]["status"],
                "observed_exact_pending_human_review",
            )
            self.assertEqual(
                benchmarks["AB.123"]["status"],
                "normalization_only_match_not_accepted",
            )
            self.assertEqual(
                benchmarks["AB.123"]["normalization_only_candidates"],
                ["AB-12-3", "AB1-23"],
            )
            self.assertEqual(benchmarks["NOT-THERE-9"]["status"], "absent")

            policy = identities["policy"]
            self.assertFalse(policy["comparison_key_is_identity"])
            self.assertTrue(policy["punctuation_preserved_for_identity"])
            self.assertFalse(policy["candidate_auto_approval"])

            launcher_row = report["launchers"]["paths"][0]
            self.assertTrue(
                any("1.5.18" in version for version in launcher_row["version_mentions"])
            )

            conn = sqlite3.connect(db)
            after = conn.execute("SELECT COUNT(*) FROM product_families").fetchone()[0]
            conn.close()
            self.assertEqual(before, after)

    def test_no_explicit_database_part_number_does_not_block_family_token_audit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db, events, _ = self.make_fixture(root)
            report = audit.build_audit(db, events, [], ["MR-J2S-40A"], 3)

            self.assertEqual(
                report["sqlite"]["status"],
                "inventory_complete_no_explicit_part_number_column",
            )
            self.assertEqual(report["status"], "complete_with_launcher_warning")
            self.assertGreater(
                report["identity_audit"]["raw_identity_candidates"], 0
            )

    def test_missing_lossless_input_blocks_without_database_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db, _, launcher = self.make_fixture(root)
            report = audit.build_audit(
                db,
                root / "missing.jsonl",
                [launcher],
                ["MR-J2S-40A"],
                3,
            )
            self.assertEqual(report["status"], "blocked_lossless_family_source")
            self.assertEqual(
                report["identity_audit"]["raw_identity_candidates"], 0
            )
            self.assertFalse(
                report["identity_audit"]["policy"]["candidate_auto_approval"]
            )

    def test_rendered_and_json_outputs_state_safety_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db, events, launcher = self.make_fixture(root)
            report = audit.build_audit(db, events, [launcher], ["MR-J2S-40A"], 3)
            text = audit.render_summary(report, 10)
            self.assertIn("READ-ONLY AUDIT", text)
            self.assertIn("ZERO IDENTITIES APPROVED", text)
            self.assertIn("NEVER AUTO-MERGE", text)
            self.assertIn("Normalized comparison key treated as identity: NO", text)
            self.assertIn("Production launcher changed: NO", text)
            json.dumps(audit.compact_json_report(report))


if __name__ == "__main__":
    unittest.main(verbosity=2)
