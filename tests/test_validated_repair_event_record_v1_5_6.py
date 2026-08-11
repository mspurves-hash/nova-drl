#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_validated_repair_event_record_v1_5_6.py"

spec = importlib.util.spec_from_file_location("ver156", str(TARGET))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

assert mod.VERSION == "1.5.6"
rules = mod.load_rules(
    ROOT / "config" / "repair_event_record_rules_v1_5_6.json"
)

identity = {
    "log_number": "130813004",
    "repair_date": "2013-08-13",
    "repair_date_display": "8/13/2013",
    "daily_sequence": "004",
    "equipment_type": "RBT",
    "oem": "GENMARK",
    "model": "GB8-MT",
    "serial_number": "80010732",
    "customer": "UTI MICRON",
    "site_code": None,
    "site_name": None,
    "warranty": True,
}

approved_fields = {
    "customer_complaint": {
        "value": "Y Axis needs to be fixed",
        "reviewer": "Matt Purves",
        "decision_id": "5dd3edbfd3784440",
        "eligible_for_future_qdrant_ingestion": True,
        "qdrant_entry_created": False,
    },
    "repair_actions": [
        {
            "action_id": "83f92a7bfd33a31a",
            "action_number": 1,
            "value": (
                "Adjusted Y-FE from around 9000 down to around 3000 "
                "by slipping Y belt a few teeth"
            ),
            "decision_id": "e01f7e64a16e86e0",
        },
        {
            "action_id": "2a147a755880b4a1",
            "action_number": 2,
            "value": "Added Flanges BERS x2 to A1 + A2 upper link",
            "decision_id": "45a3f90ca8b8042c",
        },
    ],
    "parts_replaced": [
        {
            "part": "flanged bearings",
            "quantity": 2,
            "decision_id": "fa7c3ef3644b1a58",
        }
    ],
    "diagnostic_hypotheses": [
        {
            "value": (
                "High Y-FE may have caused the intermittent homing "
                "problem."
            ),
            "decision_id": "7a1f9366ddc7c5a5",
            "confirmed_root_cause": False,
        }
    ],
}

diagnostic_data = {
    "fusion_version": "1.5.4",
    "repair_identity": identity,
    "approved_fields": approved_fields,
    "approved_field_count": 4,
    "approved_repair_action_count": 2,
    "approved_parts_replaced_count": 1,
    "approved_diagnostic_hypothesis_count": 1,
    "confirmed_root_cause_count": 0,
    "root_cause_status": "not_established",
    "diagnostic_root_cause_review": {
        "root_cause_candidate_count": 0,
        "confirmed_root_cause_count": 0,
        "root_cause_status": "not_established",
    },
    "accepted_as_final_repair_summary": False,
    "qdrant_entry_created": False,
}

testing_data = {
    "fusion_version": "1.5.5.4",
    "source_fusion_version": "1.5.4",
    "repair_identity": identity,
    "approved_fields": approved_fields,
    "approved_field_count": 4,
    "approved_repair_action_count": 2,
    "approved_parts_replaced_count": 1,
    "approved_diagnostic_hypothesis_count": 1,
    "approved_testing_item_count": 0,
    "approved_final_result_count": 0,
    "testing_final_result_review": {
        "testing": {
            "candidate_count": 0,
            "approved_count": 0,
            "pending_count": 0,
            "candidates": [],
        },
        "final_result": {
            "candidate_count": 0,
            "approved_count": 0,
            "pending_count": 0,
            "status": "not_established",
            "candidates": [],
        },
    },
    "accepted_as_final_repair_summary": False,
    "qdrant_entry_created": False,
}

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    diag_path = root / "approved_repair_fields_with_diagnostics.json"
    test_path = root / "approved_repair_fields_with_testing_final.json"

    diagnostic_data["source_approved_fields_path"] = "/tmp/previous.json"
    mod.write_json(diag_path, diagnostic_data)

    testing_data["source_approved_fields_path"] = str(diag_path)
    mod.write_json(test_path, testing_data)

    loaded_test_path, loaded_testing = mod.locate_testing_source(test_path)
    loaded_diag_path, loaded_diag, warning = mod.locate_diagnostic_source(
        loaded_test_path, loaded_testing
    )
    assert warning is None

    output_dir = root / "output"
    record = mod.build_record(
        rules,
        loaded_test_path,
        loaded_testing,
        loaded_diag_path,
        loaded_diag,
        warning,
        output_dir,
    )

    assert record["consistency_checks"]["hard_error_count"] == 0
    assert record["record_human_validation"]["status"] == "pending"
    assert record["record_is_human_validated"] is False
    assert record["qdrant_entry_created"] is False
    assert record["record_level_qdrant_eligible"] is False
    assert record["accepted_as_final_repair_summary"] is False

    states = {
        k: v["state"]
        for k, v in record["knowledge_fields"].items()
    }
    assert states == {
        "customer_complaint": "approved",
        "repair_actions": "approved",
        "parts_replaced": "approved",
        "diagnostic_hypotheses": "approved",
        "root_cause": "not_established",
        "testing_performed": "not_established",
        "final_result": "not_established",
    }

    assert (
        record["knowledge_fields"]["customer_complaint"]["value"]
        == "Y Axis needs to be fixed"
    )
    assert (
        record["knowledge_fields"]["repair_actions"]["approved_items"][0]["value"]
        == "Adjusted Y-FE from around 9000 down to around 3000 by slipping Y belt a few teeth"
    )
    assert (
        record["knowledge_fields"]["parts_replaced"]["approved_items"][0]["part"]
        == "flanged bearings"
    )
    assert (
        record["knowledge_fields"]["diagnostic_hypotheses"]["approved_items"][0]["confirmed_root_cause"]
        is False
    )

    # Record-level approval certifies only the current source hashes and field
    # state digest.
    decision = mod.record_decision(
        output_dir,
        "approve-record",
        "Matt Purves",
        "Synthetic validation test.",
        record,
    )
    assert decision["decision"] == "approved"

    record2 = mod.build_record(
        rules,
        loaded_test_path,
        loaded_testing,
        loaded_diag_path,
        loaded_diag,
        warning,
        output_dir,
    )
    assert record2["record_human_validation"]["status"] == "approved"
    assert record2["record_is_human_validated"] is True
    assert (
        record2["record_human_validation"]["approved_record_matches_current_sources"]
        is True
    )

    # Change an upstream approved value. The old record approval must become
    # stale instead of silently following the changed source.
    changed = json.loads(json.dumps(loaded_testing))
    changed["approved_fields"]["customer_complaint"]["value"] = (
        "DIFFERENT APPROVED VALUE FOR TEST"
    )
    mod.write_json(test_path, changed)
    changed_loaded = mod.read_json(test_path)

    record3 = mod.build_record(
        rules,
        test_path,
        changed_loaded,
        loaded_diag_path,
        loaded_diag,
        warning,
        output_dir,
    )
    assert record3["record_human_validation"]["status"] == "pending"
    assert (
        record3["record_human_validation"]["stale_prior_decisions_ignored"]
        >= 1
    )
    assert record3["record_is_human_validated"] is False

# Pending upstream testing must remain pending_review, not not_established.
pending_testing = json.loads(json.dumps(testing_data))
pending_testing["testing_final_result_review"]["testing"] = {
    "candidate_count": 1,
    "approved_count": 0,
    "pending_count": 1,
    "candidates": [{"candidate_id": "pending-test"}],
}
pending_testing_field = mod.testing_state(
    pending_testing,
    Path("/tmp/testing.json"),
)
assert pending_testing_field["state"] == "pending_review"

# Pending final result must remain pending_review.
pending_final = json.loads(json.dumps(testing_data))
pending_final["testing_final_result_review"]["final_result"] = {
    "candidate_count": 1,
    "approved_count": 0,
    "pending_count": 1,
    "status": "candidate_pending_human_review",
    "candidates": [{"candidate_id": "pending-final"}],
}
pending_final_field = mod.final_result_state(
    pending_final,
    Path("/tmp/testing.json"),
)
assert pending_final_field["state"] == "pending_review"

# Missing diagnostic layer is explicitly not_available, not guessed.
missing_root = mod.root_cause_state(None, None)
assert missing_root["state"] == "not_available"

# A confirmed-root-cause count without an approved provenance object must not
# be promoted.
bad_diag = json.loads(json.dumps(diagnostic_data))
bad_diag["confirmed_root_cause_count"] = 1
bad_diag["root_cause_status"] = "confirmed"
bad_root = mod.root_cause_state(
    bad_diag,
    Path("/tmp/diag.json"),
)
assert bad_root["state"] == "not_available"

# Version mismatch is a hard consistency error and record approval is blocked.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    test_path = root / "testing.json"
    diag_path = root / "diag.json"

    bad_testing = json.loads(json.dumps(testing_data))
    bad_testing["fusion_version"] = "1.5.5.3"
    mod.write_json(test_path, bad_testing)
    mod.write_json(diag_path, diagnostic_data)

    fields = mod.build_knowledge_fields(
        bad_testing,
        test_path,
        diagnostic_data,
        diag_path,
    )
    checks = mod.build_consistency_checks(
        rules,
        bad_testing,
        test_path,
        diagnostic_data,
        diag_path,
        fields,
    )
    assert checks["hard_error_count"] >= 1

    fake_record = {
        "record_id": "x",
        "field_state_digest": "y",
        "source_digest": "z",
        "consistency_checks": checks,
    }
    try:
        mod.record_decision(
            root,
            "approve-record",
            "Matt Purves",
            "Should fail.",
            fake_record,
        )
        raise AssertionError("Approval was not blocked by hard errors.")
    except ValueError:
        pass

print("PASS: Nova DRL Validated Repair Event Knowledge Record v1.5.6 tests")
