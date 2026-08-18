#!/usr/bin/env python3
"""
Nova DRL RCL1A 80/20 Parts Intelligence v1.4.4

Purpose
-------
Turn the frozen v1.4.3 replacement mentions into two technician/purchasing views:

1) FUNCTIONAL REPLACEMENT FAMILIES — what commonly fails / gets replaced.
   Related components may share a useful technical family (for example MOSFETs),
   and same-spec substitute fuse part numbers may share one fuse family.

2) LIKELY ACTUAL PART-NUMBER USAGE — which individual part numbers were most
   often used/replaced. OCR/handwriting variants are consolidated provisionally,
   while every observed raw PN string remains preserved in the output.

This is an 80/20, volume-first post-processing layer. It DOES NOT rerun vision,
read source Line Card images, scan the NAS, read the prior hosted benchmark, write
Qdrant, or accept facts automatically. Python owns all frequency and quantity
counts. Qwen2.5 14B is used only to make provisional grouping/best-guess labels
from the already-extracted v1.4.3 evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.4.4"
SOURCE_VERSION = "1.4.3"
DEFAULT_SOURCE_ROOT = Path("/opt/nova-drl/output/rcl1a_indexed_focused_recovery_v1_4_3")
DEFAULT_OUTPUT_ROOT = Path("/opt/nova-drl/output/rcl1a_parts_intelligence_v1_4_4")
DEFAULT_REASON_MODEL = "qwen25-drl:14b-q6-16k"
OLLAMA_GENERATE_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"

PN_GROUP_PROMPT = """You are doing 80/20 PART-NUMBER CONSOLIDATION over repeated DRL repair evidence.
The inputs are observed handwritten/vision-read part-number strings. Python has already grouped plausible candidates into this block.

Return JSON only:
{"clusters":[{"likely_pn":"best likely actual PN or best normalized PN text","confidence":"high|medium|low","member_observed_pn_ids":["pnobs_..."],"reason":"short recurrence-based reason"}]}

Rules:
- Group strings when repeated evidence strongly suggests they are OCR/handwriting variants of the SAME actual purchased component.
- Do NOT group two genuinely different part numbers merely because both are MOSFETs, ICs, fuses, etc.
- Equivalent/substitute fuse PNs remain separate PN clusters; a later functional-family layer may group them by shared rating/function.
- likely_pn may normalize spacing, slashes, hyphens, capitalization, or an obvious one-character OCR variation when recurrence supports it.
- likely_pn must remain strongly grounded in the supplied member strings; do not use outside catalog knowledge.
- If uncertain, keep strings separate rather than forcing a merge.
- Use only supplied IDs. Each ID may appear at most once. Omitted IDs will be preserved by Python as singleton PN groups.
"""

FAMILY_STAGE1_PROMPT = """You are building technician-useful COMPONENT / REPLACEMENT FAMILIES from DRL repair evidence.
The inputs are already consolidated part-number signals plus description-only replacement signals.

Return JSON only:
{"families":[{"label":"short useful family label","functional_class":"semiconductor|fuse_protection|ic_control|passive|board_assembly|mechanical|other","member_signal_ids":["sig_..."],"reason":"short recurrence/function/spec reason"}]}

80/20 rules:
- Group by useful repair function/class, not by perfect OCR spelling.
- Different actual MOSFET PNs MAY share a MOSFET family; their exact PN rankings remain separate elsewhere.
- Same-spec substitute fuses MAY share a family when the evidence shows the same current/voltage/type function.
- Different fuse ratings/specifications should remain separate.
- Different resistor/capacitor values should remain separate when values are known.
- Board assemblies should not be merged into unrelated board-level ICs/components.
- Preserve practical distinctions a technician or purchaser would care about.
- Use only supplied signal IDs. Each ID at most once. Omitted IDs remain Python singletons.
- Do not use prior benchmark answers or outside parts lists.
"""

FAMILY_MERGE_PROMPT = """You are merging provisional DRL replacement-family groups from an earlier pass.
Return JSON only:
{"families":[{"label":"short useful final family label","functional_class":"semiconductor|fuse_protection|ic_control|passive|board_assembly|mechanical|other","member_temp_family_ids":["tf_..."],"reason":"short reason"}]}

Rules:
- Merge only groups representing the same useful technician replacement family/function.
- It is acceptable for different MOSFET PNs to share a MOSFET family while remaining separate PN groups underneath.
- Equivalent same-spec fuse PNs may share one functional fuse family; preserve different current/voltage specs separately.
- Do not over-merge unrelated components merely because they occur in the same repair.
- Use only supplied IDs; each ID at most once. Omitted groups remain separate.
- This is provisional 80/20 consolidation, not human approval.
"""


def normalized_ws(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def compact_alnum(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_hash(value: Any) -> str:
    return sha256_text(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def model_info(model: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"requested_model": model, "available": False}
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        for item in data.get("models") or []:
            if item.get("name") == model or item.get("model") == model:
                out.update({
                    "available": True,
                    "resolved_name": item.get("name") or item.get("model"),
                    "digest": item.get("digest"),
                    "size_bytes": item.get("size"),
                    "details": item.get("details"),
                })
                break
    except Exception as exc:
        out["error"] = str(exc)
    return out


def call_ollama(model: str, prompt: str, *, num_ctx: int, num_predict: int, timeout: int) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "num_ctx": int(num_ctx), "num_predict": int(num_predict)},
    }
    req = urllib.request.Request(
        OLLAMA_GENERATE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    return str(data.get("response") or "")


def parse_json_response(text: str) -> Any:
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        start = s.find("{")
        end = s.rfind("}")
        if start >= 0 and end > start:
            return json.loads(s[start:end + 1])
        raise


def call_json_with_retry(model: str, prompt: str, *, num_ctx: int, num_predict: int, timeout: int, retries: int = 1) -> Tuple[Any, List[Dict[str, Any]]]:
    attempts: List[Dict[str, Any]] = []
    current = prompt
    for attempt in range(retries + 1):
        try:
            raw = call_ollama(model, current, num_ctx=num_ctx, num_predict=num_predict, timeout=timeout)
            parsed = parse_json_response(raw)
            attempts.append({"attempt": attempt + 1, "ok": True, "response_chars": len(raw)})
            return parsed, attempts
        except Exception as exc:
            attempts.append({"attempt": attempt + 1, "ok": False, "error": str(exc)})
            current = prompt + "\n\nYour previous response was not valid JSON. Return only the requested JSON object."
    raise RuntimeError(attempts[-1].get("error") or "JSON model call failed")


def source_paths(source_root: Path) -> Dict[str, Path]:
    return {
        "mentions": source_root / "replacement_mentions_v1_4_3.jsonl",
        "family_map": source_root / "part_family_map_v1_4_3.json",
        "frequency": source_root / "part_frequency_v1_4_3.json",
        "manifest": source_root / "rcl1a_indexed_focused_manifest_v1_4_3.json",
    }


def family_hint_maps(family_map: Optional[Dict[str, Any]]) -> Tuple[Dict[str, str], Dict[str, str]]:
    if not family_map:
        return {}, {}
    descriptor_by_mention: Dict[str, str] = {}
    for d in family_map.get("descriptors") or []:
        for mid in d.get("mention_ids") or []:
            descriptor_by_mention[str(mid)] = str(d.get("descriptor_id"))
    family_by_descriptor: Dict[str, Dict[str, Any]] = {}
    for fam in family_map.get("families") or []:
        for did in fam.get("member_descriptor_ids") or []:
            family_by_descriptor[str(did)] = fam
    hint_id: Dict[str, str] = {}
    hint_label: Dict[str, str] = {}
    for mid, did in descriptor_by_mention.items():
        fam = family_by_descriptor.get(did)
        if fam:
            hint_id[mid] = str(fam.get("part_family_id") or "")
            hint_label[mid] = normalized_ws(fam.get("label") or "")
    return hint_id, hint_label


def mention_quantity_stats(rows: Sequence[Dict[str, Any]]) -> Tuple[int, int]:
    pieces = 0
    unstated = 0
    for m in rows:
        qty = m.get("quantity")
        if qty is None:
            unstated += 1
        else:
            try:
                pieces += int(qty)
            except Exception:
                unstated += 1
    return pieces, unstated


def build_pn_observations(mentions: Sequence[Dict[str, Any]], hint_id: Dict[str, str], hint_label: Dict[str, str]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for m in mentions:
        pn = normalized_ws(m.get("part_number"))
        if pn:
            grouped[pn].append(m)
    out: List[Dict[str, Any]] = []
    for pn, rows in sorted(grouped.items(), key=lambda x: x[0].lower()):
        oid = "pnobs_" + sha256_text(pn)[:16]
        pieces, unstated = mention_quantity_stats(rows)
        descs = sorted({normalized_ws(m.get("description")) for m in rows if normalized_ws(m.get("description"))})
        hints = Counter(hint_label.get(str(m.get("mention_id")), "") for m in rows if hint_label.get(str(m.get("mention_id")), ""))
        family_ids = sorted({hint_id.get(str(m.get("mention_id")), "") for m in rows if hint_id.get(str(m.get("mention_id")), "")})
        quotes: List[str] = []
        for m in rows:
            q = normalized_ws(m.get("raw_quote"))
            if q and q not in quotes:
                quotes.append(q)
            if len(quotes) >= 4:
                break
        out.append({
            "observed_pn_id": oid,
            "observed_pn": pn,
            "compact_pn": compact_alnum(pn),
            "mention_ids": [str(m.get("mention_id")) for m in rows],
            "repair_event_ids": sorted({str(m.get("repair_event_id")) for m in rows}),
            "repair_event_count": len({str(m.get("repair_event_id")) for m in rows}),
            "recorded_pieces": pieces,
            "quantity_unstated_mentions": unstated,
            "descriptions": descs[:8],
            "provisional_family_ids": family_ids,
            "provisional_family_labels": [x for x, _ in hints.most_common(6)],
            "example_quotes": quotes,
        })
    return out


def rating_tokens(text: str) -> set[str]:
    s = str(text or "").upper().replace("µ", "U")
    patterns = [
        r"\b\d+(?:\.\d+)?\s*(?:A|AMP|AMPS)\b",
        r"\b\d+(?:\.\d+)?\s*V\b",
        r"\b\d+(?:\.\d+)?\s*(?:UF|PF|NF)\b",
        r"\b\d+(?:\.\d+)?\s*(?:OHM|OHMS)\b",
    ]
    out: set[str] = set()
    for pattern in patterns:
        for m in re.findall(pattern, s):
            out.add(re.sub(r"\s+", "", m))
    return out


def pn_candidate_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    ka, kb = a.get("compact_pn") or "", b.get("compact_pn") or ""
    seq = SequenceMatcher(None, ka, kb).ratio() if ka and kb else 0.0
    prefix = 0.0
    if ka and kb:
        common = 0
        for ca, cb in zip(ka, kb):
            if ca != cb:
                break
            common += 1
        if common >= 4:
            prefix = min(0.18, common / max(len(ka), len(kb)) * 0.25)
    ha, hb = set(a.get("provisional_family_ids") or []), set(b.get("provisional_family_ids") or [])
    family_bonus = 0.16 if ha and hb and (ha & hb) else 0.0
    ra = rating_tokens((a.get("observed_pn") or "") + " " + " ".join(a.get("descriptions") or []))
    rb = rating_tokens((b.get("observed_pn") or "") + " " + " ".join(b.get("descriptions") or []))
    conflict = 0.28 if ra and rb and ra.isdisjoint(rb) else 0.0
    return seq + prefix + family_bonus - conflict


class UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))
        self.r = [0] * n

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        a, b = self.find(a), self.find(b)
        if a == b:
            return
        if self.r[a] < self.r[b]:
            a, b = b, a
        self.p[b] = a
        if self.r[a] == self.r[b]:
            self.r[a] += 1


def pn_candidate_components(observations: Sequence[Dict[str, Any]], threshold: float) -> List[List[str]]:
    if not observations:
        return []
    uf = UnionFind(len(observations))
    for i in range(len(observations)):
        for j in range(i + 1, len(observations)):
            a, b = observations[i], observations[j]
            shared_family = bool(set(a.get("provisional_family_ids") or []) & set(b.get("provisional_family_ids") or []))
            ra = rating_tokens((a.get("observed_pn") or "") + " " + " ".join(a.get("descriptions") or []))
            rb = rating_tokens((b.get("observed_pn") or "") + " " + " ".join(b.get("descriptions") or []))
            rating_conflict = bool(ra and rb and ra.isdisjoint(rb))
            # v1.4.3 family membership is only a candidate-block hint, never a forced
            # PN merge. Putting broad-family strings in the same 14B block lets the
            # model split FDH/IXFX MOSFETs while still seeing severely mangled OCR
            # variants that lexical similarity alone would miss.
            if (shared_family and not rating_conflict) or pn_candidate_similarity(a, b) >= threshold:
                uf.union(i, j)
    groups: Dict[int, List[str]] = defaultdict(list)
    for i, row in enumerate(observations):
        groups[uf.find(i)].append(str(row["observed_pn_id"]))
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))


def pack_components(components: Sequence[List[str]], max_items: int) -> List[List[str]]:
    batches: List[List[str]] = []
    current: List[str] = []
    for comp in components:
        if len(comp) > max_items:
            if current:
                batches.append(current)
                current = []
            for i in range(0, len(comp), max_items):
                batches.append(comp[i:i + max_items])
            continue
        if current and len(current) + len(comp) > max_items:
            batches.append(current)
            current = []
        current.extend(comp)
    if current:
        batches.append(current)
    return batches


def most_supported_observed(ids: Sequence[str], by_id: Dict[str, Dict[str, Any]]) -> str:
    ranked = sorted(
        (by_id[x] for x in ids),
        key=lambda r: (-int(r.get("repair_event_count") or 0), -int(r.get("recorded_pieces") or 0), -len(compact_alnum(r.get("observed_pn"))), str(r.get("observed_pn")).lower()),
    )
    return str(ranked[0].get("observed_pn") or "") if ranked else ""


def likely_pn_grounded(candidate: str, member_ids: Sequence[str], by_id: Dict[str, Dict[str, Any]]) -> bool:
    c = compact_alnum(candidate)
    if len(c) < 2:
        return False
    for mid in member_ids:
        o = compact_alnum(by_id[mid].get("observed_pn"))
        if not o:
            continue
        if c == o or c in o or o in c:
            return True
        if SequenceMatcher(None, c, o).ratio() >= 0.72:
            return True
    return False


def validate_pn_clusters(parsed: Any, batch_ids: Sequence[str], by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    known = set(batch_ids)
    assigned: set[str] = set()
    groups: List[Dict[str, Any]] = []
    clusters = parsed.get("clusters") if isinstance(parsed, dict) else None
    if not isinstance(clusters, list):
        clusters = []
    for c in clusters:
        if not isinstance(c, dict):
            continue
        ids: List[str] = []
        for x in c.get("member_observed_pn_ids") or []:
            s = str(x)
            if s in known and s not in assigned:
                ids.append(s)
        ids = list(dict.fromkeys(ids))
        if not ids:
            continue
        assigned.update(ids)
        candidate = normalized_ws(c.get("likely_pn"))
        if not likely_pn_grounded(candidate, ids, by_id):
            candidate = most_supported_observed(ids, by_id)
        confidence = str(c.get("confidence") or "low").lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        gid = "png_" + sha256_text("\n".join(sorted(ids)))[:16]
        groups.append({
            "pn_group_id": gid,
            "likely_pn": candidate,
            "confidence": confidence,
            "member_observed_pn_ids": ids,
            "reason": normalized_ws(c.get("reason")),
            "origin": "model_provisional_cluster",
        })
    for oid in batch_ids:
        if oid not in assigned:
            gid = "png_" + sha256_text(oid)[:16]
            groups.append({
                "pn_group_id": gid,
                "likely_pn": str(by_id[oid].get("observed_pn") or ""),
                "confidence": "low",
                "member_observed_pn_ids": [oid],
                "reason": "preserved singleton",
                "origin": "python_singleton",
            })
    return groups


def consolidate_same_likely_pn(groups: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for g in groups:
        key = compact_alnum(g.get("likely_pn")) or str(g.get("pn_group_id"))
        buckets[key].append(g)
    out: List[Dict[str, Any]] = []
    for key, rows in sorted(buckets.items()):
        ids: List[str] = []
        for r in rows:
            ids.extend(str(x) for x in r.get("member_observed_pn_ids") or [])
        ids = list(dict.fromkeys(ids))
        label = next((normalized_ws(r.get("likely_pn")) for r in rows if normalized_ws(r.get("likely_pn"))), key)
        confs = [str(r.get("confidence") or "low") for r in rows]
        confidence = "high" if confs and all(x == "high" for x in confs) else ("medium" if "high" in confs or "medium" in confs else "low")
        out.append({
            "pn_group_id": "png_" + sha256_text("\n".join(sorted(ids)))[:16],
            "likely_pn": label,
            "confidence": confidence,
            "member_observed_pn_ids": ids,
            "reason": "; ".join(dict.fromkeys(normalized_ws(r.get("reason")) for r in rows if normalized_ws(r.get("reason"))))[:500],
            "origin": "post_batch_consolidation" if len(rows) > 1 else rows[0].get("origin"),
        })
    return out


def run_pn_grouping(args: argparse.Namespace, mentions: Sequence[Dict[str, Any]], family_map: Optional[Dict[str, Any]], source_hash: str) -> Dict[str, Any]:
    output_root = Path(args.output_root)
    hint_id, hint_label = family_hint_maps(family_map)
    observations = build_pn_observations(mentions, hint_id, hint_label)
    by_id = {str(x["observed_pn_id"]): x for x in observations}
    components = pn_candidate_components(observations, float(args.pn_candidate_threshold))
    batches = pack_components(components, int(args.pn_batch_size))
    rinfo = model_info(args.reason_model)
    all_groups: List[Dict[str, Any]] = []
    run_meta: List[Dict[str, Any]] = []
    for idx, ids in enumerate(batches, 1):
        compact = []
        for oid in ids:
            r = by_id[oid]
            compact.append({
                "observed_pn_id": oid,
                "observed_pn": r["observed_pn"],
                "repair_event_count": r["repair_event_count"],
                "recorded_pieces": r["recorded_pieces"],
                "descriptions": r["descriptions"][:5],
                "provisional_family_labels": r["provisional_family_labels"][:4],
            })
        prompt = PN_GROUP_PROMPT + "\n\nOBSERVED PN CANDIDATES:\n" + json.dumps(compact, ensure_ascii=False)
        bdir = output_root / "pn_grouping" / f"batch_{idx:04d}"
        parsed_path = bdir / "parsed.json"
        run_path = bdir / "run.json"
        batch_hash = stable_hash({"source": source_hash, "items": compact})
        action = "model_run"
        parsed: Any = {"clusters": []}
        attempts: List[Dict[str, Any]] = []
        if parsed_path.exists() and run_path.exists() and not args.force:
            try:
                run = load_json(run_path)
                if run.get("batch_manifest_sha256") == batch_hash and run.get("reason_model_digest") == rinfo.get("digest") and run.get("prompt_sha256") == sha256_text(PN_GROUP_PROMPT):
                    parsed = load_json(parsed_path)
                    attempts = run.get("attempts") or []
                    action = "cache"
            except Exception:
                pass
        if action != "cache":
            try:
                parsed, attempts = call_json_with_retry(args.reason_model, prompt, num_ctx=args.reason_num_ctx, num_predict=args.pn_num_predict, timeout=args.timeout, retries=1)
            except Exception as exc:
                attempts = [{"ok": False, "error": str(exc)}]
                parsed = {"clusters": []}
            save_json(parsed_path, parsed)
            save_json(run_path, {
                "version": VERSION,
                "batch_manifest_sha256": batch_hash,
                "reason_model_digest": rinfo.get("digest"),
                "prompt_sha256": sha256_text(PN_GROUP_PROMPT),
                "attempts": attempts,
            })
        groups = validate_pn_clusters(parsed, ids, by_id)
        all_groups.extend(groups)
        run_meta.append({"batch": idx, "observed_pn_count": len(ids), "pn_group_count": len(groups), "run_action": action, "attempts": attempts})
        print(f"[pn {idx}/{len(batches)}] observed={len(ids)} groups={len(groups)} | {action}")
    all_groups = consolidate_same_likely_pn(all_groups)
    result = {
        "version": VERSION,
        "source_manifest_sha256": source_hash,
        "reason_model_digest": rinfo.get("digest"),
        "prompt_sha256": sha256_text(PN_GROUP_PROMPT),
        "candidate_threshold": float(args.pn_candidate_threshold),
        "observations": observations,
        "candidate_components": components,
        "groups": all_groups,
        "run_meta": run_meta,
        "accepted_facts": 0,
        "qdrant_entries": 0,
    }
    save_json(output_root / "pn_group_map_v1_4_4.json", result)
    return result


def build_signal_rows(mentions: Sequence[Dict[str, Any]], pn_map: Dict[str, Any], family_map: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hint_id, hint_label = family_hint_maps(family_map)
    obs_by_raw = {str(x.get("observed_pn")): x for x in pn_map.get("observations") or []}
    group_by_obs: Dict[str, Dict[str, Any]] = {}
    for g in pn_map.get("groups") or []:
        for oid in g.get("member_observed_pn_ids") or []:
            group_by_obs[str(oid)] = g

    mention_to_signal: Dict[str, str] = {}
    signal_meta: Dict[str, Dict[str, Any]] = {}

    for m in mentions:
        mid = str(m.get("mention_id"))
        pn = normalized_ws(m.get("part_number"))
        if pn and pn in obs_by_raw:
            obs = obs_by_raw[pn]
            group = group_by_obs.get(str(obs.get("observed_pn_id")))
            if group:
                sid = "sig_pn_" + str(group["pn_group_id"])[4:]
                signal_meta.setdefault(sid, {
                    "signal_id": sid,
                    "signal_type": "pn_group",
                    "label": normalized_ws(group.get("likely_pn")) or pn,
                    "pn_group_id": group.get("pn_group_id"),
                    "likely_pn": normalized_ws(group.get("likely_pn")) or pn,
                    "pn_confidence": group.get("confidence"),
                    "observed_pn_variants": [],
                    "mention_ids": [],
                    "repair_event_ids": set(),
                    "recorded_pieces": 0,
                    "quantity_unstated_mentions": 0,
                    "descriptions": set(),
                    "provisional_family_labels": Counter(),
                    "example_quotes": [],
                })
                mention_to_signal[mid] = sid
                continue
        desc = normalized_ws(m.get("description")) or normalized_ws(m.get("raw_quote")) or "unlabeled replacement"
        key = re.sub(r"[^a-z0-9]+", " ", desc.lower()).strip()[:180]
        sid = "sig_desc_" + sha256_text(key)[:16]
        signal_meta.setdefault(sid, {
            "signal_id": sid,
            "signal_type": "description_only",
            "label": desc,
            "pn_group_id": None,
            "likely_pn": None,
            "pn_confidence": None,
            "observed_pn_variants": [],
            "mention_ids": [],
            "repair_event_ids": set(),
            "recorded_pieces": 0,
            "quantity_unstated_mentions": 0,
            "descriptions": set(),
            "provisional_family_labels": Counter(),
            "example_quotes": [],
        })
        mention_to_signal[mid] = sid

    # Precompute PN raw variants per PN signal.
    for g in pn_map.get("groups") or []:
        sid = "sig_pn_" + str(g["pn_group_id"])[4:]
        if sid not in signal_meta:
            continue
        variants: List[str] = []
        for oid in g.get("member_observed_pn_ids") or []:
            obs = next((x for x in pn_map.get("observations") or [] if str(x.get("observed_pn_id")) == str(oid)), None)
            if obs:
                variants.append(str(obs.get("observed_pn")))
        signal_meta[sid]["observed_pn_variants"] = sorted(set(variants), key=str.lower)

    for m in mentions:
        mid = str(m.get("mention_id"))
        sid = mention_to_signal[mid]
        s = signal_meta[sid]
        s["mention_ids"].append(mid)
        s["repair_event_ids"].add(str(m.get("repair_event_id")))
        if m.get("quantity") is None:
            s["quantity_unstated_mentions"] += 1
        else:
            try:
                s["recorded_pieces"] += int(m.get("quantity"))
            except Exception:
                s["quantity_unstated_mentions"] += 1
        desc = normalized_ws(m.get("description"))
        if desc:
            s["descriptions"].add(desc)
        h = hint_label.get(mid, "")
        if h:
            s["provisional_family_labels"][h] += 1
        q = normalized_ws(m.get("raw_quote"))
        if q and q not in s["example_quotes"] and len(s["example_quotes"]) < 4:
            s["example_quotes"].append(q)

    out: List[Dict[str, Any]] = []
    for sid, s in signal_meta.items():
        out.append({
            "signal_id": sid,
            "signal_type": s["signal_type"],
            "label": s["label"],
            "pn_group_id": s["pn_group_id"],
            "likely_pn": s["likely_pn"],
            "pn_confidence": s["pn_confidence"],
            "observed_pn_variants": s["observed_pn_variants"],
            "mention_ids": s["mention_ids"],
            "repair_event_ids": sorted(s["repair_event_ids"]),
            "repair_event_count": len(s["repair_event_ids"]),
            "recorded_pieces": s["recorded_pieces"],
            "quantity_unstated_mentions": s["quantity_unstated_mentions"],
            "descriptions": sorted(s["descriptions"]),
            "provisional_family_labels": [x for x, _ in s["provisional_family_labels"].most_common(6)],
            "example_quotes": s["example_quotes"],
        })
    out.sort(key=lambda x: (-int(x["repair_event_count"]), -int(x["recorded_pieces"]), str(x["label"]).lower()))
    return out


def coarse_bucket(signal: Dict[str, Any]) -> str:
    text = " ".join([
        str(signal.get("label") or ""),
        " ".join(signal.get("descriptions") or []),
        " ".join(signal.get("provisional_family_labels") or []),
        " ".join(signal.get("observed_pn_variants") or []),
    ]).lower()
    if "fuse" in text or re.search(r"\b\d+\s*(?:a|amp).*\b\d+\s*v\b", text):
        return "fuse_protection"
    if any(x in text for x in ["mosfet", "transistor", "ixfx", "fdh", "38an08", "24n100", "irf99", "f995"]):
        return "semiconductor"
    if any(x in text for x in ["rectifier", "stth", "bridge"]):
        return "semiconductor"
    if any(x in text for x in ["isl", "ucc", "mc340", "lm5110", "lm393", "moc", "chip", " ic", "ic "]):
        return "ic_control"
    if any(x in text for x in ["resistor", "capacitor", "diode"]):
        return "passive"
    if "board" in text:
        return "board_assembly"
    if any(x in text for x in ["holder", "connector", "cover", "handle", "fan", "lug"]):
        return "mechanical"
    return "other"


def pack_signals(signals: Sequence[Dict[str, Any]], max_items: int) -> List[List[str]]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for s in signals:
        buckets[coarse_bucket(s)].append(s)
    batches: List[List[str]] = []
    for bucket in ["semiconductor", "fuse_protection", "ic_control", "passive", "board_assembly", "mechanical", "other"]:
        rows = buckets.get(bucket) or []
        rows.sort(key=lambda x: (-int(x.get("repair_event_count") or 0), str(x.get("label")).lower()))
        for i in range(0, len(rows), max_items):
            batches.append([str(x["signal_id"]) for x in rows[i:i + max_items]])
    return batches


def validate_stage1_families(parsed: Any, signal_ids: Sequence[str], by_id: Dict[str, Dict[str, Any]], batch_index: int) -> List[Dict[str, Any]]:
    known = set(signal_ids)
    assigned: set[str] = set()
    out: List[Dict[str, Any]] = []
    fams = parsed.get("families") if isinstance(parsed, dict) else None
    if not isinstance(fams, list):
        fams = []
    valid_classes = {"semiconductor", "fuse_protection", "ic_control", "passive", "board_assembly", "mechanical", "other"}
    for idx, f in enumerate(fams):
        if not isinstance(f, dict):
            continue
        ids: List[str] = []
        for x in f.get("member_signal_ids") or []:
            s = str(x)
            if s in known and s not in assigned:
                ids.append(s)
        ids = list(dict.fromkeys(ids))
        if not ids:
            continue
        assigned.update(ids)
        label = normalized_ws(f.get("label")) or str(by_id[ids[0]].get("label"))
        fc = str(f.get("functional_class") or coarse_bucket(by_id[ids[0]])).lower()
        if fc not in valid_classes:
            fc = "other"
        out.append({
            "temp_family_id": "tf_" + sha256_text(f"{batch_index}\n" + "\n".join(sorted(ids)))[:16],
            "label": label,
            "functional_class": fc,
            "member_signal_ids": ids,
            "reason": normalized_ws(f.get("reason")),
            "origin": "model_stage1",
        })
    for sid in signal_ids:
        if sid not in assigned:
            out.append({
                "temp_family_id": "tf_" + sha256_text(f"{batch_index}\n{sid}")[:16],
                "label": str(by_id[sid].get("label") or "Other component"),
                "functional_class": coarse_bucket(by_id[sid]),
                "member_signal_ids": [sid],
                "reason": "preserved singleton",
                "origin": "python_singleton",
            })
    return out


def summarize_temp_family(tf: Dict[str, Any], signal_by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    events: set[str] = set()
    pieces = 0
    pns: List[str] = []
    descs: List[str] = []
    for sid in tf.get("member_signal_ids") or []:
        s = signal_by_id[str(sid)]
        events.update(str(x) for x in s.get("repair_event_ids") or [])
        pieces += int(s.get("recorded_pieces") or 0)
        if s.get("likely_pn"):
            pns.append(str(s.get("likely_pn")))
        for d in s.get("descriptions") or []:
            if d not in descs:
                descs.append(str(d))
    return {
        "temp_family_id": tf["temp_family_id"],
        "label": tf["label"],
        "functional_class": tf["functional_class"],
        "repair_event_count": len(events),
        "recorded_pieces": pieces,
        "likely_pns": pns[:12],
        "descriptions": descs[:10],
    }


def validate_merge(parsed: Any, temp_families: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id = {str(x["temp_family_id"]): x for x in temp_families}
    known = set(by_id)
    assigned: set[str] = set()
    out: List[Dict[str, Any]] = []
    fams = parsed.get("families") if isinstance(parsed, dict) else None
    if not isinstance(fams, list):
        fams = []
    valid_classes = {"semiconductor", "fuse_protection", "ic_control", "passive", "board_assembly", "mechanical", "other"}
    for f in fams:
        if not isinstance(f, dict):
            continue
        ids: List[str] = []
        for x in f.get("member_temp_family_ids") or []:
            s = str(x)
            if s in known and s not in assigned:
                ids.append(s)
        ids = list(dict.fromkeys(ids))
        if not ids:
            continue
        assigned.update(ids)
        label = normalized_ws(f.get("label")) or str(by_id[ids[0]].get("label"))
        fc = str(f.get("functional_class") or by_id[ids[0]].get("functional_class") or "other").lower()
        if fc not in valid_classes:
            fc = "other"
        signal_ids: List[str] = []
        for tid in ids:
            signal_ids.extend(str(x) for x in by_id[tid].get("member_signal_ids") or [])
        signal_ids = list(dict.fromkeys(signal_ids))
        out.append({
            "functional_family_id": "ff_" + sha256_text("\n".join(sorted(signal_ids)))[:16],
            "label": label,
            "functional_class": fc,
            "member_temp_family_ids": ids,
            "member_signal_ids": signal_ids,
            "reason": normalized_ws(f.get("reason")),
            "origin": "model_merge",
        })
    for tid, tf in by_id.items():
        if tid not in assigned:
            signal_ids = [str(x) for x in tf.get("member_signal_ids") or []]
            out.append({
                "functional_family_id": "ff_" + sha256_text("\n".join(sorted(signal_ids)))[:16],
                "label": tf["label"],
                "functional_class": tf["functional_class"],
                "member_temp_family_ids": [tid],
                "member_signal_ids": signal_ids,
                "reason": "preserved merge singleton",
                "origin": tf.get("origin"),
            })
    return out


def run_family_grouping(args: argparse.Namespace, signals: Sequence[Dict[str, Any]], source_hash: str) -> Dict[str, Any]:
    output_root = Path(args.output_root)
    by_id = {str(x["signal_id"]): x for x in signals}
    batches = pack_signals(signals, int(args.family_batch_size))
    rinfo = model_info(args.reason_model)
    temp_families: List[Dict[str, Any]] = []
    stage1_meta: List[Dict[str, Any]] = []
    for idx, ids in enumerate(batches, 1):
        compact = []
        for sid in ids:
            s = by_id[sid]
            compact.append({
                "signal_id": sid,
                "signal_type": s["signal_type"],
                "label": s["label"],
                "likely_pn": s.get("likely_pn"),
                "observed_pn_variants": (s.get("observed_pn_variants") or [])[:8],
                "descriptions": (s.get("descriptions") or [])[:5],
                "provisional_family_labels": (s.get("provisional_family_labels") or [])[:4],
                "repair_event_count": s["repair_event_count"],
                "recorded_pieces": s["recorded_pieces"],
            })
        prompt = FAMILY_STAGE1_PROMPT + "\n\nREPLACEMENT SIGNALS:\n" + json.dumps(compact, ensure_ascii=False)
        bdir = output_root / "family_stage1" / f"batch_{idx:04d}"
        parsed_path = bdir / "parsed.json"
        run_path = bdir / "run.json"
        batch_hash = stable_hash({"source": source_hash, "items": compact})
        action = "model_run"
        parsed: Any = {"families": []}
        attempts: List[Dict[str, Any]] = []
        if parsed_path.exists() and run_path.exists() and not args.force:
            try:
                run = load_json(run_path)
                if run.get("batch_manifest_sha256") == batch_hash and run.get("reason_model_digest") == rinfo.get("digest") and run.get("prompt_sha256") == sha256_text(FAMILY_STAGE1_PROMPT):
                    parsed = load_json(parsed_path)
                    attempts = run.get("attempts") or []
                    action = "cache"
            except Exception:
                pass
        if action != "cache":
            try:
                parsed, attempts = call_json_with_retry(args.reason_model, prompt, num_ctx=args.reason_num_ctx, num_predict=args.family_num_predict, timeout=args.timeout, retries=1)
            except Exception as exc:
                parsed = {"families": []}
                attempts = [{"ok": False, "error": str(exc)}]
            save_json(parsed_path, parsed)
            save_json(run_path, {
                "version": VERSION,
                "batch_manifest_sha256": batch_hash,
                "reason_model_digest": rinfo.get("digest"),
                "prompt_sha256": sha256_text(FAMILY_STAGE1_PROMPT),
                "attempts": attempts,
            })
        fams = validate_stage1_families(parsed, ids, by_id, idx)
        temp_families.extend(fams)
        stage1_meta.append({"batch": idx, "signal_count": len(ids), "temp_family_count": len(fams), "run_action": action, "attempts": attempts})
        print(f"[family-stage1 {idx}/{len(batches)}] signals={len(ids)} families={len(fams)} | {action}")

    # Merge all stage-1 families. Keep payload compact; 80/20 final consolidation.
    merge_items = [summarize_temp_family(tf, by_id) for tf in temp_families]
    merge_prompt = FAMILY_MERGE_PROMPT + "\n\nTEMP FAMILIES:\n" + json.dumps(merge_items, ensure_ascii=False)
    mdir = output_root / "family_merge"
    parsed_path = mdir / "parsed.json"
    run_path = mdir / "run.json"
    merge_hash = stable_hash({"source": source_hash, "items": merge_items})
    action = "model_run"
    parsed: Any = {"families": []}
    attempts: List[Dict[str, Any]] = []
    if parsed_path.exists() and run_path.exists() and not args.force:
        try:
            run = load_json(run_path)
            if run.get("batch_manifest_sha256") == merge_hash and run.get("reason_model_digest") == rinfo.get("digest") and run.get("prompt_sha256") == sha256_text(FAMILY_MERGE_PROMPT):
                parsed = load_json(parsed_path)
                attempts = run.get("attempts") or []
                action = "cache"
        except Exception:
            pass
    if action != "cache":
        try:
            parsed, attempts = call_json_with_retry(args.reason_model, merge_prompt, num_ctx=args.reason_num_ctx, num_predict=args.merge_num_predict, timeout=args.timeout, retries=1)
        except Exception as exc:
            parsed = {"families": []}
            attempts = [{"ok": False, "error": str(exc)}]
        save_json(parsed_path, parsed)
        save_json(run_path, {
            "version": VERSION,
            "batch_manifest_sha256": merge_hash,
            "reason_model_digest": rinfo.get("digest"),
            "prompt_sha256": sha256_text(FAMILY_MERGE_PROMPT),
            "attempts": attempts,
        })
    final_families = validate_merge(parsed, temp_families)
    print(f"[family-merge] input={len(temp_families)} final={len(final_families)} | {action}")
    result = {
        "version": VERSION,
        "source_manifest_sha256": source_hash,
        "reason_model_digest": rinfo.get("digest"),
        "stage1_prompt_sha256": sha256_text(FAMILY_STAGE1_PROMPT),
        "merge_prompt_sha256": sha256_text(FAMILY_MERGE_PROMPT),
        "signals": list(signals),
        "stage1_families": temp_families,
        "families": final_families,
        "stage1_run_meta": stage1_meta,
        "merge_run_meta": {"run_action": action, "attempts": attempts},
        "accepted_facts": 0,
        "qdrant_entries": 0,
    }
    save_json(output_root / "functional_family_map_v1_4_4.json", result)
    return result


def aggregate_pn_usage(mentions: Sequence[Dict[str, Any]], pn_map: Dict[str, Any]) -> List[Dict[str, Any]]:
    obs_by_raw = {str(x.get("observed_pn")): x for x in pn_map.get("observations") or []}
    group_by_obs: Dict[str, Dict[str, Any]] = {}
    for g in pn_map.get("groups") or []:
        for oid in g.get("member_observed_pn_ids") or []:
            group_by_obs[str(oid)] = g
    buckets: Dict[str, Dict[str, Any]] = {}
    for m in mentions:
        pn = normalized_ws(m.get("part_number"))
        if not pn:
            continue
        obs = obs_by_raw.get(pn)
        if not obs:
            continue
        g = group_by_obs.get(str(obs.get("observed_pn_id")))
        if not g:
            continue
        gid = str(g["pn_group_id"])
        b = buckets.setdefault(gid, {
            "pn_group_id": gid,
            "likely_pn": normalized_ws(g.get("likely_pn")) or pn,
            "confidence": g.get("confidence") or "low",
            "repair_event_ids": set(),
            "mention_ids": [],
            "recorded_pieces": 0,
            "quantity_unstated_mentions": 0,
            "observed_pn_variants": set(),
            "descriptions": set(),
        })
        b["repair_event_ids"].add(str(m.get("repair_event_id")))
        b["mention_ids"].append(str(m.get("mention_id")))
        b["observed_pn_variants"].add(pn)
        if m.get("description"):
            b["descriptions"].add(normalized_ws(m.get("description")))
        if m.get("quantity") is None:
            b["quantity_unstated_mentions"] += 1
        else:
            try:
                b["recorded_pieces"] += int(m.get("quantity"))
            except Exception:
                b["quantity_unstated_mentions"] += 1
    rows: List[Dict[str, Any]] = []
    for b in buckets.values():
        rows.append({
            "pn_group_id": b["pn_group_id"],
            "likely_pn": b["likely_pn"],
            "confidence": b["confidence"],
            "repairs_containing_pn": len(b["repair_event_ids"]),
            "recorded_pieces": b["recorded_pieces"],
            "quantity_unstated_mentions": b["quantity_unstated_mentions"],
            "observed_pn_variants": sorted(b["observed_pn_variants"], key=str.lower),
            "descriptions": sorted(b["descriptions"]),
            "representative_repair_events": sorted(b["repair_event_ids"])[:20],
        })
    rows.sort(key=lambda x: (-int(x["repairs_containing_pn"]), -int(x["recorded_pieces"]), str(x["likely_pn"]).lower()))
    return rows


def aggregate_functional_families(mentions: Sequence[Dict[str, Any]], family_result: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    signal_by_id = {str(x["signal_id"]): x for x in family_result.get("signals") or []}
    family_by_signal: Dict[str, Dict[str, Any]] = {}
    for fam in family_result.get("families") or []:
        for sid in fam.get("member_signal_ids") or []:
            family_by_signal[str(sid)] = fam
    mention_to_signal: Dict[str, str] = {}
    for sid, signal in signal_by_id.items():
        for mid in signal.get("mention_ids") or []:
            mention_to_signal[str(mid)] = sid
    buckets: Dict[str, Dict[str, Any]] = {}
    pn_group_to_family: Dict[str, Counter[str]] = defaultdict(Counter)
    for m in mentions:
        mid = str(m.get("mention_id"))
        sid = mention_to_signal.get(mid)
        fam = family_by_signal.get(str(sid)) if sid else None
        if not fam:
            continue
        fid = str(fam["functional_family_id"])
        b = buckets.setdefault(fid, {
            "functional_family_id": fid,
            "label": fam.get("label") or "Other component",
            "functional_class": fam.get("functional_class") or "other",
            "repair_event_ids": set(),
            "mention_ids": [],
            "recorded_pieces": 0,
            "quantity_unstated_mentions": 0,
            "pn_groups": Counter(),
            "descriptions": set(),
        })
        event = str(m.get("repair_event_id"))
        b["repair_event_ids"].add(event)
        b["mention_ids"].append(mid)
        if m.get("quantity") is None:
            b["quantity_unstated_mentions"] += 1
        else:
            try:
                b["recorded_pieces"] += int(m.get("quantity"))
            except Exception:
                b["quantity_unstated_mentions"] += 1
        if m.get("description"):
            b["descriptions"].add(normalized_ws(m.get("description")))
        signal = signal_by_id.get(str(sid))
        if signal and signal.get("pn_group_id"):
            pg = str(signal.get("pn_group_id"))
            b["pn_groups"][pg] += 1
            pn_group_to_family[pg][fid] += 1
    rows: List[Dict[str, Any]] = []
    for b in buckets.values():
        rows.append({
            "functional_family_id": b["functional_family_id"],
            "label": b["label"],
            "functional_class": b["functional_class"],
            "repairs_containing_family": len(b["repair_event_ids"]),
            "recorded_pieces": b["recorded_pieces"],
            "quantity_unstated_mentions": b["quantity_unstated_mentions"],
            "pn_group_ids": [x for x, _ in b["pn_groups"].most_common()],
            "descriptions": sorted(b["descriptions"]),
            "representative_repair_events": sorted(b["repair_event_ids"])[:20],
        })
    rows.sort(key=lambda x: (-int(x["repairs_containing_family"]), -int(x["recorded_pieces"]), str(x["label"]).lower()))
    pn_primary_family: Dict[str, str] = {}
    for pg, counts in pn_group_to_family.items():
        if counts:
            pn_primary_family[pg] = counts.most_common(1)[0][0]
    return rows, pn_primary_family


def write_outputs(args: argparse.Namespace, mentions: Sequence[Dict[str, Any]], pn_map: Dict[str, Any], family_result: Dict[str, Any], source_hash: str, source_meta: Dict[str, Any]) -> Dict[str, Any]:
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    pn_rows = aggregate_pn_usage(mentions, pn_map)
    fam_rows, pn_primary_family = aggregate_functional_families(mentions, family_result)
    fam_by_id = {str(x["functional_family_id"]): x for x in fam_rows}
    pn_by_id = {str(x["pn_group_id"]): x for x in pn_rows}

    # Attach readable PN breakdown to families and family label to PN ranking.
    for fam in fam_rows:
        pns = []
        for pg in fam.get("pn_group_ids") or []:
            p = pn_by_id.get(str(pg))
            if p:
                pns.append({
                    "likely_pn": p["likely_pn"],
                    "confidence": p["confidence"],
                    "repairs_containing_pn": p["repairs_containing_pn"],
                    "recorded_pieces": p["recorded_pieces"],
                    "observed_pn_variants": p["observed_pn_variants"],
                    "pn_group_id": p["pn_group_id"],
                })
        pns.sort(key=lambda x: (-int(x["repairs_containing_pn"]), -int(x["recorded_pieces"]), str(x["likely_pn"]).lower()))
        fam["part_number_breakdown"] = pns
    for p in pn_rows:
        fid = pn_primary_family.get(str(p["pn_group_id"]))
        p["functional_family_id"] = fid
        p["functional_family_label"] = fam_by_id.get(fid, {}).get("label") if fid else None

    save_json(output_root / "functional_family_frequency_v1_4_4.json", {"version": VERSION, "rows": fam_rows})
    save_json(output_root / "part_number_usage_v1_4_4.json", {"version": VERSION, "rows": pn_rows})

    with (output_root / "functional_family_frequency_v1_4_4.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["rank", "family", "functional_class", "repairs_containing_family", "recorded_pieces", "quantity_unstated_mentions", "top_part_numbers", "functional_family_id"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for idx, r in enumerate(fam_rows, 1):
            w.writerow({
                "rank": idx,
                "family": r["label"],
                "functional_class": r["functional_class"],
                "repairs_containing_family": r["repairs_containing_family"],
                "recorded_pieces": r["recorded_pieces"],
                "quantity_unstated_mentions": r["quantity_unstated_mentions"],
                "top_part_numbers": "; ".join(str(x["likely_pn"]) for x in r.get("part_number_breakdown") or [] if x.get("likely_pn"))[:2000],
                "functional_family_id": r["functional_family_id"],
            })

    with (output_root / "part_number_usage_v1_4_4.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["rank", "likely_pn", "confidence", "functional_family", "repairs_containing_pn", "recorded_pieces", "quantity_unstated_mentions", "observed_pn_variants", "pn_group_id"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for idx, r in enumerate(pn_rows, 1):
            w.writerow({
                "rank": idx,
                "likely_pn": r["likely_pn"],
                "confidence": r["confidence"],
                "functional_family": r.get("functional_family_label") or "",
                "repairs_containing_pn": r["repairs_containing_pn"],
                "recorded_pieces": r["recorded_pieces"],
                "quantity_unstated_mentions": r["quantity_unstated_mentions"],
                "observed_pn_variants": "; ".join(r["observed_pn_variants"]),
                "pn_group_id": r["pn_group_id"],
            })

    lines = [
        "# Nova DRL RCL1A 80/20 Parts Intelligence v1.4.4",
        "",
        "Operating mode: PROVISIONAL 80/20 VOLUME-BASED PARTS INTELLIGENCE",
        f"Source: frozen v1.4.3 replacement evidence | {source_meta.get('mentions_path')}",
        f"Source replacement mentions: {len(mentions)}",
        f"Distinct repair events represented: {len({str(m.get('repair_event_id')) for m in mentions})}",
        f"Observed raw PN strings: {len(pn_map.get('observations') or [])}",
        f"Likely PN groups: {len(pn_rows)}",
        f"Functional replacement families: {len(fam_rows)}",
        "Vision calls: 0",
        "NAS discovery/rescan: 0",
        "Source Line Card reads: 0",
        "Prior hosted benchmark read by runtime: NO",
        "Accepted facts: 0",
        "Qdrant writes: OFF",
        "",
        "TOP FUNCTIONAL REPLACEMENT FAMILIES — PROVISIONAL",
        "--------------------------------------------------",
    ]
    for idx, r in enumerate(fam_rows[:40], 1):
        top_pns = [x["likely_pn"] for x in r.get("part_number_breakdown") or [] if x.get("likely_pn")][:8]
        suffix = f" | likely PNs: {', '.join(top_pns)}" if top_pns else ""
        lines.append(f"{idx:2d}. {r['label']} | repairs={r['repairs_containing_family']} | recorded pieces={r['recorded_pieces']} | qty-unstated mentions={r['quantity_unstated_mentions']}{suffix}")

    lines.extend(["", "MOST REPLACED COMPONENTS — LIKELY ACTUAL PN", "---------------------------------------------"])
    for idx, r in enumerate(pn_rows[:50], 1):
        variants = [x for x in r["observed_pn_variants"] if compact_alnum(x) != compact_alnum(r["likely_pn"])]
        var_text = f" | variants: {', '.join(variants[:8])}" if variants else ""
        fam_text = f" | family: {r.get('functional_family_label')}" if r.get("functional_family_label") else ""
        lines.append(f"{idx:2d}. {r['likely_pn']} | repairs={r['repairs_containing_pn']} | recorded pieces={r['recorded_pieces']} | qty-unstated mentions={r['quantity_unstated_mentions']} | confidence={r['confidence']}{fam_text}{var_text}")

    lines.extend([
        "",
        "INTERPRETATION POLICY",
        "---------------------",
        "Family grouping may intentionally combine different exact PNs when they serve the same useful repair class/function.",
        "Exact PN usage remains separately ranked underneath the family view.",
        "Same-spec alternate/substitute fuse PNs may share one functional fuse family while remaining distinct PN usage groups.",
        "likely_pn is a recurrence-based best guess grounded in observed v1.4.3 PN strings; all raw variants remain preserved.",
        "Python counts distinct repair events and explicit quantities; unstated quantities are never estimated.",
        "No new OCR/vision work is performed in v1.4.4.",
        "No automatic human approval is performed.",
    ])
    summary_path = output_root / "rcl1a_parts_intelligence_summary_v1_4_4.txt"
    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    combined = {
        "version": VERSION,
        "source_version": SOURCE_VERSION,
        "source_manifest_sha256": source_hash,
        "source_meta": source_meta,
        "functional_families": fam_rows,
        "part_number_usage": pn_rows,
        "accepted_facts": 0,
        "qdrant_entries": 0,
        "prior_hosted_benchmark_runtime_input": False,
    }
    save_json(output_root / "rcl1a_parts_intelligence_v1_4_4.json", combined)
    return {"family_rows": fam_rows, "pn_rows": pn_rows, "summary_path": summary_path}


def validate_source_files(source_root: Path) -> Tuple[Dict[str, Path], List[str]]:
    paths = source_paths(source_root)
    missing = [name for name in ("mentions",) if not paths[name].exists()]
    return paths, missing


def load_source(source_root: Path) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Dict[str, Any]]:
    paths, missing = validate_source_files(source_root)
    if missing:
        raise FileNotFoundError("Missing required v1.4.3 source file(s): " + ", ".join(str(paths[x]) for x in missing))
    mentions = load_jsonl(paths["mentions"])
    family_map = load_json(paths["family_map"]) if paths["family_map"].exists() else None
    frequency = load_json(paths["frequency"]) if paths["frequency"].exists() else None
    manifest = load_json(paths["manifest"]) if paths["manifest"].exists() else None
    meta = {
        "source_root": str(source_root),
        "mentions_path": str(paths["mentions"]),
        "family_map_path": str(paths["family_map"]) if paths["family_map"].exists() else None,
        "frequency_path": str(paths["frequency"]) if paths["frequency"].exists() else None,
        "manifest_path": str(paths["manifest"]) if paths["manifest"].exists() else None,
        "mentions_sha256": sha256_text(paths["mentions"].read_text(encoding="utf-8", errors="ignore")),
        "family_map_sha256": sha256_text(paths["family_map"].read_text(encoding="utf-8", errors="ignore")) if paths["family_map"].exists() else None,
        "source_manifest_declared_version": manifest.get("version") if isinstance(manifest, dict) else None,
    }
    return mentions, family_map, frequency, manifest, meta


def source_manifest_hash(meta: Dict[str, Any]) -> str:
    return stable_hash({"mentions_sha256": meta.get("mentions_sha256"), "family_map_sha256": meta.get("family_map_sha256")})


def print_status(args: argparse.Namespace) -> int:
    source_root = Path(args.source_root)
    paths, missing = validate_source_files(source_root)
    print(f"# Nova DRL RCL1A 80/20 Parts Intelligence Status v{VERSION}")
    print(f"v1.4.3 source root: {'FOUND' if source_root.exists() else 'NOT FOUND'} | {source_root}")
    print(f"Replacement mentions: {'FOUND' if paths['mentions'].exists() else 'NOT FOUND'} | {paths['mentions']}")
    mentions: List[Dict[str, Any]] = []
    if paths["mentions"].exists():
        try:
            mentions = load_jsonl(paths["mentions"])
        except Exception:
            pass
    print(f"Mention records:      {len(mentions)}")
    print(f"Distinct events:      {len({str(m.get('repair_event_id')) for m in mentions}) if mentions else 0}")
    print(f"v1.4.3 family map:    {'FOUND' if paths['family_map'].exists() else 'OPTIONAL / NOT FOUND'} | {paths['family_map']}")
    if paths["family_map"].exists():
        try:
            fm = load_json(paths["family_map"])
            print(f"v1.4.3 families:      {len(fm.get('families') or [])}")
        except Exception:
            pass
    rinfo = model_info(args.reason_model)
    print(f"Reason model:         {'FOUND' if rinfo.get('available') else 'NOT FOUND'} | {args.reason_model}")
    print("Vision model calls:   OFF | v1.4.4 is post-processing only")
    print("NAS scan/source reads: NONE | uses frozen local v1.4.3 evidence")
    print("Prior hosted benchmark runtime input: NONE")
    print("Accepted facts:       0")
    print("Qdrant:               OFF")
    return 1 if missing else 0


def print_plan(args: argparse.Namespace) -> int:
    mentions, family_map, frequency, manifest, meta = load_source(Path(args.source_root))
    hint_id, hint_label = family_hint_maps(family_map)
    observations = build_pn_observations(mentions, hint_id, hint_label)
    components = pn_candidate_components(observations, float(args.pn_candidate_threshold))
    pn_batches = pack_components(components, int(args.pn_batch_size))
    # Before PN model grouping, estimate signal count as raw PN observations + unique no-PN description strings.
    desc_keys = set()
    for m in mentions:
        if normalized_ws(m.get("part_number")):
            continue
        desc = normalized_ws(m.get("description")) or normalized_ws(m.get("raw_quote")) or "unlabeled replacement"
        desc_keys.add(re.sub(r"[^a-z0-9]+", " ", desc.lower()).strip()[:180])
    estimated_signals = len(observations) + len(desc_keys)
    estimated_family_batches = max(1, (estimated_signals + int(args.family_batch_size) - 1) // int(args.family_batch_size))
    print(f"# Nova DRL RCL1A 80/20 Parts Intelligence v{VERSION} — PLAN ONLY")
    print(f"Source:                    frozen v1.4.3 replacement mentions")
    print(f"Source root:               {args.source_root}")
    print(f"Replacement mentions:      {len(mentions)}")
    print(f"Distinct repair events:    {len({str(m.get('repair_event_id')) for m in mentions})}")
    print(f"v1.4.3 provisional families: {len((family_map or {}).get('families') or [])}")
    print(f"Observed raw PN strings:   {len(observations)}")
    print(f"PN candidate components:  {len(components)}")
    print(f"PN grouping calls:         {len(pn_batches)} x 14B maximum before cache")
    print(f"Estimated replacement signals: {estimated_signals}")
    print(f"Family stage-1 calls:      ~{estimated_family_batches} x 14B before cache")
    print("Family merge calls:        1 x 14B")
    print("Vision calls:              0")
    print("NAS discovery/rescan:      0")
    print("Source Line Card reads:    0")
    print("Frequency/quantity counts: Python; distinct repair events + explicit quantities")
    print("Family policy:             broad useful class allowed; exact PN ranking preserved separately")
    print("Fuse policy:               same-spec substitutes may share family; PN usage remains separate")
    print("Prior hosted benchmark:    NOT READ")
    print("Accepted facts:            0")
    print("Qdrant:                    OFF")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=f"Nova DRL RCL1A 80/20 Parts Intelligence v{VERSION}")
    p.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT))
    p.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    p.add_argument("--reason-model", default=DEFAULT_REASON_MODEL)
    p.add_argument("--reason-num-ctx", type=int, default=16384)
    p.add_argument("--pn-num-predict", type=int, default=3072)
    p.add_argument("--family-num-predict", type=int, default=3072)
    p.add_argument("--merge-num-predict", type=int, default=3072)
    p.add_argument("--timeout", type=int, default=1200)
    p.add_argument("--pn-candidate-threshold", type=float, default=0.64)
    p.add_argument("--pn-batch-size", type=int, default=50)
    p.add_argument("--family-batch-size", type=int, default=48)
    p.add_argument("--status", action="store_true")
    p.add_argument("--plan-only", action="store_true")
    p.add_argument("--force", action="store_true", help="Ignore valid v1.4.4 model caches")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.status:
        return print_status(args)
    if args.plan_only:
        return print_plan(args)

    mentions, family_map, frequency, source_manifest, source_meta = load_source(Path(args.source_root))
    if not mentions:
        raise RuntimeError("v1.4.3 replacement mention file is empty")
    rinfo = model_info(args.reason_model)
    if not rinfo.get("available"):
        raise RuntimeError(f"Required reason model not available: {args.reason_model}")
    source_hash = source_manifest_hash(source_meta)
    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    pn_map = run_pn_grouping(args, mentions, family_map, source_hash)
    signals = build_signal_rows(mentions, pn_map, family_map)
    family_result = run_family_grouping(args, signals, source_hash)
    outputs = write_outputs(args, mentions, pn_map, family_result, source_hash, source_meta)

    manifest = {
        "version": VERSION,
        "source_version": SOURCE_VERSION,
        "source_manifest_sha256": source_hash,
        "source_meta": source_meta,
        "replacement_mentions": len(mentions),
        "distinct_repair_events": len({str(m.get('repair_event_id')) for m in mentions}),
        "observed_raw_pn_strings": len(pn_map.get("observations") or []),
        "likely_pn_groups": len(outputs["pn_rows"]),
        "functional_replacement_families": len(outputs["family_rows"]),
        "vision_calls": 0,
        "nas_scans": 0,
        "source_line_card_reads": 0,
        "accepted_facts": 0,
        "qdrant_entries": 0,
        "prior_hosted_benchmark_runtime_input": False,
        "files": {
            "summary": str(outputs["summary_path"]),
            "functional_family_json": str(output_root / "functional_family_frequency_v1_4_4.json"),
            "functional_family_csv": str(output_root / "functional_family_frequency_v1_4_4.csv"),
            "part_number_json": str(output_root / "part_number_usage_v1_4_4.json"),
            "part_number_csv": str(output_root / "part_number_usage_v1_4_4.csv"),
            "intelligence_json": str(output_root / "rcl1a_parts_intelligence_v1_4_4.json"),
            "pn_group_map": str(output_root / "pn_group_map_v1_4_4.json"),
            "functional_family_map": str(output_root / "functional_family_map_v1_4_4.json"),
        },
    }
    save_json(output_root / "rcl1a_parts_intelligence_manifest_v1_4_4.json", manifest)

    print("\n# COMPLETE")
    print(f"Source replacement mentions: {len(mentions)}")
    print(f"Distinct repair events:       {manifest['distinct_repair_events']}")
    print(f"Observed raw PN strings:      {manifest['observed_raw_pn_strings']}")
    print(f"Likely PN groups:             {manifest['likely_pn_groups']}")
    print(f"Functional families:          {manifest['functional_replacement_families']}")
    print("Vision calls:                 0")
    print("NAS scans/source reads:       0")
    print("Accepted facts:               0")
    print("Qdrant:                       OFF")
    print(f"Summary: {outputs['summary_path']}")
    print(f"PN CSV:  {output_root / 'part_number_usage_v1_4_4.csv'}")
    print(f"Family CSV: {output_root / 'functional_family_frequency_v1_4_4.csv'}")
    print(f"Manifest: {output_root / 'rcl1a_parts_intelligence_manifest_v1_4_4.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
