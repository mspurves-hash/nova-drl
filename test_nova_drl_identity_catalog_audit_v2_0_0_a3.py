#!/usr/bin/env python3

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import nova_drl_identity_catalog_audit_v2_0_0_a3 as audit


def write_jsonl(path: Path, rows):
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


class TypeFirstIdentityAuditTests(unittest.TestCase):
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
            ("BRD - BM23995 EXEC CAR ASYST", 2, 0, 0, "[]", "test"),
        )
        conn.execute(
            "INSERT INTO repair_events VALUES(?,?)",
            ("db_1", "BRD - BM23995 EXEC CAR ASYST"),
        )
        conn.execute(
            "INSERT INTO product_parts VALUES(?,?,?,?)",
            ("BRD - BM23995 EXEC CAR ASYST", "1N5408", "1N5408", "1N5408"),
        )
        conn.commit()
        conn.close()

        labels = [
            "BRD - BM23995 EXEC CAR ASYST | PS - KD200-414 DIGITAL POWER",
            "BRD - BM23995 EXEC CAR",
            "PS - KD200-414 DIGITAL POWER",
            "RBT - GB7 7S3L GENMARK",
            "CNTL - 9800106571 GB7 GENMARK",
            "BRD - 1520960 4-AXIS MTR CONTROL EATON",
            "SVO-DRV - MR-J2S-40A MITSUBISHI",
            "SVO DRV - MR-J2S-40A MITSHUBISHI",
            "ALIGNER - OFH-4000Q ASYST",
            "PREALIGNER - OFH-4000Q ASYST",
            "BRD - AB-12-3 OEM1",
            "BRD - AB1-23 OEM2",
            "BRD - SAME-100 BOARD",
            "PS - SAME-100 POWER SUPPLY",
            "PS_HC1011-4D HC POWER",
            "SVO DRV 0 SGDM-04ADA YASKAWA",
            "INTEL RMA 42580 ENGINEERING SERVICES BOARD",
            "DIAGNOSTIC MODULE -",
        ]
        write_jsonl(
            events,
            [
                {
                    "repair_event_id": f"lossless_{index}",
                    "equipment_family": label,
                    "facts": {},
                }
                for index, label in enumerate(labels, 1)
            ],
        )

        launcher.write_text(
            "#!/usr/bin/env bash\n"
            "exec python3 /opt/nova-drl/nova_drl_hard_part_first_unique_prefix_v1_5_18.py \"$@\"\n",
            encoding="utf-8",
        )
        return db, events, launcher

    def build_fixture_audit(self, root: Path):
        db, events, launcher = self.make_fixture(root)
        report = audit.build_audit(
            db,
            events,
            [launcher],
            [
                "BM23995",
                "KD200-414",
                "GB7",
                "9800106571",
                "MR-J2S-40A",
                "OFH-4000Q",
                "AB.123",
                "SAME-100",
                "4-AXIS",
            ],
            3,
        )
        return db, report

    def test_pipe_joined_labels_split_before_identity_parsing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, report = self.build_fixture_audit(root)
            identities = report["identity_audit"]
            by_key = {
                row["identity_key"]: row for row in identities["all_candidates"]
            }

            bm = by_key["BRD::BM23995"]
            kd = by_key["PS::KD200-414"]
            self.assertEqual(bm["equipment_type"], "BRD")
            self.assertEqual(kd["equipment_type"], "PS")
            self.assertTrue(
                all(family.startswith("BRD - BM23995") for family in bm["mapped_families"])
            )
            self.assertTrue(
                all(family.startswith("PS - KD200-414") for family in kd["mapped_families"])
            )
            self.assertFalse(any("KD200-414" in family for family in bm["mapped_families"]))
            self.assertFalse(any("BM23995" in family for family in kd["mapped_families"]))
            self.assertEqual(len(bm["source_compound_labels"]), 1)
            self.assertEqual(len(kd["source_compound_labels"]), 1)
            self.assertEqual(report["lossless_v1_6_0"]["compound_source_labels"], 1)

    def test_only_first_post_type_token_is_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, report = self.build_fixture_audit(root)
            by_key = {
                row["identity_key"]: row
                for row in report["identity_audit"]["all_candidates"]
            }

            self.assertIn("RBT::GB7", by_key)
            self.assertIn("CNTL::9800106571", by_key)
            self.assertIn("BRD::1520960", by_key)
            self.assertNotIn("CNTL::GB7", by_key)
            self.assertNotIn("BRD::4-AXIS", by_key)
            self.assertNotIn("BRD::MTR", by_key)
            self.assertNotIn("BRD::EATON", by_key)
            self.assertNotIn("BRD::1N5408", by_key)

            gb7 = by_key["RBT::GB7"]
            self.assertEqual(gb7["mapped_families"], ["RBT - GB7 7S3L GENMARK"])

    def test_user_confirmed_type_aliases_share_one_type_fence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, report = self.build_fixture_audit(root)
            by_key = {
                row["identity_key"]: row
                for row in report["identity_audit"]["all_candidates"]
            }

            servo = by_key["SVO DRV::MR-J2S-40A"]
            self.assertEqual(servo["raw_equipment_types"], ["SVO DRV", "SVO-DRV"])
            self.assertIn("user_confirmed_type_alias", servo["review_flags"])

            aligner = by_key["PREALIGNER::OFH-4000Q"]
            self.assertEqual(aligner["raw_equipment_types"], ["ALIGNER", "PREALIGNER"])
            self.assertIn("user_confirmed_type_alias", aligner["review_flags"])

    def test_punctuation_collisions_are_type_scoped_and_never_merged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, report = self.build_fixture_audit(root)
            identities = report["identity_audit"]
            by_key = {
                row["identity_key"]: row for row in identities["all_candidates"]
            }

            self.assertIn("BRD::AB-12-3", by_key)
            self.assertIn("BRD::AB1-23", by_key)
            self.assertNotEqual(
                by_key["BRD::AB-12-3"]["identity_key"],
                by_key["BRD::AB1-23"]["identity_key"],
            )
            self.assertIn(
                "normalization_collision_within_type",
                by_key["BRD::AB-12-3"]["critical_flags"],
            )

            collision = {
                (row["equipment_type"], row["comparison_key"]): row
                for row in identities["normalization_collisions_within_type"]
            }[("BRD", "AB123")]
            self.assertEqual(collision["exact_part_numbers"], ["AB-12-3", "AB1-23"])

            benchmark = {
                row["part_number"]: row for row in identities["benchmarks"]
            }["AB.123"]
            self.assertEqual(
                benchmark["status"], "normalization_only_match_not_accepted"
            )

    def test_same_exact_part_number_across_types_remains_ambiguous(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, report = self.build_fixture_audit(root)
            identities = report["identity_audit"]
            by_key = {
                row["identity_key"]: row for row in identities["all_candidates"]
            }

            self.assertIn("BRD::SAME-100", by_key)
            self.assertIn("PS::SAME-100", by_key)
            self.assertIn(
                "cross_type_part_number",
                by_key["BRD::SAME-100"]["critical_flags"],
            )
            conflict = {
                row["part_number_key"]: row
                for row in identities["cross_type_part_numbers"]
            }["SAME-100"]
            self.assertEqual(conflict["equipment_types"], ["BRD", "PS"])

            benchmark = {
                row["part_number"]: row for row in identities["benchmarks"]
            }["SAME-100"]
            self.assertEqual(
                benchmark["status"], "observed_exact_cross_type_ambiguous"
            )

    def test_nonstandard_known_type_delimiters_are_recovered_but_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, report = self.build_fixture_audit(root)
            by_key = {
                row["identity_key"]: row
                for row in report["identity_audit"]["all_candidates"]
            }

            ps = by_key["PS::HC1011-4D"]
            self.assertIn("nonstandard_type_part_delimiter", ps["review_flags"])
            servo = by_key["SVO DRV::SGDM-04ADA"]
            self.assertIn("ocr_zero_used_as_delimiter", servo["review_flags"])

    def test_malformed_segments_are_reported_without_guessing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, report = self.build_fixture_audit(root)
            unparsed = {
                row["equipment_family_segment"]: row
                for row in report["identity_audit"]["unparsed_v1_6_0_family_segments"]
            }
            self.assertIn("INTEL RMA 42580 ENGINEERING SERVICES BOARD", unparsed)
            self.assertIn("DIAGNOSTIC MODULE -", unparsed)
            self.assertEqual(
                unparsed["DIAGNOSTIC MODULE -"]["reason"], "missing_part_number"
            )

    def test_read_only_safety_and_output_contract(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db, events, launcher = self.make_fixture(root)
            conn = sqlite3.connect(db)
            before = conn.execute("SELECT COUNT(*) FROM product_families").fetchone()[0]
            conn.close()

            report = audit.build_audit(db, events, [launcher], ["BM23995"], 3)
            self.assertEqual(report["status"], "audit_complete_no_approvals")
            self.assertEqual(report["schema"], audit.SCHEMA)
            self.assertEqual(report["safety"]["database_writes"], 0)
            self.assertEqual(report["safety"]["approved_identities_created"], 0)
            self.assertEqual(report["safety"]["cross_type_identity_merges"], 0)
            self.assertEqual(report["safety"]["qdrant"], "OFF")

            policy = report["identity_audit"]["policy"]
            self.assertTrue(policy["compound_labels_split_before_identity_parse"])
            self.assertFalse(policy["description_tokens_are_identity_candidates"])
            self.assertFalse(policy["cross_type_auto_merge"])

            text = audit.render_summary(report, 10)
            self.assertIn("READ-ONLY AUDIT", text)
            self.assertIn("ZERO IDENTITIES APPROVED", text)
            self.assertIn("TYPE - PART_NUMBER description", text)
            self.assertIn("Pipe-joined neighboring equipment merged into one identity: NO", text)
            self.assertIn("Qdrant: OFF", text)
            json.dumps(audit.compact_json_report(report))

            conn = sqlite3.connect(db)
            after = conn.execute("SELECT COUNT(*) FROM product_families").fetchone()[0]
            conn.close()
            self.assertEqual(before, after)

    def test_missing_lossless_source_blocks_without_database_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db, _, launcher = self.make_fixture(root)
            report = audit.build_audit(
                db,
                root / "missing.jsonl",
                [launcher],
                ["BM23995"],
                3,
            )
            self.assertEqual(report["status"], "blocked_lossless_family_source")
            self.assertEqual(report["identity_audit"]["raw_identity_candidates"], 0)
            self.assertFalse(
                report["identity_audit"]["policy"]["candidate_auto_approval"]
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
