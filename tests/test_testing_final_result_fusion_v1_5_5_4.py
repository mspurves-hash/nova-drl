#!/usr/bin/env python3
import importlib.util
import json
import tempfile
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingest" / "nova_testing_final_result_fusion_v1_5_5_4.py"
spec = importlib.util.spec_from_file_location("tfr1554", str(TARGET))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

assert mod.VERSION == "1.5.5.4"
rules = mod.load_rules(ROOT / "config" / "testing_final_result_rules_v1_5_5_4.json")
anchors = mod.load_anchor_profiles(ROOT / "config" / "testing_anchor_profiles_v1_5_5_4.json")

source_data = {
    "fusion_version": "1.5.4",
    "repair_identity": {
        "log_number": "130813004",
        "equipment_type": "RBT",
        "oem": "GENMARK",
        "model": "GB8-MT",
        "serial_number": "80010732",
        "customer": "UTI MICRON",
    },
    "approved_fields": {
        "customer_complaint": {"value": "Y Axis needs to be fixed"},
        "repair_actions": [
            {"value": "Adjusted Y-FE from around 9000 down to around 3000 by slipping Y belt a few teeth"},
            {"value": "Added Flanges BERS x2 to A1 + A2 upper link"},
        ],
        "parts_replaced": [{"part": "flanged bearings", "quantity": 2}],
    },
    "approved_field_count": 4,
    "approved_repair_action_count": 2,
    "approved_parts_replaced_count": 1,
    "qdrant_entry_created": False,
}

# ------------------------------------------------------------------
# Anchor finder works from OCR word boxes without absolute form coordinates.
# ------------------------------------------------------------------
words = [
    {"text":"Passed","left":100,"top":50,"width":60,"height":20,"page_num":1,"block_num":1,"par_num":1,"line_num":1,"word_num":1,"conf":95},
    {"text":"All","left":165,"top":50,"width":25,"height":20,"page_num":1,"block_num":1,"par_num":1,"line_num":1,"word_num":2,"conf":95},
    {"text":"Tests","left":195,"top":50,"width":45,"height":20,"page_num":1,"block_num":1,"par_num":1,"line_num":1,"word_num":3,"conf":95},
    {"text":"No","left":100,"top":90,"width":20,"height":20,"page_num":1,"block_num":1,"par_num":1,"line_num":2,"word_num":1,"conf":95},
    {"text":"Trouble","left":125,"top":90,"width":55,"height":20,"page_num":1,"block_num":1,"par_num":1,"line_num":2,"word_num":2,"conf":95},
    {"text":"Found","left":185,"top":90,"width":45,"height":20,"page_num":1,"block_num":1,"par_num":1,"line_num":2,"word_num":3,"conf":95},
]
match = mod.find_anchor_bbox(words, ["Passed All Tests"])
assert match is not None and match["score"] >= 0.99
assert match["bbox"][0] == 100
match2 = mod.find_anchor_bbox(words, ["No Trouble Found"])
assert match2 is not None and match2["score"] >= 0.99

# ------------------------------------------------------------------
# Live v1.5.5.2 failure: customer complaint cannot survive as testing.
# Supporting final hallucinated basis cannot survive unless OCR anchors it.
# ------------------------------------------------------------------
analyses = [
    {
        "analysis_id": "report-page1",
        "vision_status": "ok",
        "source": {
            "source_kind": "supporting_document_page",
            "document_role": "robot_test_report",
            "document_family": "DRL_ACCEPTANCE_TEST_REPORT",
            "source_document": "130813004 Robot Test Report.PDF",
            "source_path": "/mnt/drl/report.pdf",
            "page_number": 1,
            "image_path": "/tmp/report1.png",
            "template_ocr_text": "Customer Problem/Symptom Description Y Axis needs to be fixed",
        },
        "parsed_analysis": {
            "testing_items": [
                {
                    "step_label": "Customer Problem/Symptom Description",
                    "event_mark": "Y Axis needs to be Fixed",
                    "mark_type": "checkmark",
                    "result": "completed",
                    "semantic_role": "inspection",
                    "association_basis": "same_row",
                    "confidence": "high",
                }
            ],
            "final_result_items": [
                {
                    "value": "Pass",
                    "basis_label": "Final Result / Test Result / Overall Result / Pass-Fail selection",
                    "event_mark": "checkmark",
                    "result": "pass",
                    "semantic_role": "final_result_field",
                    "association_basis": "selected_option",
                    "selected_result": "pass",
                    "confidence": "high",
                }
            ],
            "other_event_observations": [],
            "printed_template_only_labels": [],
            "uncertain_marks": [],
        },
    },
]
review = mod.build_review(analyses, rules, [], source_data, field_verifications=[])
assert review["testing"]["candidate_count"] == 0
assert review["final_result"]["candidate_count"] == 0
assert any(r["reason"] == "event_header_or_customer_problem_not_testing" for r in review["hardening"]["testing_rejections"])
assert any(r["reason"] == "basis_label_not_verified_in_template_ocr" or r["reason"] == "supporting_document_lacks_known_result_field_label" for r in review["hardening"]["final_rejections"])

# A genuinely OCR-anchored supporting result field remains reviewable.
valid_support = json.loads(json.dumps(analyses[0]))
valid_support["analysis_id"] = "report-final"
valid_support["source"]["template_ocr_text"] = "FINAL RESULT PASS FAIL Technician Initials"
valid_support["parsed_analysis"]["testing_items"] = []
valid_support["parsed_analysis"]["final_result_items"] = [{
    "value": "Pass",
    "basis_label": "Final Result",
    "event_mark": "X",
    "result": "pass",
    "semantic_role": "final_result_field",
    "association_basis": "selected_option",
    "selected_result": "pass",
    "confidence": "high",
}]
review_valid = mod.build_review([valid_support], rules, [], source_data, field_verifications=[])
assert review_valid["final_result"]["candidate_count"] == 1
assert review_valid["final_result"]["candidates"][0]["basis_anchor_verified"] is True

# ------------------------------------------------------------------
# Mutually-exclusive traveler fields: multiple selections => ambiguity only,
# zero final candidates.
# ------------------------------------------------------------------
def fv(field_id, label, canonical, result, status, mark, group="final_test_disposition", profile="DRL_TRAVELER_FINAL_TEST"):
    return {
        "verification_id": "v-" + field_id,
        "profile": profile,
        "field_id": field_id,
        "target_label": label,
        "canonical_value": canonical,
        "canonical_result": result,
        "mutually_exclusive_group": group,
        "source": {
            "source_kind": "traveler_event_crop",
            "document_role": "traveler",
            "source_document": "final_test.png" if profile == "DRL_TRAVELER_FINAL_TEST" else "shipping_final_ok.png",
            "source_path": "/derived/final_test.png" if profile == "DRL_TRAVELER_FINAL_TEST" else "/derived/shipping_final_ok.png",
            "image_path": "/derived/final_test.png" if profile == "DRL_TRAVELER_FINAL_TEST" else "/derived/shipping_final_ok.png",
        },
        "anchor_status": "found",
        "anchor_match": {"score":1.0,"matched_text":label,"bbox":[100,100,120,20]},
        "anchor_crop": {"crop_path":"/tmp/crop.png"},
        "cache_status": "created",
        "verification": {
            "selection_status": status,
            "event_mark": mark,
            "mark_type": "checkmark" if mark else "other",
            "confidence": "high",
        },
    }

multi = [
    fv("passed_all_tests","Passed All Tests","Passed All Tests","pass","selected","X"),
    fv("no_trouble_found","No Trouble Found","No Trouble Found","no_trouble_found","selected","X"),
    fv("untestable_inspection_only","Untestable, Inspection Only","Untestable, Inspection Only","untestable_inspection_only","not_selected",None),
]
review_multi = mod.build_review([], rules, [], source_data, field_verifications=multi)
assert review_multi["final_result"]["candidate_count"] == 0
assert review_multi["anchor_field_verification"]["ambiguity_group_count"] == 1

# Exactly one selected and all siblings explicitly not selected => one candidate.
one = [
    fv("passed_all_tests","Passed All Tests","Passed All Tests","pass","selected","X"),
    fv("no_trouble_found","No Trouble Found","No Trouble Found","no_trouble_found","not_selected",None),
    fv("untestable_inspection_only","Untestable, Inspection Only","Untestable, Inspection Only","untestable_inspection_only","not_selected",None),
]
review_one = mod.build_review([], rules, [], source_data, field_verifications=one)
assert review_one["final_result"]["candidate_count"] == 1
assert review_one["final_result"]["candidates"][0]["value"] == "Passed All Tests"
assert review_one["final_result"]["candidates"][0]["association_basis"] == "ocr_label_anchor_plus_local_vision"

# Shipping Final O.K.: 48+ cannot serve as mark; compact non-duration mark can.
bad_ship = [fv("final_ok","Final O.K.","Final O.K.","final_ok","selected","48+",group=None,profile="DRL_TRAVELER_SHIPPING_FINAL_OK")]
review_bad_ship = mod.build_review([], rules, [], source_data, field_verifications=bad_ship)
assert review_bad_ship["final_result"]["candidate_count"] == 0
assert any(r["reason"] in {"shipping_final_ok_mark_is_testing_duration_not_final_ok_mark", "globally_ignored_testing_duration_contaminated_final_ok"} for r in review_bad_ship["hardening"]["final_rejections"])

good_ship = [fv("final_ok","Final O.K.","Final O.K.","final_ok","selected","MP",group=None,profile="DRL_TRAVELER_SHIPPING_FINAL_OK")]
review_good_ship = mod.build_review([], rules, [], source_data, field_verifications=good_ship)
assert review_good_ship["final_result"]["candidate_count"] == 1
assert review_good_ship["final_result"]["candidates"][0]["result"] == "final_ok"

# ------------------------------------------------------------------
# Anchor verification cache: identical second run reuses; test with monkeypatch
# so no local Ollama/Tesseract dependency is required by the unit test.
# ------------------------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    image_path = root / "final_test.png"
    Image.new("RGB", (600, 300), "white").save(image_path)
    source = {
        "source_kind": "traveler_event_crop",
        "document_role": "traveler",
        "document_family": "DRL_TRAVELER",
        "source_document": "final_test.png",
        "source_path": str(image_path),
        "image_path": str(image_path),
        "page_number": None,
    }

    original_tsv = mod._tesseract_tsv_words
    original_model_exists = mod.ollama_model_exists
    original_call = mod.call_ollama_vision
    try:
        mod._tesseract_tsv_words = lambda *a, **k: {"status":"ok","warning":None,"words":[
            {"text":"Passed","left":100,"top":50,"width":60,"height":20,"page_num":1,"block_num":1,"par_num":1,"line_num":1,"word_num":1,"conf":95},
            {"text":"All","left":165,"top":50,"width":25,"height":20,"page_num":1,"block_num":1,"par_num":1,"line_num":1,"word_num":2,"conf":95},
            {"text":"Tests","left":195,"top":50,"width":45,"height":20,"page_num":1,"block_num":1,"par_num":1,"line_num":1,"word_num":3,"conf":95},
        ]}
        mod.ollama_model_exists = lambda model: True
        mod.call_ollama_vision = lambda *a, **k: {"status":"ok","response":json.dumps({
            "target_label_seen": True,
            "selection_status":"selected",
            "event_mark":"X",
            "mark_type":"x_mark",
            "confidence":"high",
        }),"warning":None}

        mini_profiles = {"profiles":{"DRL_TRAVELER_FINAL_TEST":{"fields":[anchors["profiles"]["DRL_TRAVELER_FINAL_TEST"]["fields"][0]]}}}
        first = mod.analyze_anchor_fields([source], root/"out", "minicpm-v:latest", 10, mini_profiles)
        assert first[0]["cache_status"] == "created"
        second = mod.analyze_anchor_fields([source], root/"out", "minicpm-v:latest", 10, mini_profiles)
        assert second[0]["cache_status"] == "reused"
    finally:
        mod._tesseract_tsv_words = original_tsv
        mod.ollama_model_exists = original_model_exists
        mod.call_ollama_vision = original_call



# ------------------------------------------------------------------
# v1.5.5.4: human-directed global ignore and literal Final O.K. mark validation.
# ------------------------------------------------------------------
assert mod._globally_ignored_text_1554("48+") is True
assert mod._globally_ignored_text_1554("48+ hours") is True
assert mod._globally_ignored_label_1554("hours in final testing") is True
assert mod._globally_ignored_text_1554("Final O.K.") is False

# Reproduce the live bad MiniCPM response: schema placeholder mark + numeric
# "initials" from the ignored neighboring duration field. It must become
# ambiguous and cannot be a final-result candidate.
live_bad = mod._normalize_anchor_vision({
    "target_label_seen": True,
    "selection_status": "selected",
    "event_mark": "handwritten_value",
    "mark_type": "initials",
    "technician_initials": "8+",
    "date": None,
    "confidence": "high",
}, "DRL_TRAVELER_SHIPPING_FINAL_OK", {
    "field_id": "final_ok",
    "label": "Final O.K.",
    "global_ignore_policy": "ignore_final_testing_duration",
})
assert live_bad["selection_status"] == "ambiguous"
assert live_bad["event_mark"] is None
assert live_bad["technician_initials"] is None
assert "schema_placeholder_event_mark_rejected" in live_bad["validation_reasons"]
assert "invalid_or_nonliteral_technician_initials" in live_bad["validation_reasons"]

# Valid literal alphabetic initials remain selected.
valid_initials = mod._normalize_anchor_vision({
    "target_label_seen": True,
    "selection_status": "selected",
    "event_mark": "MP",
    "mark_type": "initials",
    "technician_initials": "MP",
    "date": None,
    "confidence": "high",
}, "DRL_TRAVELER_SHIPPING_FINAL_OK", {
    "field_id": "final_ok",
    "label": "Final O.K.",
})
assert valid_initials["selection_status"] == "selected"
assert valid_initials["event_mark"] == "MP"
assert valid_initials["technician_initials"] == "MP"

# Entire ignored-duration observations are removed from normal routed evidence.
ignore_analysis = {
    "analysis_id": "ignored-duration",
    "vision_status": "ok",
    "source": {
        "source_kind": "traveler_event_crop",
        "document_role": "traveler",
        "source_document": "shipping_final_ok.png",
        "source_path": "/derived/shipping_final_ok.png",
        "image_path": "/derived/shipping_final_ok.png",
    },
    "parsed_analysis": {
        "testing_items": [{
            "step_label": "48+ hours in final testing",
            "event_mark": "48+",
            "mark_type": "handwritten_value",
            "result": "recorded_value",
            "semantic_role": "procedure",
            "association_basis": "same_row",
            "confidence": "high",
        }],
        "final_result_items": [],
        "other_event_observations": [{
            "label": "hours in final testing",
            "value": "48+",
            "category": "administrative",
            "confidence": "high",
        }],
        "printed_template_only_labels": [],
        "uncertain_marks": [],
    },
}
ignore_review = mod.build_review(
    [ignore_analysis], rules, [], source_data, field_verifications=[]
)
assert ignore_review["testing"]["candidate_count"] == 0
assert ignore_review["hardening"]["routed_observation_count"] == 0
assert any(
    r["reason"] == "globally_ignored_testing_duration_reference"
    for r in ignore_review["hardening"]["testing_rejections"]
)

# Masking test: OCR word "48+" inside a Final O.K. relative crop is whitened
# and audited; no absolute form coordinate is encoded.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    image_path = root / "shipping_final_ok.png"
    Image.new("RGB", (1000, 300), "white").save(image_path)
    field = {
        "field_id": "final_ok",
        "label": "Final O.K.",
        "global_ignore_policy": "ignore_final_testing_duration",
        "crop": {
            "left_height_multiplier": 1.5,
            "right_height_multiplier": 4.0,
            "top_height_multiplier": 0.9,
            "bottom_height_multiplier": 0.9,
        },
    }
    crop = mod._relative_anchor_crop(
        image_path,
        [400, 100, 160, 40],
        field,
        root / "crop.png",
        ocr_words=[{
            "text": "48+",
            "left": 600,
            "top": 105,
            "width": 50,
            "height": 30,
        }],
    )
    assert crop["globally_ignored_regions_masked"] == 1
    assert crop["ignored_regions"][0]["policy"] == "ignore_final_testing_duration"

print("PASS: Nova DRL Testing / Final Result Fusion v1.5.5.4 tests")
