#!/usr/bin/env python3
"""Nova DRL GB8 Technician Answer Composer v1.3.8.4.

Read-only answer layer over the frozen v1.3.8.2 hybrid retrieval baseline.

Architecture:
    technician question
        -> v1.3.8.2 hybrid retrieval (Qdrant semantic + deterministic Python RRF)
        -> frozen v1.3.7.3 authoritative recurring groups + Traveler evidence
        -> local Qwen2.5 14B Q6 answer composition ONLY
        -> validated group-supported technician answer

The composer is not allowed to discover new evidence, alter recurrence counts,
approve facts, or write to Qdrant. Every model finding/check must cite at least one
recurring_group_id already selected by hybrid retrieval. Unknown IDs are dropped.
If composition fails, Python renders a deterministic evidence summary instead.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

VERSION = "1.3.8.4"
HYBRID_VERSION = "1.3.8.2"
SOURCE_SIGNAL_VERSION = "1.3.7.3"
DEFAULT_INPUT_ROOT = Path("/opt/nova-drl/output/traveler_technician_signal_v1_3_7_3")
DEFAULT_COLLECTION = "nova_drl_gb8_trial_v1_3_8_1"
DEFAULT_QDRANT_URL = "http://localhost:6333"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_COMPOSER_MODEL = "qwen25-drl:14b-q6-16k"
DEFAULT_CONFIG_NAME = "gb8_answer_composer_policy_v1_3_8_4.json"
EXPECTED_TECHNICIAN_GROUPS = 314

DEFAULT_POLICY: Dict[str, Any] = {
    "hybrid_final_top": 10,
    "compose_top": 8,
    "evidence_per_group": 3,
    "max_findings": 6,
    "max_checks": 5,
    "num_ctx": 16384,
    "num_predict": 1400,
    "temperature": 0.0,
    "json_retries": 1,
    "timeout": 180,
    "strict_expected_qdrant_points": 314,
}

COMPOSER_INSTRUCTIONS = r"""
You are the Nova DRL technician answer COMPOSER, not the evidence search engine.
The evidence set below was already selected by Nova DRL hybrid retrieval from historical GB8 Travelers.

OPERATING RULES:
- Use ONLY the supplied retrieved groups and their representative Traveler evidence.
- Do not invent a root cause, repair, part number, specification, recurrence count, test, or outcome.
- Do not claim that correlation proves causation.
- Do not convert provisional history into an approved DRL procedure or fact.
- Prefer practical 80/20 technician language: concise, direct, useful.
- Minor OCR/shop wording can be paraphrased when meaning is clear; do not dwell on wording disputes.
- A finding may summarize several supplied lines, but every finding MUST cite one or more supplied recurring_group_id values.
- A suggested first check must be clearly framed as historically motivated, not as a mandatory SOP, and MUST cite supplied recurring_group_id values.
- If evidence is mixed or weak, say so briefly instead of filling the gap.
- Never cite a group ID that is not in the supplied evidence set.

Return JSON ONLY in exactly this shape:
{
  "findings": [
    {"statement": "concise historical finding", "support_group_ids": ["rg_..."]}
  ],
  "suggested_checks": [
    {"statement": "historically motivated first check", "support_group_ids": ["rg_..."]}
  ],
  "caution": "optional short uncertainty/caveat"
}
""".strip()


def _load_module(path: Path, module_name: str):
    if not path.exists():
        raise FileNotFoundError(f"Required Nova DRL module missing: {path}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_hybrid(repo_root: Path):
    path = repo_root / "analysis" / "nova_gb8_hybrid_technician_search_v1_3_8_2.py"
    return _load_module(path, "nova_gb8_hybrid_v1382")


def load_policy(path: Path | None) -> Dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    if path is not None and path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        vals = payload.get("answer_composer_policy") if isinstance(payload, dict) else None
        if isinstance(vals, dict):
            for key in policy:
                if key in vals:
                    policy[key] = vals[key]
    return policy


def exact_model_available(model: str, names: Sequence[str]) -> bool:
    wanted = model.lower()
    return any(str(name).lower() in {wanted, wanted + ":latest"} for name in names)


def compact(value: Any, limit: int = 360) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def group_id(group: Mapping[str, Any]) -> str:
    return str(group.get("group_id") or group.get("recurring_group_id") or "")


def build_composer_payload(query: str, hybrid_result: Mapping[str, Any], compose_top: int, evidence_per_group: int) -> Dict[str, Any]:
    rows = list(hybrid_result.get("results") or [])[: max(1, int(compose_top))]
    groups: List[Dict[str, Any]] = []
    for rank, row in enumerate(rows, 1):
        g = row.get("group") or {}
        gid = str(row.get("group_id") or group_id(g))
        if not gid:
            continue
        examples: List[Dict[str, Any]] = []
        for ex in (row.get("examples") or [])[: max(1, int(evidence_per_group))]:
            examples.append({
                "log_number": ex.get("log_number"),
                "serial_number": ex.get("serial_number"),
                "raw_source_text": compact(ex.get("raw_source_text"), 420),
            })
        groups.append({
            "hybrid_rank": rank,
            "recurring_group_id": gid,
            "concept_label": g.get("concept_label") or g.get("concept_key") or gid,
            "lane": g.get("lane"),
            "service_areas": list(g.get("v1_3_7_3_service_areas") or []),
            "distinct_serial_count": int(g.get("distinct_serial_count") or 0),
            "distinct_log_count": int(g.get("distinct_log_count") or 0),
            "retrieval_source": (
                "both" if row.get("semantic_rank") is not None and row.get("python_rank") is not None
                else ("qdrant" if row.get("semantic_rank") is not None else "python")
            ),
            "representative_evidence": examples,
        })
    return {
        "question": query,
        "knowledge_status": "provisional",
        "source_version": SOURCE_SIGNAL_VERSION,
        "hybrid_version": HYBRID_VERSION,
        "coverage": {
            "serials": int(hybrid_result.get("top_union_serial_count") or 0),
            "logs": int(hybrid_result.get("top_union_log_count") or 0),
        },
        "retrieved_groups": groups,
    }


def make_prompt(payload: Mapping[str, Any]) -> str:
    return COMPOSER_INSTRUCTIONS + "\n\nRETRIEVED EVIDENCE SET:\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def parse_json_text(text: str) -> Any:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


def ollama_compose_json(qmod: Any, ollama_url: str, model: str, prompt: str, *, num_ctx: int, num_predict: int, temperature: float, timeout: int, retries: int) -> Tuple[Any, List[Dict[str, Any]]]:
    attempts: List[Dict[str, Any]] = []
    current_prompt = prompt
    for attempt in range(1, max(0, int(retries)) + 2):
        try:
            _, outer = qmod.http_json(
                "POST",
                qmod.join_url(ollama_url, "/api/generate"),
                {
                    "model": model,
                    "prompt": current_prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": float(temperature),
                        "num_ctx": int(num_ctx),
                        "num_predict": int(num_predict),
                    },
                },
                timeout=int(timeout),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response_text = str(outer.get("response") or "") if isinstance(outer, dict) else ""
            parsed = parse_json_text(response_text)
            attempts.append({"attempt": attempt, "ok": True, "error": None})
            return parsed, attempts
        except Exception as e:
            attempts.append({"attempt": attempt, "ok": False, "error": str(e)})
            current_prompt = prompt + "\n\nIMPORTANT RETRY: Your prior response was invalid. Return ONLY a valid JSON object matching the required schema.\n"
    return None, attempts


def _validated_items(items: Any, allowed_ids: set[str], limit: int) -> Tuple[List[Dict[str, Any]], int]:
    valid: List[Dict[str, Any]] = []
    rejected = 0
    if not isinstance(items, list):
        return valid, int(items is not None)
    for item in items:
        if not isinstance(item, dict):
            rejected += 1
            continue
        statement = compact(item.get("statement"), 520)
        ids = []
        for gid in item.get("support_group_ids") or []:
            sgid = str(gid)
            if sgid in allowed_ids and sgid not in ids:
                ids.append(sgid)
        if not statement or not ids:
            rejected += 1
            continue
        valid.append({"statement": statement, "support_group_ids": ids})
        if len(valid) >= max(1, int(limit)):
            break
    return valid, rejected


def validate_composition(parsed: Any, allowed_ids: set[str], max_findings: int, max_checks: int) -> Dict[str, Any]:
    if not isinstance(parsed, dict):
        return {"findings": [], "suggested_checks": [], "caution": "", "rejected_items": 1, "usable": False}
    findings, r1 = _validated_items(parsed.get("findings"), allowed_ids, max_findings)
    checks, r2 = _validated_items(parsed.get("suggested_checks"), allowed_ids, max_checks)
    caution = compact(parsed.get("caution"), 420)
    return {
        "findings": findings,
        "suggested_checks": checks,
        "caution": caution,
        "rejected_items": r1 + r2,
        "usable": bool(findings or checks),
    }


def deterministic_fallback(hybrid_result: Mapping[str, Any], max_findings: int) -> Dict[str, Any]:
    findings: List[Dict[str, Any]] = []
    for row in (hybrid_result.get("results") or [])[: max(1, int(max_findings))]:
        g = row.get("group") or {}
        gid = str(row.get("group_id") or group_id(g))
        label = compact(g.get("concept_label") or g.get("concept_key") or gid, 160)
        serials = int(g.get("distinct_serial_count") or 0)
        logs = int(g.get("distinct_log_count") or 0)
        if not gid:
            continue
        findings.append({
            "statement": f"Historical GB8 pattern: {label} ({serials} distinct serials / {logs} repair logs).",
            "support_group_ids": [gid],
        })
    return {
        "findings": findings,
        "suggested_checks": [],
        "caution": "14B composition was unavailable or unusable; this answer is a deterministic summary of the hybrid retrieval results.",
        "rejected_items": 0,
        "usable": bool(findings),
    }


def support_text(ids: Sequence[str], by_id: Mapping[str, Mapping[str, Any]]) -> str:
    """Human-facing support text. Internal recurring-group IDs remain in JSON/provenance."""
    parts = []
    for gid in ids:
        g = by_id.get(str(gid)) or {}
        label = compact(g.get("concept_label") or g.get("concept_key") or "historical pattern", 90)
        serials = int(g.get("distinct_serial_count") or 0)
        logs = int(g.get("distinct_log_count") or 0)
        parts.append(f"{label} ({serials} serials / {logs} logs)")
    return "; ".join(parts)


def render_answer(result: Mapping[str, Any], show_retrieval: bool = False, show_evidence: bool = False, hybrid_renderer: Any | None = None) -> str:
    hybrid = result.get("hybrid") or {}
    composition = result.get("composition") or {}
    rows = hybrid.get("results") or []
    by_id = {
        str(r.get("group_id") or group_id(r.get("group") or {})): (r.get("group") or {})
        for r in rows
        if str(r.get("group_id") or group_id(r.get("group") or {}))
    }
    labels = []
    for r in rows[:3]:
        g = r.get("group") or {}
        label = compact(g.get("concept_label") or g.get("concept_key") or r.get("group_id"), 90)
        if label and label not in labels:
            labels.append(label)

    lines = [
        f"# Nova DRL GB8 Technician Answer v{VERSION}",
        f"Question: {result.get('query')}",
        "Status: FAST PROVISIONAL 80/20 — historical guidance, not an approved SOP or final fact set",
        "",
        "ANSWER",
        "------",
        (
            f"Hybrid retrieval selected {len(rows)} recurring GB8 patterns covering approximately "
            f"{int(hybrid.get('top_union_serial_count') or 0)} distinct serials / {int(hybrid.get('top_union_log_count') or 0)} repair logs."
        ),
    ]
    if labels:
        lines.append("Strongest retrieved themes: " + "; ".join(labels) + ".")

    findings = composition.get("findings") or []
    lines += ["", "HISTORICAL FINDINGS", "-------------------"]
    if findings:
        for item in findings:
            lines.append(f"- {item.get('statement')}")
            lines.append("  support: " + support_text(item.get("support_group_ids") or [], by_id))
    else:
        lines.append("- No model-composed finding survived source-ID validation; review the retrieved evidence below.")

    checks = composition.get("suggested_checks") or []
    lines += ["", "SUGGESTED FIRST CHECKS — HISTORICALLY MOTIVATED", "----------------------------------------------"]
    if checks:
        for item in checks:
            lines.append(f"- {item.get('statement')}")
            lines.append("  support: " + support_text(item.get("support_group_ids") or [], by_id))
    else:
        lines.append("- No additional check was composed; follow unit-specific evidence and the retrieved historical patterns.")

    if composition.get("caution"):
        lines += ["", "CAUTION", "-------", str(composition.get("caution"))]

    if show_evidence:
        lines += ["", "REPRESENTATIVE TRAVELER EVIDENCE", "-------------------------------"]
        for i, row in enumerate(rows[:5], 1):
            g = row.get("group") or {}
            gid = str(row.get("group_id") or group_id(g))
            label = compact(g.get("concept_label") or g.get("concept_key") or "historical pattern", 120)
            lines.append(f"{i}. {label} | {int(g.get('distinct_serial_count') or 0)} serials / {int(g.get('distinct_log_count') or 0)} logs")
            for ex in (row.get("examples") or [])[:2]:
                text = compact(ex.get("raw_source_text"), 260)
                lines.append(f"   - {ex.get('log_number') or '?'} | {ex.get('serial_number') or '?'} | {text}")
                source_path = str(ex.get("source_path") or "").strip()
                if source_path:
                    lines.append(f"     source: {source_path}")
    else:
        lines += ["", "Traveler evidence: hidden by default; use --show-evidence (or :evidence on) when needed."]

    lines += [
        "",
        "RETRIEVAL / COMPOSITION POLICY",
        "------------------------------",
        f"Hybrid retrieval: v{HYBRID_VERSION} (Qdrant + deterministic Python RRF)",
        f"Answer composer: {result.get('composer_model')} | model calls={result.get('composer_calls')} | fallback={str(result.get('fallback_used')).upper()}",
        f"Unsupported/invalid composed items dropped by Python: {int(composition.get('rejected_items') or 0)}",
        "Recurrence counts: inherited from frozen Python-counted source; model cannot modify them",
        "Accepted facts: 0",
        "Qdrant writes: 0",
        "Source evidence modified: NO",
    ]

    if show_retrieval and hybrid_renderer is not None:
        lines += ["", "RAW HYBRID RETRIEVAL", "--------------------", hybrid_renderer(hybrid).rstrip()]
    return "\n".join(lines) + "\n"


def compose_answer(hmod: Any, qmod: Any, pmod: Any, index: Mapping[str, Any], aliases: Mapping[str, List[str]], args: argparse.Namespace, query: str, hybrid_policy: Mapping[str, Any], composer_policy: Mapping[str, Any], api_key: str) -> Dict[str, Any]:
    # Answer composition must never change the retrieval pool. v1.3.8.2 remains
    # the evidence selector and frozen v1.3.7.3 remains authoritative.
    hybrid_result = hmod.hybrid_search(qmod, pmod, index, aliases, args, query, hybrid_policy, api_key)
    compose_top = int(args.compose_top if args.compose_top is not None else composer_policy["compose_top"])
    evidence_per_group = int(args.compose_evidence if args.compose_evidence is not None else composer_policy["evidence_per_group"])
    payload = build_composer_payload(query, hybrid_result, compose_top, evidence_per_group)
    allowed_ids = {str(g.get("recurring_group_id")) for g in payload.get("retrieved_groups") or [] if g.get("recurring_group_id")}
    prompt = make_prompt(payload)

    parsed, attempts = ollama_compose_json(
        qmod,
        args.ollama_url,
        args.composer_model,
        prompt,
        num_ctx=int(composer_policy["num_ctx"]),
        num_predict=int(args.num_predict if args.num_predict is not None else composer_policy["num_predict"]),
        temperature=float(composer_policy["temperature"]),
        timeout=int(args.compose_timeout if args.compose_timeout is not None else composer_policy["timeout"]),
        retries=int(composer_policy["json_retries"]),
    )
    composition = validate_composition(parsed, allowed_ids, int(composer_policy["max_findings"]), int(composer_policy["max_checks"]))
    fallback_used = False
    if not composition.get("usable"):
        composition = deterministic_fallback(hybrid_result, int(composer_policy["max_findings"]))
        fallback_used = True

    return {
        "version": VERSION,
        "query": query,
        "hybrid": hybrid_result,
        "composition": composition,
        "composer_model": args.composer_model,
        "composer_calls": len(attempts),
        "composer_attempts": attempts,
        "fallback_used": fallback_used,
        "accepted_fact_count": 0,
        "qdrant_writes": 0,
        "source_modified": False,
        "knowledge_status": "provisional",
    }


def status_report(hmod: Any, qmod: Any, pmod: Any, args: argparse.Namespace, hybrid_policy: Mapping[str, Any], composer_policy: Mapping[str, Any], api_key: str) -> int:
    print(f"# Nova DRL GB8 Technician Answer Composer Status v{VERSION}")
    try:
        index = pmod.load_index(Path(args.input_root))
        print(f"Frozen source: PASS | source={index['counts']['source']} technician={index['counts']['technician']} reference={index['counts']['reference']}")
    except Exception as e:
        print(f"Frozen source: ERROR | {e}")
    try:
        models = qmod.ollama_models(args.ollama_url, args.timeout)
        print(f"Ollama embed:  {'FOUND' if qmod.model_available(args.embed_model, models) else 'NOT FOUND'} | {args.embed_model}")
        print(f"Ollama compose:{'FOUND' if exact_model_available(args.composer_model, models) else 'NOT FOUND'} | {args.composer_model}")
    except Exception as e:
        print(f"Ollama: ERROR | {e}")
    try:
        info = qmod.qdrant_collection_info(args.qdrant_url, args.collection, api_key, args.timeout)
        if info is None:
            print(f"Qdrant trial: NOT FOUND | {args.collection}")
        else:
            count = qmod.qdrant_count(args.qdrant_url, args.collection, api_key, args.timeout)
            expected = int(composer_policy.get("strict_expected_qdrant_points") or EXPECTED_TECHNICIAN_GROUPS)
            state = "PASS" if count == expected else "WARNING"
            print(f"Qdrant trial: {state} | {args.collection} | points={count} expected={expected}")
    except Exception as e:
        print(f"Qdrant trial: ERROR | {e}")
    print("Composer role: read-only synthesis over retrieved evidence; accepted facts=0; Qdrant writes=0")
    return 0


def self_check(pmod: Any, args: argparse.Namespace, composer_policy: Mapping[str, Any]) -> int:
    index = pmod.load_index(Path(args.input_root))
    expected = int(composer_policy.get("strict_expected_qdrant_points") or EXPECTED_TECHNICIAN_GROUPS)
    if int(index["counts"]["technician"]) != expected:
        raise ValueError(f"Expected {expected} technician groups, found {index['counts']['technician']}")
    allowed = {"rg_a", "rg_b"}
    parsed = {
        "findings": [
            {"statement": "supported", "support_group_ids": ["rg_a"]},
            {"statement": "unknown", "support_group_ids": ["rg_x"]},
        ],
        "suggested_checks": [{"statement": "supported check", "support_group_ids": ["rg_b", "rg_x"]}],
        "caution": "provisional",
    }
    val = validate_composition(parsed, allowed, 6, 5)
    if len(val["findings"]) != 1 or len(val["suggested_checks"]) != 1 or val["suggested_checks"][0]["support_group_ids"] != ["rg_b"]:
        raise AssertionError("Composer source-ID validation failed")
    print(
        f"PASS: v{VERSION} frozen-source self-check | source={index['counts']['source']} technician={index['counts']['technician']} "
        f"reference={index['counts']['reference']} | hybrid=v{HYBRID_VERSION} | composer_source_id_validation=PASS | accepted_facts=0 | qdrant_writes=0"
    )
    return 0


def interactive_loop(hmod: Any, qmod: Any, pmod: Any, index: Mapping[str, Any], aliases: Mapping[str, List[str]], args: argparse.Namespace, hybrid_policy: Mapping[str, Any], composer_policy: Mapping[str, Any], api_key: str) -> int:
    print(f"Nova DRL GB8 Technician Answer Composer v{VERSION}")
    print(f"Loaded frozen groups: {index['counts']['technician']} technician + {index['counts']['reference']} reference.")
    print(f"Hybrid retrieval: v{HYBRID_VERSION} | composer: {args.composer_model}")
    print("Type only the technician question. Commands: :status, :evidence on, :evidence off, :retrieval on, :retrieval off, :quit")
    show_retrieval = bool(args.show_retrieval)
    show_evidence = bool(args.show_evidence)
    while True:
        try:
            q = input("nova> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            continue
        if q in {":quit", ":q", "quit", "exit"}:
            return 0
        if q == ":status":
            status_report(hmod, qmod, pmod, args, hybrid_policy, composer_policy, api_key)
            continue
        if q == ":evidence on":
            show_evidence = True
            print("Traveler evidence display: ON")
            continue
        if q == ":evidence off":
            show_evidence = False
            print("Traveler evidence display: OFF")
            continue
        if q == ":retrieval on":
            show_retrieval = True
            print("Raw hybrid retrieval display: ON")
            continue
        if q == ":retrieval off":
            show_retrieval = False
            print("Raw hybrid retrieval display: OFF")
            continue
        mode = pmod.detect_mode(q, args.mode)
        if mode != "search":
            result = pmod.execute_query(dict(index), q, dict(aliases), mode, max(1, int(args.top or hybrid_policy["final_top"])), max(1, int(args.examples)), False)
            print(pmod.render_result(result))
            continue
        result = compose_answer(hmod, qmod, pmod, index, aliases, args, q, hybrid_policy, composer_policy, api_key)
        print(render_answer(result, show_retrieval=show_retrieval, show_evidence=show_evidence, hybrid_renderer=hmod.render_hybrid))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nova DRL GB8 Technician Answer Composer v1.3.8.4 — clean technician output")
    parser.add_argument("query", nargs="*", help="Technician question, e.g. 'Y axis drifting'")
    parser.add_argument("--input-root", default=str(DEFAULT_INPUT_ROOT))
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--qdrant-url", default=DEFAULT_QDRANT_URL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    parser.add_argument("--composer-model", default=DEFAULT_COMPOSER_MODEL)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--hybrid-config", default="", help="v1.3.8.2 hybrid policy JSON")
    parser.add_argument("--config", default="", help="v1.3.8.4 answer composer policy JSON")
    parser.add_argument("--mode", choices=["auto", "search", "stocking", "service-areas", "serial", "log"], default="auto")
    parser.add_argument("--semantic-top", type=int, default=None)
    parser.add_argument("--python-top", type=int, default=None)
    parser.add_argument("--top", type=int, default=None, help="Hybrid final results before composition")
    parser.add_argument("--examples", type=int, default=3, help="Hybrid evidence examples per result")
    parser.add_argument("--rrf-k", type=float, default=None)
    parser.add_argument("--semantic-weight", type=float, default=None)
    parser.add_argument("--python-weight", type=float, default=None)
    parser.add_argument("--recurrence-weight", type=float, default=None)
    parser.add_argument("--score-threshold", type=float, default=None)
    parser.add_argument("--compose-top", type=int, default=None, help="Top hybrid groups supplied to 14B")
    parser.add_argument("--compose-evidence", type=int, default=None, help="Evidence lines per supplied group")
    parser.add_argument("--num-predict", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=120, help="Retrieval/network timeout")
    parser.add_argument("--compose-timeout", type=int, default=None)
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--show-evidence", action="store_true", help="Show representative Traveler evidence; hidden by default")
    parser.add_argument("--show-retrieval", action="store_true", help="Append raw v1.3.8.2 hybrid retrieval")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[1]
    hmod = load_hybrid(repo_root)
    qmod, pmod = hmod.load_engines(repo_root)
    hybrid_config_path = Path(args.hybrid_config) if args.hybrid_config else repo_root / "config" / hmod.DEFAULT_CONFIG_NAME
    hybrid_policy = hmod.load_policy(hybrid_config_path)
    composer_config_path = Path(args.config) if args.config else repo_root / "config" / DEFAULT_CONFIG_NAME
    composer_policy = load_policy(composer_config_path)
    if args.score_threshold is None:
        args.score_threshold = hybrid_policy.get("score_threshold")
    if args.top is None:
        args.top = int(composer_policy.get("hybrid_final_top") or hybrid_policy["final_top"])
    api_key = args.api_key or os.getenv("QDRANT_API_KEY", "")

    if args.self_check:
        return self_check(pmod, args, composer_policy)
    if args.status:
        return status_report(hmod, qmod, pmod, args, hybrid_policy, composer_policy, api_key)

    index = pmod.load_index(Path(args.input_root))
    alias_path = repo_root / "config" / "gb8_query_aliases_v1_3_8_0.json"
    aliases = pmod.load_aliases(alias_path if alias_path.exists() else None)

    if args.interactive or not args.query:
        return interactive_loop(hmod, qmod, pmod, index, aliases, args, hybrid_policy, composer_policy, api_key)

    query = " ".join(args.query).strip()
    mode = pmod.detect_mode(query, args.mode)
    if mode != "search":
        result = pmod.execute_query(index, query, aliases, mode, max(1, int(args.top)), max(1, int(args.examples)), False)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(pmod.render_result(result), end="")
        return 0

    result = compose_answer(hmod, qmod, pmod, index, aliases, args, query, hybrid_policy, composer_policy, api_key)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(render_answer(result, show_retrieval=args.show_retrieval, show_evidence=args.show_evidence, hybrid_renderer=hmod.render_hybrid), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
