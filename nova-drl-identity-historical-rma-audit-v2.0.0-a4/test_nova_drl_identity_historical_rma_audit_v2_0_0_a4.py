#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "nova_drl_identity_historical_rma_audit_v2_0_0_a4.py"
SPEC = importlib.util.spec_from_file_location("nova_rma_a4", MODULE_PATH)
assert SPEC and SPEC.loader
audit_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit_module
SPEC.loader.exec_module(audit_module)


def inline_cell(reference: str, value: object) -> str:
    if isinstance(value, (int, float)):
        return f'<c r="{reference}"><v>{value}</v></c>'
    return (
        f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">'
        f'{escape(str(value))}</t></is></c>'
    )


def make_xlsx(path: Path, rows: list[dict[str, object]]) -> None:
    row_xml = []
    for row_number, values in enumerate(rows, start=1):
        cells = "".join(inline_cell(f"{column}{row_number}", value) for column, value in values.items())
        row_xml.append(f'<row r="{row_number}">{cells}</row>')

    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Table 1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


def make_a3(path: Path) -> None:
    queue = [
        {
            "identity_key": "BRD::BM23995",
            "equipment_type": "BRD",
            "part_number": "BM23995",
            "comparison_key": "BM23995",
        },
        {
            "identity_key": "PS::KD200-414",
            "equipment_type": "PS",
            "part_number": "KD200-414",
            "comparison_key": "KD200414",
        },
        {
            "identity_key": "CNTL::UTC800P",
            "equipment_type": "CNTL",
            "part_number": "UTC800P",
            "comparison_key": "UTC800P",
        },
        {
            "identity_key": "RBT::UTC800P",
            "equipment_type": "RBT",
            "part_number": "UTC800P",
            "comparison_key": "UTC800P",
        },
        {
            "identity_key": "RBT::XU-RCM7231",
            "equipment_type": "RBT",
            "part_number": "XU-RCM7231",
            "comparison_key": "XURCM7231",
        },
        {
            "identity_key": "P_S::0010-93145",
            "equipment_type": "P_S",
            "part_number": "0010-93145",
            "comparison_key": "001093145",
        },
    ]
    path.write_text(
        json.dumps(
            {
                "schema": "nova-drl-identity-catalog-audit-v2-a3",
                "version": "2.0.0-a3",
                "status": "audit_complete_no_approvals",
                "identity_audit": {"review_queue": queue},
            }
        ),
        encoding="utf-8",
    )


class HistoricalRmaAuditTests(unittest.TestCase):
    def test_recovers_crossed_columns_and_split_description(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            xlsx = Path(temporary) / "source.xlsx"
            make_xlsx(
                xlsx,
                [
                    {"A": 1001, "C": "BRD - BM23995"},
                    {"A": 1002, "D": "P/S - KD200-414"},
                    {"A": 1003, "E": "CNTL - UTC800P"},
                    {"A": 1004, "F": "RBT - UTC800P"},
                    {"A": 1005, "F": "C1716T", "G": "INTERNAL DRIVE"},
                    {},
                ],
            )
            source, deduped, summary = audit_module.extract_associations(xlsx)

            self.assertEqual(len(source), 5)
            self.assertEqual(len(deduped), 5)
            self.assertEqual(summary["rows_with_exactly_one_part_cell"], 4)
            self.assertEqual(summary["rows_with_split_part_description"], 1)
            self.assertEqual(summary["part_cell_counts_by_column"]["G"], 1)
            self.assertEqual(summary["source_alignment_status"], "complete_same_row_pairing")
            self.assertEqual(source[-1].part_label, "C1716T INTERNAL DRIVE")

    def test_deduplicates_exact_rma_label_pairs_without_losing_traceability(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            xlsx = Path(temporary) / "source.xlsx"
            make_xlsx(
                xlsx,
                [
                    {"A": 1001, "C": "BRD - BM23995"},
                    {"A": 1001, "F": "BRD - BM23995"},
                ],
            )
            _source, deduped, summary = audit_module.extract_associations(xlsx)

            self.assertEqual(summary["exact_duplicate_rows_removed"], 1)
            self.assertEqual(len(deduped), 1)
            self.assertEqual(deduped[0].source_rows, [1, 2])
            self.assertEqual(deduped[0].column_patterns, {"C", "F"})

    def test_exact_type_fence_and_no_prefix_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            xlsx = root / "source.xlsx"
            a3 = root / "a3.json"
            make_a3(a3)
            make_xlsx(
                xlsx,
                [
                    {"A": 1001, "C": "BRD - BM23995"},
                    {"A": 1002, "C": "P/S - KD200-414"},
                    {"A": 1003, "C": "CNTL - UTC800P"},
                    {"A": 1004, "C": "RBT - UTC800P"},
                    {"A": 1005, "C": "RBT - XU-RCM7"},
                    {"A": 1006, "C": "PS - BM23995"},
                    {"A": 1007, "C": "BM23995"},
                    {"A": 1008, "C": "P/S - 0010-93145"},
                ],
            )

            audit, reference = audit_module.build_audit(xlsx, a3, root / "reference.jsonl")
            corroboration = audit["corroboration"]
            by_identity = {
                row["identity_key"]: row for row in corroboration["identity_evidence"]
            }

            self.assertEqual(audit["status"], "audit_complete_no_approvals")
            self.assertEqual(by_identity["BRD::BM23995"]["historical_same_type_rows"], 1)
            self.assertEqual(by_identity["BRD::BM23995"]["historical_other_type_rows"], 1)
            self.assertEqual(
                by_identity["BRD::BM23995"]["disposition"],
                "mixed_type_historical_evidence_review",
            )
            self.assertEqual(by_identity["PS::KD200-414"]["historical_same_type_rows"], 1)
            self.assertEqual(
                by_identity["PS::KD200-414"]["disposition"],
                "corroborated_same_type_not_approved",
            )
            self.assertNotIn(
                "RBT::XU-RCM7231",
                {row["identity_key"] for row in corroboration["identity_evidence"]},
            )
            self.assertEqual(
                by_identity["P_S::0010-93145"]["canonical_equipment_type_for_comparison"],
                "PS",
            )
            self.assertEqual(
                by_identity["P_S::0010-93145"]["disposition"],
                "type_alias_corroboration_not_approved",
            )
            self.assertTrue(
                all(row["approved"] is False for row in corroboration["identity_evidence"])
            )
            self.assertEqual(audit["safety"]["canonical_identities_created"], 0)
            self.assertEqual(audit["safety"]["database_writes"], 0)
            xu_record = next(row for row in reference if row["raw_part_label"] == "RBT - XU-RCM7")
            self.assertEqual(xu_record["a3_identity_keys"], [])

    def test_incomplete_or_generic_labels_never_become_identity_evidence(self) -> None:
        aliases = {"RBT": "RBT", "PS": "PS"}
        generic = audit_module.parse_historical_label("PREALIGNER -", aliases)
        short = audit_module.parse_historical_label("PS - 15", aliases)
        no_digit = audit_module.parse_historical_label("RBT - ROBOT", aliases)

        self.assertNotEqual(generic.parse_status, "conservative_part_token")
        self.assertEqual(short.parse_status, "rejected_token_too_short")
        self.assertEqual(no_digit.parse_status, "rejected_no_digit")

    def test_main_writes_only_requested_audit_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            xlsx = root / "source.xlsx"
            a3 = root / "a3.json"
            output = root / "a4.json"
            reference = root / "reference.jsonl"
            make_a3(a3)
            make_xlsx(xlsx, [{"A": 1001, "C": "BRD - BM23995"}])

            result = audit_module.main(
                [
                    "--xlsx", str(xlsx),
                    "--a3", str(a3),
                    "--output", str(output),
                    "--reference-output", str(reference),
                ]
            )

            self.assertEqual(result, 0)
            self.assertTrue(output.is_file())
            self.assertTrue(reference.is_file())
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(written["status"], "audit_complete_no_approvals")
            self.assertEqual(written["safety"]["qdrant"], "OFF")


if __name__ == "__main__":
    unittest.main(verbosity=2)
