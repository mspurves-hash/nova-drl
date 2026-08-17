#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "analysis" / "nova_gb8_technician_answer_composer_v1_3_8_4.py"
spec = importlib.util.spec_from_file_location("composer", SCRIPT)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def group(gid="rg_a", label="Y Axis Drift", serials=10, logs=14):
    return {
        "group_id": gid,
        "concept_label": label,
        "lane": "diagnostics",
        "distinct_serial_count": serials,
        "distinct_log_count": logs,
        "v1_3_7_3_service_areas": ["Servo / drift / homing"],
    }


def hybrid_result():
    return {
        "query": "Y axis drifting",
        "top_union_serial_count": 12,
        "top_union_log_count": 16,
        "results": [
            {
                "group_id": "rg_a",
                "group": group("rg_a", "Y Axis Drift", 10, 14),
                "semantic_rank": 1,
                "python_rank": 1,
                "examples": [
                    {"log_number": "130613003", "serial_number": "80010732", "raw_source_text": "Y axis is drifting and skipping belts."},
                    {"log_number": "191029004", "serial_number": "80050608", "raw_source_text": "Y-axis will drift unless ground is connected"},
                ],
            },
            {
                "group_id": "rg_b",
                "group": group("rg_b", "Y Home Flag Checks", 6, 6),
                "semantic_rank": None,
                "python_rank": 2,
                "examples": [
                    {"log_number": "170306003", "serial_number": "80050618", "raw_source_text": "Checked Y Home flag for binding"},
                ],
            },
        ],
    }


def test_payload_contains_only_selected_retrieval():
    payload = m.build_composer_payload("Y axis drifting", hybrid_result(), 1, 2)
    assert len(payload["retrieved_groups"]) == 1
    assert payload["retrieved_groups"][0]["recurring_group_id"] == "rg_a"
    assert payload["retrieved_groups"][0]["retrieval_source"] == "both"
    assert payload["coverage"] == {"serials": 12, "logs": 16}


def test_unknown_support_ids_are_dropped():
    parsed = {
        "findings": [
            {"statement": "Supported", "support_group_ids": ["rg_a"]},
            {"statement": "Unsupported", "support_group_ids": ["rg_x"]},
        ],
        "suggested_checks": [
            {"statement": "Check Y home flag based on history", "support_group_ids": ["rg_b", "rg_x"]}
        ],
        "caution": "Provisional",
    }
    val = m.validate_composition(parsed, {"rg_a", "rg_b"}, 6, 5)
    assert [x["statement"] for x in val["findings"]] == ["Supported"]
    assert val["suggested_checks"][0]["support_group_ids"] == ["rg_b"]
    assert val["rejected_items"] == 1
    assert val["usable"] is True


def test_invalid_composition_is_not_usable():
    val = m.validate_composition({"findings": [{"statement": "No support", "support_group_ids": []}]}, {"rg_a"}, 6, 5)
    assert not val["usable"]
    assert val["findings"] == []


def test_deterministic_fallback_preserves_group_ids():
    fb = m.deterministic_fallback(hybrid_result(), 2)
    assert fb["usable"] is True
    assert len(fb["findings"]) == 2
    assert fb["findings"][0]["support_group_ids"] == ["rg_a"]
    assert "10 distinct serials / 14 repair logs" in fb["findings"][0]["statement"]


def test_prompt_forbids_new_evidence():
    prompt = m.make_prompt(m.build_composer_payload("Y axis drifting", hybrid_result(), 2, 2))
    assert "Use ONLY the supplied retrieved groups" in prompt
    assert "Never cite a group ID" in prompt
    assert "rg_a" in prompt and "rg_b" in prompt


def test_ollama_json_call_and_parse():
    class FakeQ:
        @staticmethod
        def join_url(base, path):
            return base.rstrip("/") + path

        @staticmethod
        def http_json(method, url, payload=None, timeout=120, headers=None):
            assert payload["model"] == "qwen25-drl:14b-q6-16k"
            assert payload["format"] == "json"
            return 200, {"response": json.dumps({"findings": [{"statement": "x", "support_group_ids": ["rg_a"]}], "suggested_checks": [], "caution": ""})}

    parsed, attempts = m.ollama_compose_json(FakeQ, "http://fake", "qwen25-drl:14b-q6-16k", "prompt", num_ctx=16384, num_predict=1000, temperature=0, timeout=10, retries=1)
    assert parsed["findings"][0]["support_group_ids"] == ["rg_a"]
    assert len(attempts) == 1 and attempts[0]["ok"] is True


def test_render_displays_support_and_policy():
    result = {
        "query": "Y axis drifting",
        "hybrid": hybrid_result(),
        "composition": {
            "findings": [{"statement": "Y-axis drift recurs in the historical corpus.", "support_group_ids": ["rg_a"]}],
            "suggested_checks": [{"statement": "Based on history, inspect Y home-flag behavior.", "support_group_ids": ["rg_b"]}],
            "caution": "History is mixed; follow unit-specific evidence.",
            "rejected_items": 0,
        },
        "composer_model": "qwen25-drl:14b-q6-16k",
        "composer_calls": 1,
        "fallback_used": False,
    }
    text = m.render_answer(result)
    assert "HISTORICAL FINDINGS" in text
    assert "Y Axis Drift (10 serials / 14 logs)" in text
    assert "rg_a" not in text
    assert "REPRESENTATIVE TRAVELER EVIDENCE" not in text
    assert "Traveler evidence: hidden by default" in text
    assert "Accepted facts: 0" in text
    assert "Qdrant writes: 0" in text



def test_render_evidence_is_opt_in_and_clean():
    result = {
        "query": "Y axis drifting",
        "hybrid": hybrid_result(),
        "composition": {
            "findings": [{"statement": "Y-axis drift recurs.", "support_group_ids": ["rg_a"]}],
            "suggested_checks": [],
            "caution": "",
            "rejected_items": 0,
        },
        "composer_model": "qwen25-drl:14b-q6-16k",
        "composer_calls": 1,
        "fallback_used": False,
    }
    text = m.render_answer(result, show_evidence=True)
    assert "REPRESENTATIVE TRAVELER EVIDENCE" in text
    assert "130613003 | 80010732 | Y axis is drifting and skipping belts." in text
    assert "| rg_a" not in text


def test_support_text_never_exposes_internal_group_ids():
    txt = m.support_text(["rg_a"], {"rg_a": group("rg_a", "Y Axis Drift", 10, 14)})
    assert txt == "Y Axis Drift (10 serials / 14 logs)"
    assert "rg_" not in txt

def test_config_load():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "p.json"
        p.write_text(json.dumps({"answer_composer_policy": {"compose_top": 5, "num_predict": 900}}), encoding="utf-8")
        policy = m.load_policy(p)
        assert policy["compose_top"] == 5
        assert policy["num_predict"] == 900
        assert policy["hybrid_final_top"] == 10


def test_compose_answer_pipeline_uses_hybrid_then_14b():
    class FakeH:
        @staticmethod
        def hybrid_search(qmod, pmod, index, aliases, args, query, policy, api_key):
            assert query == "Y axis drifting"
            return hybrid_result()

    class FakeQ:
        @staticmethod
        def join_url(base, path):
            return base.rstrip("/") + path

        @staticmethod
        def http_json(method, url, payload=None, timeout=120, headers=None):
            body = {
                "findings": [{"statement": "Historical Y-axis drift includes home-position instability.", "support_group_ids": ["rg_a"]}],
                "suggested_checks": [{"statement": "Based on history, inspect Y home-flag behavior.", "support_group_ids": ["rg_b"]}],
                "caution": "Provisional history only."
            }
            return 200, {"response": json.dumps(body)}

    class FakeP:
        pass

    from argparse import Namespace
    args = Namespace(
        ollama_url="http://fake", composer_model="qwen25-drl:14b-q6-16k",
        compose_top=None, compose_evidence=None, num_predict=None, compose_timeout=None,
    )
    policy = dict(m.DEFAULT_POLICY)
    result = m.compose_answer(FakeH, FakeQ, FakeP, {}, {}, args, "Y axis drifting", {}, policy, "")
    assert result["fallback_used"] is False
    assert result["composer_calls"] == 1
    assert result["composition"]["findings"][0]["support_group_ids"] == ["rg_a"]
    assert result["composition"]["suggested_checks"][0]["support_group_ids"] == ["rg_b"]


def test_exact_composer_model_check_does_not_accept_32b_alias():
    names = ["qwen25-drl:32b-16k", "nomic-embed-text:latest"]
    assert m.exact_model_available("qwen25-drl:14b-q6-16k", names) is False
    names.append("qwen25-drl:14b-q6-16k")
    assert m.exact_model_available("qwen25-drl:14b-q6-16k", names) is True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in tests:
        fn()
    print("PASS: Nova DRL GB8 Technician Answer Composer v1.3.8.4 tests")
